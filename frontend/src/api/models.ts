/**
 * 契约类型适配层。
 *
 * `types.ts` 是 `openapi-typescript` 从后端 openapi.json **自动生成**的，
 * 结构是 `paths` / `components`，且**禁止手工编辑**。本文件是它与业务代码
 * 之间的唯一接口：业务代码一律从这里 import，不再直接碰 `types.ts`。
 *
 * 这样做的价值不只是"类型更准"，而是**契约一变就报错**：
 * 后端改了字段名或收紧了取值，跑一次生成 → 这里或页面立刻编译失败，
 * 而不是等到联调时才发现对不上。
 *
 * 重新生成：
 *   curl -s http://localhost:8000/openapi.json -o openapi.json
 *   npx openapi-typescript openapi.json -o src/api/types.ts
 */
import type { components } from './types'

type Schemas = components['schemas']

/* ── 直接来自后端契约 ─────────────────────────────────────────── */

export type Token = Schemas['Token']
export type AdmissionInfo = Schemas['AdmissionInfo']
export type PlanItem = Schemas['PlanItem']
export type SecurityEvent = Schemas['SecurityEvent']
export type RewriteInfo = Schemas['RewriteInfo']
export type DegradationInfo = Schemas['DegradationInfo']
export type MetricsInfo = Schemas['MetricsInfo']
export type ResultSet = Schemas['ResultSet']
export type QueryResponse = Schemas['QueryResponse']

export type DatasourceInfo = Schemas['DatasourceInfo']
export type SchemaColumn = Schemas['SchemaColumn']
export type SchemaTable = Schemas['SchemaTable']
export type DatasourceSchema = Schemas['DatasourceSchema']

export type CrossDomainRule = Schemas['CrossDomainRule']
export type Policy = Schemas['Policy']

export type ReportSummary = Schemas['ReportSummary']
export type ReportDetail = Schemas['ReportDetail']

export type MetricValue = Schemas['MetricValue']
export type DetectionMetrics = Schemas['DetectionMetrics']

/* ── 从契约**派生**的联合类型 ───────────────────────────────────
   这几条刻意不手写：手写一份就会和后端各改各的。
   派生意味着后端一改取值，前端立刻编译失败——那正是我们要的。 */

/** 令牌类型。后端 `Token.type` 是 Literal，故可直接取。 */
export type TokenType = Token['type']

/** 降级等级 L0–L3。 */
export type DegradationLevel = DegradationInfo['level']

/** ECL 三值（自由 / 受控 / 禁止）。
 *  从 Policy.column_labels 的取值派生——后端若加第四级，这里会报错。 */
export type EclLabel = NonNullable<Policy['column_labels']>[string][string]

/* ── 仅存在于前端的类型 ─────────────────────────────────────────
   它们不属于后端契约，没有对应 schema，只能手写。 */

/** 预设查询。清单在前端 `store/presets.ts`，与后端 demo/queries.json 的键对应。 */
export interface PresetQuery {
  id: string
  question: string
  tokenTypes: TokenType[]
}

/* ── 智慧医生（智慧医生.docx）──────────────────────────────────
   后端 schemas.py 已定义同名模型，但 types.ts 是 openapi 生成物；
   本次未重新生成，因此此处先以手写接口提供。将来执行
   `npx openapi-typescript openapi.json -o src/api/types.ts` 后，
   应改为从 components 派生（与上方各类型一致）。

   注意两点：
   - 原注释写的「本机无 Node 无法重新生成」不成立：Node 装在 D:\nodejs，
     只是不在 PATH 里，跑 npx 前把它加进 PATH 即可。
   - openapi.json 本身也还没更新（仍是 13 条 path、无 SmartDoctor
     schema），所以重生成前要先从后端导出最新契约，两者是一起做的一件事。 */

export interface SmartDoctorRequest {
  token: Token
  datasource_id: string
  question: string
}

export type SmartDoctorIntent =
  | 'lab'
  | 'medication'
  | 'symptom'
  | 'disease'
  | 'fallback'

export interface SmartDoctorDataBlock {
  /** 来源：医院主库（经医盾层一 + 层二审计后返回） */
  source: string
  columns: string[]
  rows: unknown[][]
  sql_after: string
  degradation_level: string
}

export interface SmartDoctorInterpretationItem {
  [key: string]: unknown
}

export interface SmartDoctorInterpretation {
  /** 来源：医院审核知识库 */
  source: string
  title: string
  text: string
  items: SmartDoctorInterpretationItem[]
}

export interface SmartDoctorAdvice {
  /** 来源：医院审核知识库 */
  source: string
  text: string
  actions: string[]
  urgent: boolean
}

export interface SmartDoctorResponse {
  intent: SmartDoctorIntent
  question: string
  admission: AdmissionInfo
  degradation: DegradationInfo
  data: SmartDoctorDataBlock | null
  interpretation: SmartDoctorInterpretation | null
  advice: SmartDoctorAdvice | null
}
