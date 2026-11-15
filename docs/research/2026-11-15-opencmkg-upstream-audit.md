# OpenCMKG 上游来源与可靠性审计

审计日期：2026-11-15。只读取本地数据和公开的一手页面；没有上传本地知识库。本文审计上游来源，不替代主线程对 MedGuard 派生 JSON 的全量复现报告。

## 结论

OpenCMKG 是多源**聚合处理产物**，不是原始临床采集库。公开证据能追溯到 QASystemOnMedicalKG（QAKG）的寻医问药网页采集与规则处理，但不能把每条 OpenCMKG 三元组精确定位到来源网页、采集时间、医生或指南。它适合作为待核验的研究素材，现有证据不足以支持“医学权威知识库”“临床已验证”或“指南推荐用药”的表述。

## 证据等级

- **A：直接可复查**——发布者仓库、公开源码、固定文件内容。
- **B：发布者声明**——README 对来源和时间的自述，尚未逐条独立重建。
- **C：推断或缺口**——由结构/源码推断，或尚缺原始证据；不当作既定事实。

## 来源链与下载入口

| 层次 | 已确认事实 | 证据 |
| --- | --- | --- |
| OpenCMKG | 发布者明确说明聚合 QAKG、OwnThink、CHIP2021，获取日分别为 2021-07-08、2022-04-23、2022-04-20 | B，[README](https://github.com/RuiqingDing/OpenCMKG#readme) |
| QAKG | 发布者称其为基于垂直医药网站、以疾病为中心的知识图谱及问答教学实现 | B，[项目说明](https://github.com/liuhuanyong/QASystemOnMedicalKG#readme) |
| QAKG 网页采集 | `prepare_data/data_spider.py` 实际请求 `jib.xywy.com` 的疾病简介、病因、预防、症状、检查、治疗、饮食和药物页面；检查库另请求 `jck.xywy.com` | A，[采集源码](https://github.com/liuhuanyong/QASystemOnMedicalKG/blob/master/prepare_data/data_spider.py) |
| QAKG 结构化 | `prepare_data/build_data.py` 将 MongoDB 抓取记录转换为字段，去重、拆分药名、按词典切并发症 | A，[处理源码](https://github.com/liuhuanyong/QASystemOnMedicalKG/blob/master/prepare_data/build_data.py)、[切词源码](https://github.com/liuhuanyong/QASystemOnMedicalKG/blob/master/prepare_data/max_cut.py) |
| OwnThink | 原发布者提供通用 CSV 知识图谱及下载方式；并非专用临床指南发布渠道 | A/B，[官方仓库](https://github.com/ownthink/KnowledgeGraphData)、[官方文档](https://www.ownthink.com/docs/kg/) |
| CHIP2021 | OpenCMKG 所链 eval1 页面本次访问失败；不能据此确定它采用了何种文件或字段 | C，[原链接](http://cips-chip.org.cn/2021/eval1) |

“最原始数据”需要分层理解：目前可直接下载的更上游**结构化数据**是 QAKG 的 `data/medical.json`；网页原始内容则来自寻医问药。未找到该次采集的完整原始 HTML/MongoDB 快照，因此网页层不能按原采集时刻完整复现。

固定下载入口（提交信息来自本次主线程读取 GitHub REST；不用易变分支作为数据版本）：

- OpenCMKG：[`triples.txt`，61ff8aa8635b7b4c4c831cc56108e4116ecc3c01](https://raw.githubusercontent.com/RuiqingDing/OpenCMKG/61ff8aa8635b7b4c4c831cc56108e4116ecc3c01/triples.txt)。该文件最后修改提交时间 2023-11-28T14:11:58Z；本地文件 19,593,875 字节，SHA-256 `1d6e6242baf20a7c9c2044809a53969ffb73e3248ac9a7911f8b053b06438632`。
- QAKG：[`medical.json`，bbc60f3a9658e7e7ea21c54f2f1cbfafb7f5d840](https://raw.githubusercontent.com/liuhuanyong/QASystemOnMedicalKG/bbc60f3a9658e7e7ea21c54f2f1cbfafb7f5d840/data/medical.json)。GitHub REST 显示该文件最后修改时间 2018-10-04T06:30:21Z。本次已下载47,138,917字节、解析8,808条记录，SHA-256为`268f8e824df0ca49e6dcd98c95babf1db04fef0b89b9483854bb637586d1e49d`。
- OwnThink：原发布者在[仓库下载部分](https://github.com/ownthink/KnowledgeGraphData#readme)提供[百度网盘](https://pan.baidu.com/s/1LZjs9Dsta0yD9NH-1y0sAw)，提取码 `3hpp`。本次未下载其大体积数据，不能证明该入口的当前可下载性或其与 2022 年输入完全一致。
- CHIP 官方数据入口可继续从 [CBLUE 官方仓库](https://github.com/CBLUEbenchmark/CBLUE)所链[天池数据集](https://tianchi.aliyun.com/dataset/95414)申请。此次未申请、未下载受登录或协议限制的文件。

## 日期和清洗质量的具体限制

1. **提交日不等于医学知识更新日。** QAKG 数据文件的 2018 年提交、OpenCMKG 的 2021/2022 年获取和 2023 年文件提交，只能证明技术版本时间，不能证明医学内容在相应时间重新审定。采集代码头部日期是 `18-10-3`，也不是每条网页内容的发布日期。[采集源码](https://raw.githubusercontent.com/liuhuanyong/QASystemOnMedicalKG/master/prepare_data/data_spider.py)
2. **溯源在处理时丢失。** 爬虫记录 `url`，但结构化处理从白名单字段生成 `data_modify`，没有把页面 URL、抓取日期、作者、审稿信息带入。所以上游可解释“怎么来的”，不能为每条派生断言提供医学原文证据。[处理源码](https://raw.githubusercontent.com/liuhuanyong/QASystemOnMedicalKG/master/prepare_data/build_data.py)
3. **存在规则提取误差入口。** 症状按首字符停用表过滤；并发症用最大匹配词典切分；未找到的检查记录返回空字符串；抓取失败只打印并继续。未见这些规则附有医学准确率评估。[处理源码](https://raw.githubusercontent.com/liuhuanyong/QASystemOnMedicalKG/master/prepare_data/build_data.py)
4. **药物“推荐”不能升级解释。** QAKG 图谱脚本把 `recommand_drug` 注释为热门药品，并将图中关系名写为“好评药品”；爬虫从药物展示区读药名。这是网站信息提取，不足以证明指南等级、适应证核准或个体处方依据。[建图源码，第 36、192 行附近](https://github.com/liuhuanyong/QASystemOnMedicalKG/blob/master/build_medicalgraph.py)、[药物抓取](https://raw.githubusercontent.com/liuhuanyong/QASystemOnMedicalKG/master/prepare_data/data_spider.py)

## SameAs 与 CHIP：能证明和不能证明的事

OpenCMKG README 将 `SameAs` 称为同义实体，同时说明只公开部分跨库对齐人工标注；没有发布完整合并脚本、逐边源标签、判定准则或全体医学审核结果。[README](https://raw.githubusercontent.com/RuiqingDing/OpenCMKG/main/README.md)

实际本地固定 triples 中存在以下可直接复查的记录（文件行号从 1 开始；A 级文件事实，语义问题需医学核验）：

| 行号 | 原始三元组 | 影响 |
| --- | --- | --- |
| 343931 | `糖尿病,SameAs,妊娠期糖尿病` | 不能把疾病大类与特定类型无条件互换 |
| 344317 | `慢性咽炎,SameAs,慢性龈炎` | 咽与龈的实体存在明显差异 |
| 344319 | `慢性咽炎,SameAs,急性咽炎` | 急慢性区别被抹去 |
| 344322 | `慢性咽炎,SameAs,化痰息风` | 疾病与治疗术语混入同义边 |

发布者的[人工标注文件](https://raw.githubusercontent.com/RuiqingDing/OpenCMKG/main/manual_annotation.csv)也含正负标注，并非“列在文件里即同义”：例如 `体质性低血压/体位性低血压` 标 0，而 `盐酸舍曲林分散片/SSRIs` 标 1。后者至少提示需要重新审查严格同一性；标 1 本身不是临床可互换性证明。

CHIP2021 eval1 的参赛团队在其[一手方案仓库](https://github.com/winninghealth/chip2021)说明，该任务是在线问诊中临床发现的阴阳性分类，数据来自春雨医生，并链接 CBLUE 的 CHIP-MDCFNPC 数据申请。**这能证实任务性质，不能证实 OpenCMKG 的 SameAs 全由该任务产生。** 原发布者没有给映射代码，故准确表述应为“OpenCMKG 声明混合了 CHIP2021 数据，但具体转化与归因未核实”。

## 权威性与使用限制

- 未找到 OpenCMKG 全库由临床指南制定机构发布、医生逐边审查、系统临床验证或持续医学更新的证据。网站抓取、NLP 竞赛标注和图谱聚合只能证明来源与处理途径，不能自动证明医学正确性。
- OpenCMKG 发布者原文限制为：**“仅用于学术研究，不得用于商用。如认为侵犯了您的版权，请联系删除”**。[原始 README](https://raw.githubusercontent.com/RuiqingDing/OpenCMKG/main/README.md)
- 本次可见的 OpenCMKG 根文件列表没有标准 LICENSE；QAKG 和 OwnThink 当前根列表同样未展示明确数据许可证。公开可下载不代表已核实所有原网页和整合数据的再分发权利；本文不对版权作法律认定。[OpenCMKG](https://github.com/RuiqingDing/OpenCMKG)、[QAKG](https://github.com/liuhuanyong/QASystemOnMedicalKG)、[OwnThink](https://github.com/ownthink/KnowledgeGraphData)
- CBLUE 代码仓库展示 Apache-2.0，不能据此将 OpenCMKG、问诊数据或各输入数据一并视为 Apache-2.0。数据文件的具体下载协议仍需单独核实。[CBLUE](https://github.com/CBLUEbenchmark/CBLUE)

## 给 MedGuard 的审计处置建议

保留固定上游文件和哈希；将现有知识标为“网络聚合、未经临床验证”；禁用未经重审的 SameAs 自动扩展；把药物关系保留为历史网页提取标签。若要作为医疗事实证据，应逐项补到指南、药品说明书或其他可引用的一手医学原文，并记录发布日期、适用人群、原文片段和审核状态。CSV 解析、关系拼写和派生截断问题另由主线程全量复现，不应与上游医学正确性混为一谈。
