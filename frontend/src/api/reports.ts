import { apiGet } from './client'
import type { DetectionMetrics, ReportDetail, ReportSummary, Token } from './models'
import { tokenScope } from './scope'

/**
 * 安全报告是**按发起人隔离**的：后端 reports 表存了 (token_type, subject_id)，
 * 读接口据此过滤；而且令牌**必须签名有效**（见 api/scope.ts）。
 *
 * 这两件事缺一不可：不隔离 → 医生跑完查询、患者登录后看到医生的记录；
 * 不验签 → 伪造一个 `token_type=patient&subject_id=P002` 就能读到别人的。
 */

export const fetchReports = (token: Token, limit = 20) =>
  apiGet<ReportSummary[]>(`/api/reports?limit=${limit}&${tokenScope(token)}`)

export const fetchReport = (token: Token, id: number) =>
  apiGet<ReportDetail>(`/api/reports/${id}?${tokenScope(token)}`)

export const fetchDetectionMetrics = () =>
  apiGet<DetectionMetrics>('/api/metrics/detection')

/** 导出走浏览器下载，不经 fetch。 */
export const exportReportUrl = (token: Token, id: number) =>
  `/api/reports/${id}/export?${tokenScope(token)}`
