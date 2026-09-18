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
   与上方各类型一致，**从契约派生**。

   这几条曾经是手写的：当时 openapi.json 停在智慧医生上线前的版本，
   生成的 types.ts 里根本没有这些 schema，只能手写顶上。2026-09-18
   契约补齐后改回派生——手写的那份会随着后端加字段慢慢失真，而派生
   让"后端一改前端就编译失败"这条保证重新生效。 */

export type SmartDoctorRequest = Schemas['SmartDoctorRequest']
export type SmartDoctorDataBlock = Schemas['SmartDoctorDataBlock']
export type SmartDoctorInterpretation = Schemas['SmartDoctorInterpretation']
export type SmartDoctorInterpretationItem =
  NonNullable<SmartDoctorInterpretation['items']>[number]
export type SmartDoctorAdvice = Schemas['SmartDoctorAdvice']
export type SmartDoctorResponse = Schemas['SmartDoctorResponse']

/** 智慧医生的意图。从响应模型派生，后端加一种意图这里就报错。 */
export type SmartDoctorIntent = SmartDoctorResponse['intent']

/* ── 账号（/api/auth）────────────────────────────────────────── */

export type LoginRequest = Schemas['LoginRequest']
export type LoginResponse = Schemas['LoginResponse']
export type RegisterRequest = Schemas['RegisterRequest']
export type UserInfo = Schemas['UserInfo']

/** 账号角色。名单只有一个来源：UserInfo.role。 */
export type UserRole = UserInfo['role']
