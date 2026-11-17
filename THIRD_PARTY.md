# 第三方资源与许可证边界

项目根目录的 MIT 许可证仅覆盖 TeamSpirit 参赛队员依法有权开放的自主代码、
配置、测试和文档，不改变第三方资源原有权利。

## 未随项目再分发的资源

- **MAC-SQL**：项目参考其论文中的多步分解思路。官方代码仓库未提供明确的
  标准许可证，团队也未取得源码再分发授权，因此本公开版本不包含复制或修改的
  MAC-SQL 源码。
- **OpenCMKG 原始及派生医学事实数据**：来源说明限定为学术研究、不得商用。
  本项目开放团队编写的处理脚本、规则和复现说明，不把原始数据及其派生医学
  事实纳入 MIT 许可范围。
- **BGE 模型权重、在线模型服务和界面素材**：分别按模型卡、服务条款和素材
  许可证使用，不由项目 MIT 许可证重新授权。

## 随仓库提交的合成演示数据

- `backend/demo/disease_facts.synthetic.json`
- `backend/demo/symptom_departments.synthetic.json`

这两份文件由团队自行编写，采用项目 MIT 许可证，仅用于演示与自动化测试。
它们的 `_meta.source` 明确标注「合成演示数据（非临床）」，不代表 OpenCMKG、
医院审核知识库或任何临床结论。真实 OpenCMKG 派生文件存在时优先使用真实文件；
仓库不随附 OpenCMKG 原始或派生数据。

## 可选在线服务

- **TypeSafe Jev**（`api.typesafe.ai`）：可选的公开问句分类服务，按服务条款使用。
  只有精确命中 `MEDGUARD_JEV_APPROVED_QUESTIONS` 的非敏感问句才会发送；
  默认内置 3 条合成问句；未配置 Key 或调用失败时自动关闭，不影响核心功能。
  Key 只存在于本地 `.env`，不随仓库分发。

## 前期研究成果

安全审计引擎由当前参赛队员在前期论文研究中形成。本次比赛复用该前期成果，
并新增医疗 SSA 扩展、身份准入、审计与执行绑定、产品前后端、知识检索适配和
评测。前期研究成果与本次新增贡献在技术报告中分别披露。

完整的组件、模型、数据和素材清单见提交材料中的《开源及第三方资源使用清单》。


## 公开机构健康资料的分类摘要（2026-11-16）

`backend/backend/knowledge/classifications.yaml` 是团队编写的简短中文事实摘要，
用于糖尿病和癌症分类科普。并非整页转载，不包含第三方图片、标志或模型；
不将第三方原文纳入项目MIT许可，也不表示机构或医院审核了本项目。

- NIDDK，*What Is Diabetes?*：https://www.niddk.nih.gov/health-information/diabetes/overview/what-is-diabetes
- National Cancer Institute，*Cancer Types*：https://www.cancer.gov/types
- National Cancer Institute，*What Is Cancer?*：https://www.cancer.gov/about-cancer/understanding/what-is-cancer

摘要保留分类口径和来源原题名，界面提供原文链接。来源核对日期为2026-11-16，
不代表临床复核或原文更新日期。用于通用信息解释，不用于判断个人疾病分型或制定治疗方案。
新增主题需单独核对来源，不能把数据集条目数量或已有两个子型误作完整分类。


## 治疗科普与分诊补充资料（2026-11-17）

`backend/backend/knowledge/public_guidance.yaml` 为团队编写的短篇中文事实摘要，
按主题和字段选择，来源原文链接随回答展示。只提供一般信息，不提供个人处方或剂量，
不构成来源机构或医院对本项目的临床审核。原文保留各自权利，不纳入项目MIT许可。

- NIDDK: Insulin, Medicines, & Other Diabetes Treatments — https://www.niddk.nih.gov/health-information/diabetes/overview/insulin-medicines-treatments
- 国家卫生健康委: 患者可以自己调整降糖方案吗？什么情况下必须立即就医？— https://www.nhc.gov.cn/xcs/nwwd/202003/8ab7b8e56fd048aaa471768a892e3509.shtml
- NHS: Stomach ache — https://www.nhs.uk/symptoms/stomach-ache/
- MedlinePlus: Cefuroxime — https://medlineplus.gov/druginfo/meds/a601206.html

通用主知识库统一标注为项目整理、未经临床复核，删除未经证实的医院审核背书。
新增主题需核对来源、记录核对日期并补回归；不能用图谱中的科室频次代替个人分诊。


检验条件核对（2026-11-17）：参考NIDDK《Diabetes Tests & Diagnosis》
https://www.niddk.nih.gov/health-information/diabetes/overview/tests-diagnosis
区分空腹、餐后或随机测量；记录未明确条件或单位不匹配时不套用空腹分档。
该处理只约束信息展示，不构成对现有参考区间的临床验证。
