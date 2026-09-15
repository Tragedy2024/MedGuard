# 医患信息数据服务云平台 — 后端开发指南

> 产品仓库的算法层是**只读依赖**（NL2SQL 医盾引擎），后端只经
> `backend/deps.py` 一个文件访问它。改算法层 = 红线，先讨论。

> **本交付包已内置算法层**（`vendor/nl2sql/`），拿到文件夹即可运行，
> 无需额外安装或配置算法层路径。

## 快速开始（拿到就能跑）

```bash
# 1) 安装依赖（Python 3.10+）
pip install -r requirements.txt

# 2) 起后端 —— 首次启动自动生成演示数据，无需手动操作
python -m uvicorn backend.main:app --reload --port 8000

# 3) 验证
curl http://localhost:8000/api/health
curl http://localhost:8000/api/datasources          # 应返回 regional_health
curl http://localhost:8000/api/metrics/detection    # 检测效能（论文实测数字）
```

跑测试：`python -m pytest tests/ -v`（85 项，含演示数据校验）。

## 给前端同学的对接材料

- 契约文件（机器可读，类型从这里生成）：`docs/api-contract/openapi.json`
- 中文契约说明（示例/枚举/预设查询表）：`docs/api-contract/API_CONTRACT.md`

前端用 `npx openapi-typescript openapi.json -o src/api/types.ts` 生成类型。
前端开发时：Vite `5173` + proxy 转发 `/api` → `8000`（CORS 已放行 5173）。

## 环境要求

- Python 3.10+（实测 3.13）
- 依赖：见 `requirements.txt`（fastapi / uvicorn / sqlglot / pydantic / pyyaml / pytest / httpx）

```bash
pip install -r requirements.txt
```

算法层（NL2SQL 医盾引擎）定位顺序：
1. 环境变量 `MEDGUARD_NL2SQL_SRC=<NL2SQL>/src`
2. **交付包内置** `vendor/nl2sql/src`（默认命中，无需任何配置）
3. 相对路径探测（MedGuard 与 NL2SQL 同级时自动命中）

验证算法层可用：

```bash
python -c "from backend.deps import algo_available; print(algo_available())"
```

## 启动

```bash
# Git Bash
bash scripts/run_dev.sh

# Windows CMD
scripts\run_dev.bat
# 或直接：
python -m uvicorn backend.main:app --reload --port 8000

# 首次使用前载入演示数据（或在页面点「载入演示数据」）
python -c "from demo.seed import build_database; from backend import config; import os; os.makedirs(config.DATA_DIR, exist_ok=True); build_database(config.BUSINESS_DB)"
```

访问 `http://localhost:8000/docs` 看 API 文档，`http://localhost:8000/api/health` 看健康检查。

## 测试

```bash
python -m pytest tests/ -v
```

85 项测试覆盖：层一准入（纯函数）、演示库 seed 锚点、SSA 策略完整性
（防 fail-open 缺口）、算法层适配（含最终答案 AVG 误伤还原）、
四个路由契约、**演示数据校验**（9 条预设查询全部按预期触发——阻塞性）。

## 目录结构

```
MedGuard/
├── backend/
│   ├── config.py           # ★ 全部路径/开关（环境变量可覆盖）
│   ├── admission.py        # 层一：病患令牌准入（纯函数）
│   ├── deps.py             # ★ 算法层唯一访问点（load_policy / audit_plan）
│   ├── labels.py           # 违规类型/严重度/降级等级中文映射
│   ├── db.py               # 平台元数据库（medguard.db）
│   ├── schemas.py          # ★ 前后端契约（Pydantic，字段名即契约）
│   ├── routers/            # datasources / policies / query / reports
│   └── main.py             # FastAPI 装配
├── demo/
│   ├── seed.py             # 建 5 表 + 一眼假数据（random.Random(42)）
│   ├── ssa/regional_health.yaml  # 手写策略（含跨域规则）
│   └── queries.json        # 预设查询库（医护 4 + 病患 5）
├── tests/                  # 85 项
├── scripts/                # 启动脚本
└── data/                   # 运行时生成（gitignore）
```

## 后续修改指引（留好的接口）

| 想改什么 | 改哪里 |
|---|---|
| 路径/端口/数据目录 | `backend/config.py`（或环境变量 `MEDGUARD_*`） |
| 病患表集合（接入真实数据源） | `config.PATIENT_TABLES`（当前演示库 4 张） |
| 界面文案（违规/降级/令牌） | `backend/labels.py` |
| 预设演示问题 | `demo/queries.json`（含 `{subject_id}` 占位符） |
| 演示库表结构与数据 | `demo/seed.py` + `demo/ssa/regional_health.yaml`（已互相校验，改一处必改另一处） |
| API 契约（字段名） | `backend/schemas.py` —— 这即契约变更，前端需重新生成类型 |
| 接入真实 NL→SQL | `POST /api/query/direct`（已预留，当前返回 501，见 `backend/routers/query.py`） |
| 新增数据源 | 在 `demo/ssa/` 放新策略 YAML + 扩展 `backend/routers/datasources.py` 的枚举逻辑 |

## 已知适配点（算法层不改的修正，集中在 deps.py）

1. **`audit_report` 三种形状**：有违规时只有整数计数，违规详情须另调
   `SecurityAuditor.audit_all()`——本后端已统一为稳定的 `AuditOutcome`。
2. **最终答案 AVG 误伤还原**：算法层 Rule A 对最终子查询的 controlled 列
   也会 AVG 包裹（审计器有 is_final 豁免、改写器没有），`deps.py` 在
   产品侧还原「原始 SQL 中本就非聚合出现」的 controlled 列。blocked 列
   永不还原。
3. **Rule C 拆分后的计划映射**：跨域 JOIN 拆成 `0_a/0_b` 后，前端展示以
   改写后计划为准，`sql_before` 回填源查询 SQL（见 `routers/query.py`）。
4. **层一只对病患令牌生效**：医护令牌由医院 RLS 策略管理（本项目不实现），
   一律放行交层二审计（与设计文档 §6.5 演示矩阵一致）。