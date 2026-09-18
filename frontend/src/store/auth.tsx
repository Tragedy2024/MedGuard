/**
 * 会话状态——「谁登录了」。
 *
 * 与 api/models 里的 `Token` 不是一回事，两者正交：
 *
 *   Token   数据访问凭证 —— 决定「能查到什么数据」（后端签发、带 HMAC 签名）
 *   Session 登录身份     —— 决定「能进哪些页面」
 *
 * 医护与病患的 Token 不同、页面相同：这正是演示要证明的东西——
 * **同一套界面、同一个问题，两类令牌给出不同结果**。
 * 管理员则相反：多出「数据源」「安全策略」「用户管理」三个页面。
 *
 * **登录走后端**。在此之前这里是三个硬编码账号 + 明文口令，令牌由浏览器
 * 现场拼出来，后端既不参与登录也验证不了令牌——"身份"只是客户端的自述，
 * 手搓一个 subject_id 就能读别人的安全报告。现在账号落在平台元数据库里，
 * 口令是 PBKDF2 哈希，令牌由 `/api/auth/login` 签发。
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { login } from '../api/auth'
import type { Token, UserRole } from '../api/models'

export type { UserRole }

/** 账号的职责范围，纯展示（身份条用）。 */
const ROLE_SCOPE: Record<UserRole, string> = {
  admin: '全院数据与安全策略',
  staff: '所辖患者群体',
  patient: '本人病历',
}

/** 演示环境统一口令。登录页会把它明示出来，不藏着。 */
export const DEMO_PASSWORD = 'medguard'

export interface DemoAccount {
  account: string
  displayName: string
  scope: string
}

/**
 * 演示账号——**仅供登录页展示与一键填入**，不再是登录的判定依据。
 *
 * 判定在后端：这三个账号由 `db.seed_demo_users` 建进 users 表，口令以
 * PBKDF2 哈希保存。这里留着是因为演示要让人一眼看到有哪三个身份可切；
 * 口令本来就印在页面上，不存在额外的泄露。
 */
export const DEMO_ACCOUNTS: DemoAccount[] = [
  { account: 'admin', displayName: '信息科管理员', scope: ROLE_SCOPE.admin },
  // 显示名与工号对齐演示库里的真实行（staff.S001 = 医生001 / 心内科），
  // 否则「我的患者」查出来的数字与登录身份对不上。
  { account: 'doctor', displayName: '医生001 · 心内科', scope: ROLE_SCOPE.staff },
  { account: 'patient', displayName: '患者001', scope: ROLE_SCOPE.patient },
]

export interface Session {
  account: string
  role: UserRole
  displayName: string
  scope: string
  token: Token
}

const STORAGE_KEY = 'medguard.session'

interface AuthCtx {
  session: Session | null
  /** 登录。失败时**抛异常**——消息由后端给，已经是给人看的中文。 */
  signIn: (account: string, password: string) => Promise<Session>
  signOut: () => void
}

const Ctx = createContext<AuthCtx | null>(null)

function restore(): Session | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const s = JSON.parse(raw) as Session
    // 形状不对、没有签名（更早版本存下的旧会话）、或**已过期**，一律丢弃。
    // 留着的话界面会显示成"已登录"，但每个接口都撞 401——用户看到的是
    // "系统坏了"，而不是"请重新登录"。8 小时后、或服务端换过签名密钥之后，
    // 不查 exp 就会变成那样。
    if (!s?.account || !s.token?.sig) return null
    if (!s.token.exp || s.token.exp * 1000 <= Date.now()) return null
    return s
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  // 用 sessionStorage 而非 localStorage：刷新页面不丢登录态（演示时
  // 误按 F5 不至于重来），但关掉标签页即登出。
  const [session, setSession] = useState<Session | null>(restore)

  const signIn = useCallback(async (account: string, password: string) => {
    const { user, token } = await login(account.trim(), password)
    const next: Session = {
      account: user.account,
      role: user.role,
      displayName: user.display_name || user.account,
      scope: ROLE_SCOPE[user.role],
      token,
    }
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    setSession(next)
    return next
  }, [])

  const signOut = useCallback(() => {
    sessionStorage.removeItem(STORAGE_KEY)
    setSession(null)
  }, [])

  const value = useMemo<AuthCtx>(
    () => ({ session, signIn, signOut }),
    [session, signIn, signOut],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth(): AuthCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useAuth 必须在 AuthProvider 内使用')
  return ctx
}
