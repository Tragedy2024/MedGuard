# API 契约说明（前端对接文档）

> 权威契约文件：**`openapi.json`**（同目录，由后端自动生成）。
> 前端用它生成类型：`npx openapi-typescript openapi.json -o src/api/types.ts`
> 本文件是中文补充说明（示例、枚举、预设查询表），与 openapi.json 不一致时以 openapi.json 为准。

---

## 1. 快速开始

```bash
# 1) 安装依赖
pip install -r requirements.txt

# 2) 起后端（首次启动自动生成演示数据，无需其他操作）
python -m uvicorn backend.main:app --reload --port 8000

# 3) 验证
curl http://localhost:8000/api/health
# → {"status":"ok","algo_engine":"available","policies":["regional_health"],...}
```

- 后端端口：**8000**；前端建议 **5173**（Vite 默认），已配置 CORS 放行 `http://localhost:5173`。
- 交互式 API 文档：`http://localhost:8000/docs`。
- 前端开发时用 Vite proxy 把 `/api` 转发到 `http://localhost:8000`（避免 CORS，也无需硬编码域名）。

## 2. 端点总表

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 健康检查（算法层可用性 + 策略列表） |
| GET | `/api/datasources` | 数据源列表（空数组=未载入演示数据） |
| POST | `/api/datasources/demo` | 一键载入演示数据（幂等重建演示库） |
| GET | `/api/datasources/{id}/schema` | 库表结构（表/列/类型/主键） |
| GET | `/api/policies` | 已定案策略的数据源 id 列表 |
| GET | `/api/policies/{datasource_id}` | 策略详情（标签/理由/跨域规则/审查状态） |
| PUT | `/api/policies/{datasource_id}` | 更新策略（只合并提交的键） |
| GET | `/api/policies/{datasource_id}/validate` | 策略可加载性校验 |
| POST | `/api/query` | **核心**：提问（准入→审计→执行） |
| POST | `/api/query/direct` | 端到端模式（预留，当前 501） |
| GET | `/api/reports?limit=20` | 历史报告列表 |
| GET | `/api/reports/{id}` | 报告详情（含完整 payload） |
| GET | `/api/reports/{id}/export` | 导出 JSON（Content-Disposition: attachment） |
| GET | `/api/metrics/detection` | 检测效能指标（来自论文仓库实测） |

## 3. 核心接口：POST /api/query

### 请求

```json
{
  "token": { "type": "patient", "subject_id": "P001" },
  "datasource_id": "regional_health",
  "question_id": "patient_my_lab"
}
```

- `token.type`：`"staff"`（医护）或 `"patient"`（病患）。
- 病患令牌必须带 `subject_id`（当前演示库固定 `P001`）；医护令牌 `subject_id` 为 `null`。
- `question_id` 来自预设查询库（见 §5），非预设值返回 404；令牌与查询不匹配返回 403。

### 响应 A：正常放行（真实示例，略去部分 SQL 文本）

```json
{
  "admission": {
    "passed": true,
    "reason": null,
    "checked_tables": ["clinical_records"],
    "bound_to_subject": true
  },
  "question": "我上次的血糖是多少",
  "plan": [
    {
      "id": 0,
      "description": "最终：我的血糖结果",
      "sql_before": "SELECT test_name, result_value, unit FROM clinical_records WHERE patient_id = 'P001' AND record_type = 'lab' AND test_name = '血糖'",
      "sql_after": "SELECT test_name, result_value, unit FROM clinical_records WHERE patient_id = 'P001' AND record_type = 'lab' AND test_name = '血糖'",
      "is_final": true
    }
  ],
  "events": [],
  "rewrite": { "applied": 0, "log": [] },
  "degradation": { "level": "L0", "label": "通过", "message": "" },
  "metrics": { "elapsed_ms": 4, "llm_calls": 0, "db_access": 0 },
  "result": {
    "columns": ["test_name", "result_value", "unit"],
    "rows": [["血糖", "6.3", "mmol/L"]]
  }
}
```

### 响应 B：层一拒绝（病患查其他患者数据）

