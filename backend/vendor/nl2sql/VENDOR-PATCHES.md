# 前期研究引擎的来源与本次交付边界

`backend/vendor/nl2sql/src/` 是当前参赛队员在前期论文研究中形成的安全审计
引擎。本次作品复用该前期成果，并在产品层完成医疗场景适配。

前期研究曾修改 MAC-SQL 源码。由于 MAC-SQL 官方仓库未提供明确的标准许可证，
团队没有取得其源码再分发授权。本次公开交付已移除 `src/core/` 中复制并修改的
MAC-SQL 源码，产品改用 `backend/llm_client.py` 与 `backend/llm_nl2sql.py`
中的独立接口适配和查询计划提示词。

本次参赛新增内容包括医疗 SSA 标识扩展、身份准入、审计与执行绑定、查询改写
与降级、条件路由、平台前后端、报告隔离、知识源适配和评测。前期论文成果不
作为本次比赛新增原创成果，MAC-SQL 的方法与原始代码归原作者。

## 本次保留的前期引擎适配

`src/ssa/loader.py` 与 `src/auditor/base.py` 支持 SSA YAML 顶层的
`personal_identifier_columns`。它把医疗场景中的 `patient_id`、`id_card`
等显式个人标识纳入跨域个人级 JOIN 判定，避免仅靠英文列名启发式造成静默漏报。
未声明该字段时保持原行为；配套回归测试见
`backend/tests/test_audit_personal_identifiers.py`。

本目录其余团队成员拥有权利的代码按项目根目录 MIT 许可证开放；其中引用的
论文、通用算法、第三方依赖和数据仍按各自来源及使用条件处理。
