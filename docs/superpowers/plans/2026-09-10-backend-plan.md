# 后端实现计划 — 医患信息数据服务云平台

> ⚠️ **本文是 2026-09-10 的实现计划，其中的代码示例反映的是当时的设计，不保证
> 与当前实现一致。**已知至少两处：reports 路由当时没有令牌身份参数
> （现在要求必填 `token_type` + `exp`/`sig`），且此后新增了 auth 与
> smart-doctor 两组路由。抄之前请对照 `backend/docs/api-contract/API_CONTRACT.md`
> 与现有代码。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 FastAPI 后端，提供数据源管理、安全策略、查询与拦截、安全事件报告四组接口，并保证病患令牌无法触及任何涉他患者数据。

**Architecture:** 纯适配器模式——产品层不修改算法层，只通过 `backend/deps.py` 这一个文件访问 `J:\race\AIC\NL2SQL`（已 `pip install -e`）。安全由两层构成：层一准入（令牌主语绑定，本计划实现）在查询执行前拦截越权查询；层二审计（算法层医盾引擎，已存在）拦截中间结果暴露。

**Tech Stack:** Python 3.12 / FastAPI 0.141 / uvicorn / sqlglot 30.18 / SQLite / pytest

**Spec:** `docs/superpowers/specs/2026-09-10-medguard-design.md`

## Global Constraints

- **算法层一行不改**。`J:\race\AIC\NL2SQL` 只读，经 `pip install -e` 复用。
- **算法层只经由 `backend/deps.py` 访问**。其他文件不得直接 `import security_auditor` / `ssa.loader`。
- Python 环境：`conda activate medguard`（Python 3.12.14）。
- 违规类型、严重度、降级等级的中文映射集中在 `backend/labels.py`，**不在路由里硬编码中文**（层一拒答文案除外，它是固定的产品文案）。
- **仿真数据必须一眼假**：`患者001` / `TEST-000001` / `TEST-PHONE-001` / `医生001`。
- 后端端口 `8000`，前端 `5173`。
- 所有 API 挂在 `/api` 前缀下。
- **算法层返回的三个坑**（设计文档 §5.1，实测确认）：
  1. `audit_report` 有三种形状，`violations` 键**仅在干净路径存在**
  2. 有违规时只有 `violations_before`/`violations_after`（**整数**），无详情
  3. 违规详情必须另行调用 `SecurityAuditor.audit_all()`
- 提交信息格式 `<type>: <描述>`，契约变更加 `[CONTRACT]` 前缀。

---

## File Structure

| 文件 | 职责 |
|---|---|
| `backend/main.py` | FastAPI 应用装配、CORS、路由挂载 |
| `backend/deps.py` | **唯一**算法层适配点；SSA 目录解析、审计调用封装 |
| `backend/admission.py` | 层一准入判定（纯函数，无 IO） |
| `backend/labels.py` | 违规类型/严重度/降级等级的中文映射 |
| `backend/schemas.py` | Pydantic 模型（前后端契约） |
| `backend/db.py` | 平台元数据库（`medguard.db`）读写 |
| `backend/routers/datasources.py` | 数据源路由 |
| `backend/routers/policies.py` | 安全策略路由 |
| `backend/routers/query.py` | 查询路由（核心） |
| `backend/routers/reports.py` | 报告与效能指标路由 |
| `demo/seed.py` | 建 5 表 + 灌虚构数据 |
| `demo/ssa/regional_health.yaml` | 演示库安全策略（手写，含跨域规则） |
| `demo/queries.json` | 预设查询库（按令牌分组） |
| `tests/test_admission.py` | 层一准入测试 |
| `tests/test_deps.py` | 算法层适配测试 |
| `tests/test_api_*.py` | 各路由测试 |
| `tests/test_demo_queries.py` | **演示数据校验（阻塞性）** |

---

## 阶段一：地基（W1 上半）

### Task 1: 层一准入判定

**Files:**
- Create: `backend/__init__.py`（空）
- Create: `backend/admission.py`
- Create: `tests/__init__.py`（空）
- Create: `tests/test_admission.py`

**Interfaces:**
- Produces:
  - `PATIENT_TABLES: set[str]` — 患者数据表集合
  - `admission_check(sql: str, subject_id: str) -> AdmissionResult`
  - `AdmissionResult` dataclass：`passed: bool`、`reason: str | None`、`checked_tables: list[str]`、`bound_to_subject: bool`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_admission.py`：

```python
"""层一准入判定测试。"""
import pytest
from backend.admission import admission_check


def test_bound_to_self_is_allowed():
    r = admission_check("SELECT * FROM lab_tests WHERE patient_id = 'P001'", "P001")
    assert r.passed is True
    assert r.bound_to_subject is True
    assert r.reason is None


def test_patient_table_without_binding_is_denied():
    r = admission_check("SELECT COUNT(*) FROM patients", "P001")
    assert r.passed is False
    assert r.reason == "该查询涉及其他患者信息，无法提供。"


def test_bound_to_other_patient_is_denied():
    """关键用例：绑定到别的患者也必须拒绝。"""
    r = admission_check("SELECT * FROM patients WHERE patient_id = 'P002'", "P001")
    assert r.passed is False
    assert r.bound_to_subject is False


def test_non_patient_table_is_allowed():
    r = admission_check(
        "SELECT name, title FROM staff WHERE department = '心内科'", "P001")
    assert r.passed is True
    assert r.checked_tables == ["staff"]


def test_clinical_records_without_binding_is_denied():
    r = admission_check("SELECT * FROM clinical_records", "P001")
    assert r.passed is False


def test_malformed_sql_is_denied_fail_closed():
    """解析失败必须拒绝（fail-closed），不能放行。"""
    r = admission_check("SELECT FROM WHERE ((", "P001")
    assert r.passed is False
    assert r.reason == "该查询涉及其他患者信息，无法提供。"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd J:\race\AIC\MedGuard && python -m pytest tests/test_admission.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend'`

- [ ] **Step 3: 实现**

创建 `backend/admission.py`：

```python
"""层一：准入判定。

规则（全称）：病患令牌的查询若引用了患者数据表，必须绑定到令牌自身的
patient_id，否则拒绝。不判断聚合结果能否反推个体——那不可判定（需知
结果基数，而基数要查库才知道）。

本模块是纯函数，无 IO、无 LLM、无数据库访问。
"""
from dataclasses import dataclass, field
from typing import List, Optional

import sqlglot
import sqlglot.expressions as exp

# 患者数据表：引用其一即需绑定本人
PATIENT_TABLES = {"patients", "visits", "clinical_records", "billing"}

# 统一拒答文案（个人数据与统计数据不作区分，避免经错误信息差异反推数据存在性）
DENY_REASON = "该查询涉及其他患者信息，无法提供。"


@dataclass
class AdmissionResult:
    passed: bool
    reason: Optional[str] = None
    checked_tables: List[str] = field(default_factory=list)
    bound_to_subject: bool = False


def admission_check(sql: str, subject_id: str) -> AdmissionResult:
    """判定病患令牌能否发起此查询。

    Args:
        sql: 单条子查询的 SQL
        subject_id: 令牌绑定的患者 ID

    Returns:
        AdmissionResult。解析失败时 fail-closed（拒绝）。
    """
    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        # fail-closed：解析不了就不放行
        return AdmissionResult(passed=False, reason=DENY_REASON)

    tables = sorted({t.name.lower() for t in tree.find_all(exp.Table)})
    touches = [t for t in tables if t in PATIENT_TABLES]

    if not touches:
        return AdmissionResult(
            passed=True, checked_tables=tables, bound_to_subject=False)

    for eq in tree.find_all(exp.EQ):
        for col, val in ((eq.this, eq.expression), (eq.expression, eq.this)):
            if isinstance(col, exp.Column) and col.name.lower() == "patient_id":
                if isinstance(val, exp.Literal) and val.this == subject_id:
                    return AdmissionResult(
                        passed=True, checked_tables=tables, bound_to_subject=True)

    return AdmissionResult(passed=False, reason=DENY_REASON, checked_tables=tables)


def admission_check_plan(plan: list, subject_id: str) -> AdmissionResult:
    """对整个查询计划判定：任一条子查询不通过则整体拒绝。"""
    checked = set()
    for sq in plan:
        r = admission_check(sq.get("sql", ""), subject_id)
        checked.update(r.checked_tables)
        if not r.passed:
            return AdmissionResult(
                passed=False, reason=r.reason,
                checked_tables=sorted(checked), bound_to_subject=False)
    return AdmissionResult(
        passed=True, checked_tables=sorted(checked),
        bound_to_subject=any(
            admission_check(sq.get("sql", ""), subject_id).bound_to_subject
            for sq in plan))
```

