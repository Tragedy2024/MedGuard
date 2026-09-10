# 团队协作规范 — 医患信息数据服务云平台

**日期**：2026-09-10
**适用**：4 周开发期，2–3 人（后端负责人 / 前端 / 弹性）
**设计文档**：`docs/superpowers/specs/2026-09-10-medguard-design.md`

---

## 0. 三条不可违反的规则

> 这三条如果破了，4 周内做不完。

1. **算法层一行不改。** `J:\race\AIC\NL2SQL` 是只读依赖，用 `pip install -e` 复用。任何"顺手改一下算法"的念头，先提出来讨论。
2. **契约冻结后走变更流程。** W1 结束前冻结 API 契约，之后每次修改必须通知全员。
3. **W2 全程不联调。** 后端返回 mock，前端对着契约开发。联调集中在 W3。

---

## 1. 分工

| 角色 | 主责 | 兼责 |
|---|---|---|
| **后端负责人** | FastAPI、层一准入、算法适配、**演示库统一审定** | 架构文档 |
| **前端** | React SPA、四页面、令牌切换 | 交互设计 |
| **弹性（第 3 人）** | 演示库 seed + SSA 标注、测试、文档、视频 | 补位 |

**演示库由后端负责人统一审定**：表结构、虚构数据、逐列 SSA 标注、跨域规则，全部由他签字确认后才算完成。造数据可由弹性人员执行，但审定权在后端负责人。

---

## 2. 开发环境

### 2.1 Python（后端）

```bash
conda activate medguard          # Python 3.12.14
cd J:\race\AIC\MedGuard
```

环境已就绪：`sqlglot 30.18.0` / `fastapi 0.141.1` / `uvicorn` / `pytest` / `httpx` / `pyyaml`，
且论文仓库已 `pip install -e`。

**验证环境**：

```bash
python -c "from security_auditor import run_security_auditor_pipeline; from ssa.loader import load_ssa; print('OK')"
```

### 2.2 Node（前端）

Node 已装在 `D:\nodejs\`，**若新终端里 `node --version` 报错**，说明 PATH 没配：

- `Win+R` → `sysdm.cpl` → 高级 → 环境变量 → **系统变量** `Path` → 新建 → `D:\nodejs` → 重开终端

**验证**：`node --version` 应输出 `v24.19.0`，`npm --version` 应输出 `11.17.0`。

### 2.3 端口约定

| 服务 | 端口 | 说明 |
|---|---|---|
| 后端 | `8000` | `uvicorn backend.main:app --reload` |
| 前端 | `5173` | Vite 默认 |

前端通过 Vite proxy 把 `/api` 转发到 `8000`，**避免跨域配置**。

---

## 3. 契约冻结 ★

### 3.1 为什么这是第一优先级

前端一旦按错误契约写完，返工成本远高于提前冻结。**W1 结束前必须完成冻结。**

### 3.2 API 契约（冻结版）

Base URL：`/api`

#### 数据源

```http
GET /api/datasources
→ 200 [{"id": "regional_health", "name": "区域医疗集团", "table_count": 5,
        "column_count": 42, "policy_ready": true}]

POST /api/datasources/demo
→ 200 {"id": "regional_health", "created": true}

GET /api/datasources/{id}/schema
→ 200 {"tables": [{"name": "patients",
                   "columns": [{"name": "patient_id", "type": "INTEGER", "pk": true},
                               {"name": "id_card", "type": "TEXT", "pk": false}]}]}
```

#### 安全策略

```http
GET /api/policies/{datasource_id}
→ 200 {"datasource_id": "regional_health",
       "column_labels": {"patients": {"id_card": "blocked", "name": "controlled",
                                      "gender": "free"}},
       "column_reasons": {"patients": {"id_card": "唯一标识符，不得出现在 SELECT"}},
       "cross_domain_rules": [{"table_pair": ["clinical_records", "billing"],
                               "join_key": "visit_id",
                               "forbid_personal_level": true,
                               "allow_aggregate_level": true,
                               "reason": "临床记录与费用结算属不同安全域"}],
       "review_status": {"patients": {"id_card": "labeled", "gender": "labeled"}}}

PUT /api/policies/{datasource_id}
  body: {"column_labels": {...}, "cross_domain_rules": [...]}
→ 200 {"saved": true}
```

#### 查询（核心）

```http
POST /api/query
  body: {
    "token": {"type": "staff" | "patient", "subject_id": "P001"},
    "datasource_id": "regional_health",
    "question_id": "doctor_dept_visits"
  }

