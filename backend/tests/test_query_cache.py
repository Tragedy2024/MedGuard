"""问答缓存测试。

缓存是演示稳定性的关键：预热过的问法要毫秒级命中、完全离线。但缓存
**不能削弱安全**——它存的是计划不是答案，命中后照常走准入与审计。
这里覆盖归一化、令牌隔离、容量控制与损坏恢复。
"""
import pytest

from backend import config, query_cache


@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    path = str(tmp_path / "query_cache.json")
    monkeypatch.setattr(config, "QUERY_CACHE_FILE", path)
    monkeypatch.setattr(config, "QUERY_CACHE_PER_TOKEN", 3)
    return path


def _plan(n: int = 1):
    return [{"id": i, "description": f"步骤{i}", "sql": f"SELECT {i}"} for i in range(n)]


def _store(question, plan, token_type, subject_id="P001", source="llm"):
    """这个文件测的是缓存机制（归一化/淘汰/自愈），主体取固定值即可。
    主体绑定行为另有专门的用例。"""
    query_cache.store(question, plan, token_type, subject_id, source=source)


def test_normalize_collapses_whitespace_and_trailing_marks():
    assert query_cache.normalize("  糖尿病患者  有多少  ") == "糖尿病患者 有多少"
    assert query_cache.normalize("血糖是多少？") == "血糖是多少"
    assert query_cache.normalize("血糖是多少?") == "血糖是多少"
    assert query_cache.normalize("　全角空格　") == "全角空格"


def test_store_then_lookup(cache_file):
    _store("血糖是多少", _plan(2), "patient")
    got = query_cache.lookup("血糖是多少", "patient")
    assert got is not None and len(got) == 2


def test_lookup_is_normalized(cache_file):
    """带空格、带问号的同一句问法应命中同一条。"""
    _store("血糖是多少", _plan(), "patient")
    assert query_cache.lookup("  血糖是多少？ ", "patient") is not None


def test_miss_returns_none(cache_file):
    assert query_cache.lookup("从没存过的问法", "staff") is None


def test_token_type_is_respected(cache_file):
    """病患专属问法不该被医护令牌命中——令牌隔离不能因为缓存而失效。"""
    _store("我的血糖", _plan(), "patient")
    assert query_cache.lookup("我的血糖", "patient") is not None
    assert query_cache.lookup("我的血糖", "staff") is None


def test_hit_counter_increments(cache_file):
    _store("血糖", _plan(), "patient")
    query_cache.lookup("血糖", "patient")
    query_cache.lookup("血糖", "patient")
    assert query_cache.stats()["entries"][0]["hits"] == 2


def test_evicts_beyond_limit_per_token(cache_file):
    """容量控制：每类令牌只留最近 N 条，演示缓存不能无限膨胀。"""
    for i in range(5):
        _store(f"问法{i}", _plan(), "staff")
    assert query_cache.stats()["count"] == 3


def test_eviction_does_not_touch_other_token_types(cache_file):
    for i in range(5):
        _store(f"医护问法{i}", _plan(), "staff")
    _store("病患问法", _plan(), "patient")
    st = query_cache.stats()
    assert st["count"] == 4   # 3 条医护 + 1 条病患
    assert any(e["question"] == "病患问法" for e in st["entries"])


def test_corrupted_file_degrades_to_empty(cache_file):
    """缓存损坏不该让查询失败——当作空缓存，由调用方回落到模型。"""
    with open(cache_file, "w", encoding="utf-8") as f:
        f.write("{ 这不是合法 JSON")
    assert query_cache.lookup("任意", "staff") is None
    _store("任意", _plan(), "staff")   # 应能自愈
    assert query_cache.lookup("任意", "staff") is not None


def test_empty_plan_is_not_stored(cache_file):
    """空计划不该进缓存——那会让后续命中的查询静默变成"无需审计"。"""
    _store("空计划", [], "staff")
    assert query_cache.lookup("空计划", "staff") is None


# ── 主体绑定：跨主体复用缓存的计划就是数据越权 ────────────────

def test_subject_agnostic_plan_is_shared(cache_file):
    """用 `{subject_id}` 占位符的计划与主体无关，可以共用。"""
    query_cache.store(
        "我的血糖", [{"id": 0, "description": "d",
                   "sql": "SELECT * FROM clinical_records "
                          "WHERE patient_id = '{subject_id}'"}],
        "patient", "P001")
    assert query_cache.lookup("我的血糖", "patient", "P002") is not None


def test_subject_free_plan_is_shared(cache_file):
    """压根不涉及主体的聚合查询也可以共用。"""
    query_cache.store(
        "各科室接诊量", [{"id": 0, "description": "d",
                     "sql": "SELECT department, COUNT(*) FROM visits "
                            "GROUP BY department"}],
        "staff", "S001")
    assert query_cache.lookup("各科室接诊量", "staff", "S002") is not None


def test_subject_bound_plan_is_not_shared(cache_file):
    """把主体写成**字面量**的计划只对那个主体成立。

    跨主体复用它是数据越权：S002 会拿到 S001 的患者名单，而且
    `admission.passed=true`——层一看不出问题，因为计划"绑定"得好好的，
    只是绑的是别人。
    """
    query_cache.store(
        "我治疗了哪些患者", [{"id": 0, "description": "d",
                        "sql": "SELECT DISTINCT patient_id FROM visits "
                               "WHERE doctor_id = 'S001'"}],
        "staff", "S001")
    assert query_cache.lookup("我治疗了哪些患者", "staff", "S001") is not None
    assert query_cache.lookup("我治疗了哪些患者", "staff", "S002") is None


def test_plan_baking_someone_elses_id_is_shared_by_nobody(cache_file):
    """模型万一写错了别人的编号，那种计划更不该被任何人复用。"""
    query_cache.store(
        "我治疗了哪些患者", [{"id": 0, "description": "d",
                        "sql": "SELECT DISTINCT patient_id FROM visits "
                               "WHERE doctor_id = 'S002'"}],
        "staff", "S001")
    assert query_cache.lookup("我治疗了哪些患者", "staff", "S001") is None
    assert query_cache.lookup("我治疗了哪些患者", "staff", "S002") is None


def test_shipped_demo_cache_records_its_subject_bindings():
    """随包发布的演示缓存里，凡是写死了主体的条目都必须显式记下主体。

    这条防的是"重新导出缓存时把 subject_id 字段丢了"——那会让所有条目
    退化成可共用，等于把这次修好的越权又放回去，而且没有任何症状。
    """
    import json
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "demo", "query_cache.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for question, entry in data["entries"].items():
        bound = not query_cache._is_subject_agnostic(entry.get("plan") or [])
        has_field = entry.get("subject_id") is not None
        assert bound == has_field, (
            f"「{question}」的绑定状态与 subject_id 字段不一致："
            f"写死了主体={bound}，记录了主体={has_field}")
