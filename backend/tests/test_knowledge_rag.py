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


# ── OpenCMKG 第二知识源（2026-09 设计评审：双知识源 + 加权 RRF + YAML 配额）──

def _doc(id_, name, text, kind="disease"):
    return corpus.Doc(id=id_, kind=kind, key=name, name=name,
                      aliases=(name,), text=text)


def test_opencmkg_adapter_loads_docs(monkeypatch, tmp_path):
    """适配器把 rag_documents.json 展平成带来源/质量等级的 Doc。"""
    import json as _json
    from backend.knowledge import opencmkg
    doc_file = tmp_path / "rag_documents.json"
    doc_file.write_text(_json.dumps({
        "documents": [
            {"id": "opencmkg:disease:冠心病", "kind": "disease", "key": "冠心病",
             "name": "冠心病", "aliases": ["冠心病"],
             "text": "关联症状：胸痛；就诊科室：心血管内科"},
            {"id": "opencmkg:symptom:胸痛", "kind": "symptom", "key": "胸痛",
             "name": "胸痛", "aliases": ["胸痛"], "text": "关联疾病：冠心病"},
        ],
    }, ensure_ascii=False))
    monkeypatch.setattr(opencmkg, "_DOCS_FILE", str(doc_file))
    opencmkg.reset()
    docs = opencmkg.load_docs()
    assert len(docs) == 2
    assert docs[0].id == "opencmkg:disease:冠心病"
    assert docs[0].quality_tier == "opencmkg"
    assert docs[0].source == "OpenCMKG"
    assert docs[0].license
    opencmkg.reset()


def test_opencmkg_missing_file_yields_empty(monkeypatch, tmp_path):
    """文件缺失/损坏：第二知识源降级为空，不影响第一知识源。"""
    from backend.knowledge import opencmkg
    monkeypatch.setattr(opencmkg, "_DOCS_FILE",
                        str(tmp_path / "不存在.json"))
    opencmkg.reset()
    assert opencmkg.load_docs() == []
    opencmkg.reset()


def test_rrf_weights_rank_sources(monkeypatch):
    """加权 RRF：低权重来源排在同等位置的高权重来源之后。"""
    d1 = _doc("p1", "知识库条目", "人工整理")
    d2 = _doc("e1", "OpenCMKG条目", "开源图谱")
    r1 = [(d1, 1.0)]           # 两路都是对方 rank=1
    r2 = [(d2, 1.0)]
    fused_heavy = hybrid.reciprocal_rank_fusion([r1, r2], weights=(1.0, 0.1))
    assert fused_heavy[0][0].id == "p1"
    fused_light = hybrid.reciprocal_rank_fusion([r1, r2], weights=(0.1, 1.0))
    assert fused_light[0][0].id == "e1"


def test_yaml_quota_keeps_primary_in_topk():
    """YAML 配额：第一知识源有可靠命中但被第二知识源挤掉时，补进 top_k。"""
    primary = [_doc("p1", "失眠", "失眠，夜间难以入睡，睡眠质量下降")]
    extras = [
        _doc("e1", "失眠症候", "失眠，睡眠质量差，入睡困难"),
        _doc("e2", "睡眠障碍", "失眠多梦，睡眠障碍"),
        _doc("e3", "入睡困难", "失眠，入睡困难，夜间易醒"),
    ]
    retriever = hybrid.HybridRetriever(
        primary, extra_docs=extras, extra_weight=0.7,
        candidate_k=20, bm25_min_score=0.0,
    )
    hits = retriever.search("失眠", top_k=3)
    ids = [d.id for d, _ in hits]
    assert len(hits) == 3
    assert "p1" in ids          # 配额保证人工条目在场（排名可被 OpenCMKG 占据，但必须有人工条）


def test_opencmkg_switch_controls_second_source(monkeypatch, tmp_path):
    """开关：默认关 = get_retriever 不带第二知识源；打开 = 带上且可被检索到。"""
    import json as _json
    from backend import config
    from backend.knowledge import opencmkg
    path = tmp_path / "rag_documents.json"
    path.write_text(_json.dumps({"documents": [
        {"id": "opencmkg:disease:草原病", "kind": "disease", "key": "草原病",
         "name": "草原病", "aliases": ["草原病"],
         "text": "关联症状：皮疹；就诊科室：皮肤科"},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(opencmkg, "_DOCS_FILE", str(path))
    opencmkg.reset()
    hybrid.reset()

    kb = smart_doctor.load_knowledge()

    monkeypatch.setattr(config, "RAG_OPENCMKG_ENABLED", False)
    hybrid.reset()
    retriever_off = hybrid.get_retriever(kb)
    assert retriever_off._extra_docs == []

    monkeypatch.setattr(config, "RAG_OPENCMKG_ENABLED", True)
    hybrid.reset()
    retriever_on = hybrid.get_retriever(kb)
    assert len(retriever_on._extra_docs) == 1
    assert retriever_on._extra_docs[0].id == "opencmkg:disease:草原病"

    # 行为断言：合成小语料用低阈值，确认 OpenCMKG 条目能真正被检索命中
    from backend.knowledge import corpus as _corpus
    primary = _corpus.build_docs(kb)
    probe = hybrid.HybridRetriever(
        primary, extra_docs=retriever_on._extra_docs, extra_weight=0.7,
        bm25_min_score=0.0,
    )
    hits = probe.search("草原病", top_k=3)
    assert any(d.id.startswith("opencmkg:") for d, _ in hits)
    opencmkg.reset()
    hybrid.reset()


def test_opencmkg_failure_falls_back_to_yaml(monkeypatch):
    """第二知识源加载失败：仅 YAML，不连累检索。"""
    from backend import config
    from backend.knowledge import opencmkg
    monkeypatch.setattr(config, "RAG_OPENCMKG_ENABLED", True)

    def boom():
        raise RuntimeError("rag_documents.json 损坏")
    monkeypatch.setattr(opencmkg, "load_docs", boom)
    hybrid.reset()
    hits = hybrid.search(smart_doctor.load_knowledge(), "头痛", top_k=3)
    assert isinstance(hits, list)
    hybrid.reset()
