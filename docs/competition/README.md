# 半决赛材料索引

本目录收拢 AIC「AI+开源」赛道的提交与答辩材料。所有内容都基于仓库当前代码
与可复现实验；不是对外宣传页，数字与边界以代码和测试输出为准。

| 文件 | 用途 |
|---|---|
| [`story-script.md`](story-script.md) | 故事版 6 分钟脚本：一个入口、两个故事、三层证据 |
| [`technical-report.md`](technical-report.md) | 技术报告：问题、架构、机制、数据、评测、合规、边界 |
| [`demo-script.md`](demo-script.md) | 6 分钟视频逐镜头脚本 + 录制前检查 + 备用问答 |
| [`ppt-outline.md`](ppt-outline.md) | 12–14 页 PPT 大纲（不含学校/导师/校徽） |

## 提交前检查清单

### 资格与时间

- [ ] 已确认报名状态与半决赛材料截止时间；
- [ ] 每队 ≤ 3 人、同校组队、≤ 2 名指导教师；
- [ ] 确认作品与前期论文/其他比赛的关系，技术报告中如实披露；
- [ ] 确认 PPT/视频不含学校名称、指导教师姓名、校徽等标识。

### 仓库与开源

- [ ] 仓库公开可访问，默认分支是最新提交；
- [ ] `LICENSE`（MIT）与 `THIRD_PARTY.md` 完整；
- [ ] 未提交 `.env`、真实患者数据、OpenCMKG 原始/派生数据、向量缓存；
- [ ] CI 绿色（`.github/workflows/ci.yml`）；
- [ ] 已创建 release / tag（建议 `v0.1.0`），release notes 指向 CHANGELOG；
- [ ] README 顶部有 CI badge 与在线 demo 链接（若可用）。

### 可复现

- [ ] 全新克隆：`pip install -r backend/requirements.txt` + `python -m pytest -q` 全绿；
- [ ] 无 OpenCMKG 数据时，合成回退路径全绿；
- [ ] 完整知识：`prepare_demo_data.py` + `prewarm_rag.py` 跑通；
- [ ] `npm ci && npm run build` 通过；
- [ ] `npm run e2e` 48 项全绿（本地 Chrome 或 `PW_CHANNEL=chromium`）；
- [ ] 断网演练一次：预置问题与缓存路径可完成演示。

### 演示

- [ ] 演示库已按当前 seed 重建（P001 血糖 6.3 mmol/L、307 行）；
- [ ] RAG 状态为 `full`（BGE + BM25）或已在视频中如实说明降级；
- [ ] Jev 白名单问句可用；不可用时可跳过，不影响主线；
- [ ] 双令牌矩阵 7/7；
- [ ] 指标条显示 LLM 调用 0 / 数据库访问 0；
- [ ] 备份录屏已准备，现场只播放不排障；
- [ ] 录屏中无密钥、无真实数据、无内部地址。

### 材料

- [ ] 技术报告 PDF（由 `technical-report.md` 导出）；
- [ ] 6 分钟以内视频（超时会影响评分）；
- [ ] PPT（无学校/导师/校徽）；
- [ ] 开源及第三方资源使用清单（来源、版本、许可证、使用方式）；
- [ ] 在线 demo 或本地演示说明；
- [ ] 团队分工与前期成果/本次新增贡献说明。

## 推荐演示主线

1. 病患血糖：事实来自主库，解读来自审核知识库；
2. 医护跨域费用：3 条违规、Rule A 改写、L2、零 LLM/零查库；
3. 病患越权：层一执行前拒绝；
4. 开源数据管线：固定版本 + 校验 + 本地生成 + 合成回退。

完整脚本见 [`demo-script.md`](demo-script.md)。
