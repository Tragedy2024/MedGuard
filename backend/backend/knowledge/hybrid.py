"""MedGuard 的混合知识检索深模块。

外部接口只有 ``search(kb, query, top_k)``：调用方不需要知道 BM25、BGE 或
RRF。默认尝试 BGE adapter；未安装依赖、模型加载/建索引/查询失败时，模块
确定性回退到现有 BM25。

论文对应：BGE-M3/FlagEmbedding（语义召回）+ RAG-Fusion 的 RRF 排名
融合 + CRAG 的相关性门控思想。这里不采用 CRAG 的开放网页搜索：医疗语境中
证据不足应拒答，而不是自动扩大到不受控来源。
"""
from __future__ import annotations

import math
import threading
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

from backend import config
from backend.knowledge import corpus
from backend.knowledge.retrieval import BM25Index

_RRF_K = 60


class Encoder(Protocol):
    """内部 seam：生产用 BGE adapter，测试用内存 fake。"""

    def encode_corpus(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...

    def encode_query(self, text: str) -> Sequence[float]: ...


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class DenseIndex:
    """小语料的精确向量检索；无需额外向量数据库。"""

    def __init__(self, docs: Sequence[corpus.Doc], encoder: Encoder,
                 min_score: float):
        self.docs = list(docs)
        self._encoder = encoder
        self._min_score = min_score
        texts = [corpus.doc_text_for_index(d) for d in self.docs]
        self._vectors = [list(v) for v in encoder.encode_corpus(texts)]
        if len(self._vectors) != len(self.docs):
            raise ValueError("BGE 返回的向量数与知识条目数不一致")

    def search(self, query: str, top_k: int) -> List[Tuple[corpus.Doc, float]]:
        qv = list(self._encoder.encode_query(query))
        if not qv:
            return []
        hits = [(d, _cosine(qv, v)) for d, v in zip(self.docs, self._vectors)]
        hits = [(d, s) for d, s in hits if s >= self._min_score]
        hits.sort(key=lambda x: x[1], reverse=True)
        return hits[:top_k]


class BGEEncoder:
    """FlagEmbedding adapter；构造时才加载权重。"""

    def __init__(self, model_name: str):
        self._is_m3 = model_name.rstrip("/").lower().endswith("bge-m3")
        if self._is_m3:
            from FlagEmbedding import BGEM3FlagModel
            self._model = BGEM3FlagModel(model_name, use_fp16=False)
        else:
            from FlagEmbedding import FlagModel
            self._model = FlagModel(
                model_name,
                query_instruction_for_retrieval="为这个句子生成表示以用于检索相关文章：",
                use_fp16=False,
            )

    def _encode(self, texts: Sequence[str], *, queries: bool
                ) -> Sequence[Sequence[float]]:
        if self._is_m3:
            method = (self._model.encode_queries if queries
                      else self._model.encode_corpus)
            result = method(
                list(texts), batch_size=12, max_length=512,
                return_dense=True, return_sparse=False, return_colbert_vecs=False,
            )
            return result["dense_vecs"]
        method = (self._model.encode_queries if queries
                  else self._model.encode_corpus)
        return method(list(texts), batch_size=32, max_length=512,
                      convert_to_numpy=True)

    def encode_corpus(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return self._encode(texts, queries=False)

    def encode_query(self, text: str) -> Sequence[float]:
        rows = self._encode([text], queries=True)
        return rows[0] if len(rows) else []


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[Tuple[corpus.Doc, float]]],
    *, rrf_k: int = _RRF_K,
) -> List[Tuple[corpus.Doc, float]]:
    """融合不同分数尺度的排名；同一文档在多路出现会自然加分。"""
    scores: Dict[str, float] = {}
    docs: Dict[str, corpus.Doc] = {}
    for ranking in rankings:
        for rank, (doc, _raw_score) in enumerate(ranking, 1):
            docs[doc.id] = doc
            scores[doc.id] = scores.get(doc.id, 0.0) + 1.0 / (rrf_k + rank)
    ordered = sorted(scores, key=scores.get, reverse=True)
    return [(docs[doc_id], scores[doc_id] * 100) for doc_id in ordered]


class HybridRetriever:
    """BM25 + 可选 dense；任一 dense 故障均在模块内降级。"""

    def __init__(self, docs: Sequence[corpus.Doc], *,
                 encoder: Optional[Encoder] = None,
                 dense_min_score: float = 0.58,
                 candidate_k: int = 20):
        self.docs = list(docs)
        self._bm25 = BM25Index(self.docs)
        self._dense = (DenseIndex(self.docs, encoder, dense_min_score)
                       if encoder is not None else None)
        self._candidate_k = max(3, candidate_k)

    def search(self, query: str, top_k: int = 3) -> List[Tuple[corpus.Doc, float]]:
        rankings: List[List[Tuple[corpus.Doc, float]]] = [
            self._bm25.search(query, top_k=self._candidate_k)
        ]
        if self._dense is not None:
            try:
                rankings.append(self._dense.search(query, self._candidate_k))
            except Exception as exc:  # noqa: BLE001 - 运行时故障必须降级
                print(f"[rag] BGE 查询失败，回退 BM25：{exc}", flush=True)
                self._dense = None
        fused = reciprocal_rank_fusion(rankings)
        return fused[:top_k]


_LOCK = threading.Lock()
_RETRIEVER: Optional[HybridRetriever] = None


def _optional_encoder() -> Optional[Encoder]:
    if not config.RAG_DENSE_ENABLED:
        return None
    try:
        return BGEEncoder(config.RAG_DENSE_MODEL)
    except Exception as exc:  # noqa: BLE001 - 可选 adapter 失败必须安全回退
        print(f"[rag] BGE 不可用，回退 BM25：{exc}", flush=True)
        return None


def get_retriever(kb: Dict[str, Any]) -> HybridRetriever:
    global _RETRIEVER
    with _LOCK:
        if _RETRIEVER is None:
            docs = corpus.build_docs(kb)
            from backend.knowledge import tokenize
            tokenize.prepare(corpus.aliases_of(docs))
            encoder = _optional_encoder()
            try:
                _RETRIEVER = HybridRetriever(
                    docs, encoder=encoder,
                    dense_min_score=config.RAG_DENSE_MIN_SCORE,
                    candidate_k=config.RAG_CANDIDATE_K,
                )
            except Exception as exc:  # noqa: BLE001 - 建索引失败必须降级
                if encoder is None:
                    raise
                print(f"[rag] BGE 建索引失败，回退 BM25：{exc}", flush=True)
                _RETRIEVER = HybridRetriever(
                    docs, candidate_k=config.RAG_CANDIDATE_K,
                )
        return _RETRIEVER


def search(kb: Dict[str, Any], query: str,
           top_k: int = 3) -> List[Tuple[corpus.Doc, float]]:
    """模块唯一业务接口。证据不足时返回空列表，不强行补足 top-k。"""
    return get_retriever(kb).search(query, top_k=top_k)


def reset() -> None:
    global _RETRIEVER
    with _LOCK:
        _RETRIEVER = None
