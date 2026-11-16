# 医盾 MedGuard 技术报告（半决赛版）

> 作品名称：医患信息数据服务云平台 · 安全引擎「医盾 MedGuard」
> 赛道：AIC 算法主题赛（AI+开源）· 方向一「开源赋能的 AI 应用创新」
> 版本：2026-11-16 · 分支 `semifinal-hardening`
>
> 本报告中的“通过/通过率”均指本项目自建测试或评测，不是临床验证，
> 也不构成医疗建议。第三方数据的医学正确性需独立复核。

## English Abstract

MedGuard is a hospital data-query platform for Chinese natural-language
questions. Instead of trusting a prompt-level defense, it inserts a
deterministic, **zero-LLM, zero-database-access** static audit between the
agent's multi-step query plan and execution. Layer 1 binds patient tokens to
their own records; Layer 2 audits column sensitivity, cross-domain joins and
derived information flow with sqlglot ASTs; an audit–execution hash binding
ensures the executor only runs the SQL that passed the audit. A patient-facing
assistant separates curated facts (hospital database, OpenCMKG-derived tables,
reviewed YAML) from AI explanation (RAG evidence with per-claim ID/number/drug
gates). All code is MIT-licensed; restricted OpenCMKG data is never
redistributed, and synthetic demo data keeps a fresh clone runnable. The
project targets the AIC "AI + Open Source" track, direction 1 (open-source
empowered AI application innovation).

---

## 1. 作品概述

医盾是一个面向医院场景的中文数据查询平台。医生和病患用自然语言提问，
系统把问题翻译成只读 SQL 查询计划，在**执行之前**做静态安全审计，
再把审计通过的 SQL 交给业务库执行；模型不生成数据答案。

作品同时面向两类入口：

- **查询控制台**：NL → 多步查询计划 → 层一准入 → 层二零 LLM 审计 →
  哈希绑定执行 → 安全报告；
- **智慧医生**：患者用大白话问本人检验/用药，或问疾病、症状、科室；
  本人记录走同一套审计管线，知识解释走“结构化事实 + RAG + 证据门禁”。

这两者不是两个系统。患者默认进入智慧医生；需要核对 SQL 与审计回执时，
进入同一平台下的**可信查询**（患者端导航名），它与智慧医生共用同一套
审计—执行 seam。医护端仍以**可信问答（查询控制台）**完成业务查询。

核心主张：

> 大模型要参与查询，数据就必须进它的上下文。Agent 拆出来的**中间结果**
> 从来没有被审计过——这里就是数据离开医院边界的地方。
> 医盾在 Agent 和数据库之间，加一道**无法被说服的执行点**。

---

## 2. 问题与威胁模型

### 2.1 现有防线为什么不够

| 防线 | 管得住 | 管不住 |
|---|---|---|
| 列掩码 | 最终输出列 | Agent 中间子查询结果 |
| 行级权限 | 用户能读哪些行 | 合法查到的数据被送进模型 |
| 事后过滤 | 已泄露内容的抽查 | 中间结果已经离开边界 |
| 提示词防御 | 常见注入话术 | 换宿主 Agent、提示注入、混淆代理 |

### 2.2 本作品要守的边界

1. **身份边界**：病患令牌只能读本人数据；
2. **数据边界**：查询计划不能把禁止列、跨域个人级关联或派生信息带出去；
3. **执行边界**：执行器只执行审计通过的最终 SQL；
4. **解释边界**：模型只能转述检索到的证据，不能补写医学事实；
5. **审计边界**：审计器零 LLM、零查库、输出确定，不能“被说服”。

---

## 3. 系统架构

```
用户（医护 / 病患）
   │ 自然语言 + HMAC 令牌
   ▼
FastAPI 后端
   ├─ 查询控制台 /api/query, /api/query/direct
   │    ├─ 预置问题 / 学习缓存（缓存的是计划，不是答案）
   │    ├─ 未命中 → LLM 翻译成多步计划（可选）
   │    ├─ 层一：身份准入（病患必须 patient_id = 本人）
   │    ├─ 层二：医盾审计（sqlglot AST + SSA，零 LLM / 零查库）
   │    ├─ 审计—执行哈希绑定
   │    └─ SQLite 业务库 → 结果 → 安全报告
   └─ 智慧医生 /api/smart-doctor/ask
        ├─ 确定性意图识别 + 多轮指代
        ├─ 本人记录：同一套审计—执行管线
        ├─ 疾病/症状：OpenCMKG 派生事实表
        ├─ 解释/比较：BM25 + BGE + RRF → 证据 → LLM 转述
        ├─ 证据门禁：ID / 数字 / 药品名逐字校验
        └─ 红旗规则层：确定性急诊升级
```

技术栈：FastAPI + React 19 + TypeScript + SQLite + sqlglot；
检索使用 jieba + 手写 BM25 + BGE（FlagEmbedding 或 FastEmbed ONNX）+ RRF；
LLM 使用 OpenAI 兼容接口，默认 DeepSeek；Jev 为可选公开问句分类器。

---

## 4. 关键机制

### 4.1 层一：身份准入

