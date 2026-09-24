"""红旗规则层测试：确定性、零 LLM、命中即升级。

见 backend/backend/redflags.py 的设计说明——红旗层独立于模型与知识图谱，
是回答出口前的最后一道安全网（2026-09 代码评审 P1）。
"""
from backend import smart_doctor
from backend.redflags import RED_FLAGS, check


# ── 单元：命中与精度 ──────────────────────────────────────────

def test_explicit_red_flags_are_detected():
    cases = [
        "我胸痛", "最近喘不上气", "他昏迷了怎么办",
        "大出血血流不止", "全身风疹块是严重过敏吗", "突然晕倒",
    ]
    for q in cases:
        assert check(q), f"应命中红旗：{q}"


def test_common_non_emergency_questions_do_not_trigger():
    cases = [
        "我最近总是睡不着", "感冒了该吃什么药", "头晕想吐",
        "胳膊有点疼", "今天有点乏力", "空腹血糖6.8正常吗",
    ]
    for q in cases:
        assert not check(q), f"不应命中红旗：{q}"


def test_ambiguity_words_are_excluded():
    """「胸闷」「心疼」这类模糊表达不弹横幅——人人弹警告会稀释真信号。"""
    for q in ("我有点胸闷", "心疼得睡不着", "肚子疼了一天"):
        assert not check(q), f"模糊表达不应命中：{q}"


def test_each_rule_group_is_reachable():
    for label, phrases in RED_FLAGS:
        hits = check("请问" + phrases[0])
        assert any(h.label == label for h in hits), label


# ── 集成：ask 出口无条件升级 ──────────────────────────────────

def test_chest_pain_forces_urgent_without_llm():
    """无 API Key（走确定性路径）时，胸痛也必须升级。"""
    resp = smart_doctor.ask("我胸痛", subject_id="P001")
    assert resp["advice"]["urgent"] is True
    assert "急诊" in resp["advice"]["text"]
    assert "立即前往急诊就医" in resp["advice"]["actions"]


def test_redflag_overrides_ai_triage(monkeypatch):
    """即使 AI 判定不紧急，红旗层仍强制升级（红旗在 AI 之后收口）。"""
    resp = smart_doctor.ask("喘不上气，帮我看看", subject_id="P001")
    assert resp["advice"]["urgent"] is True
    assert "急救电话" in resp["advice"]["text"]


def test_non_redflag_question_not_forced():
    resp = smart_doctor.ask("我最近总是睡不着", subject_id="P001")
    assert resp["advice"]["urgent"] is False


def test_lab_intent_with_redflag_still_escalates():
    """红旗与意图正交：挂了报告解读的意图，提到红旗词也升级。"""
    resp = smart_doctor.ask("我胸痛，顺便看下血常规", subject_id="P001")
    assert resp["advice"]["urgent"] is True