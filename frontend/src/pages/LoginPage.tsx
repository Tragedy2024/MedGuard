import { useState, type FormEvent } from 'react'
import { DEMO_ACCOUNTS, DEMO_PASSWORD, useAuth } from '../store/auth'

export function LoginPage() {
  const { signIn } = useAuth()
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const s = signIn(account, password)
    if (!s) {
      setError('账号或密码不正确。')
      return
    }
    setError(null)
    // 不在这里 navigate——App 会因 session 出现而自动渲染应用外壳，
    // 避免登录页与主应用同时挂载造成闪烁。
  }

  return (
    <div className="login">
      <aside className="login-brand">
        <div className="login-mark">医盾</div>
        <h1>医患信息数据服务云平台</h1>
        <p className="login-tagline">
          让不会写 SQL 的医护人员和病患，<br />
          用大白话查到权威准确的医院数据。
        </p>
        <p className="login-note">安全引擎 · 医盾｜零 LLM 审计、零数据库访问</p>
      </aside>

      <main className="login-panel">
        <form className="login-form" onSubmit={submit}>
          <h2>登录</h2>

          <label className="field">
            <span className="field-label">账号</span>
            <input
              type="text"
              value={account}
              autoComplete="username"
              autoFocus
              onChange={(e) => setAccount(e.target.value)}
              placeholder="请输入账号"
            />
          </label>

          <label className="field">
            <span className="field-label">密码</span>
            <input
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              placeholder="请输入密码"
            />
          </label>

          {error && <div className="alert-error">{error}</div>}

          <button type="submit" className="login-submit">
            登录
          </button>
        </form>

        <div className="demo-accounts">
          <div className="demo-accounts-head">
            演示账号 · 密码均为 <code>{DEMO_PASSWORD}</code>
          </div>
          <ul>
            {DEMO_ACCOUNTS.map((a) => (
              <li key={a.account}>
                <button
                  type="button"
                  onClick={() => {
                    setAccount(a.account)
                    setPassword(DEMO_PASSWORD)
                    setError(null)
                  }}
                >
                  <code>{a.account}</code>
                  <span className="demo-role">{a.displayName}</span>
                  <span className="demo-scope">{a.scope}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </main>
    </div>
  )
}
