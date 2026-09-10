# 前端实现计划 — 医患信息数据服务云平台

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 React SPA，呈现四个模块（身份与数据源 / 安全策略管理 / 查询与拦截控制台 / 安全事件报告），把"医盾"引擎的零 LLM 审计过程做成**看得见、看得懂**的界面。

**Architecture:** 纯展示层。前端不直接读 `demo/` 或算法层——一切经由 `/api`。W2 全程对着 `src/api/types.ts` 里的冻结契约与 mock 开发，W3 才联调。

**Tech Stack:** Node 24 / Vite 7 / React 19 / TypeScript / React Router / 原生 fetch（不引 axios）

**Spec:** `docs/superpowers/specs/2026-09-10-medguard-design.md`
**团队规范:** `docs/superpowers/plans/2026-09-10-team-conventions.md`

## Global Constraints

- **前端不直接读 `demo/` 或算法层**——一切经由 `/api`。
- **`src/api/types.ts` 是自动生成文件，禁止手工编辑。** W2 期间可手写占位，W3 用 `openapi-typescript` 生成后替换。
- API 调用集中在 `src/api/`，**页面不直接 `fetch`**。
- 令牌状态放 `src/store/token.ts`，用 Context 传递。
- 端口 `5173`，通过 Vite proxy 把 `/api` 转发到 `8000`（避免跨域配置）。
- 层一拒答文案是**固定产品文案**：`该查询涉及其他患者信息，无法提供。`——直接展示后端返回的 `admission.reason`，不在前端改写。
- 两层安全的视觉区分：**层一 = 红色阻断（执行前）**，**层二 = 琥珀色拦截（审计）**。这个区分贯穿全部界面。
- 算法层「零 LLM、零查库」的数字来自 `metrics` 字段，**前端不编造**。

---

## File Structure

```
frontend/
├── index.html
├── package.json
├── vite.config.ts          # /api → localhost:8000 proxy
├── tsconfig.json
├── openapi.json            # 后端导出（Task 12 生成）
└── src/
    ├── main.tsx
    ├── App.tsx             # 路由 + 布局 + TokenProvider
    ├── api/
    │   ├── types.ts        # 生成物（W2 手写占位）
    │   ├── client.ts       # fetch 封装，唯一发请求处
    │   ├── datasources.ts
    │   ├── policies.ts
    │   ├── query.ts
    │   └── reports.ts
    ├── store/
    │   └── token.tsx       # Context + useToken
    ├── components/
    │   ├── TokenSwitcher.tsx
    │   ├── DegradationBadge.tsx
    │   ├── EventCard.tsx
    │   ├── SqlDiff.tsx
    │   ├── EclTag.tsx
    │   ├── AdmissionPanel.tsx
    │   ├── PlanPanel.tsx
    │   └── ResultTable.tsx
    └── pages/
        ├── DataSourcePage.tsx    # 模块①
        ├── PolicyPage.tsx        # 模块②
        ├── ConsolePage.tsx       # 模块③（核心）
        └── ReportPage.tsx        # 模块④
```

---

## 阶段一：骨架与契约（W1）

### Task 1: Vite 脚手架 + 代理

**Files:**
- Create: `frontend/package.json`、`vite.config.ts`、`tsconfig.json`、`index.html`
- Create: `frontend/src/main.tsx`、`frontend/src/App.tsx`

**Interfaces:**
- Produces: 可 `npm run dev` 起的空应用，`/api` 已代理到 `8000`

- [ ] **Step 1: 初始化**

```bash
cd J:/race/AIC/MedGuard
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install react-router-dom
```

- [ ] **Step 2: 配置代理**

`frontend/vite.config.ts`：

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
```

- [ ] **Step 3: 验证**

```bash
# 终端 1（后端）
cd J:/race/AIC/MedGuard && bash scripts/run_dev.sh
# 终端 2（前端）
cd J:/race/AIC/MedGuard/frontend && npm run dev
```

浏览器打开 `http://localhost:5173` 应看到 Vite 默认页。
访问 `http://localhost:5173/api/metrics/detection` 应返回后端 JSON（**证明代理通了**）。

- [ ] **Step 4: 提交**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/tsconfig.json frontend/index.html frontend/src/main.tsx frontend/src/App.tsx frontend/.gitignore
git commit -m "feat: 前端脚手架 + /api 代理"
```

---

### Task 2: 手写契约类型（W2 占位）

**Files:**
- Create: `frontend/src/api/types.ts`

**Interfaces:**
- Produces: 全部类型，**字段名与团队规范 §3.2 逐字一致**
- 被后续所有 api 模块与页面消费
- **任务结束前须向全员展示一次**——前端按此开发，后端按此实现

- [ ] **Step 1: 写类型**

创建 `frontend/src/api/types.ts`：

```ts
/**
 * API 契约类型（W2 手写占位版）。
 *
 * ⚠️ W3 起本文件由 openapi-typescript 从后端 openapi.json 生成，
 *    生成后禁止手工编辑。字段名变更 = 契约变更，走团队规范 §3.4。
 */

export type TokenType = 'staff' | 'patient'

export interface Token {
  type: TokenType
  subject_id: string | null
}

export interface AdmissionInfo {
  passed: boolean
  reason: string | null
  checked_tables: string[]
  bound_to_subject: boolean
}

export interface PlanItem {
  id: number
  description: string
  sql_before: string
  sql_after: string
  is_final: boolean
}

export interface SecurityEvent {
  sub_query_id: string
  type: string
  type_label: string
  column: string
  severity: string
  severity_label: string
  detail: string
}

export interface RewriteInfo {
  applied: number
  log: string[]
}

export interface DegradationInfo {
  level: 'L0' | 'L1' | 'L2' | 'L3'
  label: string
  message: string
}

export interface MetricsInfo {
  elapsed_ms: number
  llm_calls: number
  db_access: number
}

export interface ResultSet {
  columns: string[]
  rows: unknown[][]
}

export interface QueryResponse {
  admission: AdmissionInfo
  question: string
  plan: PlanItem[]
  events: SecurityEvent[]
  rewrite: RewriteInfo
  degradation: DegradationInfo
  metrics: MetricsInfo
  result: ResultSet | null
}

export interface DatasourceInfo {
  id: string
  name: string
  table_count: number
  column_count: number
  policy_ready: boolean
}

export interface SchemaColumn {
  name: string
  type: string
  pk: boolean
}

export interface SchemaTable {
  name: string
  columns: SchemaColumn[]
}

export type EclLabel = 'free' | 'controlled' | 'blocked'

export interface CrossDomainRule {
  table_pair: string[]
  join_key: string
  forbid_personal_level: boolean
  allow_aggregate_level: boolean
  reason: string
}