创建空的 `backend/__init__.py` 和 `tests/__init__.py`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_admission.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add backend/__init__.py backend/admission.py tests/__init__.py tests/test_admission.py
git commit -m "feat: 层一准入判定（病患令牌主语绑定）"
```

---

### Task 2: 演示库 seed

**Files:**
- Create: `demo/__init__.py`（空）
- Create: `demo/seed.py`
- Create: `tests/test_seed.py`

**Interfaces:**
- Produces:
  - `build_database(db_path: str) -> None` — 建 5 表并灌入虚构数据
  - `TABLE_DDL: list[str]` — 建表语句
  - 表结构见设计文档 §7.1（**5 表**：`patients` / `staff` / `visits` / `clinical_records` / `billing`）

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_seed.py`：

```python
"""演示库建库测试。"""
import os
import sqlite3
import tempfile

from demo.seed import build_database


def _make_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    build_database(path)
    return path


def test_all_five_tables_created():
    path = _make_db()
    con = sqlite3.connect(path)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    os.unlink(path)
    assert {"patients", "staff", "visits", "clinical_records", "billing"} <= names


def test_patient_ids_are_obviously_fake():
    """仿真数据必须一眼假。"""
    path = _make_db()
    con = sqlite3.connect(path)
    ids = [r[0] for r in con.execute("SELECT patient_id FROM patients")]
    con.close()
    os.unlink(path)
    assert ids, "patients 表不应为空"
    assert all(i.startswith("P") for i in ids)


def test_clinical_records_cover_four_types():
    """clinical_records 必须含诊断/用药/检验/影像四类记录。"""
    path = _make_db()
    con = sqlite3.connect(path)
    types = {r[0] for r in con.execute(
        "SELECT DISTINCT record_type FROM clinical_records")}
    con.close()
    os.unlink(path)
    assert types == {"diagnosis", "medication", "lab", "imaging"}


def test_patient_id_present_on_all_clinical_tables():
    """层一准入依赖 patient_id 可直接出现在查询中（反规范化设计）。"""
    path = _make_db()
    con = sqlite3.connect(path)
    for tbl in ("visits", "clinical_records", "billing"):
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
        assert "patient_id" in cols, f"{tbl} 缺少 patient_id"
    con.close()
    os.unlink(path)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'demo'`

- [ ] **Step 3: 实现**

创建 `demo/seed.py`。**关键要求**：

- 5 张表：`patients` / `staff` / `visits` / `clinical_records` / `billing`
- 临床表均含 `patient_id`（反规范化，供层一准入判定）
- `clinical_records` 用 `record_type` 区分 `diagnosis`/`medication`/`lab`/`imaging`
- 所有数据一眼假
- 30 位患者、8 位医生、约 120 条临床记录、60 条费用记录
- **必须包含这些演示锚点**（后续 Task 9 的预设查询依赖）：
  - 患者 `P001` 有 lab 记录（`test_name='血糖'`）、medication 记录、imaging 记录
  - 至少 3 位患者诊断为糖尿病（`diagnosis_name='2型糖尿病'`），供跨域演示
  - `staff` 表含 `department='心内科'` 的医生

```python
"""建演示库（医院数据仿真版）。

所有数据均为虚构，命名一眼可辨：患者001 / TEST-000001 / 医生001。
"""
import os
import sqlite3

TABLE_DDL = [
    """CREATE TABLE patients (
        patient_id   TEXT PRIMARY KEY,
        name         TEXT,          -- controlled
        phone        TEXT,          -- controlled
        birth_date   TEXT,          -- controlled
        address      TEXT,          -- controlled
        id_card      TEXT,          -- blocked
        gender       TEXT,
        blood_type   TEXT
    )""",
    """CREATE TABLE staff (
        staff_id   TEXT PRIMARY KEY,
        name       TEXT,            -- controlled
        phone      TEXT,            -- controlled
        title      TEXT,
        department TEXT,
        specialty  TEXT
    )""",
    """CREATE TABLE visits (
        visit_id    TEXT PRIMARY KEY,
        patient_id  TEXT NOT NULL,
        doctor_id   TEXT,
        department  TEXT,
        visit_type  TEXT,
        visit_date  TEXT
    )""",
    """CREATE TABLE clinical_records (
        record_id    TEXT PRIMARY KEY,
        visit_id     TEXT NOT NULL,
        patient_id   TEXT NOT NULL,
        record_type  TEXT NOT NULL,  -- diagnosis|medication|lab|imaging
        icd_code     TEXT,           -- blocked
        diagnosis_name TEXT,         -- blocked
        severity     TEXT,           -- controlled
        drug_name    TEXT,
        dosage       TEXT,
        frequency    TEXT,
        route        TEXT,
        test_name    TEXT,
        result_value TEXT,
        unit         TEXT,
        ref_range    TEXT,
        modality     TEXT,
        body_part    TEXT,
        findings     TEXT,           -- controlled
        impression   TEXT            -- controlled
    )""",
    """CREATE TABLE billing (
        bill_id        TEXT PRIMARY KEY,
        visit_id       TEXT NOT NULL,
        patient_id     TEXT NOT NULL,
        amount         REAL,         -- controlled
        insurance_type TEXT,
        item_name      TEXT,
        bill_date      TEXT
    )""",
]
```

> **实现提示**：用确定性的伪随机（固定 seed）生成数据，保证每次 seed 结果一致——否则测试与演示不可复现。用 `random.Random(42)`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_seed.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add demo/__init__.py demo/seed.py tests/test_seed.py
git commit -m "feat: 演示库 seed（5 表，虚构数据）"
```

---

### Task 3: 演示库安全策略（SSA）

**Files:**
- Create: `demo/ssa/regional_health.yaml`
- Create: `demo/ssa/__init__.py`（空）
- Create: `tests/test_demo_policy.py`

**Interfaces:**
- Produces: `demo/ssa/regional_health.yaml` — 供 `load_ssa("regional_health", <demo/ssa>)` 加载
- 消费: Task 2 的表结构

> **本任务必须由后端负责人审定。** SSA 标注是人工定案，不是自动生成。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_demo_policy.py`：

```python
"""演示库 SSA 策略测试。"""
import os

from ssa.loader import load_ssa

SSA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "demo", "ssa")


def _ssa():
    return load_ssa("regional_health", SSA_DIR)


def test_policy_loads():
    ssa = _ssa()
    assert ssa.db_id == "regional_health"
    assert len(ssa.column_labels) == 5


def test_blocked_columns():
    ssa = _ssa()
    assert ssa.get("patients.id_card") == "blocked"
    assert ssa.get("clinical_records.icd_code") == "blocked"
    assert ssa.get("clinical_records.diagnosis_name") == "blocked"


def test_controlled_columns():
    ssa = _ssa()
    assert ssa.get("patients.name") == "controlled"
    assert ssa.get("staff.phone") == "controlled"
    assert ssa.get("billing.amount") == "controlled"


def test_free_columns():
    ssa = _ssa()
    assert ssa.get("patients.gender") == "free"
    assert ssa.get("staff.department") == "free"
    assert ssa.get("clinical_records.drug_name") == "free"


def test_cross_domain_rule_exists():
    """跨域规则必须手写——论文仓库 31 个库的跨域规则实际生效数为 0。"""
    ssa = _ssa()
    assert len(ssa.cross_domain_rules) >= 1
    rule = ssa.cross_domain_rules[0]
    assert set(rule.table_pair) == {"clinical_records", "billing"}
    assert rule.join_key == "visit_id"
    assert rule.forbid_personal_level is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_demo_policy.py -v`
Expected: FAIL — `FileNotFoundError: SSA file not found`

- [ ] **Step 3: 实现**

创建 `demo/ssa/regional_health.yaml`：

