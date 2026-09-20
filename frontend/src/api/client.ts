/** API 客户端——全项目唯一发 HTTP 请求的地方。 */
import type {
  DatasourceInfo, Policy, QueryResponse, ReportSummary,
  DetectionMetrics, SchemaTable, Token,
} from './models'

/**
 * 从错误响应里挑出**给人看**的那句话。
 *
 * 后端（FastAPI）的业务错误形如 `{"detail": "该问法暂未收录……"}`。
 * 如果直接把整个响应体拼进错误消息，那句中文提示就被埋在括号和转义里，
 * 用户看到一坨 JSON——而这套界面是给医护和病患看的。
 */
async function pickErrorMessage(res: Response): Promise<string> {
  const raw = await res.text()

  try {
    const body = JSON.parse(raw) as { detail?: unknown }
    const detail = body?.detail

    if (typeof detail === 'string') return detail

    // FastAPI 的 422 校验错误：detail 是 [{loc, msg, type}, ...]
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string; loc?: unknown[] }
      const where = (first.loc ?? []).filter((x) => x !== 'body').join('.')
      return `${where ? where + '：' : ''}${first.msg ?? '请求参数有误'}`
    }
  } catch {
    // 响应体不是 JSON，原样给出（截断，避免把整页 HTML 塞进界面）
  }

  return `${res.status} ${res.statusText}${raw ? '：' + raw.slice(0, 200) : ''}`
}

/**
 * API 基地址。
 *
 * 本地 / 同源部署（start_demo 后端托管 dist）：留空，走相对路径。
 * 前后端分离部署（前端 Vercel、后端 Railway）时在构建期注入：
 *     VITE_API_BASE=https://<railway 域名>
 */
const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(API_BASE + path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    // fetch 只在网络层失败时抛（连不上、服务没起、请求被中断）。
    // 原始错误是英文的 "Failed to fetch"，对用户毫无意义。
    throw new Error('无法连接后端服务，请确认服务已启动或稍后重试。')
  }

  if (!res.ok) {
    throw new Error(await pickErrorMessage(res))
  }
  return res.json() as Promise<T>
}

/**
 * 把任意抛出物转成**只含消息**的文案。
 *
 * `String(err)` 会连类型名一起给（"Error: 无法连接…"），界面上那三个字符
 * 是纯噪音；非 Error 的抛出物才原样转字符串。
 */
export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
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
