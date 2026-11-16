# 安全策略

医盾的核心承诺是：**在 Agent 和数据库之间，加一道无法被说服的执行点。**
我们欢迎针对这条承诺的善意安全研究。

## 报告方式

请**不要**在公开 issue 中披露未修复的漏洞。优先使用 GitHub 的
[Private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
（仓库 Security 页面 → Report a vulnerability），或联系维护者。

报告请包含：

- 受影响版本 / commit；
- 复现步骤或最小 PoC；
- 影响：能否绕过层一、层二、审计—执行绑定、报告隔离或令牌验签；
- 是否涉及真实患者数据（请勿附带真实数据，用合成数据复现）。

## 范围

重点关注的攻击面：

- **令牌**：伪造、篡改 `type/subject_id/account/exp/sig`、过期令牌、跨账号读报告；
- **层一准入**：病患令牌通过子查询、JOIN、注释、大小写/别名绕过 `patient_id` 绑定；
- **层二审计**：被禁止列、跨域个人级 JOIN、派生信息流是否可绕过；
- **审计—执行绑定**：执行器是否可能拿到未审计或哈希不一致的 SQL；
- **缓存**：跨主体复用写死主体的计划；预置缓存被运行时污染；
- **智慧医生**：提示注入能否让模型输出证据外事实、泄露本人记录或绕过证据门禁；
- **报告隔离**：按账号隔离是否在列表/详情/导出三处一致。

## 已知边界（不是漏洞）

- 本引擎**不防御小样本聚合推断**（例如罕见病计数），这是设计边界；
- 演示库为虚构数据；OpenCMKG 派生知识“未经临床复核”，不是医疗建议；
- LLM 翻译与 Jev 分类是可选外部服务；其不可用不影响审计声明；
- 合成演示数据（`*.synthetic.json`）不是真实医学知识。

## 善意研究

在遵守法律、不访问真实患者数据、不破坏服务可用性的前提下，我们支持
善意测试。请给维护者合理的修复时间，再公开细节。
