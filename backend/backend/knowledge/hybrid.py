"""MedGuard 的混合知识检索深模块。

外部接口只有 ``search(kb, query, top_k)``：调用方不需要知道 BM25、BGE 或
RRF。默认尝试 BGE adapter；未安装依赖、模型加载/建索引/查询失败时，模块
确定性回退到现有 BM25。

双知识源（2026-09 设计评审）：
- 第一知识源 YAML（约 91 条，人工整理、可信度高）——权重 1.0；
- 第二知识源 OpenCMKG（约 1.5 万条检索文档，覆盖面大、质量等级低，
  knowledge/opencmkg.py）——默认关闭（``RAG_OPENCMKG_ENABLED``），开启后
  按 ``RAG_OPENCMKG_WEIGHT`` 加权参与 RRF，且当 YAML 有可靠命中时，
  top_k 至少保留一条人工证据，避免 OpenCMKG 占满结果。

论文对应：BGE-M3/FlagEmbedding（语义召回）+ RAG-Fusion 的 RRF 排名
融合 + CRAG 的相关性门控思想。这里不采用 CRAG 的开放网页搜索：医疗语境中
证据不足应拒答，而不是自动扩大到不受控来源。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

from backend import config
from backend.knowledge import corpus, opencmkg, retrieval
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


# ── 语料向量磁盘缓存（评审 A2b）────────────────────────────────
#
# BGE 编码 1.5 万条语料是冷启动大头（本机实测 60 秒级以上）。把语料向量
# 写成 .npy 缓存，键绑定「模型名 + 文档内容哈希」：模型或语料一变自动
# 重建。只对真实 BGE 编码器生效（fake encoder 与测试路径不落盘）。

def _dense_cache_path(label: str) -> str:
    """向量缓存的 .npy 路径（meta 为同名 .meta.json）。"""
    return os.path.join(config.DATA_DIR, f"dense_{label}.npy")


def _dense_vectors(texts: Sequence[str], encoder: Encoder, *,
                   cache_file: Optional[str] = None) -> Sequence[Sequence[float]]:
    """编码语料向量；带 cache_file 时优先读缓存。

    缓存元数据 = {model, docs_sha256, built_at}，加载后还要核对 .npy 形状
    （行数=文档数）；任一不匹配或损坏都静默走重新编码——没有路径能把
    错误缓存送进检索。
    """
    if cache_file:
        meta_path = cache_file[:-4] + ".meta.json"
        try:
            if os.path.exists(cache_file) and os.path.exists(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    saved = json.load(f)
                import numpy as np
                arr = np.load(cache_file)
                # 评审 P1：必须与**当前**模型名、**当前**语料哈希逐字节比较。
                # 只检查元数据非空 + 行数相等，会在「内容变了但条数不变」时
                # 复用旧向量。
                digest = hashlib.sha256(
                    "\n".join(texts).encode("utf-8")).hexdigest()
                if (saved.get("model") == getattr(
                        encoder, "cache_key",
                        getattr(encoder, "model_name", ""))
                        and saved.get("docs_sha256") == digest
                        and arr.ndim == 2 and arr.shape[0] == len(texts)):
                    return [list(v) for v in arr]
        except Exception:
            pass  # 缓存缺失/损坏/键不匹配 → 重新编码

    vectors = [list(v) for v in encoder.encode_corpus(texts)]

    if cache_file and vectors:
        try:
            import numpy as np
            digest = hashlib.sha256(
                "\n".join(texts).encode("utf-8")).hexdigest()
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            np.save(cache_file, np.asarray(vectors, dtype=np.float32))
            with open(cache_file[:-4] + ".meta.json", "w", encoding="utf-8") as f:
                json.dump({
                    "model": getattr(
                        encoder, "cache_key",
                        getattr(encoder, "model_name", "")),
                    "docs_sha256": digest,
                    "count": len(vectors),
                    "built_at": datetime.now(timezone.utc).isoformat(),
                }, f, ensure_ascii=False)
        except Exception:
            pass  # 写缓存失败不影响本次查询
    return vectors


class DenseIndex:
    """小语料的精确向量检索；无需额外向量数据库。

    向量化（评审 A2b）：语料向量预归一化成 float32 矩阵，查询时一次
    ``matrix @ query`` 完成全部余弦计算（1.5 万 × 512 维约 30 MB，
    毫秒级）；numpy 不可用时回退逐条 Python 余弦。
    """

    def __init__(self, docs: Sequence[corpus.Doc], encoder: Encoder,
                 min_score: float, cache_label: Optional[str] = None):
        self.docs = list(docs)
        self._encoder = encoder
        self._min_score = min_score
        texts = [corpus.doc_text_for_index(d) for d in self.docs]
        cache_file = None
        if (cache_label and getattr(encoder, "cache_key", "")
                and config.RAG_DENSE_CACHE):
            cache_file = _dense_cache_path(cache_label)
        self._vectors = _dense_vectors(texts, encoder, cache_file=cache_file)
        if len(self._vectors) != len(self.docs):
            raise ValueError("BGE 返回的向量数与知识条目数不一致")
        self._np = None
        try:
            import numpy as np
            arr = np.asarray(self._vectors, dtype=np.float32)
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._np = arr / norms
        except Exception:
            self._np = None  # numpy 不可用 → Python 余弦兜底

    def search(self, query: str, top_k: int) -> List[Tuple[corpus.Doc, float]]:
        qv = list(self._encoder.encode_query(query))
        if not qv:
            return []
        if self._np is not None:
            try:
                import numpy as np
                q = np.asarray(qv, dtype=np.float32)
                nq = float(np.linalg.norm(q))
                if nq > 0:
                    q = q / nq
                scores = self._np @ q
                order = np.argsort(-scores)
                hits = []
                for i in order:
                    s = float(scores[i])
                    if s < self._min_score:
                        break
                    hits.append((self.docs[int(i)], s))
                    if len(hits) >= top_k:
                        break
                return hits
            except Exception:
                pass  # 运行时故障 → 走 Python 逐条余弦
        hits = [(d, _cosine(qv, v)) for d, v in zip(self.docs, self._vectors)]
        hits = [(d, s) for d, s in hits if s >= self._min_score]
        hits.sort(key=lambda x: x[1], reverse=True)
        return hits[:top_k]


def _model_cached_locally(model_name: str) -> bool:
    """模型是否已有本地 HF 快照（用于加载前判定离线，见 BGEEncoder）。"""
    root = os.environ.get("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface")
    hub = os.environ.get("HF_HUB_CACHE") or os.path.join(root, "hub")
    return os.path.isdir(
        os.path.join(hub, "models--" + model_name.replace("/", "--"))
    )


class BGEEncoder:
    """FlagEmbedding adapter；构造时才加载权重。"""

    def __init__(self, model_name: str):
        self.model_name = model_name      # 缓存失效键之一（A2b）
        self.cache_key = f"flagembedding::{model_name}"
        self._is_m3 = model_name.rstrip("/").lower().endswith("bge-m3")
        # huggingface_hub 在**首次读取时**缓存环境变量（HF_HUB_OFFLINE 等），
        # 进程中途再设无效（实测：中途设置后 AutoTokenizer 仍联网抛
        # httpx.ConnectTimeout）。所以必须在任何 HF 导入前判定网络需求：
        #   已缓存快照 → 强制纯离线加载（确定性，演示/评测不因网络抖动退化）；
        #   无快照   → 正常在线加载（首次下载），失败由调用方统一回退 BM25。
        if _model_cached_locally(model_name):
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        self._load_model()

    def _load_model(self) -> None:
        if self._is_m3:
            from FlagEmbedding import BGEM3FlagModel
            self._model = BGEM3FlagModel(self.model_name, use_fp16=False)
        else:
            from FlagEmbedding import FlagModel
            self._model = FlagModel(
                self.model_name,
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


class FastEmbedEncoder:
    """轻量 ONNX BGE adapter，供 Vercel 等无 PyTorch 环境使用。

    FastEmbed 官方提供 ``BAAI/bge-small-zh-v1.5`` 的量化 ONNX 版本。
    模型缓存在可写的 DATA_DIR；Vercel 上即 ``/tmp/medguard``。它与
    FlagEmbedding 使用不同 cache_key，避免两种推理后端误复用彼此的语料
    向量缓存。
    """

    def __init__(self, model_name: str):
        cache_root = os.path.join(config.DATA_DIR, "fastembed_cache")
        cache_locations = {
            "FASTEMBED_CACHE_PATH": cache_root,
            "HF_HOME": os.path.join(cache_root, "huggingface"),
            "HF_HUB_CACHE": os.path.join(cache_root, "huggingface", "hub"),
            "HF_XET_CACHE": os.path.join(cache_root, "huggingface", "xet"),
            "XDG_CACHE_HOME": os.path.join(cache_root, "xdg"),
        }
        on_vercel = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))
        for name, path in cache_locations.items():
            if on_vercel:
                os.environ[name] = path
            else:
                os.environ.setdefault(name, path)
            os.makedirs(os.environ[name], exist_ok=True)
        # Vercel 的 HOME 只读；禁用 Xet 后 Hugging Face 走普通 HTTP 下载，
        # 避免 Xet 即使模型目录在 /tmp，仍尝试向 ~/.cache 写日志。
        if on_vercel:
            os.environ["HF_HUB_DISABLE_XET"] = "1"

        from fastembed import TextEmbedding

        self.model_name = model_name
        self.cache_key = f"fastembed::{model_name}"
        self._model = TextEmbedding(
            model_name=model_name,
            cache_dir=cache_root,
        )

    def encode_corpus(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return list(self._model.passage_embed(list(texts), batch_size=32))

    def encode_query(self, text: str) -> Sequence[float]:
        rows = list(self._model.query_embed(text))
        return rows[0] if rows else []


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[Tuple[corpus.Doc, float]]],
    *, rrf_k: int = _RRF_K,
    weights: Sequence[float] = (),
) -> List[Tuple[corpus.Doc, float]]:
    """融合不同分数尺度的排名；同一文档在多路出现会自然加分。

    weights：与 rankings 一一对应的来源权重（默认全 1.0）——用于压制
    低质量知识源（如 OpenCMKG）对排名的淹没。
    """
    if weights and len(weights) != len(rankings):
        raise ValueError("weights 与 rankings 长度不一致")
    scores: Dict[str, float] = {}
    docs: Dict[str, corpus.Doc] = {}
    for i, ranking in enumerate(rankings):
        w = weights[i] if weights else 1.0
        for rank, (doc, _raw_score) in enumerate(ranking, 1):
            docs[doc.id] = doc
            scores[doc.id] = scores.get(doc.id, 0.0) + w / (rrf_k + rank)
    ordered = sorted(scores, key=scores.get, reverse=True)
    return [(docs[doc_id], scores[doc_id] * 100) for doc_id in ordered]


class HybridRetriever:
    """BM25 + 可选 dense；可选第二知识源（OpenCMKG），默认不启用。"""

    def __init__(self, docs: Sequence[corpus.Doc], *,
                 encoder: Optional[Encoder] = None,
                 dense_min_score: float = 0.58,
                 candidate_k: int = 20,
                 bm25_min_score: Optional[float] = None,
                 extra_docs: Sequence[corpus.Doc] = (),
                 extra_weight: float = 0.7):
        self.docs = list(docs)
        self._bm25 = BM25Index(self.docs)
        self._dense = (DenseIndex(self.docs, encoder, dense_min_score,
                                  cache_label="yaml")
                       if encoder is not None else None)
        self._candidate_k = max(3, candidate_k)
        # 检索阈值可调（默认用 retrieval.DEFAULT_MIN_SCORE 的 1.5；
        # 测试用合成小语料时按需调低，生产路径不传即保持默认）
        self._bm25_min_score = (bm25_min_score if bm25_min_score is not None
                                else retrieval.DEFAULT_MIN_SCORE)

        # 第二知识源：独立建索引，融合时按来源加权（默认关 = 空）
        self._extra_docs = list(extra_docs)
        self._extra_weight = float(extra_weight)
        self._extra_bm25 = (BM25Index(self._extra_docs)
                            if self._extra_docs else None)
        self._extra_dense = (
            DenseIndex(self._extra_docs, encoder, dense_min_score,
                       cache_label="opencmkg")
            if self._extra_docs and encoder is not None else None)

    def search(self, query: str, top_k: int = 3) -> List[Tuple[corpus.Doc, float]]:
        primary_rankings: List[List[Tuple[corpus.Doc, float]]] = [
            self._bm25.search(query, top_k=self._candidate_k,
                              min_score=self._bm25_min_score)
        ]
        if self._dense is not None:
            try:
                primary_rankings.append(self._dense.search(query, self._candidate_k))
            except Exception as exc:  # noqa: BLE001 - 运行时故障必须降级
                print(f"[rag] BGE 查询失败，回退 BM25：{exc}", flush=True)
                self._dense = None

        rankings: List[List[Tuple[corpus.Doc, float]]] = list(primary_rankings)
        weights: List[float] = [1.0] * len(primary_rankings)
        if self._extra_bm25 is not None:
            try:
                rankings.append(self._extra_bm25.search(
                    query, top_k=self._candidate_k,
                    min_score=self._bm25_min_score))
                weights.append(self._extra_weight)
            except Exception as exc:  # noqa: BLE001 - 第二知识源任何时候都不能连累检索
                print(f"[rag] OpenCMKG BM25 检索失败，弃用：{exc}", flush=True)
                self._extra_bm25 = None
        if self._extra_dense is not None:
            try:
                rankings.append(self._extra_dense.search(query, top_k=self._candidate_k))
                weights.append(self._extra_weight)
            except Exception as exc:  # noqa: BLE001
                print(f"[rag] OpenCMKG BGE 查询失败，弃用：{exc}", flush=True)
                self._extra_dense = None

        fused = reciprocal_rank_fusion(rankings, weights=weights)
        candidates = fused[: self._candidate_k]

        # YAML 配额（设计评审）：第一知识源有可靠命中但被第二知识源挤掉时，
        # 把最强的人工条目补进 top_k，避免 OpenCMKG 占满前三。
        # 注意在**完整 fused 列表**里找人工条目——只看 candidates 的话，
        # 当人工条目掉出 candidate_k（1.5 万条语料下可能发生）配额会静默失效。
        primary_ids = {d.id for d in self.docs}
        if fused and not any(d.id in primary_ids for d, _ in candidates[:top_k]):
            primary_hits = [(d, s) for d, s in fused if d.id in primary_ids]
            if primary_hits:
                final = list(candidates[:top_k])
                if len(final) == top_k:
                    final[-1] = primary_hits[0]
                else:
                    final.append(primary_hits[0])
                candidates = final
        return candidates[:top_k]


_LOCK = threading.Lock()
_RETRIEVER: Optional[HybridRetriever] = None


def _optional_encoder() -> Optional[Encoder]:
    if not config.RAG_DENSE_ENABLED:
        return None
    try:
        return BGEEncoder(config.RAG_DENSE_MODEL)
    except Exception as exc:  # noqa: BLE001 - 可选 adapter 失败必须安全回退
        print(f"[rag] FlagEmbedding 不可用，尝试 FastEmbed：{exc}", flush=True)
    try:
        return FastEmbedEncoder(config.RAG_DENSE_MODEL)
    except Exception as exc:  # noqa: BLE001 - 两种 adapter 都失败才回退
        print(f"[rag] BGE 不可用，回退 BM25：{exc}", flush=True)
        return None


def get_retriever(kb: Dict[str, Any]) -> HybridRetriever:
    global _RETRIEVER
    with _LOCK:
        if _RETRIEVER is None:
            docs = corpus.build_docs(kb)
            from backend.knowledge import tokenize
            aliases = corpus.aliases_of(docs)
            extra_docs: List[corpus.Doc] = []
            if config.RAG_OPENCMKG_ENABLED:
                try:
                    extra_docs = opencmkg.load_docs()
                except Exception as exc:  # noqa: BLE001 - 第二知识源故障必须降级
                    print(f"[rag] OpenCMKG 不可用，仅 YAML：{exc}", flush=True)
                aliases = [*aliases, *corpus.aliases_of(extra_docs)]
            tokenize.prepare(aliases)
            encoder = _optional_encoder()
            try:
                _RETRIEVER = HybridRetriever(
                    docs, encoder=encoder,
                    dense_min_score=config.RAG_DENSE_MIN_SCORE,
                    candidate_k=config.RAG_CANDIDATE_K,
                    extra_docs=extra_docs,
                    extra_weight=config.RAG_OPENCMKG_WEIGHT,
                )
            except Exception as exc:  # noqa: BLE001 - 建索引失败必须降级
                if encoder is None:
                    raise
                print(f"[rag] BGE 建索引失败，回退 BM25：{exc}", flush=True)
                _RETRIEVER = HybridRetriever(
                    docs, candidate_k=config.RAG_CANDIDATE_K,
                    extra_docs=extra_docs,
                    extra_weight=config.RAG_OPENCMKG_WEIGHT,
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
