/**
 * 患者单一入口：一次提问，服务端自动路由。
 *
 * 患者界面只有一个输入框，服务端先选智慧医生或查询控制台；有歧义时确认功能。
 * `feature` 展示顶层功能，`query_scope` 展示控制台的数据范围，`route`
 * 只保留内部策略与对应的响应载荷形状：
 *
 *   knowledge / clarify / unsupported → `smart_doctor`
 *   personal_record / general_query   → 原查询控制台 `query`
 *
 * `error` 是 200 响应里的可读中文原因（例如通用查询翻译失败），
 * 与 HTTP 异常分开：前者是「这次问题没办成」，后者是「服务/身份出了问题」。
 */
import { apiPost } from './client'
import type {
  QueryResponse,
  SmartDoctorRequest,
  SmartDoctorResponse,
  Token,
} from './models'

/** 服务端路由结果。契约见 `.scratch/patient-unified-entry/CONTRACT.md`。 */
export type PatientRoute =
  | 'personal_record'
  | 'general_query'
  | 'knowledge'
  | 'clarify'
  | 'unsupported'

export type PatientFeature = 'smart_doctor' | 'query_console'
export type PatientQueryScope = 'self' | 'hospital'
export interface PatientRoutingChoice {
  label: string
  question: string
  feature: PatientFeature
  query_scope?: PatientQueryScope | null
}

/** 上一轮识别结果，用于「它正常吗」这类指代。与智慧医生请求同形。 */
export type PatientAskContext = NonNullable<SmartDoctorRequest['context']>

export interface PatientAskRequest {
  token: Token
  datasource_id: string
  question: string
  context?: PatientAskContext | null
}

export interface PatientAskResponse {
  route: PatientRoute
  feature?: PatientFeature | null
  query_scope?: PatientQueryScope | null
  interpretation_feature?: 'smart_doctor' | null
  /** 短句，说明为什么走这条路由。服务端生成，前端只展示。 */
  reason: string
  smart_doctor: SmartDoctorResponse | null
  routing_choices?: PatientRoutingChoice[]
  query: QueryResponse | null
  /** HTTP 200 下的可读中文错误；成功时为 null。 */
  error: string | null
}

/** 患者统一入口：POST /api/patient/ask。 */
export const askPatient = (req: PatientAskRequest) =>
  apiPost<PatientAskResponse>('/api/patient/ask', req)
