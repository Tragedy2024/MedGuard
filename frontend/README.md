# 前端 — 医患信息数据服务云平台

React 19 + Vite 8 + TypeScript 的纯展示层。**不直接读 `demo/` 或算法层，
一切经由 `/api`**——这条保证了架构分层不被绕过。

## 快速开始

```bash
# 后端先跑起来（另开终端，见 backend/README-dev.md）
conda activate medguard
cd backend && python -m uvicorn backend.main:app --reload --port 8000

# 前端
cd frontend
npm install
npm run dev          # http://localhost:5173
```

后端端口 `8000`，前端 `5173`，Vite proxy 把 `/api` 转发到后端（无需配 CORS）。

**演示账号**（密码均为 `medguard`）：`admin` / `doctor` / `patient`。
见登录页左下角，点一下自动填入。

## 命令

| 命令 | 作用 |
|---|---|
| `npm run dev` | 开发服务器 + HMR |
| `npm run build` | `tsc -b && vite build` → `dist/` |
| `npm run preview` | 预览生产构建 |
| `npm run lint` | oxlint |
| `npm run visual` | **视觉核查**：用系统 Chrome 遍历关键页面截图 + 收集控制台报错 |

`npm run visual` 输出到 `../.visual-out/`（gitignore）。录演示视频前跑一遍，
能挡住「样式塌了 / 页面白屏 / 接口报错 / 越权能进」这类翻车。
它用系统已装的 Chrome（`channel: 'chrome'`），不下载 Playwright 自带浏览器。

## 目录结构

```
frontend/
├── PRODUCT.md              # 产品真相（设计决策的依据，技能也读它）
├── openapi.json            # 后端导出的契约（生成 types.ts 的输入）
├── public/
│   ├── favicon.svg         # 品牌标记
│   ├── fonts/              # 自托管字体（4 个 woff2，见下）
│   └── photos/             # 登录页背景照片
├── scripts/visual-check.mjs
└── src/
    ├── design.css          # ★ 设计系统（全部样式都在这里）
    ├── fonts.css           # @font-face
    ├── api/
    │   ├── types.ts        # ⚠️ openapi-typescript 生成物，禁止手工编辑
    │   ├── models.ts       # ★ 业务代码的唯一类型入口
    │   ├── client.ts       # ★ 唯一发 HTTP 请求的地方
    │   └── {datasources,policies,query,reports}.ts
    ├── store/              # auth（会话）/ aliases（业务别名）/ presets
    ├── components/         # EclTag、DegradationBadge、SqlDiff、PlanPanel…
    ├── lib/time.ts         # UTC → 北京时间
    └── pages/              # 登录页 + 各角色模块页（含智慧医生、用户管理）
```

## 契约变更流程

`src/api/types.ts` 由 `openapi.json` 生成，`models.ts` 是业务代码与它之间的
适配层——**业务代码一律从 `models.ts` import，不直接碰 `types.ts`**。

契约要改（字段名、类型、取值）：

1. 改后端 `backend/schemas.py`
2. 重启后端，重新导出与生成：

   ```bash
   curl -s http://localhost:8000/openapi.json -o openapi.json
   npx openapi-typescript openapi.json -o src/api/types.ts
   npx tsc -b          # 有错就是契约对不上——这正是这一步的目的
   ```

3. 提交信息加 `[CONTRACT]` 前缀，并通知全员（团队规范 §3.4）

`models.ts` 里的联合类型（`TokenType` / `DegradationLevel` / `EclLabel`）
是**从契约派生**的，不是手写。后端一改取值，前端立刻编译失败——这是有意为之。

> ✅ 2026-09-18 已重新生成（17 条 path）。此前 `SmartDoctor*` 系列类型一度是
> **手写**的——当时 openapi.json 停在智慧医生上线前，生成的 types.ts 里没有
> 这些 schema。现在它们已改回从契约派生，"契约一变就编译报错"对全部链路生效。
>
> 重新生成时暴露过一件事，值得记住：后端用 `Field(default_factory=list)` 声明的
> 数组字段，在 OpenAPI 里是**非必填**，前端会拿到 `| undefined`。**不要在
> `models.ts` 里手工抹平**（那正是这道防线要拦的东西），在调用点兜底：
> `const rows = resp.data?.rows ?? []`。

## 设计系统

全部样式集中在 `src/design.css`（一个文件，约 1400 行）。方向是
**「导视系统 × 仪表精度」**：医院走廊的导视牌 + 阅片室的仪器面板。

**两条不可违反的规则**（依据见 `PRODUCT.md`）：

- **红 / 琥珀 / 绿是安全语义专用**：层一准入=红、层二审计=琥珀、L0=绿。
  **绝不用于装饰**——连角色标签与指标数字都刻意避开。
- **界面上只用中文业务别名**，不出现 `frequency` / `clinical_records`
  这类物理名（那会让产品退回成开发者工具，需求分析阶段已否决的形态）。
  别名来自策略 YAML 的产品层字段，前端经 `store/aliases.tsx` 取用。

### 字体（自托管）

`public/fonts/` 下 4 个 woff2（Newsreader / IBM Plex Sans / IBM Plex Mono
×2，共 202KB）。**不依赖 CDN**——演示常为断网或弱网环境，字体加载失败会
直接毁掉整个视觉。

Newsreader 与 Plex Sans 是**可变字体**，每个家族只保留一份，用
`font-weight` 区间声明。中文不走这些文件（CJK 字体动辄数 MB），由
`design.css` 里的系统字体栈承接。

重新生成：从 Google Fonts 取 CSS，只保留 `latin` 子集与 `woff2` 格式，
按家族去重后写入 `src/fonts.css`。

## 已知边界

- **无暗色模式**。设计文档定的是冷白临床风，暗色不在需求内。
- **无隐私政策 / 服务条款链接**。医疗数据产品化时应补。
