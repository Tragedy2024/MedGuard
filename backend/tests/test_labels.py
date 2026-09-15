"""中文映射完整性测试。

`labels.py` 集中定义面向用户的算法层术语。这里的守卫针对一类具体问题：
**算法层返回的英文字符串若直接透出到界面**（如 degradation_message
"Cannot compute exact result due to privacy policy constraints..."、
rewrite_log "Rule A: Removing clinical_records.diagnosis_name ..."），
产品就退回成开发者工具——会议记录 §1.1 已否决的形态。

算法层一行不改（红线），所以中文必须由产品层并置提供，且不能漏。
"""
from backend.labels import (DEGRADATION_LABELS, DEGRADATION_MESSAGES,
                            SEVERITY_LABELS, TOKEN_LABELS, VIOLATION_LABELS)

LEVELS = ("L0", "L1", "L2", "L3")


def _has_cjk(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


def test_all_degradation_levels_have_label_and_message():
    """每个降级等级都必须同时有中文标签与中文说明，漏一个界面上就会
    掉回英文原文。"""
    for lv in LEVELS:
        assert lv in DEGRADATION_LABELS, f"{lv} 缺中文标签"
        assert lv in DEGRADATION_MESSAGES, f"{lv} 缺中文说明"


def test_face_up_labels_are_chinese():
    """所有面向用户的标签都必须是中文。"""
    merged = {**VIOLATION_LABELS, **SEVERITY_LABELS, **DEGRADATION_LABELS,
              **TOKEN_LABELS}
    not_chinese = {k: v for k, v in merged.items() if not _has_cjk(v)}
    assert not not_chinese, f"以下标签不是中文：{not_chinese}"


def test_degradation_messages_are_chinese():
    """L1–L3 的说明必须是中文。L0（通过）为空字符串是刻意的——
    没有发生降级就无话可说，不该编一句废话填满界面。"""
    for lv in LEVELS:
        if lv == "L0":
            assert DEGRADATION_MESSAGES[lv] == ""
            continue
        msg = DEGRADATION_MESSAGES[lv]
        assert _has_cjk(msg), f"{lv} 的说明不是中文：{msg!r}"


def test_l2_message_explains_the_limitation_not_just_states_it():
    """L2 是最容易让用户困惑的等级——结果「相关但不完全对应」。
    说明必须讲清"为什么"，否则用户会以为系统出错。"""
    msg = DEGRADATION_MESSAGES["L2"]
    assert "不泄露" in msg or "个人信息" in msg, "L2 说明未交代原因"
    assert "口径" in msg or "统计" in msg, "L2 说明未交代结果是什么"
