import { apiGet, apiPut } from './client'
import type { Policy, Token } from './models'

export const fetchPolicy = (id: string) => apiGet<Policy>(`/api/policies/${id}`)

/**
 * 更新安全策略。**要带管理员令牌**——这个接口决定医盾拦什么，把
 * `id_card` 从 blocked 改成 free 整条防线就没了。它曾经完全敞开：
 * 匿名 PUT 就能改写磁盘上的策略文件。
 */
export const updatePolicy = (
  token: Token,
  id: string,
  body: Partial<Pick<Policy, 'column_labels' | 'cross_domain_rules'>>,
) => apiPut<{ saved: boolean }>(`/api/policies/${id}`, { token, ...body })
