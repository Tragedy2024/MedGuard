## 变更说明

<!-- 一句话说明改了什么、为什么 -->

## 类型

- [ ] feat
- [ ] fix
- [ ] test
- [ ] docs
- [ ] refactor
- [ ] chore
- [ ] [CONTRACT]（需要前后端双方确认）

## 验证

- [ ] `cd backend && python -m pytest -q`
- [ ] `cd frontend && npm run build`
- [ ] `cd frontend && npm run lint`
- [ ] `cd frontend && MEDGUARD_PYTHON=<python> npm run e2e`（涉及查询/智慧医生/登录/报告时）

请贴出关键命令的真实输出或失败日志，不要只写“应该没问题”。

## 安全影响

<!-- 是否影响层一准入、层二审计、审计—执行绑定、令牌验签、报告隔离或证据门禁？ -->

- [ ] 不涉及安全边界
- [ ] 涉及，已在下方说明

## 数据与许可证

- [ ] 未新增数据/模型/素材
- [ ] 新增，已更新 `THIRD_PARTY.md` 并写清来源、版本、许可证

- [ ] 我确认没有提交 `.env`、真实患者数据、OpenCMKG 原始/派生数据或向量缓存
