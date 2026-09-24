# 近三年 RAG 论文与开源实现调研：MedGuard 适配评估

> 调研日期：2026-09-23  
> 时间范围：2023-09 至 2026-09（论文按正式会议年份或首个公开版本年份标注）  
> 项目约束：中文医疗问答/导诊、小型静态知识库、现有手写 BM25、OpenCMKG 派生表、引用可追溯、低成本本地部署、医疗安全优先。

## 1. 结论先行

MedGuard 不适合直接移植一个“大而全”的 Agentic RAG 或 GraphRAG 框架。当前最合适的升级路线是：

1. **保留确定性实体/红旗规则和现有 BM25**，修复单实体短查询召回与通用词误召回；
2. **加入 BGE 稠密检索，以 RRF 融合 BM25 与向量结果**；项目实测精排收益不佳，运行链路已移除 reranker；
3. **把 `source_id` 贯穿检索、生成和前端，回答按 claim 返回引用 ID，并允许“证据不足，拒答/建议就医”**；
4. **用 MedRGB 的噪声、证据不足和反事实测试思想，加上 RAGChecker 的 claim-level 指标建立中文医疗回归集**。

最值得套用的开源代码分为两类：

- **运行时首选**：[FlagEmbedding / BGE-M3](https://github.com/FlagOpen/FlagEmbedding)（MIT；重排模型页面标注 Apache-2.0）。
- **离线评测首选**：[RAGChecker](https://github.com/amazon-science/RAGChecker)（Apache-2.0）、[MedRGB 数据集](https://huggingface.co/datasets/ngotrnghia1811/MedRGB)（CC-BY-4.0）、[MedRAG](https://github.com/gzxiong/MedRAG)（Public Domain Notice）。

不建议近期接入 Self-RAG、完整 CRAG、Adaptive-RAG 或 Microsoft GraphRAG：它们分别要求专用大模型/训练、外网纠错搜索、复杂度分类训练或昂贵的 LLM 图谱索引，和本项目的规模、离线目标及医疗安全边界不匹配。

另有一个必须先解决的数据许可问题：OpenCMKG 官方仓库写明“**仅用于学术研究，不得用于商用**”，而且仓库没有标准 LICENSE 文件；项目必须保留来源声明，比赛展示应标注研究用途，不能把其派生 JSON 当作可自由商用的数据资产。[OpenCMKG 官方说明](https://github.com/RuiqingDing/OpenCMKG)

## 2. 当前实现与论文方案的差距

当前检索实现位于 `backend/backend/knowledge/`：

- `retrieval.py`：进程内手写 Okapi BM25，固定 `min_score=1.5`、`min_matches=2`、默认 top-3；
- `tokenize.py`：jieba 加 aliases 用户词典，删除单字 token；
- `corpus.py`：只把 `smart_knowledge.yaml` 展平为约 91 个可检索文档；
- OpenCMKG 派生的 `symptom_departments.json`、`disease_facts.json` 是确定性表查询，**没有进入 BM25/向量索引**；
- 返回给前端的 `items` 是“检索候选”，并非经过模型逐条确认的“实际引用”。

这意味着当前首要瓶颈不是生成模型能力，而是：大部分数据不可达、短查询被 `min_matches=2` 拦截、候选相关性与引用真实性没有独立验证、急症安全完全不能依赖 RAG 分数。

## 3. 代表性论文与官方代码筛选

### 3.1 A 级：建议直接试验或接入

| 论文/项目 | 年份与核心方法 | 官方代码、许可证与活跃度 | 接入成本 | MedGuard 适用点 | 主要风险/结论 |
|---|---|---|---|---|---|
| **BGE M3-Embedding** | 2024。一个模型同时支持 dense、learned sparse 和 multi-vector，覆盖 100+ 语言、最长 8192 token；官方建议“混合检索 + 重排”。[论文](https://arxiv.org/abs/2402.03216) | [FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding)，代码 MIT；[bge-m3 模型](https://huggingface.co/BAAI/bge-m3)标注 MIT；仓库约 493+ commits/持续维护。 | 中。首次下载模型；13k 级语料可离线预计算向量，不需要部署独立向量数据库。 | 中文短问句、别名外表达、BM25 与语义互补；可把 YAML 与清洗后的 OpenCMKG 统一索引。 | 模型体积和 CPU 延迟高于纯 BM25；医疗领域需在本项目金标集上调阈值，不能直接相信通用相似度。
| **BGE reranker v2** | 2024。cross-encoder 对 query-document 成对评分，用于初检后的精排。与 BGE-M3 同一官方工具链。[模型卡](https://huggingface.co/BAAI/bge-reranker-v2-m3) | [FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding)，工具代码 MIT；`bge-reranker-v2-m3` 模型卡标注 Apache-2.0，官方称其多语言、易部署、快速。 | 中低。只重排 top-10/20；可做可选本地服务并缓存结果。 | 直接解决“BM25 命中几个通用词就进入上下文”的问题；适合现有小语料渐进升级。 | 仍是相关性模型，不是医学事实审核器；低分必须拒答，不能强制凑够 top-k。
| **RAGChecker** | 2024，NeurIPS Datasets & Benchmarks。把回答拆成 claims，分别诊断检索 claim recall/context precision，以及生成的 context utilization、hallucination、faithfulness 等。[论文](https://arxiv.org/abs/2408.08067) | [官方仓库](https://github.com/amazon-science/RAGChecker)，Apache-2.0；仓库约 63 commits，2024-12 发布 benchmark，并提供中文教程。 | 中，仅离线 CI/评测；需要 claim extractor/checker LLM，不放在线请求链。 | 最接近 MedGuard 当前“检索候选被当作引用”的诊断需求；可验证回答是否真的被证据支持。 | 默认流程和示例偏英文，LLM-as-judge 有成本及偏差；关键红旗场景仍需人工复核。
| **MedRGB** | 2024 首发预印本、2026 workshop。医学 RAG 的四类评测：标准问答、证据充分性、跨文档整合、噪声/反事实鲁棒性；论文指出现有模型处理噪声和错误检索内容的能力有限。[论文](https://arxiv.org/abs/2411.09213) | [官方数据集](https://huggingface.co/datasets/ngotrnghia1811/MedRGB)，CC-BY-4.0；3680 个实例，数据页持续提供 parquet 版本。 | 中，仅离线。不能直接代表中文导诊，需要翻译/重建本地病例并由医学人员复核。 | 评测设计高度适配医疗安全：尤其是“证据不足应拒答”和“检索到错误材料仍不能被带偏”。 | 原始题目主要是英文医学 QA/选择题，不可把其准确率直接当作中文导诊安全证明。

### 3.2 B 级：借鉴设计或作为评测基线，不整体移植

| 论文/项目 | 年份与核心方法 | 官方代码、许可证与活跃度 | 接入成本 | MedGuard 适用点 | 主要风险/结论 |
|---|---|---|---|---|---|
| **Benchmarking RAG for Medicine / MIRAGE（MedRAG）** | ACL Findings 2024。7663 道题、5 个医学 QA 数据集；比较 BM25、Contriever、SPECTER、MedCPT 与多语料组合，报告组合语料/检索器通常最佳并观察到 lost-in-the-middle。[论文](https://aclanthology.org/2024.findings-acl.372/) | [MedRAG](https://github.com/gzxiong/MedRAG)，Public Domain Notice；约 30 commits，2025-02 仍有 RAG-Gym 更新。 | 高。完整语料可达数千万 chunks，PyTorch、Java、git-lfs、FAISS/HNSW 与大模型依赖明显。 | 借用 retriever 对比、固定测试集、top-k 消融和预定 snippets 接口；不必导入大语料。 | 英文医学考试 QA 与中文导诊差异大；各外部 corpus 许可证必须分别核验。建议“借评测方法，不接整套”。
| **i-MedRAG** | 2024/PSB 2025。让模型多轮生成 follow-up queries，迭代检索后再回答。[论文信息及实现入口](https://github.com/gzxiong/MedRAG#medrag-with-pre-determined-snippets) | 已并入 MedRAG，同一 Public Domain Notice；2024-10 后可直接 `follow_up=True`。 | 中高，多轮 LLM 与检索增加延迟和成本。 | 可用于极少数复杂、多症状组合查询；概念上可做“追问缺失信息”。 | 不应让模型自由扩展成诊断链。导诊应优先向用户询问确定性的红旗问题，而不是多轮搜索。
| **RAG-Fusion** | 2024。LLM 生成多个查询，分别检索，再用 Reciprocal Rank Fusion 合并排名。[论文](https://arxiv.org/abs/2402.03367) | [官方实现](https://github.com/Raudaschl/rag-fusion)，MIT；截至调研时约 900+ stars，仓库仍有后续评测内容。 | 低到中。RRF 本身几十行；多查询生成增加 LLM 调用。 | **RRF 值得直接重写到项目中**，用于 BM25 与 BGE dense 两路融合；不必复制 Chroma/OpenAI 示例。 | 医疗查询的自由 LLM 改写可能发生语义漂移（部位、否定、严重度被改写）；只建议受控别名扩展，不建议默认 4～5 个自由改写。
| **Query Rewriting for RAG** | EMNLP 2023。Rewrite-Retrieve-Read；进一步用读者反馈强化学习训练小型 rewriter。[论文](https://aclanthology.org/2023.emnlp-main.322/) | [作者仓库](https://github.com/xbmxb/RAG-query-rewriting)，约 15 commits；**未找到 LICENSE，默认不可复制/再分发**。 | 提示式改写低；复现 RL 训练高。 | 借鉴“用户口语 → 检索查询”的接口，但优先用受控症状/疾病同义词表和否定保留规则。 | 无许可证；原实验是开放域 QA/网页检索。医疗改写一旦丢失“不、没有、突然”等信息会产生安全问题。
| **EasyRAG** | 2024。双路稀疏粗排、BGE reranker、生成与推理加速；论文强调无需微调和较低显存。[论文](https://arxiv.org/abs/2410.10315) | [官方仓库](https://github.com/BUAADreamer/EasyRAG)，MIT；约 600+ stars。 | 中；完整仓库依赖较多，业务结构与 MedGuard 不同。 | “廉价粗排 → BGE 精排”的架构适合照搬思想。 | 网络运维数据域而非医疗；不建议整体复制框架，只把 reranker 接口和批处理/缓存思想用于现有模块。

### 3.3 C 级：近期不接入

| 论文/项目 | 年份与核心方法 | 官方代码、许可证与活跃度 | 接入成本 | 不推荐原因 |
|---|---|---|---|---|
| **Self-RAG** | ICLR 2024。模型学习 retrieval、relevance、support、utility 等 reflection tokens，在生成过程中决定是否检索并自我批评。[论文](https://openreview.net/forum?id=hSyW5go0v8) | [官方仓库](https://github.com/AkariAsai/self-rag)，代码 MIT；初始发布 2023-10，官方 checkpoint 为 Llama2 7B/13B 系。 | 高：专用模型、vLLM/GPU、特殊 token 与解码流程。 | MedGuard 已有外部 LLM 适配层，不适合替换成专用 7B/13B 模型；“自我评分”也不能替代医疗安全规则。可借鉴按需检索/拒答思想。
| **Corrective RAG（CRAG）** | 2024。先由 retrieval evaluator 判断文档正确/错误/模糊，再做文档分解过滤；低质量时触发 web search。[论文](https://arxiv.org/abs/2401.15884) | [作者仓库](https://github.com/HuskyInSalt/CRAG)；**未找到 LICENSE，默认不可复制/再分发**；约 473 stars。 | 中高，需要 evaluator 与在线搜索。 | 无许可证；医疗场景自动搜索开放网页会扩大来源和内容安全风险。只借鉴“低置信度走拒答/人工复核”，不要启用 web fallback。
| **Adaptive-RAG** | NAACL 2024。用问题复杂度分类器在 no-retrieval、single-step、iterative retrieval 间路由。[论文](https://arxiv.org/abs/2403.14403) | [官方仓库](https://github.com/starsuzi/Adaptive-RAG)，Apache-2.0；官方实现和训练数据可用。 | 中高，需要复杂度标签、分类器、迭代检索链。 | 本项目只有约 91 个 RAG 文档且问题主要是导诊/解释；训练分类器投入大于收益。可用确定性规则做轻量路由。
| **Microsoft GraphRAG** | 2024。LLM 从非结构化文档抽取实体/关系，做社区发现与社区摘要，支持局部和全局查询。[论文](https://arxiv.org/abs/2404.16130) | [官方仓库](https://github.com/microsoft/graphrag)，MIT；约 36k stars、493 commits，但官方已明确“largely in maintenance mode”。 | 很高；官方警告索引可能昂贵，并要求 prompt tuning。 | OpenCMKG 本身已经是结构化图，没必要再用 LLM 抽图；当前需求是实体精确查询而不是全库主题综合。应直接实现可追溯的 1～2 跳图遍历。

## 4. 推荐的目标架构

```text
用户问题
  │
  ├─ ① 医疗安全前置层（不依赖 LLM/RAG）
  │     红旗症状、否定词、持续时间、严重度、特殊人群
  │     命中红旗 → 急诊/人工建议 + 固定来源，不被后续模型降级
  │
  ├─ ② 查询标准化（确定性优先）
  │     jieba + 术语词典 + alias/SameAs + 拼写归一 + 保留否定/数值
  │     可选：LLM 只输出受 schema 约束的关键词，不允许改变原意
  │
  ├─ ③ 多路召回
  │     A. 实体/alias 精确匹配
  │     B. 现有 BM25（动态阈值）
  │     C. BGE-M3 dense cosine（离线预计算）
  │     D. OpenCMKG 清洗表的确定性 1～2 跳图查询
  │
  ├─ ④ RRF 融合 → top-20
  │
  ├─ ⑤ dense 置信阈值 + 证据规则 → top-3/5
  │     低于阈值 / 来源冲突 / 证据不足 → 拒答或建议线下就医
  │
  ├─ ⑥ LLM 生成结构化结果
  │     claims[{text, citation_ids[]}], uncertainty, follow_up_questions
  │     系统消息与用户消息分离；检索内容标为不可执行数据
  │
  └─ ⑦ 后置校验
        citation_id 必须存在；每条医学事实必须有来源；
        急症标志只能保持或升级；不允许模型生成剂量/确诊结论
```

### 为什么小语料也建议 hybrid，而不是只换向量库

- 91 条 YAML 时，向量数据库没有必要，NumPy 矩阵就够；
- BM25 对药名、检验缩写、明确疾病名强，dense 对口语、同义改写强；
- OpenCMKG 派生数据约一万余实体，仍可在单机内预计算并 mmap/加载；
- 用 RRF 只融合“排名”，避免不同分数尺度难以手工对齐；
- 不增加实测收益不稳定的 reranker，避免额外模型、延迟与误排序。

## 5. 三阶段落地方案

### 阶段 1：一周内，先修安全与可评测性

不引入新模型：

1. 建 200～300 条中文导诊金标集，至少包括：单实体短问句、口语同义词、否定、错别字、症状组合、急症、证据不足、噪声、错误/冲突文档、提示注入；
2. 指标：Recall@3/5、MRR、nDCG@5、误召回率、拒答准确率、急症召回率、citation precision、claim faithfulness；
3. 将 `min_matches` 改为依查询有效词数动态设置，单医学实体允许 1，通用词必须停用/降权；
4. 统一 `source_id/source_title/source_version/reviewed_at/license` 元数据；
5. 输出真实引用 ID，而不是把所有候选都显示成“已参考”；
6. 建立确定性红旗症状层，并规定后续 LLM 不得把急症降级。

验收门槛建议：急症召回 100%；无证据时不生成医学事实；引用 ID 100% 可定位；任何模型更换不得降低这三项。

### 阶段 2：一至两周，接入混合检索

1. 引入 `FlagEmbedding`，把**已清洗且许可允许**的知识条目编码为 BGE-M3 向量；
2. 实现 BM25 top-20 + dense top-20，以 RRF 融合；
3. 以阶段 1 金标集做消融：BM25、dense、hybrid 三组对比；
4. 记录 p50/p95 延迟、内存、冷启动时间；BGE 不可用时自动降级 BM25。

只有在 Recall@5、误召回率和引用正确率有显著提升且急症门槛不下降时，才默认开启。

### 阶段 3：两至四周，医疗可靠性与图查询

1. 依 MedRGB 创建中文“充分性、噪声、整合、反事实”测试子集；
2. 依 RAGChecker 思路做离线 claim-level 诊断，关键样本再由人工/医学顾问复核；
3. 清洗 OpenCMKG：去人员名/生产商污染、异常符号、重复/循环边、来源冲突；每条派生事实保留原三元组与版本哈希；
4. 对 OpenCMKG 只做白名单关系的确定性图查询，例如“症状→候选疾病→科室”，限制 1～2 跳；
5. 若复杂问题占比确实较高，再实现轻量 Adaptive-RAG：规则路由“直接表查/单次 hybrid/追问”，不要先训练复杂度分类器。

## 6. 数据与医疗安全要求

### 6.1 数据质量门禁

每个可展示事实至少需要：

```json
{
  "source_id": "stable-id",
  "claim": "事实文本",
  "source_title": "原始来源",
  "source_url": "可定位链接或本地版本",
  "source_version": "commit/hash/date",
  "license": "明确许可证或研究限定",
  "review_status": "unreviewed|rule_checked|clinician_reviewed",
  "reviewed_at": "ISO date|null"
}
```

OpenCMKG 的关系表示“图中有关联”，不能自动改写为“常见”“首选”“推荐”“安全可用”。尤其不能仅按三元组出现顺序截取前 5 项后称为“常见症状/常用药物”。

### 6.2 生成边界

- 允许：科室导诊、一般性解释、建议携带的检查资料、明确的急诊提示；
- 高风险：疾病确诊、药物处方/剂量、停药换药、检验结果的单一结论；
- 无可靠来源或来源冲突时，输出“当前资料不足”，并提出最少必要追问；
- 任何医学事实 claim 必须带至少一个 `citation_id`；建议性话术须单独标为“AI 导诊建议”，不能伪装成数据集事实。

## 7. 最终推荐清单

| 优先级 | 选择 | 如何使用 |
|---|---|---|
| **立即采用** | FlagEmbedding / BGE-M3 + RRF | 新增 dense 支路，保留 BM25；本地预计算，先不引入向量数据库。 |
| **不采用（实测后移除）** | bge-reranker-v2-m3 | 当前数据集精排效果不佳，增加延迟且可能误排序；不进入运行链路。 |
| **立即采用（离线）** | MedRGB 评测设计 | 重建中文导诊的充分性、噪声、冲突和反事实测试。 |
| **立即采用（离线）** | RAGChecker 指标体系 | 诊断检索与生成，抽样人工复核；不要放在线链路。 |
| **借鉴，不整体集成** | MedRAG/MIRAGE、EasyRAG、RAG-Fusion | 借 retriever 消融、粗排-精排与 RRF；保留现有轻量代码结构。 |
| **概念借鉴** | Query Rewriting、Adaptive-RAG、CRAG | 采用受控同义词扩展、低置信拒答、规则路由；不复制无许可证代码，不做开放网页纠错。 |
| **暂不采用** | Self-RAG、Microsoft GraphRAG | 模型/索引过重；对当前小型、已有结构图谱的项目收益不足。 |

## 8. 证据与许可证核验说明

- 调研只引用论文原文、作者/机构官方 GitHub、官方模型卡或官方数据卡；未使用博客结论替代论文证据。
- “未找到 LICENSE”按保守规则处理：公开可读不等于获得复制、修改或再分发授权。
- GitHub stars、commit 数和维护状态是 2026-09-23 页面快照，只用于判断生态成熟度，不代表技术质量。
- 软件许可证不自动覆盖模型权重、训练数据、语料和上游数据集；正式发布前仍需分别制作第三方许可清单。
- 本报告是工程筛选，不是对医疗有效性或合规性的认证；上线前仍需医学审核、隐私影响评估和适用法规审查。

## 9. 一手来源索引

1. BGE-M3 paper: https://arxiv.org/abs/2402.03216  
2. FlagEmbedding: https://github.com/FlagOpen/FlagEmbedding  
3. BGE-M3 model card: https://huggingface.co/BAAI/bge-m3  
4. BGE reranker model card: https://huggingface.co/BAAI/bge-reranker-v2-m3  
5. RAGChecker paper/code: https://arxiv.org/abs/2408.08067 / https://github.com/amazon-science/RAGChecker  
6. MedRAG/MIRAGE paper/code: https://aclanthology.org/2024.findings-acl.372/ / https://github.com/gzxiong/MedRAG  
7. MedRGB paper/data: https://arxiv.org/abs/2411.09213 / https://huggingface.co/datasets/ngotrnghia1811/MedRGB  
8. Self-RAG paper/code: https://openreview.net/forum?id=hSyW5go0v8 / https://github.com/AkariAsai/self-rag  
9. CRAG paper/code: https://arxiv.org/abs/2401.15884 / https://github.com/HuskyInSalt/CRAG  
10. Adaptive-RAG paper/code: https://arxiv.org/abs/2403.14403 / https://github.com/starsuzi/Adaptive-RAG  
11. Microsoft GraphRAG paper/code: https://arxiv.org/abs/2404.16130 / https://github.com/microsoft/graphrag  
12. Query Rewriting paper/code: https://aclanthology.org/2023.emnlp-main.322/ / https://github.com/xbmxb/RAG-query-rewriting  
13. RAG-Fusion paper/code: https://arxiv.org/abs/2402.03367 / https://github.com/Raudaschl/rag-fusion  
14. EasyRAG paper/code: https://arxiv.org/abs/2410.10315 / https://github.com/BUAADreamer/EasyRAG  
15. OpenCMKG data source: https://github.com/RuiqingDing/OpenCMKG
