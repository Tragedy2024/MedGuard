import { apiGet } from './client'
import type { DetectionMetrics, ReportDetail, ReportSummary } from './models'

export const fetchReports = (limit = 20) =>
  apiGet<ReportSummary[]>(`/api/reports?limit=${limit}`)

export const fetchReport = (id: number) =>
  apiGet<ReportDetail>(`/api/reports/${id}`)

export const fetchDetectionMetrics = () =>
  apiGet<DetectionMetrics>('/api/metrics/detection')

/** 导出走浏览器下载，不经 fetch。 */
export const exportReportUrl = (id: number) => `/api/reports/${id}/export`
