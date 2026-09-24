"""分词：中文没有空格，检索之前必须先切词。

选 jieba 而不是字符二元组的理由：知识库里全是专业词（`空腹葡萄糖`、
`糖化血红蛋白`），字符二元组会把它们切碎成 `空腹/腹葡/葡萄/萄糖` 这种
噪声，而 jieba 配**用户词典**能把整个术语当一个词——用户词典直接由语料的
`aliases` 生成，等于让知识库自己定义"什么算一个词"。

代价：jieba 约 20MB（含词典），**首次调用要加载词典约 1 秒**。已在
backend/main.py 的 lifespan 里预热，不让第一个提问的患者吃这一下。
"""
import threading
from typing import Iterable, List

import jieba

_LOCK = threading.Lock()
_READY = False

# 小于这个长度的 token 一律丢弃。见 cut() 的说明——中文单字会让 BM25 失效。
_MIN_TOKEN_LEN = 2

# 只在“查询侧”过滤。正文仍完整建索引，避免这些词在固定短语里丢失语义。
# 这些通用问法词曾让「这个问题有什么建议」误召回失眠/皮肤瘙痒。
QUERY_STOPWORDS = frozenset({
    "这个", "那个", "问题", "建议", "请问", "一下", "什么", "怎么",
    "怎么办", "如何", "情况", "有点", "感觉", "可以", "需要",
})


def prepare(aliases: Iterable[str] = ()) -> None:
    """把知识库的别名灌进 jieba 用户词典（幂等，进程内只做一次）。

    幂等是有意的：调用方可能从 lifespan 预热时调一次、在测试里再调一次。
    只认第一次的别名集合——进程里只有一份知识库，不需要支持多份。
    """
    global _READY
    with _LOCK:
        if _READY:
            return
        for a in aliases:
            a = (a or "").strip()
            if len(a) >= 2:      # 单字进用户词典只会制造噪声
                jieba.add_word(a)
        # 触发词典加载。放在锁内是**故意**的：多个请求同时打进来时，
        # 让它们排队等这一次加载完成，而不是每个都去加载一遍。
        jieba.lcut("预热")
        _READY = True


def cut(text: str) -> List[str]:
    """切词，丢掉空白、标点与**单字** token。

    为什么必须丢单字：中文单字在短文本上会让 BM25 彻底失效。实测
    （.scratch/rag-ux/bm25-debug.py，2026-09-21）：「我膝盖疼，上下楼特别
    难受」被切成含 `上` 的 token，而 `上` 只出现在 1 篇文档里（胃溃疡，
    正文有"上腹痛"），于是 IDF 高达 4.12——**一个偶然的单字命中就把
    「胃溃疡」顶到了第一名**，而「膝盖」在语料里根本不存在，本该不返回
    任何上下文。

    标点同理：全角逗号出现在 91 篇中的 73 篇，白白给所有文档加 0.31 分。

    多字 token 不受影响：中文的语义单位基本都是 2 字以上，英文缩写
    （CT / TSH / CRP）也都在 2 字以上。
    """
    out: List[str] = []
    for raw in jieba.lcut(text or ""):
        t = raw.strip()
        if len(t) < _MIN_TOKEN_LEN:
            continue
        if not any(ch.isalnum() for ch in t):
            continue          # 纯标点（如「……」）
        out.append(t)
    return out


def query_terms(text: str) -> List[str]:
    """返回用于检索的有效查询词；通用问法词不参与相关性计算。"""
    return [t for t in cut(text) if t not in QUERY_STOPWORDS]


def is_ready() -> bool:
    """词典是否已加载（健康检查/测试用）。"""
    return _READY
