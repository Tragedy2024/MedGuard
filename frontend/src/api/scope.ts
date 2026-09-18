import type { Token } from './models'

/**
 * 把令牌摊成 GET 端点的查询参数。
 *
 * GET 带不了请求体，而 `/api/reports*` 与 `/api/auth/users` 都要验签，
 * 所以令牌的**每一个字段**都得放进查询串——包括 `exp` 和 `sig`。
 * 少一个 `sig` 就退回"客户端自述身份"，而伪造一个
 * `token_type=patient&subject_id=P002` 恰好能读到别人的安全报告。
 *
 * 注意 `type` → `token_type` 的改名：`type` 是 Python 内置名，FastAPI 的
 * 查询参数不能叫这个。
 *
 * 空值不下发：空串会被 FastAPI 当成合法的字符串值（`?sig=` → 401），
 * 而 `?exp=` 会让整数解析失败（422）——两种都会把"令牌不完整"这件事
 * 伪装成别的错误。
 */
export const tokenScope = (token: Token): string => {
  const p = new URLSearchParams({ token_type: token.type })
  if (token.subject_id) p.set('subject_id', token.subject_id)
  if (token.account) p.set('account', token.account)
  if (token.exp != null) p.set('exp', String(token.exp))
  if (token.sig) p.set('sig', token.sig)
  return p.toString()
}