```json
{
  "admission": {
    "passed": false,
    "reason": "该查询涉及其他患者信息，无法提供。",
    "checked_tables": ["patients"],
    "bound_to_subject": false
  },
  "question": "得这个病的有多少人",
  "plan": [],
  "events": [],
  "rewrite": { "applied": 0, "log": [] },
  "degradation": { "level": "L3", "label": "已拒绝", "message": "该查询涉及其他患者信息，无法提供。" },
  "metrics": { "elapsed_ms": 2, "llm_calls": 0, "db_access": 0 },
  "result": null
}
```

> **前端约定**：层一拒绝时 `plan == []`、`result == null`；
> 固定文案直接用 `admission.reason`（后端返回什么就展示什么，前端不要改写）。

### 响应 C：层二拦截（跨域 JOIN 被 Rule C 拆分，真实示例）

```json
{
  "admission": { "passed": true, "reason": null, "checked_tables": ["clinical_records", "billing"], "bound_to_subject": false },
  "question": "糖尿病患者产生了多少费用",
  "plan": [
    { "id": 0, "description": "Aggregate query for clinical_records ...", "sql_before": "SELECT c.patient_id, c.diagnosis_name, b.amount FROM clinical_records c JOIN billing b ON ...", "sql_after": "SELECT COUNT(*) AS aggregate_count\nFROM clinical_records AS c\nWHERE c.record_type = 'diagnosis'", "is_final": false },
    { "id": 1, "description": "Aggregate query for billing ...", "sql_before": "（同上，拆分源）", "sql_after": "SELECT COUNT(*) AS aggregate_count\nFROM billing AS b", "is_final": false },
    { "id": 2, "description": "最终：按诊断汇总费用", "sql_before": "SELECT SUM(amount) FROM billing", "sql_after": "SELECT SUM(amount) FROM billing", "is_final": true }
  ],
  "events": [
    { "sub_query_id": "0", "type": "blocked_column_in_select", "type_label": "禁止列出现在 SELECT", "column": "clinical_records.diagnosis_name", "severity": "must_degrade", "severity_label": "必须阻断", "detail": "..." },
    { "sub_query_id": "0", "type": "column_needs_aggregation", "type_label": "需聚合化", "column": "billing.amount", "severity": "rewritable", "severity_label": "可拦截", "detail": "..." },
    { "sub_query_id": "0", "type": "cross_domain_personal_join", "type_label": "跨域个人级 JOIN", "column": "visit_id", "severity": "degradable", "severity_label": "需降级", "detail": "..." }
  ],
  "rewrite": { "applied": 1, "log": ["Rule A: Removing clinical_records.diagnosis_name ...", "..."] },
  "degradation": { "level": "L2", "label": "意图变更", "message": "Cannot compute exact result due to privacy policy constraints. ..." },
  "metrics": { "elapsed_ms": 18, "llm_calls": 0, "db_access": 0 },
  "result": { "columns": ["SUM(amount)"], "rows": [[123456.78]] }
}
```

> **两个关键点**：
> 1. `plan` 是**改写后**的计划：Rule C 拆分后子查询数量可能与原始不同，
>    `id` 是展示序号（0..n）；`sql_before` 是改写前 SQL（diff 左栏），
>    `sql_after` 是改写后 SQL（diff 右栏）。`is_final=true` 的最后一条是最终答案。
> 2. `events[].sub_query_id` 匹配的是**原始**子查询 id（字符串），
>    与 `plan[].id` 不一定一一对应（拆分/消除时对不上属正常）。

## 4. 枚举字典

**ECL 标签**（策略页用）：`free` 自由 / `controlled` 受控 / `blocked` 禁止

**违规类型**（events[].type → type_label）：

| type | 中文 |
|---|---|
| `column_unnecessary_exposure` | 不必要的列暴露 |
| `column_needs_aggregation` | 需聚合化 |
| `blocked_column_in_select` | 禁止列出现在 SELECT |
| `cross_domain_personal_join` | 跨域个人级 JOIN |
| `blocked_column_in_derived` | 派生表达式引用禁止列 |
| `controlled_column_in_derived` | 派生表达式暴露受控列 |

**严重度**（severity → severity_label）：`rewritable` 可拦截 / `degradable` 需降级 / `must_degrade` 必须阻断

**降级等级**（degradation.level → label）：
`L0` 通过 / `L1` 聚合替代 / `L2` 意图变更 / `L3` 已拒绝

