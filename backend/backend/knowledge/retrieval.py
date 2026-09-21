"""BM25 检索：为**生成**找出与问题相关的知识条目（RAG 的 R 那一步）。

**用途界定**（很重要，别和上一次搞混）：

    · 判断"用户在问哪个条目" → 用别名做字面匹配（smart_doctor._find_entity）。
      那里不需要本模块：索引 token 全来自别名、别名又在分词词典里，
      于是"检索能命中"等价于"别名是子串"，而字面匹配已经先跑过了。
    · **为生成提供相关段落** → 用本模块。这时要的不是"这是哪一条"，
      而是"哪些条目的内容与这个问题有关"，正文必须参与——见
      corpus.doc_text_for_index 的说明。

**为什么手写而不引第三方库**：BM25 的公式是公开且固定的（下面有完整式子），
三十来行就能写清；而多一个依赖就多一份离线部署与体积的负担——这个项目的
演示要求是「断网也能完整跑」。

公式（Okapi BM25）：

    score(q, d) = Σ_t IDF(t) · f(t,d)·(k1+1) / (f(t,d) + k1·(1 − b + b·|d|/avgdl))
    IDF(t)      = ln(1 + (N − n(t) + 0.5) / (n(t) + 0.5))

k1 控制词频饱和，b 控制长度归一化。1.5 / 0.75 是文献常用取值，本项目不调参。
"""
import math
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.knowledge import corpus, tokenize

K1 = 1.5
B = 0.75

# 检索结果的最低分。低于它宁可**不提供**上下文——硬凑几条不相关的知识
# 塞给模型，比不给更糟：模型会照着无关材料编。
DEFAULT_MIN_SCORE = 1.5


class BM25Index:
    """一份文档集合的 BM25 索引。"""

    def __init__(self, docs: Sequence[corpus.Doc]):
        self.docs: List[corpus.Doc] = list(docs)
        self._n = len(self.docs)

        # 倒排表：term -> [(doc_idx, 词频)]。用倒排而不是"每个词扫全部文档"，
        # 是为了语料长大之后仍然只碰命中的文档。
        self._postings: Dict[str, List[Tuple[int, int]]] = {}
        self._doclen: List[int] = []

        for i, d in enumerate(self.docs):
            toks = tokenize.cut(corpus.doc_text_for_index(d))
            self._doclen.append(len(toks))
            tf: Dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t, f in tf.items():
                self._postings.setdefault(t, []).append((i, f))

        self._avgdl = (sum(self._doclen) / self._n) if self._n else 0.0
        self._idf = {
            t: math.log(1 + (self._n - len(pl) + 0.5) / (len(pl) + 0.5))
            for t, pl in self._postings.items()
        }

    def score(self, query_tokens: Sequence[str]) -> List[float]:
        scores = [0.0] * self._n
        avgdl = self._avgdl or 1.0
        for t in query_tokens:
            idf = self._idf.get(t)
            if idf is None:          # 查询里的词不在任何文档中 → 不贡献
                continue
            for i, f in self._postings[t]:
                denom = f + K1 * (1 - B + B * self._doclen[i] / avgdl)
                scores[i] += idf * (f * (K1 + 1)) / denom
        return scores

    def search(self, query: str, top_k: int = 3,
               min_score: float = DEFAULT_MIN_SCORE,
               exclude_kinds: Sequence[str] = (),
               min_matches: int = 2) -> List[Tuple[corpus.Doc, float]]:
        """返回 (文档, 分数) 降序，已按 min_score 截断。

        过滤条件缺一不可：
          · `s > 0`      —— 一个词都没重合的文档不是"相关"
          · `s >= min_score`
          · **命中至少 `min_matches` 个不同的查询词**
          · 类型不在 exclude 之列

        `min_matches` 这条是实测加的（.scratch/rag-ux/rag-check.py）：
        小语料里通用词（「问题」「建议」）的 IDF 也不低，只靠一个共享词
        就能把不相干的条目顶上来——问「体检查出甲状腺有问题」时，
        「便秘」靠正文里的「问题」二字挤进前三。**一个共享词不构成相关性。**
        """
        qt = tokenize.cut(query)
        if not qt:
            return []

        # 每篇文档命中了几个**不同**的查询词（同一个词出现多次只算一个）
        n_matched: Dict[int, int] = {}
        for t in set(qt):
            for i, _ in self._postings.get(t, ()):
                n_matched[i] = n_matched.get(i, 0) + 1

        skip = set(exclude_kinds)
        hits = [(d, s, n_matched.get(i, 0))
                for i, (d, s) in enumerate(zip(self.docs, self.score(qt)))
                if s > 0 and s >= min_score and d.kind not in skip]
        hits = [(d, s) for d, s, n in hits if n >= min_matches]
        hits.sort(key=lambda x: x[1], reverse=True)
        return hits[:top_k]


# ── 进程内单例 ────────────────────────────────────────────────
#
# 与 smart_doctor.load_knowledge 的缓存模式一致：一把锁，读缓存与写缓存
# 放在同一临界区里，避免两个请求同时判定"还没建"而各建一遍。

_LOCK = threading.Lock()
_INDEX: Optional[BM25Index] = None


def get_index(kb: Dict[str, Any]) -> BM25Index:
    """取（必要时构建）索引。首次调用会连同 jieba 用户词典一起准备好。"""
    global _INDEX
    with _LOCK:
        if _INDEX is None:
            docs = corpus.build_docs(kb)
            tokenize.prepare(corpus.aliases_of(docs))
            _INDEX = BM25Index(docs)
        return _INDEX


def reset() -> None:
    """丢掉索引（测试用）。"""
    global _INDEX
    with _LOCK:
        _INDEX = None
