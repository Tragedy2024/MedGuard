import { apiPost } from './client'
import type { QueryResponse, Token } from './models'

export interface QueryRequest {
  token: Token
  datasource_id: string
  question_id: string
}

export const runQuery = (req: QueryRequest) =>
  apiPost<QueryResponse>('/api/query', req)

export interface DirectQueryRequest {
  token: Token
  datasource_id: string
  question: string
}

/**
 * 自由提问。
 *
 * 后端是**缓存优先**：命中缓存毫秒级返回；未命中才调模型翻译，约需
 * 数十秒。两条路径返回的形状完全一样，前端不必区分。
 */
export const runDirectQuery = (req: DirectQueryRequest) =>
  apiPost<QueryResponse>('/api/query/direct', req)