```yaml
db_id: regional_health
description: 区域医疗集团演示库（医院数据仿真版）

column_labels:
  patients:
    patient_id: free
    name: controlled
    phone: controlled
    birth_date: controlled
    address: controlled
    id_card: blocked
    gender: free
    blood_type: free

  staff:
    staff_id: free
    name: controlled
    phone: controlled
    title: free
    department: free
    specialty: free

  visits:
    visit_id: free
    patient_id: free
    doctor_id: free
    department: free
    visit_type: free
    visit_date: free

  clinical_records:
    record_id: free
    visit_id: free
    patient_id: free
    record_type: free
    icd_code: blocked
    diagnosis_name: blocked
    severity: controlled
    drug_name: free
    dosage: free
    frequency: free
    route: free
    test_name: free
    result_value: free
    unit: free
    ref_range: free
    modality: free
    body_part: free
    findings: controlled
    impression: controlled

  billing:
    bill_id: free
    visit_id: free
    patient_id: free
    amount: controlled
    insurance_type: free
    item_name: free
    bill_date: free

# 逐列标注理由——产品层字段，算法层不读（load_ssa 只读 column_labels
# 与 cross_domain_rules，未知键静默忽略，因此加此键零算法改动）。
column_reasons:
  patients:
    id_card: 唯一标识符，任何场景不得出现在 SELECT
    name: 个人标识
    phone: 个人联系方式
    birth_date: 出生日期可结合其他信息重识别
    address: 住址属个人标识
    gender: 不具识别性
    blood_type: 不具识别性
  staff:
    name: 医生执业信息公开可查
    phone: 个人联系方式，非执业信息
  clinical_records:
    icd_code: ICD 编码等同诊断结论
    diagnosis_name: 诊断信息公开可推断就医事实
    severity: 病情程度属个人健康信息
    findings: 影像所见属个人健康信息
    impression: 影像结论属个人健康信息
  billing:
    amount: 费用金额属个人财务信息

cross_domain_rules:
  - table_pair: [clinical_records, billing]
    join_key: visit_id
    forbid_personal_level: true
    allow_aggregate_level: true
    reason: 临床记录与费用结算属不同安全域，个人级关联可推断特定疾病的治疗成本

review_notes: |
  标注依据：
  - id_card：唯一标识符，任何场景不得出现在 SELECT
  - icd_code / diagnosis_name：诊断信息公开可推断就医事实，列为禁止
  - name / phone / birth_date / address：个人标识，受控
  - staff.name / staff.phone：医生执业信息可查（姓名），但联系方式属个人信息，受控
  - billing.amount：费用金额属个人财务信息，受控
  - findings / impression / severity：影像与诊断结论，受控
  - 跨域规则：临床与费用分属不同安全域，由后端负责人审定
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_demo_policy.py -v`
Expected: 5 passed

> **术语**：本文件里 `free` 是 ECL 标签「自由」，与 Python
> 内置的 `free` 无关。

- [ ] **Step 5: 提交**

```bash
git add demo/ssa/ tests/test_demo_policy.py
git commit -m "feat: 演示库 SSA 策略（含手写跨域规则）"
```

---

### Task 4: 算法层适配（deps.py）

**Files:**
- Create: `backend/deps.py`
- Create: `backend/labels.py`
- Create: `tests/test_deps.py`

**Interfaces:**
- Consumes: Task 3 的 `demo/ssa/regional_health.yaml`
- Produces:
  - `POLICY_DIR: str` — 策略目录（当前指向 `demo/ssa`）
  - `load_policy(datasource_id: str) -> SSALabels`
  - `audit_plan(plan: list, datasource_id: str) -> AuditOutcome`
  - `AuditOutcome` dataclass：`violations: list[dict]`、`audited_plan: list[dict]`、`rewrite_log: list[str]`、`rewrites_applied: int`、`degradation_level: str`、`degradation_message: str`
  - `labels.py`：`VIOLATION_LABELS: dict[str, str]`、`SEVERITY_LABELS: dict[str, str]`、`DEGRADATION_LABELS: dict[str, str]`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_deps.py`：

```python
"""算法层适配测试。"""
from backend.deps import load_policy, audit_plan


def test_load_policy_returns_labels():
    ssa = load_policy("regional_health")
    assert ssa.db_id == "regional_health"
    assert ssa.get("patients.id_card") == "blocked"


def test_audit_plan_clean_passes():
    """最后一条子查询是最终答案，controlled 列合法。"""
    plan = [
        {"id": 0, "description": "聚合", "sql": "SELECT COUNT(*) FROM patients"},
    ]
    out = audit_plan(plan, "regional_health")
    assert out.degradation_level == "L0"
    assert out.violations == []


def test_audit_plan_detects_intermediate_exposure():
    """中间子查询 SELECT 受控列应被检出。

    注意：最后一个子查询的 controlled 列永不违规（is_final 豁免），
    因此必须构造「中间子查询 + 最终子查询」两段式计划。
    """
    plan = [
        {"id": 0, "description": "中间：取患者姓名",
         "sql": "SELECT p.name, p.phone FROM patients p"},
        {"id": 1, "description": "最终：计数",
         "sql": "SELECT COUNT(*) FROM patients"},
    ]
    out = audit_plan(plan, "regional_health")
    assert len(out.violations) >= 1
    types = {v["type"] for v in out.violations}
    assert "column_unnecessary_exposure" in types
    # 违规详情必须带这些字段（前端事件卡片依赖）
    v = out.violations[0]
    assert {"type", "type_label", "column", "severity", "severity_label", "detail"} <= set(v)
    assert out.rewrites_applied >= 1


def test_audit_plan_blocked_column_triggers_degradation():
    """blocked 列出现在 SELECT（含最终子查询）必须降级。"""
    plan = [
        {"id": 0, "description": "最终：导出身份证",
         "sql": "SELECT id_card FROM patients"},
    ]
    out = audit_plan(plan, "regional_health")
    assert len(out.violations) >= 1
    assert out.degradation_level in ("L2", "L3")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_deps.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.deps'`

- [ ] **Step 3: 实现 labels.py**

创建 `backend/labels.py`：

```python
"""中文映射。所有面向用户的算法层术语在此集中定义。"""

VIOLATION_LABELS = {
    "column_unnecessary_exposure": "不必要的列暴露",
    "column_needs_aggregation": "需聚合化",
    "blocked_column_in_select": "禁止列出现在 SELECT",
    "cross_domain_personal_join": "跨域个人级 JOIN",
    "blocked_column_in_derived": "派生表达式引用禁止列",
    "controlled_column_in_derived": "派生表达式暴露受控列",
}

SEVERITY_LABELS = {
    "rewritable": "可拦截",
    "degradable": "需降级",
    "must_degrade": "必须阻断",
}

DEGRADATION_LABELS = {
    "L0": "通过",
    "L1": "聚合替代",
    "L2": "意图变更",
    "L3": "已拒绝",
}
```

- [ ] **Step 4: 实现 deps.py**

创建 `backend/deps.py`。**这是算法层的唯一访问点。**

> **实现要点（三个坑，设计文档 §5.1 实测确认）**：
> 1. `run_security_auditor_pipeline` 的 `audit_report` 有**三种形状**，有违规时**只有整数计数**（`violations_before`/`violations_after`），**没有 `violations` 数组**
> 2. 违规**详情**必须另行调用 `SecurityAuditor.audit_all()` 获取
> 3. `rewrite_log` 是**字符串数组**

```python
"""算法层适配——本项目访问 J:\\race\\AIC\\NL2SQL 的唯一入口。

其他模块不得直接 import security_auditor / ssa.loader。
"""
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

from ssa.loader import load_ssa, SSALabels
from auditor.base import SecurityAuditor
from security_auditor import run_security_auditor_pipeline

from backend.labels import VIOLATION_LABELS, SEVERITY_LABELS

# 策略目录：当前指向演示库；将来接入真实数据源时改为配置项
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_DIR = os.path.join(_PROJECT_ROOT, "demo", "ssa")


@dataclass
class AuditOutcome:
    violations: List[Dict[str, Any]] = field(default_factory=list)
    audited_plan: List[Dict[str, Any]] = field(default_factory=list)
    rewrite_log: List[str] = field(default_factory=list)
    rewrites_applied: int = 0
    degradation_level: str = "L0"
    degradation_message: str = ""


def load_policy(datasource_id: str) -> SSALabels:
    """加载数据源的安全策略（SSA）。"""
    return load_ssa(datasource_id, POLICY_DIR)


def audit_plan(plan: List[Dict[str, Any]], datasource_id: str) -> AuditOutcome:
    """跑完整审计管线，返回违规详情 + 改写 + 降级。

    内部调用算法层两次（都是零 LLM、零查库的纯 AST 计算）：
      1. SecurityAuditor.audit_all()  → 违规详情（管线返回里没有）
      2. run_security_auditor_pipeline() → 改写后的计划与降级等级
    """
    ssa = load_policy(datasource_id)

    # ① 违规详情
    auditor = SecurityAuditor(ssa)
    audit_results = auditor.audit_all(
        [{"id": sq["id"], "sql": sq["sql"]} for sq in plan])

    violations = []
    for ar in audit_results:
        for v in ar.violations:
            violations.append({
                "sub_query_id": v.sub_query_id if v.sub_query_id is not None
                                else str(v.sub_query_index),
                "type": v.type.value,
                "type_label": VIOLATION_LABELS.get(v.type.value, v.type.value),
                "column": v.column,
                "severity": v.severity.value,
                "severity_label": SEVERITY_LABELS.get(v.severity.value, v.severity.value),
                "detail": v.detail,
            })

    # ② 改写与降级
    out = run_security_auditor_pipeline(
        decomposition_plan=plan,
        db_id=datasource_id,
        ssa_dir=POLICY_DIR,
    )
    report = out.get("audit_report", {})

    return AuditOutcome(
        violations=violations,
        audited_plan=out.get("audited_plan", []),
        rewrite_log=report.get("rewrite_log", []) or [],
        rewrites_applied=report.get("rewrites_applied", 0) or 0,
        degradation_level=out.get("degradation_level", "L0"),
        degradation_message=out.get("degradation_message", "") or "",
    )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_deps.py -v`
