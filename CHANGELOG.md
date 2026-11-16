# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循语义化版本。本项目首先是竞赛作品与安全演示，版本号不代表临床可用性。

## [Unreleased]

### Added

- `backend/demo/*.synthetic.json`：合成演示知识（MIT，明确标注“非临床”）。
  新克隆没有 OpenCMKG 派生数据时，疾病/症状路径仍可运行；真实
  OpenCMKG 文件存在时优先使用真实文件。
- `backend/scripts/prepare_demo_data.py`：下载/校验固定版本 OpenCMKG
  triples，生成三份派生 JSON，可选重建演示业务库。
- `backend/scripts/prewarm_rag.py`：演示前预热 BGE + BM25，构建向量缓存。
- `MEDGUARD_JEV_TIMEOUT_SEC`：Jev 分类等待上限（默认 2.5s，范围 0.1–10s）。
- GitHub Actions：`ci.yml`（后端 pytest + 前端 build/lint）与手动
  `e2e.yml`（Playwright 端到端）。
- `CONTRIBUTING.md`、`SECURITY.md`、`CODE_OF_CONDUCT.md`、issue/PR 模板。
- `docs/competition/`：技术报告、6 分钟演示脚本、PPT 大纲。

### Changed

- `dataset_kb` 在主文件**缺失**时回退到合成演示数据；主文件存在但损坏时
  不回退，避免把数据故障伪装成正常。
- Jev 默认等待从 0.8s 调整为 2.5s（实测 TypeSafe 单次 0.6–0.9s）。
- e2e 多轮上下文断言改为字段子集，不再因新增 `personal` 字段误报。
- e2e 数据来源断言接受 `OpenCMKG` 或 `合成演示数据`。

### Fixed

- 无 OpenCMKG 数据时，2 个后端测试与 4 个 e2e 断言失败的问题。
- 演示业务库与当前 `demo/seed.py` 不一致（P001 血糖 6.3 mmol/L、307 行）。
- `docs/TestPlan.txt` 的测试数量与 e2e 数量更新为当前值。
- 启动脚本的依赖检查加入 `jieba`，避免选到“有 fastapi 但无 jieba”的
  系统 Python，导致 `/api/health` 直接 500。
- OpenCMKG 合并病名（如“糖尿病和高血压”）不再吞掉比较实体；
  只有所有部分都是已知实体时才拆分。
- 停止跟踪 `backend/data/fastembed_cache`（约 93MB 第三方模型缓存），
  并加入 `.gitignore`；本地文件保留，演示不受影响。

### Security

- 未改变层一准入、层二零 LLM 审计、审计—执行绑定与报告隔离逻辑。
- 合成演示数据不冒充 OpenCMKG 或医院审核知识；来源标注在界面可见。
