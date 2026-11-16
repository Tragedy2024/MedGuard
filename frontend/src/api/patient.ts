/**
 * 患者单一入口：一次提问，服务端自动路由。
 *
 * 患者界面上只有一个输入框，不再让用户在「问答」和「查询」之间做选择。
 * 路由在服务端完成（`backend/patient_router.py`），前端只负责按 `route`
 * 渲染对应形状的回答：
 *
 *   personal_record / knowledge / clarify / unsupported → `smart_doctor`
 *   general_query                                       → `query`
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
  /** 短句，说明为什么走这条路由。服务端生成，前端只展示。 */
  reason: string
  smart_doctor: SmartDoctorResponse | null
  query: QueryResponse | null
  /** HTTP 200 下的可读中文错误；成功时为 null。 */
  error: string | null
}

/** 患者统一入口：POST /api/patient/ask。 */
export const askPatient = (req: PatientAskRequest) =>
  apiPost<PatientAskResponse>('/api/patient/ask', req)
