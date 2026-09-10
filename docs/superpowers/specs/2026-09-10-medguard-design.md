# 医盾 MedGuard — 设计文档

**日期**：2026-09-10
**状态**：待实现
**赛事**：AI+软件创新赛题（推动人工智能算法的工程化落地与软件产品创新）

---

## 1. 背景与定位

### 1.1 技术底座

本产品建立在论文仓库 `J:\race\AIC\NL2SQL` 之上。该仓库实现了**面向多智能体 Text-to-SQL 的零 LLM 安全审计组件**，核心能力：

- 形式化定义**信息剖面（Information Profile）**，量化分解方案的中间结果暴露
- **三维度审计**（列级 / 跨域 / 派生信息流），纯 sqlglot AST 分析
- **零 LLM 调用、零数据库访问**，单次查询开销 <20ms
- **投影级改写 + L0–L3 降级**，可修复违规消除率 S-AR = 100%

论文仓库同时产出 28 项回归测试与完整实验数据（见 §7）。

### 1.2 产品定位

> **医盾 MedGuard** —— 医疗 AI 的数据暴露审计平台。
> 在 Agent 把数据送进大模型上下文之前，看清它到底送了什么、为什么不该送、以及怎么拦。

### 1.3 用户故事

某医疗集团的 IT 主管批准了 AI 数据分析助手，唯一要求是"不能把患者数据发给 OpenAI"。上线三个月后他发现，问题不是"数据被发出去"这么简单——而是**没人知道到底发出去了什么**。助手回答"各科室接诊量"时，中间子查询悄悄 SELECT 了患者姓名和电话，这些数据已经进入大模型的上下文窗口。合规部门问起来，他一个字都答不上来。

**这个痛点现有方案解决不了**：列掩码和 RLS 都是按 (列, 角色) 静态配置的，不理解查询意图。助手是"合法"查询，只是查多了。

### 1.4 目标用户

**受监管行业（医疗、金融、HR）中部署 agentic Text-to-SQL 的企业数据安全工程师。**

他们的核心恐惧具体且可验证：**中间结果会进入 LLM 的上下文窗口，也就是被发送给了第三方 API。**

---

## 2. 赛题对应关系

| 赛题要求 | 产品体现 |
|---|---|
| **需求分析** | 用户故事 + 数据源接入流程（§5.1） |
| **架构设计** | 算法层/产品层严格分离，仅 2 个接触点（§3） |
| **开发测试** | 算法层 28 项回归测试 + 产品层 API/前端测试（§8） |
| **用户体验优化** | 三维度违规可视化 + 改写 diff + 降级徽章（§5.3） |

---

## 3. 架构

### 3.1 分层

```
┌─────────────────────────────────────────────┐
│  产品层 MedGuard（本次实现）                  │
│  前端 SPA (React) + FastAPI + SQLite         │
└──────────────────┬──────────────────────────┘
                   │ 只调用 3 个公开接口
                   ▼
┌─────────────────────────────────────────────┐
│  算法层 NL2SQL（已有，一行不改）              │
│  run_security_auditor_pipeline(plan,db_id)   │
│  load_ssa(db_id, ssa_dir)                    │
└─────────────────────────────────────────────┘
```

> `annotate_database()`（LLM 辅助标注）**不在产品调用路径上**。演示库的 SSA 由团队手写（原因见 §6.2），标注工作台保存的是人工审查结果而非 LLM 生成结果。该函数保留在算法层，作为将来"接入真实企业库"时的候选标签生成工具。

### 3.2 核心原则：算法层一行不改

产品层是**纯适配器**。两个接触点：

```python
# 1. 加载标注
ssa = load_ssa(db_id=datasource_id, ssa_dir=<数据源专属目录>)

# 2. 完整管线（主路径，内部自会构造 SecurityAuditor）
out = run_security_auditor_pipeline(
    decomposition_plan=plan,      # [{id, description, sql}, ...]
    db_id=datasource_id,
    ssa_dir=<数据源专属目录>,
)
```

> `SecurityAuditor` / `audit_single()` 是 `run_security_auditor_pipeline` 内部使用的类。产品层若需展示**单条子查询**的细粒度审计结果，可直接调用；否则走管线即可。

**复用方式**：`pip install -e J:\race\AIC\NL2SQL`（论文仓库已含 `pyproject.toml`）。

### 3.3 数据流

```
用户输入问题
    │
    ▼
预置查询库（演示模式）─→ [{id, description, sql}, ...]
    │
    ▼
run_security_auditor_pipeline ─→ 算法层
    │
    ▼
{audited_plan, audit_report{passed,violations,rewrites_applied,rewrite_log,semantic_degradation},
 degradation_level, degradation_message, audit_trace}
    │
    ▼
审计控制台三面板：① 分解方案 ② 违规高亮 ③ 改写 diff
```

