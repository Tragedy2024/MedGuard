import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { DEMO_ACCOUNTS, DEMO_PASSWORD, useAuth } from '../store/auth'

type FieldErrors = { account?: string; password?: string }

/**
 * 登录页。
 *
 * 左栏不放装饰图，放**产品机制本身**：一次查询如何在执行前被两层检查
 * 处理掉。这是访客在这一页唯一需要理解的东西——他们看到的不是一张图，
 * 是这台机器在工作。
 */
export function LoginPage() {
  const { signIn } = useAuth()
  const navigate = useNavigate()
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})

  const submit = (e: FormEvent) => {
    e.preventDefault()

    // 行内校验：空字段的错误贴在该字段下方，而不是只在顶部给一句笼统提示。
    // 只在提交时校验——每次失焦就弹错会让人没法正常输入。
    const errs: FieldErrors = {}
    if (!account.trim()) errs.account = '请输入账号'
    if (!password) errs.password = '请输入密码'
    setFieldErrors(errs)
    if (errs.account || errs.password) return

    if (!signIn(account, password)) {
      setFormError('账号或密码不正确。')
      return
    }
    setFormError(null)
    // 登录成功统一回到该角色的首页（'/'）。
    // 不这样做的后果：上一个账号停留的受限地址会跟着新账号——例如
    // admin 在 /policy（安全策略）退出，患者登录后仍停留在 /policy，
    // 而该路由只对管理员开放，患者会撞上「无权访问」。直接敲地址
    // 的越权拦截仍然保留（那是故意的纵深防御），这里只修登录场景。
    navigate('/', { replace: true })
  }

  const clearField = (k: keyof FieldErrors) =>
    setFieldErrors((f) => ({ ...f, [k]: undefined }))

  return (
    <div className="login">
      <aside className="login-brand">
        <div className="login-head">
          <span className="login-mark" aria-hidden="true">盾</span>
          <div className="login-head-text">
            <h1>医患信息数据服务云平台</h1>
            <p className="login-engine">安全引擎 · 医盾</p>
          </div>
        </div>

        <p className="login-tagline">
          让不会写 SQL 的医护人员和病患，
          <br />
          用大白话查到权威准确的医院数据。
        </p>

        {/* 机制演示：一次查询的审计过程。纯装饰，故 aria-hidden——
            屏幕阅读器用户不需要听一段动画。 */}
        <div className="demo" aria-hidden="true">
          <div className="demo-row">
            <span className="demo-tag demo-tag-1">层一 · 准入</span>
            <span className="demo-verdict demo-verdict-pass">放行 · 绑定本人</span>
          </div>

          <pre className="demo-sql">
            <span className="demo-kw">SELECT</span> test_name, result_value
            <span className="demo-struck">, patient_id</span>
            {'\n'}
            <span className="demo-kw">FROM</span> clinical_records
          </pre>

          <div className="demo-row">
            <span className="demo-tag demo-tag-2">层二 · 审计</span>
            <span className="demo-verdict demo-verdict-cut">移除 1 处不必要的列</span>
          </div>

          <div className="demo-metrics">
            <span>LLM 调用 <strong>0</strong></span>
            <span>数据库访问 <strong>0</strong></span>
          </div>
        </div>

        <p className="login-footnote">审计在查询执行之前完成 · 零模型调用 · 零数据库访问</p>
      </aside>

      <main className="login-panel">
        <form className="login-form" onSubmit={submit}>
          <h2>登录</h2>

          <div className="field">
            <label className="field-label" htmlFor="login-account">
              账号
            </label>
            <input
              id="login-account"
              type="text"
              value={account}
              autoComplete="username"
              autoFocus
              aria-invalid={fieldErrors.account ? true : undefined}
              aria-describedby={fieldErrors.account ? 'login-account-error' : undefined}
              onChange={(e) => {
                setAccount(e.target.value)
                clearField('account')
              }}
              placeholder="请输入账号"
            />
            {fieldErrors.account && (
              <p className="field-error" id="login-account-error">
                {fieldErrors.account}
              </p>
            )}
          </div>

          <div className="field">
            <label className="field-label" htmlFor="login-password">
              密码
            </label>
            <input
              id="login-password"
              type="password"
              value={password}
              autoComplete="current-password"
              aria-invalid={fieldErrors.password ? true : undefined}
              aria-describedby={fieldErrors.password ? 'login-password-error' : undefined}
              onChange={(e) => {
                setPassword(e.target.value)
                clearField('password')
              }}
              placeholder="请输入密码"
            />
            {fieldErrors.password && (
              <p className="field-error" id="login-password-error">
                {fieldErrors.password}
              </p>
            )}
          </div>

          {formError && (
            <div className="alert-error" role="alert">
              {formError}
            </div>
          )}

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
                    setFormError(null)
                    setFieldErrors({})
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