→ 200 {
  "admission": {
    "passed": true,
    "reason": null | "该查询涉及其他患者信息，无法提供。",
    "checked_tables": ["visits"],
    "bound_to_subject": false
  },
  "question": "统计各科室接诊量",
  "plan": [
    {"id": 0, "description": "中间：按科室聚合",
     "sql_before": "SELECT ...", "sql_after": "SELECT ...", "is_final": false}
  ],
  "events": [
    {"sub_query_id": "0",
     "type": "column_unnecessary_exposure",
     "type_label": "不必要的列暴露",
     "column": "patients.name",
     "severity": "rewritable",
     "severity_label": "可拦截",
     "detail": "patients.name (ECL=controlled) is SELECTed but not consumed by any"}
  ],
  "rewrite": {"applied": 1,
              "log": ["Rule A: Removing patients.name (ECL=controlled, not needed downstream)"]},
  "degradation": {"level": "L0", "label": "通过", "message": "..."},
  "metrics": {"elapsed_ms": 14, "llm_calls": 0, "db_access": 0},
  "result": {"columns": ["department", "visit_count"],
             "rows": [["心内科", 128], ["内分泌科", 96]]}
}

# 层一拒绝时（不执行查询、不返回 result）
→ 200 {
  "admission": {"passed": false,
                "reason": "该查询涉及其他患者信息，无法提供。",
                "checked_tables": ["clinical_records"],
                "bound_to_subject": false},
  "question": "得这个病的有多少人",
  "plan": [], "events": [], "rewrite": {"applied": 0, "log": []},
  "degradation": {"level": "L3", "label": "已拒绝", "message": "..."},
  "metrics": {"elapsed_ms": 3, "llm_calls": 0, "db_access": 0},
  "result": null
}
```

#### 安全事件报告

```http
GET /api/reports?limit=20
→ 200 [{"id": 12, "question": "...", "token_type": "staff",
        "created_at": "2026-09-10T14:32:01Z",
        "degradation_level": "L0", "event_count": 2}]

GET /api/reports/{id}
→ 200 {完整记录，结构同 POST /api/query 的响应，外加 id / created_at}

GET /api/reports/{id}/export
→ 200 application/json （Content-Disposition: attachment）
```

#### 检测效能

```http
GET /api/metrics/detection
→ 200 {"precision": {"value": 1.0, "detail": "误报 0/66"},
       "recall": {"value": 1.0, "detail": "漏报 0/20"},
       "blocked_detection": {"value": 1.0, "detail": "检出 10/10"},
       "source": "NL2SQL/results/rq3/"}
```

### 3.3 契约的强制同步机制

**后端每完成一个路由，立即导出 OpenAPI，前端重新生成类型。**

```bash
# 后端（在 MedGuard/backend 目录）
python -c "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('http://localhost:8000/openapi.json')), ensure_ascii=False))" > ../frontend/openapi.json

# 前端
cd frontend && npx openapi-typescript openapi.json -o src/api/types.ts
```

**效果**：契约一变，前端**编译报错**——比群里喊一声可靠。

> W1 期间后端尚无真实路由时，前端可先手写 `src/api/types.ts`，待后端 `openapi.json` 可用后替换为生成版。**不要**在生成版上手工修改。

### 3.4 契约变更流程

冻结后要改契约，必须：

1. 提出人在群里说明：改什么、为什么、影响谁
2. **后端负责人 + 前端双方确认**
3. 改 `schemas.py` → 重启后端 → 重新生成 `types.ts` → 提交
4. 提交信息标注 `[CONTRACT]` 前缀

**未经此流程的契约变更一律回滚。**

---

## 4. Git 工作流

### 4.1 仓库

| 仓库 | 路径 | 用途 |
|---|---|---|
| `NL2SQL` | `J:\race\AIC\NL2SQL` | 论文仓库 / 算法层（**只读**） |
| `MedGuard` | `J:\race\AIC\MedGuard` | 产品仓库（日常开发） |

**两个仓库都已 `git init` 并有初始提交。**

### 4.2 分支

```
master          ← 稳定，随时可演示
  └── dev       ← 日常集成分支
       ├── feat/backend-admission
       ├── feat/frontend-query-console
       └── fix/xxx
