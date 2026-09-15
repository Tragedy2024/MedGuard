import { apiPost } from './client'
import type { QueryResponse, Token } from './types'

export interface QueryRequest {
  token: Token
  datasource_id: string
  question_id: string
}

export const runQuery = (req: QueryRequest) =>
  apiPost<QueryResponse>('/api/query', req)