---

## 4. 算法层接口契约

> 以下结构经实机验证（2026-09-10）。

### 4.1 `run_security_auditor_pipeline` 返回值

```python
{
  "audited_plan": [{"id": 0, "description": "...", "sql": "..."}],  # 改写后
  "audit_report": {
      "passed": bool,
      "violations": [...],
      "rewrites_applied": int,
      "rewrite_log": ["Rule A: Removing A11 (ECL=controlled, not needed downstream)"],
      "semantic_degradation": "L0"|"L1"|"L2"|"L3",
      "dimensions_checked": [...],
  },
  "degradation_level": "L0",
  "degradation_message": "...",
  "audit_trace": [{"agent": "security_auditor", "decision": "rewrite_and_pass",
                   "rewrites": 1, "rationale": "..."}],
}
```

**注意**：`rewrite_log` 是**字符串数组**，非结构化对象。改写 diff 面板需要产品层自行做 SQL 行级 diff 或解析这些字符串。

### 4.2 六种违规类型（需产品化文案）

| 枚举值 | 严重度 | UI 标签 |
|---|---|---|
| `column_unnecessary_exposure` | rewritable | 不必要的列暴露 |
| `column_needs_aggregation` | rewritable | 需聚合化 |
| `blocked_column_in_select` | must_degrade | 禁止列出现在 SELECT |
| `cross_domain_personal_join` | degradable | 跨域个人级 JOIN |
| `blocked_column_in_derived` | must_degrade | 派生表达式引用禁止列 |
| `controlled_column_in_derived` | rewritable | 派生表达式暴露受控列 |

### 4.3 降级等级文案

L0 通过 / L1 聚合替代 / L2 意图变更 / L3 已拒绝

---

## 5. 产品模块

### 5.1 模块① 数据源接入

- 展示数据源列表（名称、表数、列数、标注状态）
- "载入演示数据"一键创建 `regional_health` 数据源
- Schema 树展示

**需求分析体现**：新数据源接入是可复制流程，非为比赛硬编码。

### 5.2 模块② SSA 标注工作台

逐列审查界面：列名 / 标签（free·受控·禁止）/ 标注理由 / 审查状态，附跨域规则编辑区。

**产品差异化核心**：列掩码、RLS 都是"配一次就完事"，而**标注是会腐烂的资产**——新加一列即失效。本产品把标注做成一等公民的工作流。

### 5.3 模块③ 审计控制台（核心）

三面板：
1. **分解方案** —— 逐个子查询列出，违规处高亮
2. **审计结果** —— 违规卡片（类型标签 + 列名 + 解释）
3. **改写对比** —— 改写前后 SQL diff + 改写日志 + 降级徽章

**用户体验优化体现**。

### 5.4 模块④ 审计报告

历史记录列表 + 一键导出 JSON。

---

## 6. 演示库设计（医疗：区域医疗集团）

### 6.1 表结构与 SSA 标签

| 表 | 列 | 标签 |
|---|---|---|
| `patients` | patient_id / gender / city | free |
| | name / phone / birth_date | **controlled** |
| | **id_card** | **blocked** |
| `diagnoses` | diagnosis_id / patient_id / diagnosed_at | free |
| | **icd_code / diagnosis_name** | **blocked** |
| `visits` | visit_id / patient_id / doctor_id / department / visit_date | free |
| | chief_complaint | controlled |
| `insurance_claims` | claim_id / patient_id / claim_date / status | free |
| | claim_amount | controlled |
| `doctors` | doctor_id / department / title | free |
| | name / salary | controlled |

**数据规模**：200–500 行虚构数据。用 `患者001`、`TEST-000001` 等一眼假的命名。

### 6.2 跨域规则（必须手写）

论文仓库 31 个库的跨域规则**实际生效数为 0**（经 `load_ssa()` 加载验证 + 穷举表对×列名触发验证）。文件层面分两种情况：

| | 数量 | 状态 |
|---|---|---|
| Spider 库 | 20 | 有 `cross_domain_rules` 键，值为 `[]` |
| BIRD 库 | 11 | **无该键** |

**根因定性（经实测）**：这**既不是数据集的问题，也不是代码 bug，而是标注工作流缺口**——代码路径完备、数据支持、但没有任何工具或流程步骤去填这个字段。

三条证据：