```

- 日常提交到 `feat/*` 分支
- **每周五**合并到 `dev`
- **W4 结束**合并到 `master`，打 tag `v1.0-demo`

**不强制 PR**——2-3 人的团队，口头 review + 周五集中合并更高效。但涉及**契约变更**的提交必须两人确认。

### 4.3 提交信息

格式：`<type>: <描述>`

| type | 用于 |
|---|---|
| `feat` | 新功能 |
| `fix` | 修 bug |
| `test` | 测试 |
| `docs` | 文档 |
| `refactor` | 重构（不改行为） |
| `[CONTRACT]` | **契约变更**（必须两人确认） |

示例：

```
feat: 层一准入判定（病患令牌主语绑定）
test: 补充 admission 边界用例
docs: 补充演示库 SSA 标注依据
[CONTRACT] query 响应增加 metrics 字段
```

### 4.4 不要提交的东西

`.gitignore` 已配置，确认以下**不入库**：

- `data/*.db`（演示库运行时生成）
- `node_modules/`、`dist/`
- `.env`（**含真实 API Key**）
- `__pycache__/`、`.pytest_cache/`

**提交前 `git status` 扫一眼。** 尤其别把 `.env` 带进去——论文仓库的 `.env` 是真实密钥，已排除。

---

## 5. 代码约定

### 5.1 目录边界

```
MedGuard/
├── backend/          后端全部代码，前端不碰
├── frontend/         前端全部代码，后端不碰
├── demo/             演示库（seed.py + SSA + 预设查询）
├── docs/             文档
└── data/             运行时生成，gitignore
```

**前端不直接读 `demo/` 或算法层**——一切经由 `/api`。这条保证了架构分层不被绕过。

### 5.2 后端约定

- **算法层只经由 `backend/deps.py` 访问**。其他文件不得直接 `import security_auditor` / `ssa.loader`。这样算法层的耦合点收敛在一处，将来替换或升级只改一个文件。
- 中文映射集中在 `backend/labels.py`（违规类型、严重度、降级等级）。**不在路由里硬编码中文**。
- 路由函数不写业务逻辑，只做参数校验 + 调用 + 返回。
- **策略 YAML 可以加产品层字段**（如 `column_reasons`）。`load_ssa` 只读 `column_labels` 与 `cross_domain_rules`，未知键静默忽略——**加产品层字段是零算法改动的**。但**不能改这两个键的结构**，那会真影响算法。

### 5.3 前端约定

- API 调用集中在 `src/api/`，页面不直接 `fetch`。
- 令牌状态放 `src/store/token.ts`，用 Context 传递。
- `src/api/types.ts` 是**自动生成的文件，禁止手工编辑**。

### 5.4 演示数据约定 ⚠️

**所有仿真数据必须一眼假**：

- 患者姓名：`患者001`、`患者002`
- 身份证：`TEST-000001`
- 电话：`TEST-PHONE-001`
- 医生姓名：`医生001`

**绝不使用看起来真实的姓名、身份证号、手机号**——即使是编造的。这是为了避免任何"疑似真实个人信息"的观感风险。

---

## 6. 质量门

### 6.1 完成的定义（DoD）

一个任务算完成，必须：

- [ ] 代码写了
- [ ] **测试写了并通过**
- [ ] `git status` 干净（无意外文件）
- [ ] 提交信息符合规范

**不做完这四项不算完成。** 尤其第二条——"我手动试过了"不算。

### 6.2 每周五的检查点

| 检查项 | 谁 |
|---|---|
| `pytest` 全绿 | 后端 |
| 前端能起、页面能开 | 前端 |
| **演示库的预设查询全部按预期触发** | 弹性 |
| 合并到 `dev` | 全员 |

**第三条是重中之重**——演示库触发失败 = 视频录不出来 = 全盘皆输。从 W1 起每周验一次。

### 6.3 演示数据校验

`tests/test_demo_queries.py` 必须验证**每一个预设查询**：

- 医护令牌 → 预期触发的维度与事件数
- 病患令牌 → 预期放行或被层一拒绝

**任何一条不符，视为阻塞性缺陷，当天修。**

---

## 7. 沟通

| 事项 | 方式 | 频率 |
|---|---|---|
| 日常同步 | 口头/群消息 | 随时 |
| 契约变更 | 群里明说 + 两人确认 | 变更时 |
| 周检查点 | 集中 15 分钟 | 每周五 |
| 阻塞问题 | **立即提，不要自己扛** | 随时 |

**一条硬规矩**：任何卡住超过 **半天** 的问题，立即提出来。4 周很短，闷头自己钻是最贵的错误。

---

## 8. 风险与红线

| 红线 | 后果 |
|---|---|
| **改算法层** | 两个仓库耦合、`pip install -e` 失效、排期失控 |
| **契约偷偷改** | 前端返工，W3 联调崩溃 |
| **演示库触发失败** | 视频录不出来 |
| **往演示库加列却不标注 SSA** | `SSALabels.get()` 对未标注列返回 `FREE`——医盾**静默放行**该列 |
| **提交真实密钥** | 安全事故 |
| **仿真数据看起来像真的** | 观感风险 |

**遇到这六条中的任何一条，停下来先讨论。**
