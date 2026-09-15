/** 会话栏——显示当前登录身份与数据范围，并提供退出。
 *
 * 取代了早先的令牌切换器：身份不能再"随手切"，必须重新登录。
 */
import { useAuth, type UserRole } from '../store/auth'

const ROLE_LABEL: Record<UserRole, string> = {
  admin: '医院管理员',
  staff: '医护人员',
  patient: '病患',
}

export function SessionBar() {
  const { session, signOut } = useAuth()
  if (!session) return null

  return (
    <div className="session-bar">
      <div className="session-id">
        <span className={`role-tag role-${session.role}`}>{ROLE_LABEL[session.role]}</span>
        <span className="session-name">{session.displayName}</span>
      </div>
      <span className="session-scope">{session.scope}</span>
      <button type="button" className="session-signout" onClick={signOut}>
        退出
      </button>
    </div>
  )
}
