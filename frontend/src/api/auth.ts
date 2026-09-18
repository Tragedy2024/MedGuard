import { apiGet, apiPost } from './client'
import type { LoginResponse, RegisterRequest, Token, UserInfo } from './models'
import { tokenScope } from './scope'

/**
 * 账号接口。
 *
 * **登录由后端签发令牌**——在此之前登录完全发生在前端（`store/auth.tsx`
 * 里硬编码三个账号、口令明文写在源码里），令牌是浏览器现场拼的，后端既
 * 验证不了也拦不住。于是"身份"只是客户端的自述。
 */

export const login = (account: string, password: string) =>
  apiPost<LoginResponse>('/api/auth/login', { account, password })

/** 建号。后端只认管理员令牌——非管理员会收到 403。 */
export const registerUser = (body: RegisterRequest) =>
  apiPost<UserInfo>('/api/auth/register', body)

export const fetchUsers = (token: Token) =>
  apiGet<UserInfo[]>(`/api/auth/users?${tokenScope(token)}`)