- 只对病患令牌执行；医护令牌的权限范围由医院 RLS 式策略管理；
- 病患查询若引用 `patients / visits / clinical_records / billing`，
  必须出现 `patient_id = <令牌绑定值>` 的字面量；
- 解析失败 fail-closed；
- 子查询、JOIN、占位符 `:param` 都不能替代字面量绑定。

### 4.2 层二：零 LLM 静态审计

- 输入：查询计划 `[{id, description, sql}, …]` + SSA 安全策略；
- 列级：`free / controlled / blocked`；
- 跨域：如 `clinical_records × billing` 的个人级 JOIN 禁止；
- 派生信息流：聚合、别名、表达式仍要追踪来源；
- 输出：违规事件、改写后的计划、Rule A/C 改写日志、L0–L3 降级；
- 审计器不访问数据库、不调用模型、输出确定。

### 4.3 审计—执行绑定

- 审计后对每条子查询做规范化 SQL 的 SHA-256；
- 执行器只收到最终一条 SQL；
- 执行前再次计算哈希，必须等于审计通过的最终子查询；
- 不一致**根本不执行**，而不是执行后再丢弃结果；
- 报告落盘 `decision_id`、计划哈希、执行哈希、执行时刻，可回看。

### 4.4 智慧医生：事实与解释分开

- **事实**：本人记录来自医院主库；疾病/症状来自 OpenCMKG 派生表；
  检验分档、用药说明来自医院审核 YAML；
- **解释**：只有知识库未覆盖或需要解释/比较时，才进入 RAG；
- **生成**：模型只把证据改写成通俗中文，输出 `claims` 必须引用证据 ID；
- **门禁**：普通代码检查证据 ID、数字、药品名；失败则回退原始证据或模板；
- **急诊**：红旗规则层独立于模型与知识图谱，命中即升级，模型无权决定紧急程度。

### 4.5 Jev：可选的公开问句分类

- 只在本地规则完全未识别、问句精确命中白名单、且红旗未命中时调用；
- 四分类：`disease / symptom / clarify / out_of_scope`；
- 不接触患者记录，不生成医学事实，只影响检索策略；
- 默认 2.5s 等待上限，失败静默回退；白名单默认只有 3 条合成问句；
- 可完全关闭，不影响核心演示与安全声明。

---

## 5. 数据与知识管线

### 5.1 演示业务库

- `backend/demo/seed.py` 以固定 `random.Random(42)` 生成 5 张表、307 行虚构数据；
- 锚点：P001 血糖 6.3 mmol/L、至少 4 位“2型糖尿病”患者、心内科医生；
- 首次启动自动建库；已存在的库不会被覆盖；
- SSA 策略 `demo/ssa/regional_health.yaml` 逐列标注敏感性、跨域规则与个人标识列。

### 5.2 OpenCMKG 知识管线

- 固定上游版本 `61ff8aa…`，`triples.txt` SHA-256 `1d6e6242…`；
- 标准 CSV 解析（保留引号内逗号），三列校验；
- 精度优先清洗：作者署名、罗马数字残渣、截断/OCR 残片、括号不配对、
  字段级黑名单；宁可漏洗，不可误杀；
- 关系映射：科室 / 症状 / 检查进入事实卡；SameAs、并发、药物、治疗
  只进复核日志，不自动使用；
- 输出：疾病事实卡、症状→科室候选、约 1.5 万条检索文档；
- 当前本地构建：8,803 张疾病卡、6,594 个症状项、15,397 条检索文档；
- **许可**：仅学术研究、不得商用；仓库不分发原始/派生数据。

### 5.3 新克隆的可复现性

- 仓库提交 `*.synthetic.json` 合成演示数据（MIT，明确标注“非临床”）；
- 主文件缺失时回退到合成数据；主文件存在则优先真实数据；
- 主文件损坏时不回退，避免把数据故障伪装成正常；
- 需要完整 OpenCMKG 演示时，运行
  `python scripts/prepare_demo_data.py --download` 本地生成；
- 合成/真实两条路径都纳入测试与 e2e。

---

## 6. 评测与证据

### 6.1 自动化测试（本机实测，2026-11-16）

- 后端：**386 项通过**（32 个测试文件；无 OpenCMKG 数据时 385 通过 + 1 跳过，
  有完整数据时 386 通过）；
- 前端：`tsc -b` + `vite build` 通过；
- 端到端：**48 项断言通过**（完整数据与合成回退两条路径各跑一次）；
- 覆盖重点：层一拒绝、缓存不绕审计、审计—执行绑定、报告按账号隔离、
  令牌篡改/过期、知识库结构守卫、RAG 降级、Jev 精确白名单、红旗规则。

### 6.2 检索评测（历史报告，2026-10）

金标集当前 86 条（YAML / 口语 / 无证据 / 急症）。66 条四路对照：

| 配置 | Recall@3 |
|---|---:|
| 纯 BM25（91 条 YAML） | 0.2391 |
| YAML + BGE | 0.6304 |
| 全量混合（BGE + OpenCMKG） | 0.9130 |

无关问题拒答率四路均为 100%，误召回 0；但这是检索指标，不是医学正确率。

