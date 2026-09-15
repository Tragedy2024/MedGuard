import { apiGet, apiPost } from './client'
import type { DatasourceInfo, SchemaTable } from './types'

export const fetchDatasources = () => apiGet<DatasourceInfo[]>('/api/datasources')

export const createDemoDatasource = () =>
  apiPost<{ id: string; created: boolean }>('/api/datasources/demo')

export const fetchSchema = (id: string) =>
  apiGet<{ tables: SchemaTable[] }>(`/api/datasources/${id}/schema`)