1. **代码是好的**（实测）：向 `thrombosis_prediction.yaml` 写入一条规则后，审计器在"双侧非聚合 JOIN + 个人属性列"时正确触发 1 条 `cross_domain_personal_join`，在"仅单侧 / 已聚合 / 非个人列"三个负样本上均 0 误报。规则一进 YAML 即生效。
2. **数据是支持的**：`thrombosis_prediction` 存在真实外键 `Examination.ID → Patient.ID`，两侧都持有 controlled 列（`Patient.Diagnosis`；`Examination` 11 列）——正是跨域规则要防的场景。BIRD 中类似结构不止一个库。
3. **从没人生成过它**：`src/ssa/annotator.py` 的提示词只要求 `column_labels`，全文无 `cross_domain` 字样；那 20 个空数组是 `config/ssa/update_spider_ssa.py` 用 `yaml.dump()` 重写文件时序列化空 list 带出的。

人工审查记录则直接证实了"留白"这一动作：

| 审查记录 | 跨域小节 | 内容 |
|---|---|---|
| BIRD（11 份） | **有** `## 跨域规则` | 统一为 **"（尚未定义）"** |
| Spider（20 份） | **没有该小节** | 结尾 "Generation Rules Used" 本身也是空的 |

即：BIRD 的审查者**看到了这个格子并主动留空**，Spider 的记录生成器**连格子都没建**。

**为什么留白是合理的**：列级标签可从列名推断（`id_card` 显然是 blocked），但跨域规则要求回答"这个库里哪些表分属不同安全域"——这是**业务判断，不是数据判断**。学术基准库没有业务上下文，编造规则反而会污染 S-VR（跨域是三维度之一）。论文仓库选择留白而非虚构，这个做法本身是对的。

**这正是产品的价值点**：真实企业部署时，有人能回答这个问题——医疗集团的信息安全负责人知道"患者身份"和"理赔记录"是两个域。§6.4 的 Q2 不是"造数据凑演示"，而是补齐基准库无法提供、真实部署必然存在的那一层标注。SSA 工作台的跨域规则编辑区就是让这个回答有地方落地。

演示库必须手写：

```yaml
cross_domain_rules:
  - table_pair: [patients, insurance_claims]
    join_key: patient_id
    forbid_personal_level: true
    allow_aggregate_level: true
    reason: 患者身份与理赔记录属不同安全域，个人级关联可推断就医史
```

### 6.3 ⚠️ 演示库构造的硬性约束

经源码验证，以下三条决定演示能否触发：

1. **最后一个子查询的 controlled 列永不违规**
   `_audit_column_level` 中 `if is_final: continue` —— 最终子查询是给用户看的答案，controlled 列合法。**违规必须落在中间子查询上**。这与论文叙事（中间结果进入上下文窗口）一致。
   `blocked` 列无此豁免。

2. **纯聚合表达式会被派生审计跳过**
   `_audit_derived_info_flow` 开头 `if isinstance(expression_body, (exp.Column, exp.AggFunc)): continue`。
   `MAX(d.diagnosis_name)` 不触发；`CASE WHEN d.diagnosis_name LIKE ...` 触发。

3. **`is_personal_attribute()` 是子串匹配**
   `'name' in col_lower` —— `diagnosis_name` 会被判为个人属性。列名需避开此类误伤。

### 6.4 四个预设查询

| 查询 | 问题 | 触发维度 |
|---|---|---|
| Q1 | 统计各科室接诊量 | 列级 — q2 多选 name/phone |
| Q2 | 糖尿病患者产生了多少理赔 | 跨域 — patients↔insurance_claims 个人级 JOIN |
| Q3 | 按诊断结果分类统计患者数 | 派生 — `CASE WHEN diagnosis_name LIKE '%糖尿病%'` |
| Q4 | 导出患者基本信息核对表 | blocked — id_card 出现在 SELECT |

### 6.5 演示策略

**预置查询库模式**：示例问题下拉选择，直接加载预先存好的分解方案，走真实审计管线。

- 审计部分 100% 真实，NL→SQL 那步缓存
- 理由：产品创新点全在审计侧，NL→SQL 是 MAC-SQL 的功劳
- MAC-SQL 单查询 43s，真调会导致视频录制不可控

API 预留端到端模式字段，4 周内只实现演示模式。

---

## 7. 有效性论证（引用论文仓库实验结果）

| 指标 | 结果 | 来源 |
|---|---|---|
| 安全违规率 S-VR | BIRD 0.77% / Spider 1.52% | `NL2SQL/results/offline_rerun/` |
| 违规消除率 S-AR | 100%（主实验 + QSG 泛化） | 同上 |
| 执行准确率 EX | 改写前后不变 | 同上 |
| 组件开销 | <20ms/查询（<0.05% of 43.1s） | `NL2SQL/results/` |
| 回归测试 | 28 项全通过 | `NL2SQL/tests/` |

**关键论证**：审计引擎零 LLM 调用、零数据库访问——这是产品能在生产环境低延迟部署的前提。

