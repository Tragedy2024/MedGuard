"""A1 回归：明确疾病意图下，数据集 7495 个疾病名可达（2026-10 评审）。

背景：意图识别此前只认 YAML 的 25 个疾病别名，「我想了解帕金森病」解析为
``(disease, None)``，实体为空导致 7495 张疾病事实卡够不着，只能依赖 AI
兜底（RAG/LLM）。修复（评审约束）：

- 只在明确的疾病意图信号（想了解 / 是什么病 / 科普 …）出现时扫描疾病名；
- 只做规范名的精确/最长字面匹配，**不做模糊匹配**（错配疾病比答不上来更糟）；
- 「我胃痛」走症状导诊的歧义规则保持；
- 命中后直接走 dataset_kb 事实卡，不依赖 RAG / BGE / LLM。
"""
import pytest

from backend import smart_doctor as sd


@pytest.fixture(scope="module", autouse=True)
def optional_dataset_fixture():
    """用最小本地派生表验证疾病可达性，不依赖不可再分发的数据文件。"""
    from backend import dataset_kb

    old_cache = dict(dataset_kb._CACHE)
    dataset_kb._CACHE[dataset_kb.DISEASE_FILE] = {
        "_meta": {"source": "OpenCMKG"},
        "diseases": {
            name: {
                "dept": "相关专科", "symptoms": ["示例症状"],
                "checks": ["相关检查"], "drugs": [],
                "complications": [], "treatments": [],
            }
            for name in ("帕金森病", "荨麻疹", "颈椎病")
        },
    }
    yield
    dataset_kb._CACHE.clear()
    dataset_kb._CACHE.update(old_cache)


def test_parse_intent_known_yaml_disease_unchanged():
    """YAML 已有疾病仍走原路径（优先级不变）。"""
    assert sd.parse_intent("我想了解糖尿病") == ("disease", "糖尿病")


def test_parse_intent_dataset_disease_with_signal():
    """明确疾病意图 + 数据集疾病名 → 实体命中。"""
    assert sd.parse_intent("我想了解帕金森病") == ("disease", "帕金森病")
    assert sd.parse_intent("荨麻疹是什么病") == ("disease", "荨麻疹")


def test_parse_intent_no_signal_no_dataset_scan():
    """没有疾病意图信号时不扫描数据集（保守，不给错配机会）。"""
    intent, entity = sd.parse_intent("帕金森病严重吗")
    assert not (intent == "disease" and entity == "帕金森病")


def test_parse_intent_no_fuzzy_match():
    """不模糊匹配：名字不全不得命中（「帕金森」≠「帕金森病」）。"""
    intent, entity = sd.parse_intent("科普一下帕金森")
    assert not (intent == "disease" and entity == "帕金森病")


def test_symptom_guard_kept():
    """「我胃痛」/「我睡不着」仍走症状路径，不被疾病扫描抢走。"""
    assert sd.parse_intent("我胃痛")[0] == "symptom"
    assert sd.parse_intent("我睡不着")[0] == "symptom"


def test_ask_dataset_disease_returns_fact_card_offline():
    """离线（无 API Key）也能出疾病事实卡，不依赖 RAG/BGE/LLM。"""
    out = sd.ask("我想了解帕金森病", "P001")
    assert out["intent"] == "disease"
    it = out["interpretation"] or {}
    assert "帕金森病" in (it.get("title") or "")
    assert it.get("source")           # 数据集出处标注
    assert len(it.get("items") or []) > 0
    assert (out["advice"] or {}).get("text")   # 确定性建议，无 Key 也返回

def test_disease_check_phrasing_not_hijacked_by_lab():
    """「X 做什么检查」必须归疾病意图（C7 评测发现：曾被 _LAB_SIGNALS 抢走）。"""
    assert sd.parse_intent("颈椎病一般做什么检查") == ("disease", "颈椎病")
    assert sd.parse_intent("糖尿病需要做什么检查") == ("disease", "糖尿病")
    # 没有疾病名的检查措辞不受影响
    assert sd.parse_intent("我想做个体检")[0] != "disease"
    assert sd.parse_intent("我的血糖怎么检查")[0] != "disease"
