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


def test_normalize_collapses_whitespace_and_trailing_marks():
    assert query_cache.normalize("  糖尿病患者  有多少  ") == "糖尿病患者 有多少"
    assert query_cache.normalize("血糖是多少？") == "血糖是多少"
    assert query_cache.normalize("血糖是多少?") == "血糖是多少"
    assert query_cache.normalize("　全角空格　") == "全角空格"


def test_store_then_lookup(cache_file):
    query_cache.store("血糖是多少", _plan(2), "patient")
    got = query_cache.lookup("血糖是多少", "patient")
    assert got is not None and len(got) == 2


def test_lookup_is_normalized(cache_file):
    """带空格、带问号的同一句问法应命中同一条。"""
    query_cache.store("血糖是多少", _plan(), "patient")
    assert query_cache.lookup("  血糖是多少？ ", "patient") is not None


def test_miss_returns_none(cache_file):
    assert query_cache.lookup("从没存过的问法", "staff") is None


def test_token_type_is_respected(cache_file):
    """病患专属问法不该被医护令牌命中——令牌隔离不能因为缓存而失效。"""
    query_cache.store("我的血糖", _plan(), "patient")
    assert query_cache.lookup("我的血糖", "patient") is not None
    assert query_cache.lookup("我的血糖", "staff") is None


def test_hit_counter_increments(cache_file):
    query_cache.store("血糖", _plan(), "patient")
    query_cache.lookup("血糖", "patient")
    query_cache.lookup("血糖", "patient")
    assert query_cache.stats()["entries"][0]["hits"] == 2


def test_evicts_beyond_limit_per_token(cache_file):
    """容量控制：每类令牌只留最近 N 条，演示缓存不能无限膨胀。"""
    for i in range(5):
        query_cache.store(f"问法{i}", _plan(), "staff")
    assert query_cache.stats()["count"] == 3


def test_eviction_does_not_touch_other_token_types(cache_file):
    for i in range(5):
        query_cache.store(f"医护问法{i}", _plan(), "staff")
    query_cache.store("病患问法", _plan(), "patient")
    st = query_cache.stats()
    assert st["count"] == 4   # 3 条医护 + 1 条病患
    assert any(e["question"] == "病患问法" for e in st["entries"])


def test_corrupted_file_degrades_to_empty(cache_file):
    """缓存损坏不该让查询失败——当作空缓存，由调用方回落到模型。"""
    with open(cache_file, "w", encoding="utf-8") as f:
        f.write("{ 这不是合法 JSON")
    assert query_cache.lookup("任意", "staff") is None
    query_cache.store("任意", _plan(), "staff")   # 应能自愈
    assert query_cache.lookup("任意", "staff") is not None


def test_empty_plan_is_not_stored(cache_file):
    """空计划不该进缓存——那会让后续命中的查询静默变成"无需审计"。"""
    query_cache.store("空计划", [], "staff")
    assert query_cache.lookup("空计划", "staff") is None