**QSG 泛化数据**（说明问题普遍性）：同一审计器换宿主后，S-VR 从 0.78% 升至 71.4%（BIRD fewshot）。说明泄漏率取决于**宿主 Agent 的激进程度**，而生产环境的 ReAct 循环、Agent 更像 QSG 而非保守的 MAC-SQL 流水线。

---

## 8. 测试策略

| 层 | 方式 |
|---|---|
| 算法层 | 已有 28 项回归测试（`NL2SQL/tests/`），不重复 |
| 产品 API | pytest 覆盖四个路由的请求/响应 |
| 演示数据 | 校验四个预设查询确实触发预期维度（防演示扑空） |
| 前端 | 关键交互冒烟测试 |

**演示数据校验是重点**：必须实测每个预设查询触发的违规类型与数量，避免录视频时"什么都没发生"。

---

## 9. 4 周排期

| 周 | 后端 | 前端 | 文档/演示 |
|---|---|---|---|
| W1 | 仓库搭建、`pip install -e` 通路、演示库 seed + SSA 手写 | 脚手架、路由、API 契约 | 需求分析文档、演示脚本初稿 |
| W2 | FastAPI 四路由 + labels 映射表 | 模块①② | 架构设计文档 |
| W3 | 审计 API 联调、错误处理 | 模块③④ | 测试报告、视频分镜 |
| W4 | 集成测试、演示数据校验 | 体验打磨 | 录视频、文档定稿 |

---

## 10. 目录结构

```
J:\race\AIC\
├── NL2SQL\                    ← 论文仓库（已就位，3.9G）
│   ├── pyproject.toml         ← 新增，使 pip install -e 可用
│   └── ...
│
└── MedGuard\                  ← 产品仓库
    ├── backend/
    │   ├── main.py            FastAPI 入口
    │   ├── deps.py            算法层适配 + SSA 目录解析
    │   ├── routers/           datasources / ssa / audit / reports
    │   ├── schemas.py         Pydantic 模型（前后端契约）
    │   └── labels.py          ★ 违规类型与降级等级的中文映射
    ├── frontend/              React SPA
    │   └── src/
    │       ├── pages/         Datasource / SsaWorkbench / AuditConsole / AuditReport
    │       ├── components/    PlanViewer / SqlDiff / ViolationCard / DegradationBadge
    │       └── api/
    ├── demo/
    │   ├── seed.py            建库 + 灌虚构数据
    │   ├── ssa/regional_health.yaml
    │   └── queries.json       预置查询库
    ├── data/                  运行时生成（gitignore）
    │   ├── medguard.db        产品元数据
    │   └── regional_health.db 演示业务库
    └── docs/
```

**设计要点**：产品元数据库（`medguard.db`）与演示业务库（`regional_health.db`）**分离**——前者是产品自身状态，后者是被审计对象。

---

## 11. 关键设计决策

| 决策 | 理由 |
|---|---|
| 算法层一行不改 | 产品是纯适配器；`import` 即接口契约 |
| `datasource_id` 映射到 `db_id` | 每个数据源在专属目录放 YAML，调用时传 `ssa_dir`；零侵入 |
| 演示库 SSA 手写而非 LLM 生成 | annotator 不生成 `cross_domain_rules`；blocked 标签需人工定案 |
| 演示模式与端到端模式在 API 层分开 | 视频可控；契约预留扩展 |
| 产品元数据与业务库分离 | 产品状态 vs 被审计对象，职责不同 |
| 诚实地标注演示数据 | 分两条线：科学验证线（BIRD/Spider 真实数字）与产品演示线（仿真库） |

---

## 12. 已知风险

| 风险 | 说明 | 应对 |
|---|---|---|
| **演示库触发失败** | 三维度在基准库上零触发（跨域/派生），演示库构造必须逐条实测 | W1 完成演示库后立即验证四个查询 |
| **L3 未经验证** | 全量数据仅 1 列 blocked，L3 路径未在规模上检验 | 演示库 `id_card`/`diagnosis_name` 提供真实触发场景 |
| **改写语义变化** | Rule A/B/D 不保证一般语义等价；EX 不变是实测经验结果 | 文档中如实说明，不声称"由构造保证" |
| **改写 diff 需自研** | `rewrite_log` 是字符串数组 | 产品层做 SQL 行级 diff |
| **论文仓库与产品漂移** | 两个仓库共存 | 用 `pip install -e` 而非复制，`import` 即契约 |

---

## 13. 参考文献

- 论文仓库：`J:\race\AIC\NL2SQL`
- 实验手册：`NL2SQL/EXPERIMENT_MANUAL.md`
- 数据集说明：`NL2SQL/DATA.md`
- 核心实现：`NL2SQL/src/auditor/base.py`、`NL2SQL/src/security_auditor.py`
