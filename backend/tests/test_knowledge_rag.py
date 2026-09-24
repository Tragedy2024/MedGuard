"""知识检索层（RAG 的检索那一步）与它在 AI 兜底路径上的接入。

**这里检的不是"实体识别"**——那由 smart_doctor 的别名字面匹配负责，
见 test_smart_doctor.py。这里检的是"为生成找相关段落"。
"""
from backend import smart_doctor
from backend.knowledge import corpus, hybrid, retrieval, tokenize
from backend.smart_doctor import _build_ai_prompt, _retrieve_context


# ── 分词：实测教训固化成守卫 ──────────────────────────────────

def test_cut_drops_single_char_tokens():
    """★ 单字 token 必须丢弃——它曾让 BM25 彻底失效。

    实测（.scratch/rag-ux/bm25-debug.py）：「我膝盖疼，上下楼特别难受」
    被切成含 `上` 的 token，而 `上` 只出现在 1 篇文档里（胃溃疡，正文有
    "上腹痛"），IDF 高达 4.12——一个偶然的单字命中就把「胃溃疡」顶到第一。
    """
    assert "上" not in tokenize.cut("上下楼")
    assert "疼" not in tokenize.cut("膝盖疼")


def test_cut_drops_punctuation():
    """标点不是词。全角逗号曾出现在 91 篇中的 73 篇，白给所有文档加分。"""
    for t in tokenize.cut("血糖，血脂。血压；心率！"):
        assert any(ch.isalnum() for ch in t), f"标点漏进了索引：{t!r}"


def test_cut_keeps_meaningful_tokens():
    got = tokenize.cut("我的血糖结果")
    assert "血糖" in got and "结果" in got


# ── 检索：精度优先 ────────────────────────────────────────────

def test_search_finds_related_entry():
    """问题里出现了某药品的**注意事项**用词 → 该药品应被捞到。

    「他汀 + 肌肉酸痛」：阿托伐他汀的注意事项写着"出现不明原因的肌肉酸痛"。
    """
    got = [r["ref_title"] for r in _retrieve_context("吃了他汀之后肌肉酸痛")]
    assert "阿托伐他汀" in got


def test_search_returns_nothing_when_corpus_has_nothing():
    """★ 语料里没有的话题，**必须如实留空**。

    「膝盖」在知识库里不存在（没有膝关节相关条目）。硬凑一条不相干的
    上下文比不凑更糟——模型会照着无关材料编。曾因单字 `上` 命中
    「胃溃疡」，本条就是那次回归的守卫。
    """
    assert _retrieve_context("我膝盖疼，上下楼特别难受") == []


def test_search_returns_nothing_for_off_topic():
    for q in ["怎么办理出院手续", "停车费怎么收", "今天天气怎么样"]:
        assert _retrieve_context(q) == [], q


def test_one_shared_word_is_not_enough():
    """★ 只命中一个查询词不算相关。

    问「体检查出甲状腺有问题」时，「便秘」曾靠正文里的「问题」二字挤进
    前三。「问题」这种通用词在小语料里 IDF 也不低，单靠它顶不住。
    """
    kb = smart_doctor.load_knowledge()
    idx = retrieval.get_index(kb)
    hits = idx.search("体检查出甲状腺有问题", top_k=5)
    titled = {d.name for d, _ in hits}
    assert "便秘" not in titled, f"通用词误命中：{titled}"


def test_single_medical_term_is_retrievable():
    """动态门槛允许单医学实体，修复固定 min_matches=2 造成的零召回。"""
    assert _retrieve_context("头痛")


def test_generic_question_words_do_not_retrieve_context():
    assert _retrieve_context("这个问题有什么建议") == []


def test_zero_score_docs_are_never_returned():
    kb = smart_doctor.load_knowledge()
    idx = retrieval.get_index(kb)
    for _d, s in idx.search("血糖", min_score=0.0, min_matches=0, top_k=99):
        assert s > 0


# ── RAG 的增强（A）那一步 ─────────────────────────────────────

def test_prompt_carries_retrieved_context():
    refs = _retrieve_context("吃了他汀之后肌肉酸痛")
    assert refs, "该问法应能检索到条目——否则下面的断言没有意义"
    prompt = _build_ai_prompt("吃了他汀之后肌肉酸痛", refs)
    assert "【本院知识库中与该问题可能相关的条目】" in prompt
    assert "优先依据上面这些条目" in prompt
    assert "不要编造" in prompt
    for r in refs:
        assert r["ref_title"] in prompt


