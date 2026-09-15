/**
 * 会话状态——「谁登录了」。
 *
 * 与 api/types 里的 `Token` 不是一回事，两者正交：
 *
 *   Token   数据访问凭证 —— 决定「能查到什么数据」
 *   Session 登录身份     —— 决定「能进哪些页面」
 *
 * 医护与病患的 Token 不同、页面相同：这正是演示要证明的东西——
 * **同一套界面、同一个问题，两类令牌给出不同结果**。
 * 管理员则相反：多出「数据源」与「安全策略」两个页面。
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import type { Token } from '../api/models'

export type UserRole = 'admin' | 'staff' | 'patient'

export interface DemoAccount {
  account: string
  password: string
  role: UserRole
  displayName: string
  scope: string
  /**
   * 令牌绑定的主体。
   *
   * - 病患：患者编号（P001）——层一据此校验"只能查本人"
   * - 医护：医生工号（S001）——让「我的患者」这类第一人称问法能解析出
   *   "我"是谁；层一不管医护令牌（由医院 RLS 负责，本项目不实现）
   * - 管理员：不绑定，只做平台管理
   */
  subjectId: string | null
}

/** 演示环境统一密码。登录页会把这三个账号明示出来，不藏着。 */
export const DEMO_PASSWORD = 'medguard'

export const DEMO_ACCOUNTS: DemoAccount[] = [
  {
    account: 'admin',
    password: DEMO_PASSWORD,
    role: 'admin',
    displayName: '信息科管理员',
    scope: '全院数据与安全策略',
    subjectId: null,
  },
  {
    // 显示名与工号对齐演示库里的真实行（staff.S001 = 医生001 / 心内科），
    // 否则「我的患者」查出来的数字与登录身份对不上。
    account: 'doctor',
    password: DEMO_PASSWORD,
    role: 'staff',
    displayName: '医生001 · 心内科',
    scope: '所辖患者群体',
    subjectId: 'S001',
  },
  {
    account: 'patient',
    password: DEMO_PASSWORD,
    role: 'patient',
    displayName: '患者001',
    scope: '本人病历',
    subjectId: 'P001',
  },
]

export interface Session {
  account: string
  role: UserRole
  displayName: string
  scope: string
  token: Token
}

function toSession(a: DemoAccount): Session {
  return {
    account: a.account,
    role: a.role,
    displayName: a.displayName,
    scope: a.scope,
    // 管理员的查询以医护令牌发起——后端 Token.type 只有 staff/patient
    // 两类（设计文档 §1.2）。管理员的「管理」能力体现在页面准入上，
    // 不体现在数据权限上，故不绑定工号。
    token: {
      type: a.role === 'patient' ? 'patient' : 'staff',
      subject_id: a.subjectId,
    },
  }
}

const STORAGE_KEY = 'medguard.session'

interface AuthCtx {
  session: Session | null
  signIn: (account: string, password: string) => Session | null
  signOut: () => void
}

const Ctx = createContext<AuthCtx | null>(null)

function restore(): Session | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as Session) : null
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  // 用 sessionStorage 而非 localStorage：刷新页面不丢登录态（演示时
  // 误按 F5 不至于重来），但关掉标签页即登出。
  const [session, setSession] = useState<Session | null>(restore)

  const signIn = useCallback((account: string, password: string): Session | null => {
    const found = DEMO_ACCOUNTS.find(
      (a) => a.account === account.trim() && a.password === password,
    )
    if (!found) return null
    const next = toSession(found)
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
