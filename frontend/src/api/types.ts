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
  /** 算法层原文（英文），折叠展示供技术观众核对 */
  message: string
  /** 产品层中文说明，面向医护与病患的主文案 */
  message_cn: string
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
  /**
   * 面向医护/病患的中文名。物理表名列名不应直接出现在界面上——
   * 那会让产品退回成开发者工具（会议记录 §1.1 已否决的形态）。
   * 来源：策略 YAML 的产品层字段，零算法改动（团队规范 §5.2）。
   */
  table_aliases: Record<string, string>
  column_aliases: Record<string, Record<string, string>>
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