**令牌**：`staff` 医护人员 / `patient` 病患

**配色建议（设计文档约定）**：层一 = 红色阻断；层二 = 琥珀色拦截；L0 = 绿色。

## 5. 预设查询表（前端下拉框用）

**医护令牌**（`staff`）：

| question_id | 问题 | 演示效果 |
|---|---|---|
| `doctor_dept_visits` | 统计各科室接诊量 | 干净查询 L0（对照组） |
| `doctor_diabetes_cost` | 糖尿病患者产生了多少费用 | **跨域**：3 类违规，Rule C 拆分成聚合查询，L2 |
| `doctor_diagnosis_stats` | 按诊断结果分类统计患者数 | **派生**：CASE WHEN 引用禁止列，L2 |
| `doctor_export_roster` | 导出患者基本信息核对表 | **禁止列**：id_card 被移除，L2 |

**病患令牌**（`patient`，subject_id=P001）：

| question_id | 问题 | 演示效果 |
|---|---|---|
| `patient_my_lab` | 我上次的血糖是多少 | 放行 + 真实结果 |
| `patient_my_medication` | 医生给我开的药怎么吃 | 放行 + 真实结果 |
| `patient_my_imaging` | 我的影像报告怎么说 | 放行 + 真实结果 |
| `patient_doctors` | 心内科有哪些医生 | 放行（不涉及患者表） |
| `patient_others_count` | 得这个病的有多少人 | **层一执行前拒绝**（固定文案） |

## 6. 其他接口响应要点

**GET /api/datasources**（未载入时为空数组）

```json
[{"id": "regional_health", "name": "区域医疗集团", "table_count": 5, "column_count": 46, "policy_ready": true}]
```

**POST /api/datasources/demo** → `{"id":"regional_health","created":true}`

**GET /api/datasources/regional_health/schema**

```json
{"tables": [{"name": "patients", "columns": [{"name": "patient_id", "type": "TEXT", "pk": true}, ...]}, ...]}
```

**GET /api/policies/regional_health**

```json
{
  "datasource_id": "regional_health",
  "column_labels": {"patients": {"id_card": "blocked", "name": "controlled", ...}, ...},
  "column_reasons": {"patients": {"id_card": "唯一标识符，任何场景不得出现在 SELECT", ...}, ...},
  "cross_domain_rules": [{"table_pair": ["clinical_records", "billing"], "join_key": "visit_id", "forbid_personal_level": true, "allow_aggregate_level": true, "reason": "..."}],
  "review_status": {"patients": {"patient_id": "labeled", "id_card": "labeled", ...}, ...}
}
```

> `review_status` 中 `unlabeled` 的列会被引擎**静默放行**（策略腐烂检测）——界面需高亮告警。

**PUT /api/policies/{id}** body `{"column_labels": {...}, "cross_domain_rules": [...]}` → `{"saved": true, "datasource_id": "..."}`

**GET /api/metrics/detection**

```json
{"precision": {"value": 1.0, "detail": "误报 0/66"},
 "recall": {"value": 1.0, "detail": "漏报 0/20"},
 "blocked_detection": {"value": 1.0, "detail": "检出 10/10（L2=8 / L3=2）"},
 "source": "NL2SQL/results/rq3/"}
```

**GET /api/reports** → `[{"id": 5, "question": "统计各科室接诊量", "token_type": "staff", "created_at": "2026-09-14 15:20:01", "degradation_level": "L0", "event_count": 0}, ...]`（最新在前）

**GET /api/reports/{id}** → 上述字段 + `payload`（完整 QueryResponse 快照）。

## 7. 前端接入清单

1. `npx openapi-typescript openapi.json -o src/api/types.ts` 生成类型（**禁止手工编辑生成文件**）；
2. Vite proxy：`'/api': { target: 'http://localhost:8000', changeOrigin: true }`；
3. 所有请求集中在一个 `client.ts`，页面不直接 fetch；
4. 指标条上的 `LLM 调用 0 / 数据库访问 0` 直接读 `metrics.llm_calls` / `metrics.db_access`（恒为 0，**不要在前端编造**）；
5. 契约要改：先找后端改 `backend/schemas.py` → 重新导出 openapi.json → 重新生成类型（走 `[CONTRACT]` 提交流程）。