Expected: 4 passed

> **若 `test_audit_plan_blocked_column_triggers_degradation` 失败**：说明演示库的 blocked 列触发路径与预期不同。这是**真实发现**，不要改测试来迁就实现——先查清 `id_card` 为何没触发降级，必要时在演示库补足触发条件（设计文档 §7.4 三条硬约束）。

- [ ] **Step 6: 提交**

```bash
git add backend/deps.py backend/labels.py tests/test_deps.py
git commit -m "feat: 算法层适配（deps.py 唯一访问点）"
```

---

## 阶段二：API（W2）

### Task 5: 平台元数据库

**Files:**
- Create: `backend/db.py`
- Create: `tests/test_db.py`

**Interfaces:**
- Produces:
  - `init_db(db_path: str) -> None`
  - `save_report(db_path: str, record: dict) -> int` — 返回新记录 id
  - `list_reports(db_path: str, limit: int) -> list[dict]`
  - `get_report(db_path: str, report_id: int) -> dict | None`

> 平台元数据库（`medguard.db`）与演示业务库（`regional_health.db`）**分离**——前者是平台自身状态，后者是被审计对象。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_db.py`：

```python
"""平台元数据库测试。"""
import os
import tempfile

from backend.db import init_db, save_report, list_reports, get_report


def _db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    return path


def test_init_creates_table():
    path = _db()
    assert os.path.exists(path)
    assert list_reports(path, 10) == []
    os.unlink(path)


def test_save_and_get():
    path = _db()
    rid = save_report(path, {
        "question": "统计各科室接诊量",
        "token_type": "staff",
        "degradation_level": "L0",
        "event_count": 2,
        "payload": {"admission": {"passed": True}},
    })
    assert rid > 0
    rec = get_report(path, rid)
    assert rec["question"] == "统计各科室接诊量"
    assert rec["payload"]["admission"]["passed"] is True
    os.unlink(path)


def test_list_returns_newest_first():
    path = _db()
    for q in ("问题一", "问题二", "问题三"):
        save_report(path, {"question": q, "token_type": "staff",
                           "degradation_level": "L0", "event_count": 0,
                           "payload": {}})
    rows = list_reports(path, 10)
    assert len(rows) == 3
    assert rows[0]["question"] == "问题三"
    os.unlink(path)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.db'`

- [ ] **Step 3: 实现**

创建 `backend/db.py`：

```python
"""平台元数据库（medguard.db）。

与被审计的演示业务库分离——本库存平台自身状态。
"""
import json
import sqlite3
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    question          TEXT NOT NULL,
    token_type        TEXT NOT NULL,
    degradation_level TEXT NOT NULL,
    event_count       INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    payload           TEXT NOT NULL
)
"""


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def init_db(db_path: str) -> None:
    con = _connect(db_path)
    con.execute(_SCHEMA)
    con.commit()
    con.close()


def save_report(db_path: str, record: Dict[str, Any]) -> int:
    con = _connect(db_path)
    cur = con.execute(
        "INSERT INTO reports (question, token_type, degradation_level,"
        " event_count, payload) VALUES (?, ?, ?, ?, ?)",
        (record["question"], record["token_type"], record["degradation_level"],
         record.get("event_count", 0),
         json.dumps(record.get("payload", {}), ensure_ascii=False)))
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def list_reports(db_path: str, limit: int = 20) -> List[Dict[str, Any]]:
    con = _connect(db_path)
    rows = con.execute(
        "SELECT id, question, token_type, degradation_level, event_count,"
        " created_at FROM reports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_report(db_path: str, report_id: int) -> Optional[Dict[str, Any]]:
    con = _connect(db_path)
    row = con.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    con.close()
    if row is None:
        return None
    rec = dict(row)
    rec["payload"] = json.loads(rec["payload"])
    return rec
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_db.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add backend/db.py tests/test_db.py
git commit -m "feat: 平台元数据库"
```

---

### Task 6: Pydantic 契约模型

**Files:**
- Create: `backend/schemas.py`
- Create: `tests/test_schemas.py`

**Interfaces:**
- Produces: 契约模型，**字段名必须与团队规范 §3.2 完全一致**
- 被 Task 7–11 的所有路由消费

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_schemas.py`：

```python
"""契约模型测试。字段名即前后端契约，不可随意改名。"""
from backend.schemas import QueryRequest, Token


def test_token_types():
    assert Token(type="staff", subject_id=None).type == "staff"
    assert Token(type="patient", subject_id="P001").subject_id == "P001"


def test_query_request_shape():
    req = QueryRequest(
        token=Token(type="patient", subject_id="P001"),
        datasource_id="regional_health",
        question_id="patient_my_lab",
    )
    assert req.datasource_id == "regional_health"
    assert req.question_id == "patient_my_lab"


