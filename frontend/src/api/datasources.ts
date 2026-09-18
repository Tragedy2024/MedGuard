import { apiGet, apiPost } from './client'
import type { DatasourceInfo, SchemaTable, Token } from './models'

export const fetchDatasources = () => apiGet<DatasourceInfo[]>('/api/datasources')

/** 载入演示数据。**要带管理员令牌**——它会先删库再重建。 */
export const createDemoDatasource = (token: Token) =>
  apiPost<{ id: string; created: boolean }>('/api/datasources/demo', { token })

export const fetchSchema = (id: string) =>
  apiGet<{ tables: SchemaTable[] }>(`/api/datasources/${id}/schema`)
