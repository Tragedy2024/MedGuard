"""验证路由和过滤的可观察行为，不使用联网模型或私有数据文件。"""
from dataclasses import replace

import pytest

from backend import config, dataset_kb, smart_doctor as sd
from backend.knowledge import corpus, hybrid, opencmkg, strategy


def doc(id_, **changes):
    return corpus.Doc(id=id_, kind="disease", key=id_, name=id_,
                      aliases=(id_,), text="症状 头晕 检查 血糖", **changes)


class Encoder:
    def encode_corpus(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def encode_query(self, text):
        return [1.0, 0.0]


@pytest.mark.parametrize("dense", [False, True])
def test_filter_before_topk_and_fusion(dense):
    # 正确文档排在候选窗口外；先截断再过滤会将其永久丢掉。
    distractors = [doc(f"wrong-{i}", source="excluded") for i in range(30)]
    target = doc("target", source="approved", source_version="v2",
                 sections=(("checks", "关联检查：血糖"),))
    filters = corpus.MetadataFilter(sources=("approved",), source_versions=("v2",),
                                    fields=("checks",), entities=("target",))
    r = hybrid.HybridRetriever(distractors, extra_docs=[target], candidate_k=3,
                              encoder=Encoder() if dense else None,
                              bm25_min_score=0.0)
    hits = r.search("头晕 血糖 检查", top_k=1, filters=filters)
    assert [d.id for d, _ in hits] == ["target"]
    # 核心资料配额不得把过滤掉的来源补回。
    assert all(filters.matches(d) for d, _ in hits)


def test_dense_numpy_and_python_filter_equivalent():
    index = hybrid.DenseIndex([doc("a"), doc("b")], Encoder(), 0.5)
    f = corpus.MetadataFilter(entities=("b",))
    fast = index.search("检查", 1, filters=f)
    index._np = None
    assert index.search("检查", 1, filters=f) == fast


@pytest.mark.parametrize("filters", [
    corpus.MetadataFilter(kinds=("medication",)),
    corpus.MetadataFilter(entities=("unknown",)),
    corpus.MetadataFilter(sources=("unknown",)),
    corpus.MetadataFilter(quality_tiers=("opencmkg",)),
    corpus.MetadataFilter(source_versions=("unknown",)),
    corpus.MetadataFilter(fields=("checks",)),
])
def test_empty_filter_result_never_widens(filters):
    r = hybrid.HybridRetriever([doc("a")], encoder=Encoder(), bm25_min_score=0)
    assert r.search("头晕 血糖", filters=filters) == []


def test_metadata_dimensions_are_and_values_are_or():
    d = doc("b", source="approved", sections=(("checks", "检查资料"),))
    f = corpus.MetadataFilter(entities=("a", "b"), sources=("approved",), fields=("checks",))
    assert f.matches(d)
    assert not replace(f, kinds=("medication",)).matches(d)


def test_source_fields_do_not_leak_to_generation():
    d = doc("病A", sections=(("checks", "关联检查：检查A"), ("symptoms", "关联症状：症状B")))
    assert corpus.evidence_text(d, corpus.MetadataFilter(fields=("checks",))) == "关联检查：检查A"
    assert opencmkg._sections("关联检查：检查A；未知字段：秘密；就诊科室：科室B") == (
        ("checks", "关联检查：检查A"), ("department", "就诊科室：科室B"))


@pytest.fixture
def facts(monkeypatch):
    monkeypatch.setattr(dataset_kb, "_CACHE", {
        dataset_kb.DISEASE_FILE: {
            "_meta": {"source": "OpenCMKG"},
            "diseases": {"糖尿病": {"dept": "内分泌科", "checks": ["检查A"],
                                    "symptoms": ["症状B"], "drugs": ["药物C"]}},
        },
        dataset_kb.SYMPTOM_FILE: {"symptoms": {}},
    })
    def forbidden(*args, **kwargs):
        raise AssertionError("本路径不应检索、调用模型或访问个人数据库")
    monkeypatch.setattr(sd, "_call_llm", forbidden)
    monkeypatch.setattr(sd, "fetch_personal_data", forbidden)
    return forbidden


@pytest.mark.parametrize(("question", "context"), [
    ("糖尿病挂什么科", None),
    ("糖尿病该去哪个科室", None),
    ("它挂哪一科", {"intent": "disease", "entity": "糖尿病"}),
])
def test_disease_department_returns_only_department(facts, monkeypatch, question, context):
    monkeypatch.setattr(sd, "_apply_ai_fallback", facts)
    out = sd.ask(question, "P001", context=context)
    assert out["intent"] == "disease"
    assert out["entity"] == "糖尿病"
    assert out["retrieval_plan"]["strategy"] == "structured_lookup"
    assert out["retrieval_plan"]["filters"]["fields"] == ["department"]
    assert out["data"] is None
    assert out["audit_status"] == "not_applicable"
    assert [i["ref_title"] for i in out["interpretation"]["items"]] == ["内分泌科"]
    assert out["interpretation"]["items"][0]["ref_source_type"] == "open_dataset"
    assert "OpenCMKG" in out["interpretation"]["source"]
    assert out["advice"] is None


def test_department_does_not_guess_when_the_subtype_is_missing(facts, monkeypatch):
    monkeypatch.setattr(sd, "_apply_ai_fallback", facts)
    out = sd.ask("1型糖尿病挂什么科", "P001")
    assert out["evidence"]["status"] == "insufficient"
    assert out["interpretation"]["items"] == []


def test_department_shows_conflicting_source_departments_without_picking_one(facts, monkeypatch):
    monkeypatch.setattr(sd, "_apply_ai_fallback", facts)
    cards = dataset_kb._CACHE[dataset_kb.DISEASE_FILE]["diseases"]
    cards["示例病"] = {"departments": ["科室甲", "科室乙"], "checks": ["检查A"]}
    out = sd.ask("示例病要挂哪个科", "P001")
    assert [i["ref_title"] for i in out["interpretation"]["items"]] == ["科室甲", "科室乙"]
    assert "确认" in out["interpretation"]["text"]


def test_unknown_department_question_does_not_inherit_an_unrelated_disease(facts, monkeypatch):
    monkeypatch.setattr(sd, "_apply_ai_fallback", facts)
    out = sd.ask("未收录示例病挂什么科", "P001",
                 context={"intent": "disease", "entity": "糖尿病"})
    assert out["retrieval_plan"]["strategy"] == "clarify"
    assert out["interpretation"]["items"] == []


@pytest.mark.parametrize("question", [
    "糖尿病有哪几种",
    "糖尿病分几种",
    "糖尿病有哪些类型",
])
def test_disease_type_enumeration_does_not_return_parent_card(facts, question):
    """问分型时不能拿父疾病的事实卡冒充答案。

    数据集没有完整的 1 型/2 型糖尿病条目；这类问题应明确说“分型资料缺失”，
    而不是返回父疾病的科室/症状/检查。
    """
    out = sd.ask(question, "P001")
    assert out["retrieval_plan"]["strategy"] == "insufficient"
    assert out["evidence"]["status"] == "insufficient"
    assert out["interpretation"]["items"] == []
    assert "分型" in out["interpretation"]["text"]


def test_disease_symptom_question_still_returns_facts(facts):
    """普通字段问题（有哪些症状）不能被分型守卫误伤。"""
    out = sd.ask("糖尿病有哪些症状", "P001")
    assert out["retrieval_plan"]["strategy"] == "structured_lookup"
    assert out["interpretation"]["items"]


def test_disease_type_enumeration_lists_known_subtypes(facts):
    """有完整子型条目时，应列出分型，而不是只说资料不足。"""
    cards = dataset_kb._CACHE[dataset_kb.DISEASE_FILE]["diseases"]
    cards["1型糖尿病"] = {
        "dept": "内分泌科", "symptoms": ["多饮", "多尿"], "checks": ["血糖"],
    }
    cards["2型糖尿病"] = {
        "dept": "内分泌科", "symptoms": ["多饮", "体重下降"], "checks": ["糖化血红蛋白"],
    }

    out = sd.ask("糖尿病有哪几种", "P001")

    assert out["retrieval_plan"]["strategy"] == "subtype_lookup"
    titles = [item.get("ref_title") for item in out["interpretation"]["items"]]
    assert "1型糖尿病" in titles
    assert "2型糖尿病" in titles
    assert out["evidence"]["status"] == "curated"


def test_subtype_field_question_uses_subtype_card(facts):
    """限定分型的字段问题不能回退到父疾病卡。"""
    cards = dataset_kb._CACHE[dataset_kb.DISEASE_FILE]["diseases"]
    cards["1型糖尿病"] = {"dept": "内分泌科", "symptoms": ["多饮"]}
    cards["2型糖尿病"] = {
        "dept": "内分泌科", "symptoms": ["多饮", "体重下降"],
    }

    out = sd.ask("2型糖尿病有哪些症状", "P001")

    assert out["retrieval_plan"]["strategy"] == "structured_lookup"
    assert "2型糖尿病" in out["interpretation"]["title"]
    excerpts = " ".join(
        str(item.get("ref_excerpt", ""))
        for item in out["interpretation"]["items"])
    assert "体重下降" in excerpts


def test_ascii_roman_subtype_normalization():
    """OpenCMKG 使用 I型/II型；必须和 1型/2型 归一。"""
    assert corpus.normalize_entity("II型糖尿病") == "2型糖尿病"
    assert corpus.normalize_entity("I型糖尿病") == "1型糖尿病"


@pytest.mark.parametrize(("question", "strategy_name"), [
    ("糖尿病有哪几种并发症", "structured_lookup"),
    ("糖尿病有哪几种药", "structured_lookup"),
    ("糖尿病有哪几种治疗方法", "structured_lookup"),
    ("糖尿病有哪几种检查", "structured_lookup"),
    ("糖尿病有哪几种症状", "structured_lookup"),
])
def test_subtype_guard_does_not_hijack_field_questions(facts, question, strategy_name):
    """“有哪几种并发症/药/治疗/检查/症状”是字段问题，不是分型枚举。"""
    out = sd.ask(question, "P001")
    assert out["retrieval_plan"]["strategy"] == strategy_name
    assert "分型" not in (out["interpretation"].get("title") or "")


@pytest.mark.parametrize("question", [
    "糖尿病是什么原因引起的",
    "糖尿病会遗传吗",
    "糖尿病严重吗",
    "糖尿病能治好吗",
])
def test_unknown_disease_field_does_not_return_parent_card(facts, question):
    """资料没有的字段要如实说暂缺，不能拿父疾病整张卡冒充答案。"""
    out = sd.ask(question, "P001")
    assert out["evidence"]["status"] == "insufficient"
    assert out["interpretation"]["items"] == []


def test_dataset_definition_question_matches_disease(facts):
    """“甲状腺结节是什么”应由数据集病名命中，而不是只走 fallback/RAG。"""
    cards = dataset_kb._CACHE[dataset_kb.DISEASE_FILE]["diseases"]
    cards["甲状腺结节"] = {
        "dept": "普外科", "symptoms": ["颈部肿块"], "checks": ["甲状腺超声"],
    }

    out = sd.ask("甲状腺结节是什么", "P001")

    assert out["intent"] == "disease"
    assert out["entity"] == "甲状腺结节"
    assert out["interpretation"]["items"]


def test_lab_alias_not_swallowed_by_disease_alias(facts):
    """疾病别名“血糖高”不能把检验项“血糖”从实体集合中挤掉。"""
    out = sd.ask("我血糖高怎么办", "P001")
    filters = out["retrieval_plan"]["filters"]
    assert filters["kinds"] == ["lab"]
    assert filters["entities"] == ["血糖"]
    assert out["interpretation"]["items"]


def test_subtype_comparison_lists_both_cards(facts):
    """1型 vs 2型 的比较应列出两个子型卡，而不是只说“换个问法”。"""
    cards = dataset_kb._CACHE[dataset_kb.DISEASE_FILE]["diseases"]
    cards["1型糖尿病"] = {"dept": "内分泌科", "symptoms": ["多饮"], "checks": ["血糖"]}
    cards["2型糖尿病"] = {"dept": "内分泌科", "symptoms": ["体重下降"], "checks": ["糖化血红蛋白"]}

    out = sd.ask("1型糖尿病和2型糖尿病有什么区别", "P001")

    assert out["retrieval_plan"]["strategy"] == "subtype_lookup"
    titles = [item.get("ref_title") for item in out["interpretation"]["items"]]
    assert "1型糖尿病" in titles
    assert "2型糖尿病" in titles
    assert out["evidence"]["status"] == "curated"


def test_which_is_more_severe_is_a_comparison(facts):
    """“哪个更严重”也是比较问法，不能落回父疾病事实卡。"""
    cards = dataset_kb._CACHE[dataset_kb.DISEASE_FILE]["diseases"]
    cards["高血压"] = {"dept": "心血管内科", "symptoms": ["头晕"]}

    plan = strategy.choose(
        "糖尿病和高血压哪个更严重", "disease", "糖尿病",
        sd.load_knowledge(), disease_names=dataset_kb.disease_names())

    assert plan.strategy == "hybrid"
    assert set(plan.filters.entities) == {"糖尿病", "高血压"}


def test_new_symptom_stays_on_triage_even_with_known_disease_context(facts, monkeypatch):
    monkeypatch.setattr(sd, "_apply_ai_fallback", facts)
    dataset_kb._CACHE[dataset_kb.SYMPTOM_FILE]["symptoms"]["头晕"] = {
        "total": 1, "depts": [["神经内科", 1]],
    }
    out = sd.ask("我有糖尿病，现在头晕挂什么科", "P001",
                 context={"intent": "disease", "entity": "糖尿病"})
    assert (out["intent"], out["entity"]) == ("symptom", "头晕")
    assert [i["ref_title"] for i in out["interpretation"]["items"]] == ["神经内科"]


@pytest.mark.parametrize("question", ["我想了解糖尿病", "糖尿病需要做什么检查"])
def test_structured_lookup_skips_retrieval_and_model(facts, monkeypatch, question):
    monkeypatch.setattr(hybrid, "search", facts)
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    out = sd.ask(question, "P001")
    assert out["retrieval_plan"]["strategy"] == "structured_lookup"
    assert out["audit_status"] == "not_applicable"
    assert out["interpretation"]["items"]
    if "检查" in question:
        texts = str(out["interpretation"]["items"])
        assert "检查A" in texts and "药物C" not in texts and "症状B" not in texts


@pytest.mark.parametrize("question", ["1型糖尿病是什么", "一型糖尿病和二型糖尿病区别", "1型和2型糖尿病有什么区别"])
def test_unknown_subtypes_do_not_use_parent_card(facts, monkeypatch, question):
    monkeypatch.setattr(hybrid, "search", facts)
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    out = sd.ask(question, "P001")
    assert out["retrieval_plan"]["strategy"] == "insufficient"
    assert out["evidence"]["status"] == "insufficient"
    assert out["interpretation"]["items"] == []
    assert dataset_kb.lookup_disease("2型糖尿病") is None


def test_general_medication_knowledge_does_not_query_patient(facts):
    out = sd.ask("阿托伐他汀有哪些注意事项", "P001")
    assert out["retrieval_plan"]["strategy"] == "structured_lookup"
    assert out["data"] is None
    assert out["interpretation"]["items"]


@pytest.mark.parametrize("question,intent,entity", [
    ("我的血糖参考范围及结果正常吗", "lab", "血糖"),
    ("医生给我开的药怎么吃", "medication", None),
])
def test_personal_records_keep_audited_sql(question, intent, entity):
    p = strategy.choose(question, intent, entity, sd.load_knowledge())
    assert p.strategy == "personal_sql"


def test_vague_question_asks_for_details(facts):
    out = sd.ask("我不舒服", "P001")
    assert out["retrieval_plan"]["strategy"] == "clarify"
    assert out["evidence"]["status"] == "insufficient"


def test_explanation_reaches_filtered_hybrid(facts, monkeypatch):
    seen = []
    def search(kb, query, top_k, *, filters):
        seen.append(filters)
        return []
    monkeypatch.setattr(hybrid, "search", search)
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    out = sd.ask("糖尿病为什么会发生", "P001")
    assert out["retrieval_plan"]["strategy"] == "hybrid"
    assert seen and seen[0].entities == ("糖尿病",)
    assert seen[0].fields == ("causes",)
    assert out["evidence"]["status"] == "insufficient"


def test_comparison_needs_both_entities_before_generation(facts, monkeypatch):
    cards = dataset_kb._CACHE[dataset_kb.DISEASE_FILE]["diseases"]
    cards["高血压"] = {"dept": "心血管内科", "symptoms": ["头晕"]}
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(sd, "_retrieve_context", lambda q, **kwargs: [
        {"ref_id": "a", "ref_entity": "糖尿病", "ref_title": "糖尿病",
         "ref_excerpt": "已有资料", "ref_source": "测试资料"}])
    out = sd.ask("糖尿病和高血压有什么区别", "P001")
    assert out["retrieval_plan"]["strategy"] == "hybrid"
    assert out["evidence"]["status"] == "insufficient"
    assert "比较资料不足" in out["interpretation"]["title"]


def test_compound_dataset_name_does_not_swallow_comparison_entities(facts):
    """OpenCMKG 里有「糖尿病和高血压」这种合并病名。

    最长匹配若把它当成一个实体，两个真实疾病都会被它“包住”而丢掉，
    比较题就被误判成「需要明确两个对象」。拆分后必须保留两个实体。
    """
    cards = dataset_kb._CACHE[dataset_kb.DISEASE_FILE]["diseases"]
    cards["高血压"] = {"dept": "心血管内科", "checks": ["检查B"]}
    cards["糖尿病和高血压"] = {"dept": "内分泌科", "checks": ["检查C"]}

    plan = strategy.choose(
        "糖尿病和高血压有什么区别", "disease", "糖尿病",
        sd.load_knowledge(), disease_names=dataset_kb.disease_names())

    assert plan.strategy == "hybrid"
    assert set(plan.filters.entities) == {"糖尿病", "高血压"}


def test_subtype_normalization_keeps_identity():
    f = corpus.MetadataFilter(entities=("一型糖尿病",))
    assert f.matches(doc("1型糖尿病"))
    assert not f.matches(doc("糖尿病"))
    assert not f.matches(doc("2型糖尿病"))


def test_followup_uses_resolved_entity_filter(facts, monkeypatch):
    seen = []
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    def search(kb, query, top_k, *, filters):
        seen.append(filters)
        return []
    monkeypatch.setattr(hybrid, "search", search)
    out = sd.ask("它为什么会发生", "P001", context={"intent": "disease", "entity": "糖尿病"})
    assert out["retrieval_plan"]["strategy"] == "hybrid"
    assert seen and seen[0].entities == ("糖尿病",)


def test_unavailable_metadata_does_not_load_bge(monkeypatch):
    monkeypatch.setattr(config, "RAG_OPENCMKG_ENABLED", False)
    def forbidden(*args):
        raise AssertionError("不存在的字段不应初始化 BGE")
    monkeypatch.setattr(hybrid, "get_retriever", forbidden)
    assert hybrid.search(sd.load_knowledge(), "为什么", filters=corpus.MetadataFilter(fields=("unknown",))) == []