export interface Policy {
  datasource_id: string
  column_labels: Record<string, Record<string, EclLabel>>
  /** 逐列标注理由。算法层不读，界面展示用。 */
  column_reasons: Record<string, Record<string, string>>
  cross_domain_rules: CrossDomainRule[]
  /**
   * 库中实际列 vs 策略标注的对照。
   * `unlabeled` 的列会被医盾**静默放行**（SSA 未标注列默认 free）。
   */
  review_status: Record<string, Record<string, 'labeled' | 'unlabeled'>>
}

export interface ReportSummary {
  id: number
  question: string
  token_type: TokenType
  created_at: string
  degradation_level: DegradationInfo['level']
  event_count: number
}

export interface DetectionMetrics {
  precision: { value: number; detail: string }
  recall: { value: number; detail: string }
  blocked_detection: { value: number; detail: string }
  source: string
}

/** 预设查询（前端下拉用；与后端 demo/queries.json 的键对应） */
export interface PresetQuery {
  id: string
  question: string
  tokenTypes: TokenType[]
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误

- [ ] **Step 3: 提交**

```bash
git add frontend/src/api/types.ts
git commit -m "feat: 手写契约类型（W2 占位）"
```

---

### Task 3: API 客户端与令牌状态

**Files:**
- Create: `frontend/src/api/client.ts`、`datasources.ts`、`policies.ts`、`query.ts`、`reports.ts`
- Create: `frontend/src/store/token.tsx`
- Create: `frontend/src/store/presets.ts`

**Interfaces:**
- Produces:
  - `apiGet<T>(path)` / `apiPost<T>(path, body)` / `apiPut<T>(path, body)`
  - `fetchDatasources()`、`createDemoDatasource()`、`fetchSchema(id)`
  - `fetchPolicy(id)`、`updatePolicy(id, body)`
  - `runQuery(req)`、`fetchReports()`、`fetchReport(id)`、`fetchDetectionMetrics()`
  - `TokenProvider`、`useToken()` → `{ token, setTokenType, setSubjectId }`
  - `PRESET_QUERIES: PresetQuery[]`

- [ ] **Step 1: 写 client.ts**

```ts
/** API 客户端——全项目唯一发 HTTP 请求的地方。 */
import type {
  DatasourceInfo, Policy, QueryResponse, ReportSummary,
  DetectionMetrics, SchemaTable, Token,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${body}`)
  }
  return res.json() as Promise<T>
}

export const apiGet = <T>(path: string) => request<T>(path)

export const apiPost = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })

export const apiPut = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) })

export type {
  DatasourceInfo, Policy, QueryResponse, ReportSummary,
  DetectionMetrics, SchemaTable, Token,
}
```

- [ ] **Step 2: 写各资源模块**

`frontend/src/api/datasources.ts`：

```ts
import { apiGet, apiPost } from './client'
import type { DatasourceInfo, SchemaTable } from './types'

export const fetchDatasources = () => apiGet<DatasourceInfo[]>('/api/datasources')

export const createDemoDatasource = () =>
  apiPost<{ id: string; created: boolean }>('/api/datasources/demo')

export const fetchSchema = (id: string) =>
  apiGet<{ tables: SchemaTable[] }>(`/api/datasources/${id}/schema`)
```

`frontend/src/api/policies.ts`：

```ts
import { apiGet, apiPut } from './client'
import type { Policy } from './types'

export const fetchPolicy = (id: string) => apiGet<Policy>(`/api/policies/${id}`)

export const updatePolicy = (
  id: string,
  body: Partial<Pick<Policy, 'column_labels' | 'cross_domain_rules'>>,
) => apiPut<{ saved: boolean }>(`/api/policies/${id}`, body)
```

`frontend/src/api/query.ts`：

```ts
import { apiPost } from './client'
import type { QueryResponse, Token } from './types'

export interface QueryRequest {
  token: Token
  datasource_id: string
  question_id: string
}

export const runQuery = (req: QueryRequest) =>
  apiPost<QueryResponse>('/api/query', req)
```

`frontend/src/api/reports.ts`：

```ts
import { apiGet } from './client'
import type { DetectionMetrics, ReportSummary } from './types'

export const fetchReports = (limit = 20) =>
  apiGet<ReportSummary[]>(`/api/reports?limit=${limit}`)

export const fetchReport = (id: number) =>
  apiGet<Record<string, unknown>>(`/api/reports/${id}`)

export const fetchDetectionMetrics = () =>
  apiGet<DetectionMetrics>('/api/metrics/detection')

/** 导出走浏览器下载，不经 fetch。 */
export const exportReportUrl = (id: number) => `/api/reports/${id}/export`
```

- [ ] **Step 3: 写令牌状态**

`frontend/src/store/token.tsx`：

```tsx
/**
 * 令牌状态。演示用：两类身份可切换。
 * 病患令牌固定绑定 P001（对应演示库中"本人"）。
 */
import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'
import type { Token, TokenType } from '../api/types'

const STAFF_SUBJECT = null
const PATIENT_SUBJECT = 'P001'

interface TokenCtx {
  token: Token
  setTokenType: (t: TokenType) => void
}

const Ctx = createContext<TokenCtx | null>(null)

export function TokenProvider({ children }: { children: ReactNode }) {
  const [tokenType, setTokenType] = useState<TokenType>('staff')

  const value = useMemo<TokenCtx>(() => ({
    token: {
      type: tokenType,
      subject_id: tokenType === 'patient' ? PATIENT_SUBJECT : STAFF_SUBJECT,
    },
    setTokenType,
  }), [tokenType])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useToken(): TokenCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useToken 必须在 TokenProvider 内使用')
  return ctx
}
```

- [ ] **Step 4: 写预设查询表**

`frontend/src/store/presets.ts`：

```ts
import type { PresetQuery } from '../api/types'

/** 与后端 demo/queries.json 的键一一对应。 */
export const PRESET_QUERIES: PresetQuery[] = [
  { id: 'doctor_dept_visits',     question: '统计各科室接诊量',       tokenTypes: ['staff'] },
  { id: 'doctor_diabetes_cost',   question: '糖尿病患者产生了多少费用', tokenTypes: ['staff'] },
  { id: 'doctor_diagnosis_stats', question: '按诊断结果分类统计患者数', tokenTypes: ['staff'] },
  { id: 'doctor_export_roster',   question: '导出患者基本信息核对表',   tokenTypes: ['staff'] },
  { id: 'patient_my_lab',         question: '我上次的血糖是多少',     tokenTypes: ['patient'] },
  { id: 'patient_my_medication',  question: '医生给我开的药怎么吃',   tokenTypes: ['patient'] },
  { id: 'patient_my_imaging',     question: '我的影像报告怎么说',     tokenTypes: ['patient'] },
  { id: 'patient_doctors',        question: '心内科有哪些医生',       tokenTypes: ['patient'] },
  { id: 'patient_others_count',   question: '得这个病的有多少人',     tokenTypes: ['patient'] },
]

export const presetsFor = (type: 'staff' | 'patient') =>
  PRESET_QUERIES.filter(q => q.tokenTypes.includes(type))
