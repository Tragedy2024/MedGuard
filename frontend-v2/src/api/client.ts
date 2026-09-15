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
