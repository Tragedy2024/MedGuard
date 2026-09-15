import { apiGet, apiPut } from './client'
import type { Policy } from './types'

export const fetchPolicy = (id: string) => apiGet<Policy>(`/api/policies/${id}`)

export const updatePolicy = (
  id: string,
  body: Partial<Pick<Policy, 'column_labels' | 'cross_domain_rules'>>,
) => apiPut<{ saved: boolean }>(`/api/policies/${id}`, body)