### 6.3 医学安全评测（历史报告，2026-10）

四维 14 例：红旗升级 / 拒答适当 / 证据忠实度 / 有害误导率。
自动模型判读是风险代理指标，未经临床复核，不作为有效性证明。

### 6.4 性能（历史报告，2026-10）

热查询 P50 约 30ms、冷启动约 25s、峰值内存约 1GB；审计层单次 <20ms。
本机 FastEmbed 预热后首次检索约 1.25s，暖态查询毫秒级；
这些数字随机器与模型缓存变化，演示前需重测。

### 6.5 本机新增实测

- Jev 分类 6/6 命中（TypeSafe 单次 0.6–0.9s，2.5s 预算）；
- 完整知识：P001 血糖 6.3 → “偏高（空腹血糖受损）”；
- 帕金森病 / 甲状腺结节 / 失眠 → 事实卡或科室候选；
- 双令牌矩阵 7/7，层一拒答文案逐字匹配，审计指标恒为 0。

---

## 7. 开源合规

| 资源 | 使用方式 | 许可证 / 边界 |
|---|---|---|
| 本项目自主代码 | 公开仓库、可复现 | MIT |
| 前期审计引擎 | 本团队前期论文成果，本次复用并披露 | 见 `THIRD_PARTY.md` |
| MAC-SQL | 只参考论文思路，不再分发源码 | 未取得再分发授权 |
| OpenCMKG | 本地生成派生知识；不分发原始/派生数据 | 仅学术研究，不得商用 |
| BGE / FastEmbed | 本地语义检索 | 按模型卡与依赖许可 |
| TypeSafe Jev | 可选问句分类 | 按服务条款；默认仅 3 条合成问句 |
| 合成演示数据 | 仓库自带，MIT | 明确标注“非临床” |

作品没有把第三方数据包装成“医院审核知识”或“临床指南”；
界面上的来源标注与数据实际来源一致。

---

## 8. 创新点与差异化

1. **审计位置前移**：从“输出过滤”前移到“计划执行前”，补上中间结果审计空白；
2. **零 LLM 审计**：审计器无提示词、不查库、输出确定，具备引用监视器属性；
3. **审计—执行绑定**：哈希绑定，证明“执行的就是审计通过的那份”；
4. **事实/解释分离**：确定的事实留给数据与规则，解释交给模型并逐条标注；
5. **证据门禁**：模型输出不是“自证清白”，而是普通代码校验 ID/数字/药名；
6. **双向降级**：BGE 不可用回退 BM25；模型/外部服务不可用回退确定性文案；
7. **可复现的许可边界**：受限数据本地生成，合成数据开箱可跑，不混充来源。

---

## 9. 局限性

- 不是医疗器械，不提供诊断或处方；
- 演示库为虚构数据；OpenCMKG 未经临床复核；
- 不防御小样本聚合推断（如罕见病计数）；
- RAG 召回率不等于医学正确率；无证据时拒答是设计目标；
- LLM 翻译/生成的失败率未做统计性量化；演示依赖缓存保确定性；
- 前端无组件级单元测试；并发、多数据源、真实 HIS 接入未充分测试；
- Jev 是可选外部服务，评委环境可能没有 Key；核心演示不依赖它。

---

## 10. 复现与演示

```bash
# 1) 后端
cd backend
pip install -r requirements.txt
python -m pytest -q

# 2) 可选：完整 OpenCMKG 知识（需遵守学术使用边界）
python scripts/prepare_demo_data.py --download --proxy http://127.0.0.1:7897
python scripts/prewarm_rag.py

# 3) 一键演示
cd ..
./start_demo.sh              # 或 start_demo.bat
# http://localhost:8000
# 账号：admin/medguard, doctor/medguard, patient/medguard
```

没有 OpenCMKG 数据时，系统自动使用合成演示知识，核心审计/双令牌/
智慧医生血糖路径仍可演示；报告与视频中会如实标注“合成演示数据”。

---

## 11. 前期成果与本次新增（必须如实披露）

| 内容 | 归属 |
|---|---|
| 静态安全审计引擎（SSA / 改写 / 降级） | 参赛队员前期论文研究 |
| 医疗 SSA 扩展、层一准入 | 本次新增 |
| 审计—执行哈希绑定、报告留痕 | 本次新增 |
| 产品前后端、登录/令牌/报告隔离 | 本次新增 |
| RAG 适配、证据门禁、意图路由、红旗规则 | 本次新增 |
| OpenCMKG 清洗/派生管线与评测 | 本次新增 |
| Jev 公开问句路由 | 本次新增（可选） |

技术报告与 PPT 中不把前期成果说成本次从零完成；也不把第三方数据
说成本团队临床审核结果。

---

## 12. 参考

- 本项目 README、`docs/TestPlan.txt`、`docs/research/` 下的检索/安全/性能报告；
- Adaptive-RAG（NAACL 2024）：按需检索思想，本项目只借鉴策略选择；
- RAG-Fusion / RRF：排名融合；
- Corrective RAG：相关性门控思想；本项目不采用开放网页扩检；
- OpenCMKG：固定版本三元组，仅学术研究使用。
