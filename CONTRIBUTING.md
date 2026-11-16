# 贡献指南

感谢关注医盾 MedGuard。这个项目同时是竞赛作品和可运行的安全演示，
所以除了“能跑”，我们还要求**可复现、可审计、不泄露真实数据**。

## 开始之前

请先读：

- `README.md`：产品定位、机制、演示动线；
- `frontend/PRODUCT.md`：产品硬约束（界面文案、令牌语义）；
- `backend/docs/api-contract/API_CONTRACT.md`：前后端契约；
- `docs/TestPlan.txt`：测试资产与已知未覆盖项。

## 本地环境

```bash
# 后端
cd backend
pip install -r requirements.txt
python -m pytest -q

# 前端
cd frontend
npm ci
npm run build
npm run lint

# 一键演示
./start_demo.sh          # macOS / Linux / Git Bash
start_demo.bat           # Windows
```

完整知识演示（疾病事实卡 / 症状导诊 / OpenCMKG 第二检索源）需要本地生成
受限数据：

```bash
cd backend
python scripts/prepare_demo_data.py --download --proxy http://127.0.0.1:7897
python scripts/prewarm_rag.py
```

**不要把以下内容提交进仓库**：

- `.env`、任何真实密钥；
- `backend/data/` 下的运行时库、向量缓存、学习缓存；
- OpenCMKG 原始 `triples.txt`、派生 `disease_facts.json` /
  `symptom_departments.json` / `rag_documents.json`；
- 任何真实患者数据或可识别信息。

仓库自带的 `*.synthetic.json` 是**合成演示数据**，可以提交；它必须保持
`_meta.source = "合成演示数据（非临床）"`，不得冒充 OpenCMKG 或医院审核资料。

## 代码约定

- 提交信息：`<type>: <描述>`，type 取 `feat` / `fix` / `test` / `docs` /
  `refactor` / `chore`；
- 契约变更必须加 `[CONTRACT]` 前缀，并同步更新 OpenAPI 与
  `frontend/src/api/types.ts`（生成物，禁止手改）；
- 新增端点必须声明 `response_model`；
- 安全相关改动必须说明：它影响层一、层二、审计—执行绑定还是报告隔离；
- 不引入“模型自证清白”的安全机制：能确定性校验的就用普通代码校验。

## 测试

提交前至少运行：

```bash
cd backend && python -m pytest -q
cd frontend && npm run build && npm run lint
```

改动查询控制台、智慧医生、登录或报告页时，再运行：

```bash
cd frontend
MEDGUARD_PYTHON=<python> npm run e2e
```

没有 OpenCMKG 数据时，`pytest` 与 e2e 会走 `*.synthetic.json` 回退；
有完整数据时，真实 OpenCMKG 派生文件优先。两条路径都应当全绿。

## 数据与许可证

- 项目自主代码采用 MIT；
- OpenCMKG 仅限学术研究、不得商用，且未明确授予派生数据再分发权；
- MAC-SQL 源码不再分发；
- BGE / FastEmbed / TypeSense Jev 等第三方组件按各自许可或服务条款使用。

提交涉及新数据源、新模型或新素材时，请在 PR 中写清来源、版本、许可证和
使用方式，并同步更新 `THIRD_PARTY.md`。

## 行为准则

- 尊重不同背景的贡献者，不做人身攻击；
- 讨论技术问题以证据为准，不用“论文分数更高”替代本项目实测；
- 保护隐私：不要在 issue/PR/截图中放真实患者数据、密钥或内部地址。