```

- [ ] **Step 5: 类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误

- [ ] **Step 6: 提交**

```bash
git add frontend/src/api/ frontend/src/store/
git commit -m "feat: API 客户端与令牌状态"
```

---

### Task 4: 布局与令牌切换

**Files:**
- Create: `frontend/src/components/TokenSwitcher.tsx`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/App.css`

**Interfaces:**
- Consumes: Task 3 `useToken`
- Produces: 四页路由外壳 + 全局令牌切换器

- [ ] **Step 1: 写 TokenSwitcher**

```tsx
/** 令牌切换器——全局顶部，随时可切。 */
import { useToken } from '../store/token'

export function TokenSwitcher() {
  const { token, setTokenType } = useToken()

  return (
    <div className="token-switcher">
      <span className="token-label">当前身份</span>
      <div className="token-buttons">
        <button
          className={token.type === 'staff' ? 'active' : ''}
          onClick={() => setTokenType('staff')}
        >
          医护人员
        </button>
        <button
          className={token.type === 'patient' ? 'active' : ''}
          onClick={() => setTokenType('patient')}
        >
          病患
        </button>
      </div>
      {token.type === 'patient' && (
        <span className="token-subject">绑定：{token.subject_id}</span>
      )}
    </div>
  )
}
```

- [ ] **Step 2: 写 App.tsx**

```tsx
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import { TokenProvider } from './store/token'
import { TokenSwitcher } from './components/TokenSwitcher'
import { DataSourcePage } from './pages/DataSourcePage'
import { PolicyPage } from './pages/PolicyPage'
import { ConsolePage } from './pages/ConsolePage'
import { ReportPage } from './pages/ReportPage'
import './App.css'

export default function App() {
  return (
    <TokenProvider>
      <BrowserRouter>
        <div className="app">
          <header className="app-header">
            <div className="brand">
              <h1>医患信息数据服务云平台</h1>
              <span className="engine-tag">安全引擎 · 医盾</span>
            </div>
            <TokenSwitcher />
          </header>

          <nav className="app-nav">
            <NavLink to="/">数据源</NavLink>
            <NavLink to="/policy">安全策略</NavLink>
            <NavLink to="/console">查询控制台</NavLink>
            <NavLink to="/reports">安全报告</NavLink>
          </nav>

          <main className="app-main">
            <Routes>
              <Route path="/" element={<DataSourcePage />} />
              <Route path="/policy" element={<PolicyPage />} />
              <Route path="/console" element={<ConsolePage />} />
              <Route path="/reports" element={<ReportPage />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </TokenProvider>
  )
}
```

- [ ] **Step 3: 写四个占位页面**

每个页面先只放标题，Task 5–10 逐个填实。创建：
`src/pages/DataSourcePage.tsx`、`PolicyPage.tsx`、`ConsolePage.tsx`、`ReportPage.tsx`，
每个导出同名组件，返回 `<div className="page"><h2>…</h2></div>`。

- [ ] **Step 4: 写 App.css**

关键：**两层安全的配色贯穿全局**。

```css
:root {
  --layer1: #c0392b;        /* 层一：准入阻断（红） */
  --layer1-bg: #fdecea;
  --layer2: #b7791f;        /* 层二：审计拦截（琥珀） */
  --layer2-bg: #fef6e7;
  --pass: #2f855a;
  --pass-bg: #eaf7ef;
  --ink: #1a202c;
  --muted: #6b7280;
  --line: #e2e8f0;
}

body { margin: 0; font-family: -apple-system, "Microsoft YaHei", sans-serif; color: var(--ink); }
.app { min-height: 100vh; background: #f7f8fa; }

.app-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 24px; background: #fff; border-bottom: 1px solid var(--line);
}
.brand { display: flex; align-items: baseline; gap: 12px; }
.brand h1 { font-size: 18px; margin: 0; }
.engine-tag {
  font-size: 12px; color: var(--layer2); background: var(--layer2-bg);
  padding: 2px 8px; border-radius: 10px;
}

.token-switcher { display: flex; align-items: center; gap: 10px; font-size: 14px; }
.token-label { color: var(--muted); }
.token-buttons { display: flex; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
.token-buttons button {
  padding: 6px 16px; border: 0; background: #fff; cursor: pointer; font-size: 14px;
}
.token-buttons button.active { background: var(--ink); color: #fff; }
.token-subject { color: var(--muted); font-size: 13px; }

.app-nav { display: flex; gap: 4px; padding: 0 24px; background: #fff; border-bottom: 1px solid var(--line); }
.app-nav a {
  padding: 12px 16px; text-decoration: none; color: var(--muted);
  font-size: 14px; border-bottom: 2px solid transparent;
}
.app-nav a.active { color: var(--ink); border-bottom-color: var(--ink); font-weight: 600; }

.app-main { padding: 24px; max-width: 1280px; margin: 0 auto; }
```

- [ ] **Step 5: 验证**

Run: `cd frontend && npm run dev`
浏览器验证：顶部能看到"医患信息数据服务云平台 / 安全引擎·医盾"，切换令牌按钮能切换并高亮，四个导航能跳转。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/App.tsx frontend/src/App.css frontend/src/components/TokenSwitcher.tsx frontend/src/pages/
git commit -m "feat: 布局、路由与令牌切换"
```

---

## 阶段二：四个模块（W2）

### Task 5: 模块① 数据源页

**Files:**
- Create: `frontend/src/pages/DataSourcePage.tsx`
- Create: `frontend/src/components/EclTag.tsx`（后续模块②③复用）

**Interfaces:**
- Consumes: Task 3 `fetchDatasources`、`createDemoDatasource`、`fetchSchema`
- Produces: `EclTag({ label }: { label: EclLabel })` 组件

- [ ] **Step 1: 写 EclTag**

```tsx
/** ECL 标签：free / 受控 / 禁止。全站统一配色。 */
import type { EclLabel } from '../api/types'

const TEXT: Record<EclLabel, string> = {
  free: '自由',
  controlled: '受控',
  blocked: '禁止',
}