def test_query_request_serializes_with_expected_keys():
    req = QueryRequest(
        token=Token(type="staff", subject_id=None),
        datasource_id="regional_health",
        question_id="doctor_dept_visits",
    )
    data = req.model_dump()
    assert set(data.keys()) == {"token", "datasource_id", "question_id"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.schemas'`

- [ ] **Step 3: 实现**

创建 `backend/schemas.py`，字段严格对齐团队规范 §3.2：

```python
"""前后端契约（Pydantic 模型）。

字段名即契约。改字段名 = 契约变更，必须走团队规范 §3.4 流程。
"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class Token(BaseModel):
    type: Literal["staff", "patient"]
    subject_id: Optional[str] = None   # 病患令牌必填，医护令牌为 None


class QueryRequest(BaseModel):
    token: Token
    datasource_id: str
    question_id: str


class AdmissionInfo(BaseModel):
    passed: bool
    reason: Optional[str] = None
    checked_tables: List[str] = Field(default_factory=list)
    bound_to_subject: bool = False


class PlanItem(BaseModel):
    id: int
    description: str
    sql_before: str
    sql_after: str
    is_final: bool


class SecurityEvent(BaseModel):
    sub_query_id: str
    type: str
    type_label: str
    column: str
    severity: str
    severity_label: str
    detail: str


class RewriteInfo(BaseModel):
    applied: int = 0
    log: List[str] = Field(default_factory=list)


class DegradationInfo(BaseModel):
    level: str
    label: str
    message: str = ""


class MetricsInfo(BaseModel):
    """审计阶段的开销——**不是查询执行的开销**。

    `llm_calls` / `db_access` 恒为 0，这是「零 LLM、零查库」安全声明的
    可验证形式：审计器不调模型（无提示注入面）、不碰数据库（无困惑代理面）。
    查询本身的执行不计入本指标——`result` 非空即表示执行过。
    """
    elapsed_ms: int = 0      # 层一 + 层二的审计耗时
    llm_calls: int = 0       # 恒为 0
    db_access: int = 0       # 恒为 0


class ResultSet(BaseModel):
    columns: List[str] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)


class QueryResponse(BaseModel):
    admission: AdmissionInfo
    question: str
    plan: List[PlanItem] = Field(default_factory=list)
    events: List[SecurityEvent] = Field(default_factory=list)
    rewrite: RewriteInfo = Field(default_factory=RewriteInfo)
    degradation: DegradationInfo
    metrics: MetricsInfo = Field(default_factory=MetricsInfo)
    result: Optional[ResultSet] = None


class DatasourceInfo(BaseModel):
    id: str
    name: str
    table_count: int
    column_count: int
    policy_ready: bool


class ReportSummary(BaseModel):
    id: int
    question: str
    token_type: str
    created_at: str
    degradation_level: str
    event_count: int
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_schemas.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add backend/schemas.py tests/test_schemas.py
git commit -m "feat: Pydantic 契约模型"
```

---

### Task 7: 数据源路由

**Files:**
- Create: `backend/routers/__init__.py`（空）
- Create: `backend/routers/datasources.py`
- Create: `backend/main.py`
- Create: `tests/test_api_datasources.py`

**Interfaces:**
- Consumes: Task 2 `build_database`、Task 6 schemas
- Produces: `GET /api/datasources`、`POST /api/datasources/demo`、`GET /api/datasources/{id}/schema`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_api_datasources.py`：

```python
"""数据源路由测试。"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDGUARD_DATA_DIR", str(tmp_path))
    from backend import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_list_datasources_empty_initially(client):
    r = client.get("/api/datasources")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_create_demo_datasource(client):
    r = client.post("/api/datasources/demo")
    assert r.status_code == 200
    assert r.json()["id"] == "regional_health"


def test_schema_lists_five_tables(client):
    client.post("/api/datasources/demo")
    r = client.get("/api/datasources/regional_health/schema")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tables"]}
    assert {"patients", "staff", "visits", "clinical_records", "billing"} <= names
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_api_datasources.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.main'`

- [ ] **Step 3: 实现**

创建 `backend/config.py`（运行时路径，供测试注入）：

```python
"""运行时路径配置。"""
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("MEDGUARD_DATA_DIR", os.path.join(_PROJECT_ROOT, "data"))
METADATA_DB = os.path.join(DATA_DIR, "medguard.db")
BUSINESS_DB = os.path.join(DATA_DIR, "regional_health.db")
```

创建 `backend/routers/datasources.py`：

```python
"""数据源路由。"""
import os
import sqlite3

from fastapi import APIRouter, HTTPException

from backend import config
from backend.schemas import DatasourceInfo

router = APIRouter(prefix="/api/datasources", tags=["datasources"])

DEMO_ID = "regional_health"
DEMO_NAME = "区域医疗集团"


@router.get("", response_model=list[DatasourceInfo])
def list_datasources():
    if not os.path.exists(config.BUSINESS_DB):
        return []
    tables = _tables(config.BUSINESS_DB)
    return [DatasourceInfo(
        id=DEMO_ID, name=DEMO_NAME,
        table_count=len(tables),
        column_count=sum(len(c) for c in tables.values()),
        policy_ready=True)]


@router.post("/demo")
def create_demo():
    from demo.seed import build_database
    os.makedirs(config.DATA_DIR, exist_ok=True)
    build_database(config.BUSINESS_DB)
    return {"id": DEMO_ID, "created": True}


@router.get("/{datasource_id}/schema")
def get_schema(datasource_id: str):
    if datasource_id != DEMO_ID or not os.path.exists(config.BUSINESS_DB):
        raise HTTPException(status_code=404, detail="数据源不存在")
    con = sqlite3.connect(config.BUSINESS_DB)
    tables = []
    for name in _tables(config.BUSINESS_DB):
        cols = [{"name": r[1], "type": r[2], "pk": bool(r[5])}
                for r in con.execute(f"PRAGMA table_info({name})")]
        tables.append({"name": name, "columns": cols})
    con.close()
    return {"tables": tables}


def _tables(db_path: str) -> dict:
    con = sqlite3.connect(db_path)
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    out = {n: list(con.execute(f"PRAGMA table_info({n})")) for n in names}
    con.close()
    return out
```

创建 `backend/main.py`：

```python
"""FastAPI 应用装配。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import datasources

app = FastAPI(title="医患信息数据服务云平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(datasources.router)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_api_datasources.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add backend/config.py backend/routers/ backend/main.py tests/test_api_datasources.py
git commit -m "feat: 数据源路由"
```

---

### Task 8: 安全策略路由

**Files:**
- Create: `backend/routers/policies.py`
- Modify: `backend/main.py`（挂载路由）
- Create: `tests/test_api_policies.py`

**Interfaces:**
- Consumes: Task 3 `demo/ssa/regional_health.yaml`、Task 4 `load_policy`
- Produces: `GET /api/policies/{datasource_id}`、`PUT /api/policies/{datasource_id}`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_api_policies.py`：

```python
"""安全策略路由测试。"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_get_policy_returns_labels(client):
    r = client.get("/api/policies/regional_health")
    assert r.status_code == 200
    data = r.json()
    assert data["column_labels"]["patients"]["id_card"] == "blocked"
    assert data["column_labels"]["patients"]["gender"] == "free"


def test_get_policy_returns_cross_domain_rules(client):
    r = client.get("/api/policies/regional_health")
    rules = r.json()["cross_domain_rules"]
    assert len(rules) >= 1
    assert set(rules[0]["table_pair"]) == {"clinical_records", "billing"}


def test_get_policy_returns_column_reasons(client):
    """逐列标注理由是产品层字段——算法层不读，但界面要展示。"""
    r = client.get("/api/policies/regional_health")
    reasons = r.json()["column_reasons"]
    assert reasons["patients"]["id_card"]
    assert reasons["billing"]["amount"]


def test_all_demo_columns_are_labeled(client):
    """演示库的每一列都必须有 ECL 标注。

    这不是洁癖：`SSALabels.get()` 对未标注的列返回 FREE（fail-open），
    漏标一列 = 医盾静默放行该列。此测试守住演示库不出现这种缺口。
    """
    r = client.get("/api/policies/regional_health")
    status = r.json()["review_status"]
    assert status, "review_status 为空——演示库未载入"
    unlabeled = [
        f"{t}.{c}"
        for t, cols in status.items()
        for c, s in cols.items() if s == "unlabeled"
    ]
    assert unlabeled == [], f"以下列未标注（将被静默放行）：{unlabeled}"


def test_put_policy_updates_label(client):
    r = client.put("/api/policies/regional_health", json={
        "column_labels": {"patients": {"gender": "controlled"}},
    })
    assert r.status_code == 200
    assert r.json()["saved"] is True
    # 回读确认
    after = client.get("/api/policies/regional_health").json()
    assert after["column_labels"]["patients"]["gender"] == "controlled"

    # 复原，避免影响其他测试
    client.put("/api/policies/regional_health", json={
        "column_labels": {"patients": {"gender": "free"}},
    })
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_api_policies.py -v`
Expected: FAIL — 404（路由不存在）

- [ ] **Step 3: 实现**

创建 `backend/routers/policies.py`：

```python
"""安全策略路由。

策略以 YAML 形式存放在 demo/ssa/，本路由读写该文件。
"""
import os
import sqlite3
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import config
from backend.deps import POLICY_DIR

router = APIRouter(prefix="/api/policies", tags=["policies"])


class PolicyUpdate(BaseModel):
    column_labels: Optional[Dict[str, Dict[str, str]]] = None
    cross_domain_rules: Optional[List[Dict[str, Any]]] = None


def _path(datasource_id: str) -> str:
    return os.path.join(POLICY_DIR, f"{datasource_id}.yaml")


@router.get("/{datasource_id}")
def get_policy(datasource_id: str):
    p = _path(datasource_id)
    if not os.path.exists(p):
        raise HTTPException(status_code=404, detail="策略不存在")
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    labels = data.get("column_labels", {}) or {}
    return {
        "datasource_id": datasource_id,
        "column_labels": labels,
        "column_reasons": data.get("column_reasons", {}) or {},
        "cross_domain_rules": data.get("cross_domain_rules", []) or [],
        "review_status": _review_status(labels),
    }


def _review_status(labels: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """对比库中实际列与策略标注，标出未标注的列。

    **为什么这个字段是安全相关的**：`SSALabels.get()` 对未标注的列
    返回 `FREE`（fail-open）。因此"库里有一列、策略里没有它"意味着
    医盾会**静默放行**该列——这是策略腐烂的具体形态，不是理论担忧。

    Returns:
        {table: {column: "labeled" | "unlabeled"}}
    """
    if not os.path.exists(config.BUSINESS_DB):
        return {}
    con = sqlite3.connect(config.BUSINESS_DB)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        status: Dict[str, Dict[str, str]] = {}
        for t in tables:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
            known = {k.lower() for k in labels.get(t, {})}
            status[t] = {
                c: ("labeled" if c.lower() in known else "unlabeled")
                for c in cols
            }
        return status
    finally:
        con.close()


@router.put("/{datasource_id}")
def update_policy(datasource_id: str, body: PolicyUpdate):
    p = _path(datasource_id)
    if not os.path.exists(p):
        raise HTTPException(status_code=404, detail="策略不存在")
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if body.column_labels:
        labels = data.setdefault("column_labels", {})
        for table, cols in body.column_labels.items():
            labels.setdefault(table, {}).update(cols)
    if body.cross_domain_rules is not None:
        data["cross_domain_rules"] = body.cross_domain_rules

    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return {"saved": True}
```

在 `backend/main.py` 中挂载：

```python
from backend.routers import datasources, policies
# ...
app.include_router(policies.router)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_api_policies.py -v`
Expected: 5 passed

> 若 `test_all_demo_columns_are_labeled` 失败，说明 Task 2 的 seed 建了
> `demo/ssa/regional_health.yaml` 里没有的列。**补 YAML，不要改测试**。

- [ ] **Step 5: 提交**

```bash
git add backend/routers/policies.py backend/main.py tests/test_api_policies.py
git commit -m "feat: 安全策略路由"
```

---

### Task 9: 预设查询库

**Files:**
- Create: `demo/queries.json`
- Create: `tests/test_queries.py`

**Interfaces:**
- Produces: `demo/queries.json`，结构为按令牌分组的查询列表
- 被 Task 10 的查询路由消费

`demo/queries.json` 结构：

```json
{
  "doctor_dept_visits": {
    "token_types": ["staff"],
    "question": "统计各科室接诊量",
    "plan": [
      {"id": 0, "description": "中间：按科室聚合就诊",
       "sql": "SELECT department, COUNT(*) AS visit_count FROM visits GROUP BY department"},
      {"id": 1, "description": "最终：输出科室接诊量",
       "sql": "SELECT department, visit_count FROM visits GROUP BY department"}
    ]
  },
  "doctor_diabetes_cost": {
    "token_types": ["staff"],
    "question": "糖尿病患者产生了多少费用",
    "plan": [
      {"id": 0, "description": "中间：关联临床记录与费用",
       "sql": "SELECT c.patient_id, c.diagnosis_name, b.amount FROM clinical_records c JOIN billing b ON c.visit_id = b.visit_id WHERE c.record_type = 'diagnosis'"},
      {"id": 1, "description": "最终：按诊断汇总费用",
       "sql": "SELECT SUM(amount) FROM billing"}
    ]
  },
  "doctor_diagnosis_stats": {
    "token_types": ["staff"],
    "question": "按诊断结果分类统计患者数",
    "plan": [
      {"id": 0, "description": "中间：按诊断分类",
       "sql": "SELECT CASE WHEN diagnosis_name LIKE '%糖尿病%' THEN '糖尿病' ELSE '其他' END AS grp, patient_id FROM clinical_records WHERE record_type = 'diagnosis'"},
      {"id": 1, "description": "最终：统计各类患者数",
       "sql": "SELECT COUNT(DISTINCT patient_id) FROM clinical_records"}
    ]
  },
  "doctor_export_roster": {
    "token_types": ["staff"],
    "question": "导出患者基本信息核对表",
    "plan": [
      {"id": 0, "description": "最终：导出患者信息",
       "sql": "SELECT patient_id, name, id_card FROM patients"}
    ]
  },
  "patient_my_lab": {
    "token_types": ["patient"],
    "question": "我上次的血糖是多少",
    "plan": [
      {"id": 0, "description": "最终：我的血糖结果",
       "sql": "SELECT test_name, result_value, unit FROM clinical_records WHERE patient_id = '{subject_id}' AND record_type = 'lab' AND test_name = '血糖'"}
    ]
  },
  "patient_my_medication": {
    "token_types": ["patient"],
    "question": "医生给我开的药怎么吃",
    "plan": [
      {"id": 0, "description": "最终：我的用药说明",
       "sql": "SELECT drug_name, dosage, frequency, route FROM clinical_records WHERE patient_id = '{subject_id}' AND record_type = 'medication'"}
    ]
  },
  "patient_my_imaging": {
    "token_types": ["patient"],
    "question": "我的影像报告怎么说",
    "plan": [
      {"id": 0, "description": "最终：我的影像结论",
       "sql": "SELECT modality, body_part, impression FROM clinical_records WHERE patient_id = '{subject_id}' AND record_type = 'imaging'"}
    ]
  },
  "patient_doctors": {
    "token_types": ["patient"],
    "question": "心内科有哪些医生",
    "plan": [
      {"id": 0, "description": "最终：心内科医生列表",
       "sql": "SELECT name, title, specialty FROM staff WHERE department = '心内科'"}
    ]
  },
  "patient_others_count": {
    "token_types": ["patient"],
    "question": "得这个病的有多少人",
    "plan": [
      {"id": 0, "description": "统计患者数",
       "sql": "SELECT COUNT(*) FROM patients"}
    ]
  }
}
```

> `{subject_id}` 是占位符，由查询路由填入令牌绑定的患者 ID。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_queries.py`：

```python
"""预设查询库测试。"""
import json
import os

QUERIES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "demo", "queries.json")


def _load():
    with open(QUERIES_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_all_expected_queries_present():
    q = _load()
    assert set(q) == {
        "doctor_dept_visits", "doctor_diabetes_cost", "doctor_diagnosis_stats",
        "doctor_export_roster", "patient_my_lab", "patient_my_medication",
        "patient_my_imaging", "patient_doctors", "patient_others_count",
    }


def test_doctor_queries_tagged_staff():
    q = _load()
    for key in ("doctor_dept_visits", "doctor_diabetes_cost",
                "doctor_diagnosis_stats", "doctor_export_roster"):
        assert "staff" in q[key]["token_types"]


def test_patient_queries_tagged_patient():
    q = _load()
    for key in ("patient_my_lab", "patient_my_medication", "patient_my_imaging",
                "patient_doctors", "patient_others_count"):
        assert "patient" in q[key]["token_types"]


def test_every_plan_has_required_fields():
    q = _load()
    for key, item in q.items():
        assert item["plan"], f"{key} 计划为空"
        for sq in item["plan"]:
            assert {"id", "description", "sql"} <= set(sq), f"{key} 子查询字段不全"


def test_patient_own_queries_use_subject_placeholder():
    """病患查本人数据的查询必须带 {subject_id} 占位符，否则层一会拒绝。"""
    q = _load()
    for key in ("patient_my_lab", "patient_my_medication", "patient_my_imaging"):
        sqls = " ".join(sq["sql"] for sq in q[key]["plan"])
        assert "{subject_id}" in sqls, f"{key} 缺少 {subject_id} 占位符"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_queries.py -v`
Expected: FAIL — `FileNotFoundError`

- [ ] **Step 3: 实现**

创建 `demo/queries.json`，内容如上。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_queries.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add demo/queries.json tests/test_queries.py
git commit -m "feat: 预设查询库（医护 4 条 + 病患 5 条）"
```

---

### Task 10: 查询路由（核心）

**Files:**
- Create: `backend/routers/query.py`
- Modify: `backend/main.py`（挂载路由）
- Create: `tests/test_api_query.py`

**Interfaces:**
- Consumes: Task 1 `admission_check_plan`、Task 4 `audit_plan`、Task 5 `save_report`、Task 6 schemas、Task 9 queries.json
- Produces: `POST /api/query` → `QueryResponse`

**执行顺序（必须严格遵循）**：

```
读预设查询 → 填 {subject_id} → 【层一】准入 → 【层二】审计 → 执行 SQL → 存报告 → 返回
                                  ↓ 拒绝
                            直接返回（不审计、不执行）
```

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_api_query.py`：

```python
"""查询路由测试。"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDGUARD_DATA_DIR", str(tmp_path))
    from backend import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "METADATA_DB", str(tmp_path / "medguard.db"))
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "regional_health.db"))
    from backend.db import init_db
    init_db(str(tmp_path / "medguard.db"))
    c = TestClient(app)
    c.post("/api/datasources/demo")
    return c


STAFF = {"type": "staff", "subject_id": None}
PATIENT = {"type": "patient", "subject_id": "P001"}


def test_patient_own_lab_is_allowed(client):
    r = client.post("/api/query", json={
        "token": PATIENT, "datasource_id": "regional_health",
        "question_id": "patient_my_lab"})
    assert r.status_code == 200
    body = r.json()
    assert body["admission"]["passed"] is True
    assert body["result"] is not None


def test_patient_querying_others_is_denied_before_execution(client):
    """层一拒绝：不审计、不执行、无结果。"""
    r = client.post("/api/query", json={
        "token": PATIENT, "datasource_id": "regional_health",
        "question_id": "patient_others_count"})
    body = r.json()
    assert body["admission"]["passed"] is False
    assert body["admission"]["reason"] == "该查询涉及其他患者信息，无法提供。"
    assert body["result"] is None
    assert body["plan"] == []


def test_patient_can_query_doctors(client):
    """不涉及患者表的查询放行。"""
    r = client.post("/api/query", json={
        "token": PATIENT, "datasource_id": "regional_health",
        "question_id": "patient_doctors"})
    body = r.json()
    assert body["admission"]["passed"] is True
    assert body["result"] is not None


def test_patient_token_cannot_use_doctor_query(client):
    """令牌类型与查询不匹配时拒绝。"""
    r = client.post("/api/query", json={
        "token": PATIENT, "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    assert r.status_code == 403


def test_staff_query_returns_events_when_intermediate_exposes(client):
    r = client.post("/api/query", json={
        "token": STAFF, "datasource_id": "regional_health",
        "question_id": "doctor_diabetes_cost"})
    body = r.json()
    assert body["admission"]["passed"] is True
    assert len(body["events"]) >= 1
    ev = body["events"][0]
    assert ev["type_label"] and ev["severity_label"]


def test_blocked_column_query_degrades(client):
    r = client.post("/api/query", json={
        "token": STAFF, "datasource_id": "regional_health",
        "question_id": "doctor_export_roster"})
    body = r.json()
    assert len(body["events"]) >= 1
    assert body["degradation"]["level"] in ("L2", "L3")


def test_metrics_count_audit_only_not_execution(client):
    """metrics 报的是审计开销，不是查询执行开销。

    查询确实执行了（result 非空），但 db_access 仍为 0——因为
    审计阶段零查库。零 LLM、零查库是安全声明，必须可验证。
    """
    body = client.post("/api/query", json={
        "token": STAFF, "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"}).json()
    assert body["result"] is not None, "查询应已执行"
    m = body["metrics"]
    assert m["llm_calls"] == 0
    assert m["db_access"] == 0
    assert m["elapsed_ms"] >= 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_api_query.py -v`
Expected: FAIL — 404（路由不存在）

- [ ] **Step 3: 实现**

创建 `backend/routers/query.py`：

```python
"""查询路由——核心。

顺序：读预设 → 填 subject_id → 层一准入 → 层二审计 → 执行 → 存报告
"""
import json
import os
import sqlite3
import time
from typing import Optional

from fastapi import APIRouter, HTTPException

from backend import config
from backend.admission import admission_check_plan
from backend.db import save_report
from backend.deps import audit_plan
from backend.labels import DEGRADATION_LABELS
from backend.schemas import (AdmissionInfo, DegradationInfo, MetricsInfo,
                             PlanItem, QueryRequest, QueryResponse,
                             ResultSet, RewriteInfo, SecurityEvent)

router = APIRouter(prefix="/api/query", tags=["query"])

_QUERIES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "demo", "queries.json")


def _load_queries() -> dict:
    with open(_QUERIES_PATH, encoding="utf-8") as f:
        return json.load(f)


@router.post("", response_model=QueryResponse)
def run_query(req: QueryRequest):
    t0 = time.perf_counter()
    queries = _load_queries()
    spec = queries.get(req.question_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="查询不存在")
    if req.token.type not in spec["token_types"]:
        raise HTTPException(status_code=403, detail="该令牌无权使用此查询")

    # 填占位符
    subject = req.token.subject_id or ""
    plan = [
        {"id": sq["id"], "description": sq["description"],
         "sql": sq["sql"].replace("{subject_id}", subject)}
        for sq in spec["plan"]
    ]

    # 【层一】准入
    adm = admission_check_plan(plan, subject)
    if not adm.passed:
        resp = QueryResponse(
            admission=AdmissionInfo(passed=False, reason=adm.reason,
                                    checked_tables=adm.checked_tables,
                                    bound_to_subject=False),
            question=spec["question"],
            plan=[], events=[],
            rewrite=RewriteInfo(),
            degradation=DegradationInfo(level="L3", label=DEGRADATION_LABELS["L3"],
                                        message=adm.reason or ""),
            metrics=MetricsInfo(
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
                llm_calls=0, db_access=0),
            result=None,
        )
        _persist(req, spec, resp)
        return resp

    # 【层二】审计
    audited_at = int((time.perf_counter() - t0) * 1000)   # 审计耗时到此为止
    outcome = audit_plan(plan, req.datasource_id)

    # 执行（仅当未被 L3 拒绝）
    result = None
    if outcome.degradation_level != "L3":
        sql_after = {sq["id"]: sq["sql"] for sq in outcome.audited_plan} \
            if outcome.audited_plan else {sq["id"]: sq["sql"] for sq in plan}
        result = _execute(sql_after, plan)

    # 组装
    audited_by_id = {sq["id"]: sq["sql"] for sq in outcome.audited_plan}
    plan_items = [
        PlanItem(id=sq["id"], description=sq["description"],
                 sql_before=sq["sql"],
                 sql_after=audited_by_id.get(sq["id"], sq["sql"]),
                 is_final=(i == len(plan) - 1))
        for i, sq in enumerate(plan)
    ]

    resp = QueryResponse(
        admission=AdmissionInfo(passed=True, reason=None,
                                checked_tables=adm.checked_tables,
                                bound_to_subject=adm.bound_to_subject),
        question=spec["question"],
        plan=plan_items,
        events=[SecurityEvent(**v) for v in outcome.violations],
        rewrite=RewriteInfo(applied=outcome.rewrites_applied,
                            log=outcome.rewrite_log),
        degradation=DegradationInfo(
            level=outcome.degradation_level,
            label=DEGRADATION_LABELS.get(outcome.degradation_level, ""),
            message=outcome.degradation_message),
        # metrics 只报审计开销：llm_calls 与 db_access 恒为 0，
        # 这是「零 LLM、零查库」安全声明的可验证形式。查询执行不计入。
        metrics=MetricsInfo(elapsed_ms=audited_at, llm_calls=0, db_access=0),
        result=result,
    )
    _persist(req, spec, resp)
    return resp


def _execute(sql_by_id: dict, plan: list) -> Optional[ResultSet]:
    """执行改写后的最后一条子查询。

    返回 None 表示未执行或执行失败——两种情况都不返回结果集，
    前端表现为「无结果」，不会误导为「查询结果为空」。
    """
    if not os.path.exists(config.BUSINESS_DB):
        return None
    final_id = plan[-1]["id"]
    sql = sql_by_id.get(final_id)
    if not sql:
        return None
    con = sqlite3.connect(config.BUSINESS_DB)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return ResultSet(columns=cols, rows=rows)


def _persist(req: QueryRequest, spec: dict, resp: QueryResponse) -> None:
    db_path = config.METADATA_DB
    if not os.path.exists(db_path):
        return
    save_report(db_path, {
        "question": spec["question"],
        "token_type": req.token.type,
        "degradation_level": resp.degradation.level,
        "event_count": len(resp.events),
        "payload": resp.model_dump(),
    })
```

在 `backend/main.py` 挂载 `query.router`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_api_query.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add backend/routers/query.py backend/main.py tests/test_api_query.py
git commit -m "feat: 查询路由（两层安全 + 执行）"
```

---

### Task 11: 报告与效能路由

**Files:**
- Create: `backend/routers/reports.py`
- Modify: `backend/main.py`（挂载路由）
- Create: `tests/test_api_reports.py`

**Interfaces:**
- Consumes: Task 5 `list_reports`/`get_report`
- Produces: `GET /api/reports`、`GET /api/reports/{id}`、`GET /api/reports/{id}/export`、`GET /api/metrics/detection`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_api_reports.py`：

```python
"""报告路由测试。"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDGUARD_DATA_DIR", str(tmp_path))
    from backend import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "METADATA_DB", str(tmp_path / "medguard.db"))
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "regional_health.db"))
    from backend.db import init_db
    init_db(str(tmp_path / "medguard.db"))
    c = TestClient(app)
    c.post("/api/datasources/demo")
    return c


