"""自然语言 → 计划：不依赖模型的纯逻辑测试。

联网部分由 test_api_query.py 用 mock 覆盖；这里只测字符串处理与提示构造，
跑得快且不需要 API Key。
"""
import os
import sys

from backend import config
from backend.llm_nl2sql import _binding_hint, _normalize_output, build_schema_text

# decomposer_parser 在算法层目录里
if config.ALGO_SRC_DIR and config.ALGO_SRC_DIR not in sys.path:
    sys.path.insert(0, config.ALGO_SRC_DIR)
from decomposer_parser import parse_qa_pairs  # noqa: E402


# ── 输出规整 ─────────────────────────────────────────────────────
#
# 实测：deepseek-v4-flash 把「SQL」与围栏挤在同一行，而 SUBQ_PATTERN 要求
# 「SQL」独占一行。不规整的话整条解析失败，用户拿到「无法处理」——
# 而这只差一次字符串替换。

FLASH_STYLE = """Sub question 1: 获取医生 S001 治疗过的患者编号。
SQL: ```sql
SELECT DISTINCT patient_id FROM visits WHERE doctor_id = 'S001';
```

Sub question 2: 统计人数。
SQL: ```sql
SELECT COUNT(DISTINCT patient_id) FROM visits WHERE doctor_id = 'S001';
```
"""

PRO_STYLE = """Sub question 1: 找出糖尿病患者。
SQL
```sql
SELECT patient_id FROM clinical_records WHERE diagnosis_name LIKE '%糖尿病%';
```
"""


def test_normalize_fixes_inline_fence():
    """flash 的写法：规整前解析不出，规整后能解析。"""
    assert parse_qa_pairs(FLASH_STYLE) == []
    tasks = parse_qa_pairs(_normalize_output(FLASH_STYLE))
    assert len(tasks) == 2
    assert "SELECT DISTINCT patient_id" in tasks[0].sql


def test_normalize_does_not_break_already_valid_output():
    """v4-pro 的写法本来就对，规整不能把它改坏。

    这条是防回归的关键：修 flash 的格式时很容易把标准格式一起搞坏。
    """
    assert len(parse_qa_pairs(PRO_STYLE)) == 1
    assert len(parse_qa_pairs(_normalize_output(PRO_STYLE))) == 1


def test_normalize_handles_fullwidth_colon():
    """全角冒号也要认——中文模型容易写出全角标点。"""
    s = "Sub question 1: 测试。\nSQL：\n```sql\nSELECT 1;\n```\n"
    assert len(parse_qa_pairs(_normalize_output(s))) == 1


def test_normalize_is_safe_on_garbage():
    """乱码/空输入不能让规整器本身抛异常。"""
    for bad in ("", "完全没有格式的一段话", None):
        assert isinstance(_normalize_output(bad), str)


# ── 身份约束 ─────────────────────────────────────────────────────

def test_binding_hint_staff_mentions_doctor_id():
    s = _binding_hint("staff", "S001")
    assert "S001" in s and "doctor_id" in s


def test_binding_hint_patient_mentions_patient_id():
    p = _binding_hint("patient", "P001")
    assert "P001" in p and "patient_id" in p


def test_binding_hint_empty_without_subject():
    assert _binding_hint("staff", "") == ""
    assert _binding_hint("patient", "") == ""
    assert _binding_hint("admin", "X") == ""


# ── 表结构描述 ───────────────────────────────────────────────────

def test_schema_text_carries_chinese_aliases_and_examples():
    """描述里必须有中文业务名与真实取值。

    提问是中文而列名是英文（diagnosis_name 之类）。不给中文对照与取值示例，
    模型对不上「糖尿病」这种词——实测正是靠 Value examples 才对上的。
    """
    desc, fk = build_schema_text(config.DEMO_DATASOURCE_ID)
    assert "clinical_records" in desc and "临床记录" in desc
    assert "诊断名称" in desc
    assert "糖尿病" in desc          # 取值示例
    assert "visits.visit_id" in fk or "patient_id" in fk