export function EclTag({ label }: { label: EclLabel }) {
  return <span className={`ecl ecl-${label}`}>{TEXT[label]}</span>
}
```

在 `App.css` 追加：

```css
.ecl { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
.ecl-free { background: var(--pass-bg); color: var(--pass); }
.ecl-controlled { background: var(--layer2-bg); color: var(--layer2); }
.ecl-blocked { background: var(--layer1-bg); color: var(--layer1); }
```

- [ ] **Step 2: 写 DataSourcePage**

要点：
- 空态显示"尚未载入演示数据"+ 载入按钮
- 载入后展示数据源卡片（名称 / 表数 / 列数 / 策略状态）
- Schema 树：按表展开列，每列显示类型 + 主键标记

```tsx
import { useEffect, useState } from 'react'
import { createDemoDatasource, fetchDatasources, fetchSchema } from '../api/datasources'
import type { DatasourceInfo, SchemaTable } from '../api/types'

export function DataSourcePage() {
  const [sources, setSources] = useState<DatasourceInfo[]>([])
  const [tables, setTables] = useState<SchemaTable[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    try {
      setError(null)
      const list = await fetchDatasources()
      setSources(list)
      if (list.length > 0) {
        const { tables } = await fetchSchema(list[0].id)
        setTables(tables)
      }
    } catch (e) {
      setError(String(e))
    }
  }

  useEffect(() => { void load() }, [])

  const handleCreate = async () => {
    setLoading(true)
    try {
      await createDemoDatasource()
      await load()
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <h2>数据源</h2>
      {error && <div className="alert-error">{error}</div>}

      {sources.length === 0 ? (
        <div className="empty-state">
          <p>尚未载入演示数据。</p>
          <p className="hint">
            演示库为医院数据仿真版本，全部数据均为虚构（患者001 / TEST-000001）。
          </p>
          <button onClick={handleCreate} disabled={loading}>
            {loading ? '载入中…' : '载入演示数据'}
          </button>
        </div>
      ) : (
        <>
          {sources.map(s => (
            <div key={s.id} className="card">
              <h3>{s.name}</h3>
              <dl className="kv">
                <div><dt>标识</dt><dd>{s.id}</dd></div>
                <div><dt>表数</dt><dd>{s.table_count}</dd></div>
                <div><dt>列数</dt><dd>{s.column_count}</dd></div>
                <div>
                  <dt>策略状态</dt>
                  <dd>{s.policy_ready ? '已标注' : '未标注'}</dd>
                </div>
              </dl>
            </div>
          ))}

          <h3>库表结构</h3>
          {tables.map(t => (
            <details key={t.name} className="schema-table" open>
              <summary>{t.name}</summary>
              <ul className="schema-columns">
                {t.columns.map(c => (
                  <li key={c.name}>
                    <code>{c.name}</code>
                    <span className="col-type">{c.type}</span>
                    {c.pk && <span className="col-pk">PK</span>}
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 3: 验证**

`npm run dev`，点"载入演示数据"，应看到 5 张表（`patients` / `staff` / `visits` / `clinical_records` / `billing`）与各自列。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/pages/DataSourcePage.tsx frontend/src/components/EclTag.tsx frontend/src/App.css
git commit -m "feat: 模块① 数据源页"
```

---

### Task 6: 模块② 安全策略管理

**Files:**
- Create: `frontend/src/pages/PolicyPage.tsx`

**Interfaces:**
- Consumes: Task 3 `fetchPolicy`、`updatePolicy`；Task 5 `EclTag`
- Produces: 逐列审查界面 + 跨域规则展示

> **产品差异化核心**：列掩码、RLS 都是"配一次就完事"，而**策略是会腐烂的资产**——新加一列即失效。本页把这层意思讲出来。

- [ ] **Step 1: 写 PolicyPage**

```tsx
import { useEffect, useState } from 'react'
import { fetchPolicy, updatePolicy } from '../api/policies'
import type { EclLabel, Policy } from '../api/types'
import { EclTag } from '../components/EclTag'

const CX = 'regional_health'
const LABELS: EclLabel[] = ['free', 'controlled', 'blocked']

export function PolicyPage() {
  const [policy, setPolicy] = useState<Policy | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchPolicy(CX).then(setPolicy).catch(e => setError(String(e)))
  }, [])

  if (error) return <div className="page"><div className="alert-error">{error}</div></div>
  if (!policy) return <div className="page">加载中…</div>

  const change = (table: string, column: string, label: EclLabel) => {
    setPolicy({
      ...policy,
      column_labels: {
        ...policy.column_labels,
        [table]: { ...policy.column_labels[table], [column]: label },
      },
    })
    setDirty(true)
  }

  const save = async () => {
    setSaving(true)
    try {
      await updatePolicy(CX, { column_labels: policy.column_labels })
      setDirty(false)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const counts = LABELS.map(l => ({
    label: l,
    n: Object.values(policy.column_labels)
      .flatMap(cols => Object.values(cols)).filter(v => v === l).length,
  }))

  const unlabeled = Object.entries(policy.review_status)
    .flatMap(([t, cols]) =>
      Object.entries(cols).filter(([, s]) => s === 'unlabeled').map(([c]) => `${t}.${c}`))

  return (
    <div className="page">
      <h2>安全策略</h2>
      <p className="hint">
        策略是<strong>会腐烂的资产</strong>——新增一列若未标注，医盾会
        <strong>静默放行</strong>该列（SSA 对未标注列默认按「自由」处理）。
        因此本平台把逐列标注做成一等公民的工作流，而非一次性配置。
      </p>

      {unlabeled.length > 0 && (
        <div className="alert-error">
          发现 {unlabeled.length} 列未标注，将被静默放行：{unlabeled.join('、')}
        </div>
      )}

      <div className="policy-summary">
        {counts.map(c => (
          <span key={c.label} className="summary-item">
            <EclTag label={c.label} /> <strong>{c.n}</strong> 列
          </span>
        ))}
      </div>

      {Object.entries(policy.column_labels).map(([table, cols]) => (
        <details key={table} className="schema-table" open>
          <summary>{table}</summary>
          <table className="policy-table">
            <thead>
              <tr><th>列名</th><th>标签</th><th>标注理由</th><th>审查</th></tr>
            </thead>
            <tbody>
              {Object.entries(cols).map(([col, label]) => (
                <tr key={col}>
                  <td><code>{col}</code></td>
                  <td><EclTag label={label} /></td>
                  <td className="reason-cell">
                    {policy.column_reasons[table]?.[col] ?? '—'}
                  </td>
                  <td>
                    <select
                      value={label}
                      onChange={e => change(table, col, e.target.value as EclLabel)}
                    >
                      {LABELS.map(l => <option key={l} value={l}>{l}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ))}

      <h3>跨域规则</h3>
      {policy.cross_domain_rules.length === 0 && <p className="hint">尚未定义跨域规则。</p>}
      {policy.cross_domain_rules.map((r, i) => (
        <div key={i} className="card">
          <div><code>{r.table_pair.join(' ⨝ ')}</code> on <code>{r.join_key}</code></div>
          <p className="hint">{r.reason}</p>
          <div className="rule-flags">
            <span>{r.forbid_personal_level ? '✅' : '⬜'} 禁止个人级</span>
            <span>{r.allow_aggregate_level ? '✅' : '⬜'} 允许聚合级</span>
          </div>
        </div>
      ))}

      <div className="sticky-actions">
        <button onClick={save} disabled={!dirty || saving}>
          {saving ? '保存中…' : dirty ? '保存策略' : '已保存'}
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 追加样式**

```css
.policy-summary { display: flex; gap: 20px; margin: 16px 0; }
.summary-item { display: flex; align-items: center; gap: 6px; font-size: 14px; }
.policy-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.policy-table th, .policy-table td {
  text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line);
}
.policy-table th { color: var(--muted); font-weight: 500; }
.reason-cell { color: var(--muted); font-size: 13px; max-width: 320px; }
.sticky-actions {
  position: sticky; bottom: 0; background: #fff; padding: 12px 0;
  border-top: 1px solid var(--line); margin-top: 24px;
}
.sticky-actions button {
  padding: 8px 20px; background: var(--ink); color: #fff;
  border: 0; border-radius: 6px; cursor: pointer;
}
.sticky-actions button:disabled { background: var(--line); color: var(--muted); cursor: default; }
```

- [ ] **Step 3: 验证**

1. 每列应显示标注理由，无未标注告警
2. 改一列标签 → 按钮变"保存策略" → 点击 → 变回"已保存" → 刷新页面确认改动还在

- [ ] **Step 4: 提交**

```bash
git add frontend/src/pages/PolicyPage.tsx frontend/src/App.css
git commit -m "feat: 模块② 安全策略管理"
```

---

### Task 7: 核心组件（徽章、事件卡、SQL diff、准入面板）

**Files:**
- Create: `frontend/src/components/DegradationBadge.tsx`
- Create: `frontend/src/components/EventCard.tsx`
- Create: `frontend/src/components/SqlDiff.tsx`
- Create: `frontend/src/components/AdmissionPanel.tsx`
- Create: `frontend/src/components/PlanPanel.tsx`
- Create: `frontend/src/components/ResultTable.tsx`

**Interfaces:**
- Produces（Task 8 的 ConsolePage 全部消费）：
  - `DegradationBadge({ degradation }: { degradation: DegradationInfo })`
  - `EventCard({ event }: { event: SecurityEvent })`
  - `SqlDiff({ before, after }: { before: string; after: string })`
  - `AdmissionPanel({ admission }: { admission: AdmissionInfo })`
  - `PlanPanel({ plan, events }: { plan: PlanItem[]; events: SecurityEvent[] })`
  - `ResultTable({ result }: { result: ResultSet })`

- [ ] **Step 1: 写 DegradationBadge**

```tsx
/** 降级徽章。L0 绿 / L1·L2 琥珀 / L3 红。 */
import type { DegradationInfo } from '../api/types'

export function DegradationBadge({ degradation }: { degradation: DegradationInfo }) {
  const tone = degradation.level === 'L0' ? 'pass'
    : degradation.level === 'L3' ? 'layer1' : 'layer2'
  return (
    <div className={`badge badge-${tone}`}>
      <span className="badge-level">{degradation.level}</span>
      <span className="badge-label">{degradation.label}</span>
    </div>
  )
}
```

- [ ] **Step 2: 写 EventCard**

```tsx
/** 安全事件卡——层二审计发现的每一次中间结果暴露。 */
import type { SecurityEvent } from '../api/types'

export function EventCard({ event }: { event: SecurityEvent }) {
  return (
    <div className={`event-card sev-${event.severity}`}>
      <div className="event-head">
        <span className="event-type">{event.type_label}</span>
        <span className="event-sev">{event.severity_label}</span>
        <span className="event-sub">子查询 #{event.sub_query_id}</span>
      </div>
      <div className="event-column"><code>{event.column}</code></div>
      <div className="event-detail">{event.detail}</div>
    </div>
  )
}
```

- [ ] **Step 3: 写 SqlDiff**

只做行级 diff——改写日志是字符串数组，逐行比对足够，不引 diff 库。

```tsx
/** 改写前后 SQL 对比（行级）。 */
import { useMemo } from 'react'

export function SqlDiff({ before, after }: { before: string; after: string }) {
  const rows = useMemo(() => {
    const b = before.split('\n')
    const a = after.split('\n')
    const n = Math.max(b.length, a.length)
    return Array.from({ length: n }, (_, i) => ({
      before: b[i] ?? '',
      after: a[i] ?? '',
      changed: (b[i] ?? '') !== (a[i] ?? ''),
    }))
  }, [before, after])

  const unchanged = before === after

  return (
    <div className="sql-diff">
      {unchanged ? (
        <div className="diff-unchanged">未改写</div>
      ) : (
        <table>
          <thead>
            <tr><th>改写前</th><th>改写后</th></tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className={r.changed ? 'diff-changed' : ''}>
                <td><code>{r.before}</code></td>
                <td><code>{r.after}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
```

- [ ] **Step 4: 写 AdmissionPanel**

**这是"层一"的呈现——必须与层二的琥珀色明确区分。**

```tsx
/**
 * 准入判定面板（层一）。
 *
 * 层一全称规则：病患令牌的查询若引用患者数据表，必须绑定本人。
 * 它不判断聚合结果能否反推个体——那不可判定（需知结果基数）。
 */
import type { AdmissionInfo } from '../api/types'

export function AdmissionPanel({ admission }: { admission: AdmissionInfo }) {
  return (
    <div className={`admission ${admission.passed ? 'admission-pass' : 'admission-deny'}`}>
      <div className="admission-head">
        <span className="layer-tag layer-1">层一 · 准入</span>
        <span className="admission-verdict">
          {admission.passed ? '放行' : '拒绝'}
        </span>
      </div>

      {admission.passed ? (
        <div className="admission-body">
          <div className="admission-row">
            <span>涉及表</span>
            <span>
              {admission.checked_tables.length > 0
                ? admission.checked_tables.map(t => <code key={t}>{t}</code>)
                : <em>无</em>}
            </span>
          </div>
          <div className="admission-row">
            <span>主语绑定</span>
            <span>{admission.bound_to_subject ? '✅ 绑定本人' : '— 不涉及患者数据'}</span>
          </div>
        </div>
      ) : (
        <div className="admission-refusal">{admission.reason}</div>
      )}
    </div>
  )
}
```

- [ ] **Step 5: 写 PlanPanel**

```tsx
/** 分解方案面板——逐个子查询列出，违规处高亮。 */
import type { PlanItem, SecurityEvent } from '../api/types'
import { SqlDiff } from './SqlDiff'

export function PlanPanel({ plan, events }: { plan: PlanItem[]; events: SecurityEvent[] }) {
  if (plan.length === 0) return null

  const eventsOf = (id: number) =>
    events.filter(e => e.sub_query_id === String(id))

  return (
    <div className="plan-panel">
      {plan.map(sq => {
        const hits = eventsOf(sq.id)
        return (
          <div key={sq.id} className={`plan-item ${hits.length > 0 ? 'has-violation' : ''}`}>
            <div className="plan-head">
              <span className="plan-id">#{sq.id}</span>
              <span className="plan-desc">{sq.description}</span>
              {sq.is_final && <span className="plan-final">最终答案</span>}
              {hits.length > 0 && (
                <span className="plan-violation-count">{hits.length} 项违规</span>
              )}
            </div>
            <SqlDiff before={sq.sql_before} after={sq.sql_after} />
            {hits.length > 0 && (
              <div className="plan-hits">
                {hits.map((h, i) => (
                  <span key={i} className="plan-hit">{h.type_label}：<code>{h.column}</code></span>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 6: 写 ResultTable**

```tsx
/** 结果表格。结果为空时明确说明，不留白。 */
import type { ResultSet } from '../api/types'

export function ResultTable({ result }: { result: ResultSet | null }) {
  if (!result) return null
  if (result.rows.length === 0) {
    return <div className="result-empty">查询已执行，无匹配数据。</div>
  }
  return (
    <table className="result-table">
      <thead>
        <tr>{result.columns.map(c => <th key={c}>{c}</th>)}</tr>
      </thead>
      <tbody>
        {result.rows.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) => <td key={j}>{String(cell)}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
```

- [ ] **Step 7: 追加样式**

```css
.badge { display: inline-flex; align-items: center; gap: 8px; padding: 4px 12px; border-radius: 6px; font-size: 13px; }
.badge-pass { background: var(--pass-bg); color: var(--pass); }
.badge-layer2 { background: var(--layer2-bg); color: var(--layer2); }
.badge-layer1 { background: var(--layer1-bg); color: var(--layer1); }
.badge-level { font-weight: 700; }

.layer-tag { font-size: 12px; padding: 2px 8px; border-radius: 4px; }
.layer-1 { background: var(--layer1-bg); color: var(--layer1); }

.admission { border-radius: 8px; padding: 16px; border: 1px solid; }
.admission-pass { background: var(--pass-bg); border-color: #b7e0c6; }
.admission-deny { background: var(--layer1-bg); border-color: #f5c2bd; }
.admission-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.admission-verdict { font-weight: 700; }
.admission-row { display: flex; gap: 16px; font-size: 14px; margin: 6px 0; }
.admission-row > span:first-child { color: var(--muted); min-width: 72px; }
.admission-row code, .plan-hit code { margin-right: 6px; }
.admission-refusal { font-size: 16px; font-weight: 600; color: var(--layer1); padding: 8px 0; }

.event-card { background: #fff; border-left: 3px solid var(--layer2); border-radius: 6px; padding: 12px 14px; margin-bottom: 10px; }
.event-card.sev-must_degrade { border-left-color: var(--layer1); }
.event-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.event-type { font-weight: 600; font-size: 14px; }
.event-sev, .event-sub { font-size: 12px; color: var(--muted); }
.event-sev { background: var(--layer2-bg); color: var(--layer2); padding: 1px 6px; border-radius: 3px; }
.event-column { font-size: 13px; margin-bottom: 4px; }
.event-detail { font-size: 12px; color: var(--muted); line-height: 1.5; }

.sql-diff table { width: 100%; border-collapse: collapse; font-size: 12px; }
.sql-diff th { text-align: left; color: var(--muted); font-weight: 500; padding: 4px 8px; }
.sql-diff td { padding: 4px 8px; font-family: ui-monospace, Consolas, monospace; vertical-align: top; width: 50%; }
.sql-diff .diff-changed { background: var(--layer2-bg); }
.diff-unchanged { font-size: 12px; color: var(--muted); padding: 6px 8px; }

.plan-item { background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 14px; margin-bottom: 12px; }
.plan-item.has-violation { border-left: 3px solid var(--layer2); }
.plan-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; font-size: 14px; }
.plan-id { color: var(--muted); font-family: ui-monospace, monospace; }
.plan-desc { font-weight: 500; }
.plan-final { font-size: 11px; background: #eef2f7; color: var(--muted); padding: 1px 6px; border-radius: 3px; }
.plan-violation-count { font-size: 12px; color: var(--layer2); margin-left: auto; }
.plan-hits { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.plan-hit { font-size: 12px; color: var(--layer2); }

.result-table { width: 100%; border-collapse: collapse; font-size: 14px; background: #fff; }
.result-table th, .result-table td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line); }
.result-table th { background: #fafbfc; color: var(--muted); font-weight: 500; }
.result-empty { color: var(--muted); font-size: 14px; }
```

- [ ] **Step 8: 类型检查 + 提交**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误

```bash
git add frontend/src/components/ frontend/src/App.css
git commit -m "feat: 核心展示组件（徽章/事件卡/SQL diff/准入面板）"
```

---

### Task 8: 模块③ 查询与拦截控制台 ★ 核心

**Files:**
- Create: `frontend/src/pages/ConsolePage.tsx`

**Interfaces:**
- Consumes: Task 3 `runQuery`、`useToken`、`presetsFor`；Task 7 全部组件
- Produces: 演示的主舞台

> **这是整个演示视频的主舞台。** 布局必须让"同一问题、两类令牌、不同结果"一眼可见。

- [ ] **Step 1: 写 ConsolePage**

```tsx
import { useState } from 'react'
import { runQuery } from '../api/query'
import type { QueryResponse } from '../api/types'
import { useToken } from '../store/token'
import { presetsFor } from '../store/presets'
import { AdmissionPanel } from '../components/AdmissionPanel'
import { PlanPanel } from '../components/PlanPanel'
import { EventCard } from '../components/EventCard'
import { DegradationBadge } from '../components/DegradationBadge'
import { ResultTable } from '../components/ResultTable'

const DATASOURCE = 'regional_health'

export function ConsolePage() {
  const { token } = useToken()
  const [data, setData] = useState<QueryResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const presets = presetsFor(token.type)

  const ask = async (questionId: string) => {
    setRunning(true)
    setError(null)
    setData(null)
    try {
      setData(await runQuery({
        token, datasource_id: DATASOURCE, question_id: questionId,
      }))
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  const denied = data !== null && !data.admission.passed

  return (
    <div className="page console">
      <h2>查询控制台</h2>

      <div className="preset-bar">
        <span className="preset-label">
          {token.type === 'staff' ? '医护人员可用问题' : '病患可用问题'}
        </span>
        {presets.map(p => (
          <button key={p.id} onClick={() => ask(p.id)} disabled={running}>
            {p.question}
          </button>
        ))}
      </div>

      {running && <div className="loading">审计中…</div>}
      {error && <div className="alert-error">{error}</div>}

      {data && (
        <>
          <div className="console-head">
            <h3>{data.question}</h3>
            <DegradationBadge degradation={data.degradation} />
          </div>

          <AdmissionPanel admission={data.admission} />

          {denied ? (
            <div className="deny-note">
              查询在执行前被拒绝——未访问数据库，未产生结果集。
            </div>
          ) : (
            <div className="panels">
              <section className="panel">
                <h4>分解方案</h4>
                <PlanPanel plan={data.plan} events={data.events} />
              </section>

              <section className="panel">
                <h4>安全事件 <span className="count">{data.events.length}</span></h4>
                {data.events.length === 0 ? (
                  <p className="hint">未发现中间结果暴露。</p>
                ) : (
                  data.events.map((e, i) => <EventCard key={i} event={e} />)
                )}

                {data.rewrite.applied > 0 && (
                  <>
                    <h4>改写日志</h4>
                    <ul className="rewrite-log">
                      {data.rewrite.log.map((line, i) => <li key={i}>{line}</li>)}
                    </ul>
                  </>
                )}
              </section>
            </div>
          )}

          {data.degradation.message && (
            <div className="degradation-message">{data.degradation.message}</div>
          )}

          {data.result && (
            <section className="panel">
              <h4>查询结果</h4>
              <ResultTable result={data.result} />
            </section>
          )}

          <div className="metrics-bar">
            <span>审计耗时 <strong>{data.metrics.elapsed_ms} ms</strong></span>
            <span>LLM 调用 <strong>{data.metrics.llm_calls}</strong></span>
            <span>数据库访问 <strong>{data.metrics.db_access}</strong></span>
            <span className="metrics-note">
              审计阶段零模型调用、零数据库访问
            </span>
          </div>
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 2: 追加样式**

```css
.preset-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 16px 0 24px; }
.preset-label { color: var(--muted); font-size: 13px; margin-right: 4px; }
.preset-bar button {
  padding: 8px 14px; border: 1px solid var(--line); background: #fff;
  border-radius: 6px; cursor: pointer; font-size: 13px;
}
.preset-bar button:hover:not(:disabled) { border-color: var(--ink); }
.preset-bar button:disabled { color: var(--muted); cursor: default; }

.console-head { display: flex; align-items: center; gap: 16px; margin-bottom: 16px; }
.console-head h3 { margin: 0; }
.deny-note { margin-top: 16px; font-size: 14px; color: var(--muted); }

.panels { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 20px; }
@media (max-width: 1000px) { .panels { grid-template-columns: 1fr; } }
.panel { background: #fbfcfd; border: 1px solid var(--line); border-radius: 8px; padding: 16px; margin-top: 16px; }
.panel h4 { margin: 0 0 12px; font-size: 14px; color: var(--muted); font-weight: 600; }
.panel .count { background: var(--layer2-bg); color: var(--layer2); padding: 1px 7px; border-radius: 8px; font-size: 12px; }
.rewrite-log { font-size: 12px; color: var(--muted); padding-left: 18px; line-height: 1.7; }
.degradation-message { margin-top: 16px; font-size: 14px; color: var(--muted); }

.metrics-bar {
  display: flex; gap: 24px; align-items: center; margin-top: 20px;
  padding: 12px 16px; background: #fff; border: 1px solid var(--line);
  border-radius: 8px; font-size: 13px; color: var(--muted);
}
.metrics-bar strong { color: var(--ink); }
.metrics-note { margin-left: auto; font-size: 12px; }
```

- [ ] **Step 3: 验证五条关键路径**

`npm run dev`，逐条走：

| 令牌 | 问题 | 预期 |
|---|---|---|
| 病患 | 我上次的血糖是多少 | ✅ 放行，有结果表格 |
| 病患 | 心内科有哪些医生 | ✅ 放行（不涉及患者表） |
| 病患 | 得这个病的有多少人 | ❌ 红色拒绝面板，无结果，显示固定文案 |
| 医护 | 糖尿病患者产生了多少费用 | 琥珀色事件卡 + 改写日志 + 降级徽章 |
| 医护 | 导出患者基本信息核对表 | L2/L3 降级 |

**并验证**：底部指标条显示 `LLM 调用 0` / `数据库访问 0`。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/pages/ConsolePage.tsx frontend/src/App.css
git commit -m "feat: 模块③ 查询与拦截控制台"
```

---

### Task 9: 模块④ 安全事件报告

**Files:**
- Create: `frontend/src/pages/ReportPage.tsx`

**Interfaces:**
- Consumes: Task 3 `fetchReports`、`fetchReport`、`fetchDetectionMetrics`、`exportReportUrl`
- Produces: 历史列表 + 效能指标 + 一键导出

- [ ] **Step 1: 写 ReportPage**

```tsx
import { useEffect, useState } from 'react'
import { exportReportUrl, fetchDetectionMetrics, fetchReports } from '../api/reports'
import type { DetectionMetrics, ReportSummary } from '../api/types'
import { DegradationBadge } from '../components/DegradationBadge'

export function ReportPage() {
  const [reports, setReports] = useState<ReportSummary[]>([])
  const [metrics, setMetrics] = useState<DetectionMetrics | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchReports().then(setReports).catch(e => setError(String(e)))
    fetchDetectionMetrics().then(setMetrics).catch(e => setError(String(e)))
  }, [])

  return (
    <div className="page">
      <h2>安全事件报告</h2>
      {error && <div className="alert-error">{error}</div>}

      {metrics && (
        <section className="panel">
          <h4>检测效能</h4>
          <div className="metrics-grid">
            <div className="metric">
              <div className="metric-value">
                {(metrics.precision.value * 100).toFixed(0)}%
              </div>
              <div className="metric-name">精确率</div>
              <div className="metric-detail">{metrics.precision.detail}</div>
            </div>
            <div className="metric">
              <div className="metric-value">
                {(metrics.recall.value * 100).toFixed(0)}%
              </div>
              <div className="metric-name">召回率</div>
              <div className="metric-detail">{metrics.recall.detail}</div>
            </div>
            <div className="metric">
              <div className="metric-value">
                {(metrics.blocked_detection.value * 100).toFixed(0)}%
              </div>
              <div className="metric-name">禁止列检出</div>
              <div className="metric-detail">{metrics.blocked_detection.detail}</div>
            </div>
          </div>
          <p className="hint">数据来源：{metrics.source}</p>
        </section>
      )}

      <section className="panel">
        <h4>历史记录</h4>
        {reports.length === 0 ? (
          <p className="hint">暂无记录。到查询控制台提一次问试试。</p>
        ) : (
          <table className="report-table">
            <thead>
              <tr>
                <th>时间</th><th>问题</th><th>令牌</th>
                <th>降级</th><th>事件</th><th></th>
              </tr>
            </thead>
            <tbody>
              {reports.map(r => (
                <tr key={r.id}>
                  <td className="muted">{r.created_at}</td>
                  <td>{r.question}</td>
                  <td>{r.token_type === 'staff' ? '医护人员' : '病患'}</td>
                  <td>
                    <DegradationBadge degradation={{
                      level: r.degradation_level,
                      label: '',
                      message: '',
                    }} />
                  </td>
                  <td>{r.event_count}</td>
                  <td>
                    <a href={exportReportUrl(r.id)} download>导出</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
```

- [ ] **Step 2: 追加样式**

```css
.metrics-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 12px; }
.metric { background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 16px; text-align: center; }
.metric-value { font-size: 32px; font-weight: 700; color: var(--pass); }
.metric-name { font-size: 13px; color: var(--muted); margin: 4px 0 8px; }
.metric-detail { font-size: 12px; color: var(--muted); }
.report-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.report-table th, .report-table td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); }
.report-table th { color: var(--muted); font-weight: 500; font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }

.card { background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 16px; margin-bottom: 12px; }
.empty-state { background: #fff; border: 1px dashed var(--line); border-radius: 8px; padding: 40px; text-align: center; }
.empty-state button { padding: 10px 24px; background: var(--ink); color: #fff; border: 0; border-radius: 6px; cursor: pointer; }
.hint { color: var(--muted); font-size: 13px; line-height: 1.6; }
.alert-error { background: var(--layer1-bg); color: var(--layer1); padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; font-size: 14px; }
.kv { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 0; }
.kv dt { font-size: 12px; color: var(--muted); }
.kv dd { margin: 2px 0 0; font-size: 15px; }
.schema-table { background: #fff; border: 1px solid var(--line); border-radius: 8px; margin-bottom: 10px; padding: 10px 14px; }
.schema-table summary { cursor: pointer; font-weight: 600; font-size: 14px; }
.schema-columns { list-style: none; padding: 10px 0 0; margin: 0; font-size: 13px; }
.schema-columns li { display: flex; gap: 12px; align-items: center; padding: 4px 0; }
.col-type { color: var(--muted); font-size: 12px; }
.col-pk { font-size: 11px; background: #eef2f7; color: var(--muted); padding: 1px 5px; border-radius: 3px; }
.loading { color: var(--muted); padding: 20px 0; }
```

- [ ] **Step 3: 验证**

控制台跑几条查询 → 报告页应出现对应记录 → 点"导出"应下载 JSON 文件。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/pages/ReportPage.tsx frontend/src/App.css
git commit -m "feat: 模块④ 安全事件报告"
```

---

## 阶段三：联调与打磨（W3-W4）

### Task 10: 切换到生成的契约类型

**Files:**
- Modify: `frontend/src/api/types.ts`（**用生成版整体替换**）
- Modify: `frontend/src/api/*.ts`（按需调整 import）

**Interfaces:**
- Consumes: 后端 `openapi.json`（后端计划 Task 13 产出）

- [ ] **Step 1: 生成**

```bash
cd J:/race/AIC/MedGuard/frontend
# 后端需在运行
curl -s http://localhost:8000/openapi.json > openapi.json
npx openapi-typescript openapi.json -o src/api/types.ts
```

- [ ] **Step 2: 适配**

生成版是 `paths` / `components` 结构，与手写的扁平类型不同。**新建 `src/api/models.ts` 从生成版导出别名**，各 api 模块改从此处 import：

```ts
/** 从 openapi 生成版提取的领域类型别名。
 *
 * types.ts 是生成物，禁止手工编辑；本文件是它与业务代码之间的适配层。
 */
import type { components } from './types'

export type Token = components['schemas']['Token']
export type AdmissionInfo = components['schemas']['AdmissionInfo']
export type PlanItem = components['schemas']['PlanItem']
export type SecurityEvent = components['schemas']['SecurityEvent']
export type RewriteInfo = components['schemas']['RewriteInfo']
export type DegradationInfo = components['schemas']['DegradationInfo']
export type MetricsInfo = components['schemas']['MetricsInfo']
export type ResultSet = components['schemas']['ResultSet']
export type QueryResponse = components['schemas']['QueryResponse']
export type DatasourceInfo = components['schemas']['DatasourceInfo']
export type ReportSummary = components['schemas']['ReportSummary']
// 以下类型后端未用 Pydantic 建模，保留手写定义
export type EclLabel = 'free' | 'controlled' | 'blocked'
export type TokenType = 'staff' | 'patient'
```

- [ ] **Step 3: 全局替换 import**

把所有 `from '../api/types'` 改为 `from '../api/models'`（`types.ts` 只被 `models.ts` 引用）。

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误。**有错就是契约对不上——这是本任务的目的。**

- [ ] **Step 4: 提交**

```bash
git add frontend/openapi.json frontend/src/api/
git commit -m "[CONTRACT] 切换到 openapi 生成的契约类型"
```

---

### Task 11: 错误处理与空态

**Files:**
- Modify: 各页面

**Interfaces:**
- Consumes: 全部前置任务

- [ ] **Step 1: 补三处空态**

| 场景 | 文案 |
|---|---|
| 数据源未载入 | 已有（Task 5） |
| 控制台未提问 | `选择一个示例问题开始。` |
| 报告页无记录 | 已有（Task 9） |

- [ ] **Step 2: 补后端不可达的处理**

`src/api/client.ts` 里 `fetch` 失败会抛 `TypeError: Failed to fetch`。在 `request()` 中捕获并转成可读文案：

```ts
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    throw new Error('无法连接后端服务（localhost:8000），请确认已启动。')
  }
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${body}`)
  }
  return res.json() as Promise<T>
}
```

- [ ] **Step 3: 验证**

停掉后端 → 刷新前端 → 页面显示"无法连接后端服务"，不是白屏或英文报错。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/
git commit -m "feat: 错误处理与空态"
```

---

### Task 12: 演示路径打磨

**Files:**
- Modify: 各页面（微调，不加功能）

**Interfaces:**
- Consumes: 全部前置任务 + 后端计划 Task 12 的演示数据校验结果

> 录视频前最后一道。**只做减法和打磨，不加新功能。**

- [ ] **Step 1: 按演示脚本走一遍**

对着设计文档 §6.5 的双令牌矩阵逐条走，记录每一屏的实际观感。

- [ ] **Step 2: 检查三条**

1. **层一 vs 层二的视觉区分是否一眼可辨？** 红色阻断 vs 琥珀拦截。
2. **"同一问题、两类令牌、不同结果"是否无需解说就能看懂？**
3. **指标条上的 `LLM 调用 0 / 数据库访问 0` 是否醒目？** 这是零 LLM 安全声明的现场证据。

- [ ] **Step 3: 移除演示无关元素**

删掉调试打印、开发中的占位文案、未使用的 import。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/
git commit -m "polish: 演示路径打磨"
```

---

### Task 13: 生产构建与交付

**Files:**
- Create: `frontend/README.md`

- [ ] **Step 1: 构建**

```bash
cd J:/race/AIC/MedGuard/frontend
npx tsc --noEmit
npm run build
```

Expected: `dist/` 生成，无类型错误。

- [ ] **Step 2: 写前端 README**

包含：环境要求（Node 24）、启动命令、构建命令、目录结构、契约变更流程（指向团队规范 §3.4）。

- [ ] **Step 3: 提交**

```bash
git add frontend/README.md
git commit -m "docs: 前端 README"
```

---

## 完成标准

全部 13 个任务完成后：

- [ ] `npx tsc --noEmit` 无错误
- [ ] `npm run build` 成功
- [ ] 四个导航页都能打开且内容正确
- [ ] 双令牌矩阵五条路径全部符合预期（Task 8 Step 3 的表）
- [ ] 层一拒绝时显示固定文案「该查询涉及其他患者信息，无法提供。」且**无结果表格**
- [ ] 指标条显示 `LLM 调用 0` / `数据库访问 0`
- [ ] 报告页能列出历史、能导出 JSON
- [ ] 停掉后端时显示可读的中文错误，不白屏
- [ ] `git status` 干净

**前端完成的定义**：以上全部勾选。缺一不可。