def test_detection_metrics_match_paper_repo(client):
    """效能数字必须来自论文仓库实测，不得杜撰。"""
    r = client.get("/api/metrics/detection")
    assert r.status_code == 200
    m = r.json()
    assert m["precision"]["value"] == 1.0
    assert m["recall"]["value"] == 1.0
    assert "rq3" in m["source"]


def test_reports_list_after_query(client):
    client.post("/api/query", json={
        "token": {"type": "staff", "subject_id": None},
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    r = client.get("/api/reports")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    assert rows[0]["question"] == "统计各科室接诊量"


def test_report_detail_has_full_payload(client):
    client.post("/api/query", json={
        "token": {"type": "staff", "subject_id": None},
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    rid = client.get("/api/reports").json()[0]["id"]
    r = client.get(f"/api/reports/{rid}")
    assert r.status_code == 200
    assert "admission" in r.json()["payload"]


def test_export_returns_attachment(client):
    client.post("/api/query", json={
        "token": {"type": "staff", "subject_id": None},
        "datasource_id": "regional_health",
        "question_id": "doctor_dept_visits"})
    rid = client.get("/api/reports").json()[0]["id"]
    r = client.get(f"/api/reports/{rid}/export")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_api_reports.py -v`
Expected: FAIL — 404

- [ ] **Step 3: 实现**

创建 `backend/routers/reports.py`：

```python
"""报告与检测效能路由。"""
import json
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend import config
from backend.db import get_report, list_reports

router = APIRouter(prefix="/api", tags=["reports"])

# 检测效能：来自论文仓库 results/rq3/ 实测，不得杜撰
_DETECTION = {
    "precision": {"value": 1.0, "detail": "误报 0/66"},
    "recall": {"value": 1.0, "detail": "漏报 0/20"},
    "blocked_detection": {"value": 1.0, "detail": "检出 10/10（L2=8 / L3=2）"},
    "source": "NL2SQL/results/rq3/",
}


@router.get("/metrics/detection")
def detection_metrics():
    return _DETECTION


@router.get("/reports")
def reports(limit: int = 20):
    if not os.path.exists(config.METADATA_DB):
        return []
    return list_reports(config.METADATA_DB, limit)


@router.get("/reports/{report_id}")
def report_detail(report_id: int):
    if not os.path.exists(config.METADATA_DB):
        raise HTTPException(status_code=404, detail="报告不存在")
    rec = get_report(config.METADATA_DB, report_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return rec


@router.get("/reports/{report_id}/export")
def export_report(report_id: int):
    if not os.path.exists(config.METADATA_DB):
        raise HTTPException(status_code=404, detail="报告不存在")
    rec = get_report(config.METADATA_DB, report_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    body = json.dumps(rec, ensure_ascii=False, indent=2)
    return Response(
        content=body, media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="medguard-report-{report_id}.json"'})
```

在 `backend/main.py` 挂载 `reports.router`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_api_reports.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add backend/routers/reports.py backend/main.py tests/test_api_reports.py
git commit -m "feat: 报告与检测效能路由"
```

---

## 阶段三：校验与联调（W3-W4）

### Task 12: 演示数据校验 ★ 阻塞性

**Files:**
- Create: `tests/test_demo_queries.py`

**Interfaces:**
- Consumes: 全部前置任务

> **这是全项目最关键的一个测试文件。** 演示库触发失败 = 视频录不出来。
> 设计文档 §13 风险表第一条即此风险。

- [ ] **Step 1: 写测试**

创建 `tests/test_demo_queries.py`，对**每一条预设查询**断言其触发的层级与事件：

```python
"""演示数据校验——每个预设查询必须按预期触发。

任何一个失败都是阻塞性缺陷：录视频时会「什么都没发生」。
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app

STAFF = {"type": "staff", "subject_id": None}
PATIENT = {"type": "patient", "subject_id": "P001"}

# 预期：question_id -> (是否放行, 最少事件数, 期望的降级等级集合)
EXPECTED = [
    ("doctor_dept_visits",     STAFF,   True,  0, {"L0"}),
    ("doctor_diabetes_cost",   STAFF,   True,  1, {"L0", "L1", "L2"}),   # 跨域
    ("doctor_diagnosis_stats", STAFF,   True,  1, {"L0", "L1", "L2"}),   # 派生
    ("doctor_export_roster",   STAFF,   True,  1, {"L2", "L3"}),         # blocked
    ("patient_my_lab",         PATIENT, True,  0, {"L0"}),
    ("patient_my_medication",  PATIENT, True,  0, {"L0"}),
    ("patient_my_imaging",     PATIENT, True,  0, {"L0"}),
    ("patient_doctors",        PATIENT, True,  0, {"L0"}),
    ("patient_others_count",   PATIENT, False, 0, {"L3"}),               # 层一拒绝
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDGUARD_DATA_DIR", str(tmp_path))
    from backend import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "METADATA_DB", str(tmp_path / "medguard.db"))
    monkeypatch.setattr(config, "BUSINESS_DB", str(tmp_path / "regional_health.db"))
    from backend.db import init_db
    init_db(str(tmp_path / "medguard.db"))
    c = TestClient(app)
    c.post("/api/datasources/demo")
    return c


@pytest.mark.parametrize("qid,token,passed,min_events,levels", EXPECTED,
                         ids=[e[0] for e in EXPECTED])
def test_demo_query_behaves_as_expected(client, qid, token, passed,
                                        min_events, levels):
    r = client.post("/api/query", json={
        "token": token, "datasource_id": "regional_health", "question_id": qid})
    assert r.status_code == 200, f"{qid}: HTTP {r.status_code}"
    body = r.json()
    assert body["admission"]["passed"] is passed, \
        f"{qid}: 准入判定不符（期望 {passed}）"
    assert len(body["events"]) >= min_events, \
        f"{qid}: 事件数 {len(body['events'])} < 期望 {min_events}"
    assert body["degradation"]["level"] in levels, \
        f"{qid}: 降级 {body['degradation']['level']} 不在 {levels}"


def test_all_patient_own_queries_return_data(client):
    """病患查本人数据必须真的有结果——空结果说明 seed 缺锚点数据。"""
    for qid in ("patient_my_lab", "patient_my_medication", "patient_my_imaging"):
        body = client.post("/api/query", json={
            "token": PATIENT, "datasource_id": "regional_health",
            "question_id": qid}).json()
        assert body["result"] is not None, f"{qid}: 无结果集"
        assert len(body["result"]["rows"]) > 0, f"{qid}: 结果为空（seed 缺数据）"
```

- [ ] **Step 2: 运行**

Run: `python -m pytest tests/test_demo_queries.py -v`

- [ ] **Step 3: 修复所有失败**

**此步不做妥协。** 若某个查询不触发预期维度：

- 事件数不足 → 调整 `demo/queries.json` 的计划（让中间子查询暴露受控列），遵守设计文档 §7.4 三条硬约束
- 病患查本人无结果 → 补 `demo/seed.py` 的锚点数据
- **不要为了让测试通过而降低 `EXPECTED` 里的期望值**——那是在掩盖演示会失败的事实

- [ ] **Step 4: 全量回归**

Run: `python -m pytest tests/ -v`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add tests/test_demo_queries.py demo/queries.json demo/seed.py
git commit -m "test: 演示数据校验（全部预设查询按预期触发）"
```

---

### Task 13: 端到端冒烟与启动脚本

**Files:**
- Create: `scripts/run_dev.sh`、`scripts/run_dev.bat`
- Create: `README-dev.md`

- [ ] **Step 1: 写启动脚本**

`scripts/run_dev.bat`（Windows）：

```bat
@echo off
REM 医患信息数据服务云平台 — 开发环境启动
call conda activate medguard
cd /d %~dp0..
python -m uvicorn backend.main:app --reload --port 8000
```

`scripts/run_dev.sh`（Git Bash）：

```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
python -m uvicorn backend.main:app --reload --port 8000
```

- [ ] **Step 2: 手动冒烟**

```bash
# 终端 1：起后端
bash scripts/run_dev.sh

# 终端 2：验证
curl -s http://localhost:8000/api/metrics/detection
curl -s -X POST http://localhost:8000/api/datasources/demo
curl -s -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"token":{"type":"patient","subject_id":"P001"},"datasource_id":"regional_health","question_id":"patient_others_count"}'
```

Expected：最后一条返回 `"passed": false` 与 `"reason": "该查询涉及其他患者信息，无法提供。"`

- [ ] **Step 3: 导出 OpenAPI 供前端**

```bash
curl -s http://localhost:8000/openapi.json > frontend/openapi.json
```

在前端仓库中生成类型：

```bash
cd frontend && npx openapi-typescript openapi.json -o src/api/types.ts
```

- [ ] **Step 4: 写 README-dev.md**

包含：环境要求、启动命令、测试命令、目录说明、契约变更流程（指向团队规范）。

- [ ] **Step 5: 提交**

```bash
git add scripts/ README-dev.md frontend/openapi.json
git commit -m "chore: 开发启动脚本与端到端冒烟"
```

---

## 完成标准

全部 13 个任务完成后：

- [ ] `python -m pytest tests/ -v` 全绿
- [ ] `python -m pytest tests/test_demo_queries.py -v` 9 条预设查询全部按预期
- [ ] `bash scripts/run_dev.sh` 能起服务
- [ ] `curl http://localhost:8000/openapi.json` 可访问
- [ ] 病患令牌查他人数据 → 明确拒绝且无结果集
- [ ] 医护令牌跑跨域查询 → 有安全事件且被改写
- [ ] `git status` 干净

**后端完成的定义**：以上全部勾选。缺一不可。
