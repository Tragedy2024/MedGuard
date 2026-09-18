/**
 * 用户管理——**管理员视图**。
 *
 * 医院里给人开账号是信息科的活。患者和医生不该能自己把自己注册成医生，
 * 所以这里没有自助注册，只有这一个入口——后端也把权限收在 `require_admin`
 * 里：页面准入只是第二层，第一层在接口上（非管理员令牌调用建号返回 403）。
 *
 * 页面只用既有样式类（.card / .field / .kv / .hint），不引入新的视觉语言。
 */
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { fetchUsers, registerUser } from '../api/auth'
import { errorText } from '../api/client'
import type { UserInfo, UserRole } from '../api/models'
import { useAuth } from '../store/auth'

const ROLES: { value: UserRole; label: string; hint: string }[] = [
  { value: 'patient', label: '病患', hint: '必须绑定患者编号（如 P001），否则无从保证只读取本人数据' },
  { value: 'staff', label: '医护人员', hint: '绑定工号（如 S002），「我的患者」这类问法据此解析"我"是谁' },
  { value: 'admin', label: '管理员', hint: '不绑定主体——管理能力体现在页面准入与建号权限' },
]

const ROLE_TEXT: Record<UserRole, string> = {
  admin: '管理员',
  staff: '医护人员',
  patient: '病患',
}

export function UserAdminPage() {
  const { session } = useAuth()
  const [users, setUsers] = useState<UserInfo[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<UserRole>('patient')
  const [subjectId, setSubjectId] = useState('')
  const [displayName, setDisplayName] = useState('')

  const reload = useCallback(async () => {
    if (!session) return
    try {
      setUsers(await fetchUsers(session.token))
    } catch (e) {
      setError(errorText(e))
    }
  }, [session])

  useEffect(() => {
    void reload()
  }, [reload])

  // 未登录时外壳渲染的是登录页；非管理员进不来（App.tsx 的路由守卫）。
  if (!session) return null

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const created = await registerUser({
        token: session.token,
        account: account.trim(),
        password,
        role,
        // 管理员不绑主体；其余角色留空即不绑（病患会在后端被拒）
        subject_id: role === 'admin' ? null : subjectId.trim() || null,
        display_name: displayName.trim() || account.trim(),
      })
      setNotice(`账号「${created.account}」已创建。`)
      setAccount('')
      setPassword('')
      setSubjectId('')
      setDisplayName('')
      await reload()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const current = ROLES.find((r) => r.value === role) ?? ROLES[0]

  return (
    <div className="page">
      <h2>用户管理</h2>
      <p className="hint">
        账号存在平台元数据库里，口令以 <strong>PBKDF2 哈希</strong>保存（不是明文）。
        角色决定能进哪些页面，绑定主体决定能查到什么数据——两者正交。
      </p>

      {error && <div className="alert-error">{error}</div>}
      {notice && <div className="hint">{notice}</div>}

      <div className="card">
        <h3>新建账号</h3>
        <form onSubmit={submit}>
          <div className="field">
            <label className="field-label" htmlFor="nu-account">账号</label>
            <input id="nu-account" value={account} autoComplete="off"
                   onChange={(e) => setAccount(e.target.value)}
                   placeholder="如 nurse002" />
          </div>

          <div className="field">
            <label className="field-label" htmlFor="nu-password">口令</label>
            <input id="nu-password" type="password" value={password}
                   autoComplete="new-password"
                   onChange={(e) => setPassword(e.target.value)}
                   placeholder="至少 6 位" />
          </div>

          <div className="field">
            <span className="field-label">角色</span>
            {ROLES.map((r) => (
              <label key={r.value}>
                <input type="radio" name="role" value={r.value}
                       checked={role === r.value}
                       onChange={() => setRole(r.value)} />
                {' '}{r.label}
              </label>
            ))}
            <p className="hint">{current.hint}</p>
          </div>

          {role !== 'admin' && (
            <div className="field">
              <label className="field-label" htmlFor="nu-subject">
                绑定主体{role === 'patient' ? '（必填）' : '（选填）'}
              </label>
              <input id="nu-subject" value={subjectId} autoComplete="off"
                     onChange={(e) => setSubjectId(e.target.value)}
                     placeholder={role === 'patient' ? '如 P002' : '如 S002'} />
            </div>
          )}

          <div className="field">
            <label className="field-label" htmlFor="nu-display">显示名（选填）</label>
            <input id="nu-display" value={displayName} autoComplete="off"
                   onChange={(e) => setDisplayName(e.target.value)}
                   placeholder="留空则用账号名" />
          </div>

          <button type="submit" className="login-submit" disabled={busy}>
            {busy ? '创建中……' : '创建账号'}
          </button>
        </form>
      </div>

      <div className="card">
        <h3>已有账号（{users.length}）</h3>
        <table className="policy-table">
          <thead>
            <tr>
              <th>账号</th>
              <th>角色</th>
              <th>绑定主体</th>
              <th>显示名</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.account}>
                <td><code>{u.account}</code></td>
                <td>{ROLE_TEXT[u.role]}</td>
                <td>{u.subject_id ? <code>{u.subject_id}</code> : '—'}</td>
                <td>{u.display_name || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="hint">
          口令哈希不在接口返回里——<code>/api/auth/users</code> 只给公开字段。
        </p>
      </div>
    </div>
  )
}
