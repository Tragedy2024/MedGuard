"""医学安全评测金标的完整性检查（C7，2026-10）。

金标是评测的可信度底座：期望文档必须真实存在于语料（错字会让评测
静默失真），维度取值必须来自枚举，id 必须唯一。
"""
import json
import os

import pytest

_GOLD = os.path.join(os.path.dirname(__file__), "data",
                     "medical_safety_gold.jsonl")
_DIMS = {"escalation", "refusal", "evidence_fidelity", "harmful_misleading"}


def _load_gold() -> list:
    with open(_GOLD, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_gold_well_formed():
    rows = _load_gold()
    assert len(rows) >= 14
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "id 必须唯一"
    for r in rows:
        assert r["question"].strip()
        assert r["dimension"] in _DIMS, f"{r['id']} 维度非法：{r['dimension']}"
        if r["dimension"] == "escalation":
            assert r.get("expect_urgent") is True


def test_gold_expect_docs_exist_in_corpus():
    """evidence_fidelity 的期望文档必须存在于检索语料（错字直接报错）。"""
    import json as _json
    from backend.knowledge import opencmkg

    names = {d.name for d in opencmkg.load_docs()}
    if not names:
        pytest.skip("可选 OpenCMKG 本地语料未生成；生成后运行此金标一致性检查")
    for r in _load_gold():
        for doc in r.get("expect_docs", []):
            assert doc in names, (
                f"{r['id']} 期望文档 {doc!r} 不在语料中（拼写错误？）"
            )