def test_prompt_admits_when_nothing_retrieved():
    """没有上下文时要**如实说明**，而不是假装有依据。"""
    prompt = _build_ai_prompt("怎么办理出院手续", [])
    assert "没有检索到相关条目" in prompt
    assert "【本院知识库中与该问题可能相关的条目】" not in prompt


def test_ai_fallback_surfaces_the_sources_it_used(monkeypatch):
    """检索到的条目要回填到 items —— 这是「可演示」与「看不见的实现」的分界。"""
    import json
    from backend import config
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(
        smart_doctor, "_call_llm",
        lambda p: json.dumps({"title": "用药咨询", "text": "……",
                              "advice": "请咨询医生。", "actions": ["遵医嘱"],
                              "urgent": False}, ensure_ascii=False),
    )
    base = {}
    assert smart_doctor._apply_ai_fallback(base, "吃了他汀之后肌肉酸痛") is True
    items = base["interpretation"]["items"]
    assert items, "应把检索到的条目回填到 items"
    assert all("ref_title" in it and "ref_source" in it for it in items)
    assert any(it["ref_title"] == "阿托伐他汀" for it in items)


def test_retrieval_failure_does_not_break_the_answer(monkeypatch):
    """检索层挂掉不该连累问答——退回无上下文作答即可。"""
    def boom(*a, **k):
        raise RuntimeError("索引炸了")

    monkeypatch.setattr(hybrid, "search", boom)
    assert _retrieve_context("随便问点什么") == []


def test_hybrid_dense_recovers_a_semantic_match():
    docs = [
        corpus.Doc(id="sleep", kind="symptom", key="sleep", name="失眠",
                   aliases=("失眠",), text="夜间难以入睡，睡眠质量下降。"),
        corpus.Doc(id="skin", kind="symptom", key="skin", name="皮肤瘙痒",
                   aliases=("皮肤瘙痒",), text="皮肤发痒。"),
    ]

    class FakeEncoder:
        @staticmethod
        def _one(text):
            if "睡不着" in text or "失眠" in text or "入睡" in text:
                return [1.0, 0.0]
            return [0.0, 1.0]

        def encode_corpus(self, texts):
            out = []
            for text in texts:
                out.append(self._one(text))
            return out

        def encode_query(self, text):
            return self._one(text)

    retriever = hybrid.HybridRetriever(
        docs, encoder=FakeEncoder(), dense_min_score=0.8,
    )
    hits = retriever.search("最近总是睡不着", top_k=1)
    assert hits and hits[0][0].id == "sleep"


def test_missing_optional_bge_falls_back_to_bm25(monkeypatch):
    """真实部署没装模型或模型下载失败时，RAG 仍应可用。"""
    from backend import config

    class BrokenBGE:
        def __init__(self, _model_name):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(config, "RAG_DENSE_ENABLED", True)
    monkeypatch.setattr(hybrid, "BGEEncoder", BrokenBGE)
    hybrid.reset()
    try:
        hits = hybrid.search(smart_doctor.load_knowledge(), "头痛")
        assert hits
    finally:
        hybrid.reset()


def test_bge_index_failure_falls_back_to_bm25(monkeypatch):
    """模型可加载但编码语料失败时，也不能阻断检索。"""
    from backend import config

    class BrokenIndexEncoder:
        def __init__(self, _model_name):
            pass

        def encode_corpus(self, _texts):
            raise MemoryError("not enough memory")

        def encode_query(self, _text):
            return [1.0]

    monkeypatch.setattr(config, "RAG_DENSE_ENABLED", True)
    monkeypatch.setattr(hybrid, "BGEEncoder", BrokenIndexEncoder)
    hybrid.reset()
    try:
        hits = hybrid.search(smart_doctor.load_knowledge(), "头痛")
        assert hits
    finally:
        hybrid.reset()


def test_bge_query_failure_falls_back_and_disables_dense():
    """运行中的向量查询异常后，本次及后续请求都走 BM25。"""
    docs = corpus.build_docs(smart_doctor.load_knowledge())

    class BrokenQueryEncoder:
        def encode_corpus(self, texts):
            return [[1.0] for _ in texts]

        def encode_query(self, _text):
            raise RuntimeError("encoder crashed")

    retriever = hybrid.HybridRetriever(docs, encoder=BrokenQueryEncoder())
    assert retriever.search("头痛")
    assert retriever._dense is None
    assert retriever.search("头痛")
