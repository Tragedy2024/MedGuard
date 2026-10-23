import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { errorText } from '../api/client'
import { DEMO_ACCOUNTS, DEMO_PASSWORD, useAuth } from '../store/auth'

type FieldErrors = { account?: string; password?: string }

const DEMO_META = [
  { account: 'doctor', avatar: '医', note: '所辖患者群体 · 汇总与明细' },
  { account: 'patient', avatar: '患', note: '本人病历 · 报告与就医方向' },
  { account: 'admin', avatar: '管', note: '全院数据与安全策略' },
]

/** 登录页：左侧讲清身份与边界，右侧接通真实登录。 */
export function LoginPage() {
  const { signIn } = useAuth()
  const navigate = useNavigate()
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [selectedDemo, setSelectedDemo] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [status, setStatus] = useState('')
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [pending, setPending] = useState(false)
  const [paused, setPaused] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )
  const accountRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    document.title = '登录 · MedGuard 医盾'
  }, [])

  useEffect(() => {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const elements = Array.from(document.querySelectorAll<HTMLElement>('[data-reveal]'))
    if (reduce || !('IntersectionObserver' in window)) {
      elements.forEach((element) => element.classList.add('is-visible'))
      return
    }
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible')
          observer.unobserve(entry.target)
        }
      })
    }, { threshold: 0.12 })
    elements.forEach((element) => observer.observe(element))
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    document.documentElement.classList.toggle('motion-paused', paused)
    return () => document.documentElement.classList.remove('motion-paused')
  }, [paused])

  /* 左侧轨道随指针轻微偏移，不做自动旋转。 */
  useEffect(() => {
    const story = document.getElementById('authStory')
    const orbit = document.getElementById('authOrbit')
    if (!story || !orbit) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    const move = (event: PointerEvent) => {
      const rect = story.getBoundingClientRect()
      const x = (event.clientX - rect.left) / rect.width - 0.5
      const y = (event.clientY - rect.top) / rect.height - 0.5
      orbit.style.setProperty('--orbit-x', x.toFixed(2))
      orbit.style.setProperty('--orbit-y', y.toFixed(2))
    }
    const leave = () => {
      orbit.style.setProperty('--orbit-x', '0')
      orbit.style.setProperty('--orbit-y', '0')
    }
    story.addEventListener('pointermove', move, { passive: true })
    story.addEventListener('pointerleave', leave)
    return () => {
      story.removeEventListener('pointermove', move)
      story.removeEventListener('pointerleave', leave)
    }
  }, [])

  /* 左侧微尘。暂停时停在最后一帧，恢复后继续。 */
  useEffect(() => {
    const canvas = document.getElementById('authMotes') as HTMLCanvasElement | null
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let w = 0
    let h = 0
    let frame = 0
    let dots: { x: number; y: number; r: number; a: number; vx: number; vy: number }[] = []

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      w = canvas.clientWidth
      h = canvas.clientHeight
      canvas.width = Math.floor(w * dpr)
      canvas.height = Math.floor(h * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      dots = Array.from({ length: 34 }, () => ({
        x: Math.random() * w,
        y: Math.random() * h,
        r: 0.6 + Math.random() * 1.2,
        a: 0.05 + Math.random() * 0.12,
        vx: (Math.random() - 0.5) * 0.06,
        vy: -0.04 - Math.random() * 0.07,
      }))
    }

    const draw = () => {
      ctx.clearRect(0, 0, w, h)
      for (const dot of dots) {
        dot.x += dot.vx
        dot.y += dot.vy
        if (dot.y < -8) { dot.y = h + 8; dot.x = Math.random() * w }
        if (dot.x < -8) dot.x = w + 8
        if (dot.x > w + 8) dot.x = -8
        ctx.beginPath()
        ctx.fillStyle = `rgba(38, 58, 53, ${dot.a})`
        ctx.arc(dot.x, dot.y, dot.r, 0, Math.PI * 2)
        ctx.fill()
      }
      if (!paused) frame = requestAnimationFrame(draw)
    }

    resize()
    window.addEventListener('resize', resize)
    draw()

    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('resize', resize)
    }
  }, [paused])

  const clearField = (key: keyof FieldErrors) => {
    setFieldErrors((errors) => ({ ...errors, [key]: undefined }))
    setFormError(null)
    setStatus('')
  }

  const selectDemo = (key: string) => {
    setAccount(key)
    setPassword(DEMO_PASSWORD)
    setSelectedDemo(key)
    setFieldErrors({})
    setFormError(null)
    setStatus('')
    accountRef.current?.focus({ preventScroll: true })
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const errors: FieldErrors = {}
    if (!account.trim()) errors.account = '请输入账号'
    if (!password) errors.password = '请输入密码'
    setFieldErrors(errors)
    setFormError(null)
    setStatus('')
    if (errors.account || errors.password) {
      const target = errors.account ? accountRef : passwordRef
      target.current?.focus()
      return
    }

    setPending(true)
    setStatus('正在核验令牌与权限边界……')
    try {
      await signIn(account, password)
      navigate('/', { replace: true })
    } catch (err) {
      setFormError(errorText(err))
      setStatus('登录未通过，请核对账号与密码。')
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="auth-page">
      <a className="skip-link" href="#login-form">跳到登录表单</a>

      <main className="auth-shell">
        <section className="auth-story" id="authStory" aria-labelledby="auth-story-title">
          <div className="auth-story-top">
            <Link className="brand" to="/" aria-label="返回 MedGuard 首页">
              <span className="brand-mark" aria-hidden="true">盾</span>
              <span className="brand-words">
                <strong>MEDGUARD / 医盾</strong>
                <small>可信医疗信息入口</small>
              </span>
            </Link>
            <p className="eyebrow">SECURE ACCESS / 01</p>
          </div>

          <div className="auth-story-copy">
            <p className="eyebrow" data-reveal><span className="eyebrow-dot" />身份决定入口 · 令牌决定范围</p>
            <h1 id="auth-story-title" data-reveal>让每一次查询，<br />都从<em>可信</em>开始。</h1>
            <p className="lead" data-reveal>
              同一个问题，医生看到所辖患者的汇总，患者只看到本人记录。
              登录之后，身份与数据边界同时生效。
            </p>
            <div className="auth-pillars" data-reveal>
              <span>医院签发令牌</span><span>查询计划先审计</span><span>结果可回到来源</span>
            </div>
          </div>

          <div className="auth-orbit" id="authOrbit" aria-hidden="true">
            <span className="orbit-ring" /><span className="orbit-ring" /><span className="orbit-ring" />
            <span className="orbit-core">盾</span>
            <span className="orbit-node n1">医院主库</span>
            <span className="orbit-node n2">审核知识库</span>
            <span className="orbit-node n3">安全审计</span>
            <span className="orbit-node n4">下一步服务</span>
          </div>
          <canvas id="authMotes" aria-hidden="true" />

          <div className="auth-story-foot">
            <p>MedGuard 医盾提供医学信息检索与就医辅助，<br className="story-foot-break" />不替代执业医师的诊断、处方和紧急医疗服务。</p>
            <span className="story-code">NO SELF-SIGNUP / © 2026</span>
          </div>
        </section>

        <section className="auth-panel" aria-labelledby="login-title">
          <div className="auth-top">
            <Link className="auth-back" to="/"><span aria-hidden="true">←</span> 返回故事</Link>
            <p className="eyebrow">PLATFORM LOGIN</p>
          </div>

          <div className="login-wrap">
            <header className="login-intro" data-reveal>
              <h2 id="login-title">欢迎回来。</h2>
              <p>使用医院信息科分配的账号进入工作区。演示环境可直接选择下方任一身份。</p>
            </header>

            <form className="login-form" id="login-form" noValidate data-reveal style={{ '--delay': '.08s' } as CSSProperties} onSubmit={submit}>
              <div className="field">
                <div className="field-head"><label htmlFor="account">账号</label><small>ACCOUNT</small></div>
                <div className="field-control">
                  <input
                    ref={accountRef}
                    id="account"
                    name="account"
                    type="text"
                    autoComplete="username"
                    placeholder="请输入医院分配账号"
                    aria-invalid={fieldErrors.account ? true : undefined}
                    aria-describedby="account-error"
                    value={account}
                    onChange={(e) => {
                      setAccount(e.target.value)
                      setSelectedDemo(null)
                      clearField('account')
                    }}
                  />
                </div>
                <p className="field-error" id="account-error" hidden={!fieldErrors.account}>{fieldErrors.account}</p>
              </div>

              <div className="field has-toggle">
                <div className="field-head"><label htmlFor="password">密码</label><small>PASSWORD</small></div>
                <div className="field-control">
                  <input
                    ref={passwordRef}
                    id="password"
                    name="password"
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="current-password"
                    placeholder="请输入密码"
                    aria-invalid={fieldErrors.password ? true : undefined}
                    aria-describedby="password-error"
                    value={password}
                    onChange={(e) => {
                      setPassword(e.target.value)
                      setSelectedDemo(null)
                      clearField('password')
                    }}
                  />
                  <button
                    className="password-toggle"
                    type="button"
                    aria-label={showPassword ? '隐藏密码' : '显示密码'}
                    aria-pressed={showPassword}
                    onClick={() => {
                      setShowPassword((value) => !value)
                      passwordRef.current?.focus({ preventScroll: true })
                    }}
                  >
                    {showPassword ? '隐藏' : '显示'}
                  </button>
                </div>
                <p className="field-error" id="password-error" hidden={!fieldErrors.password}>{fieldErrors.password}</p>
                <p className="field-hint">没有自助注册。账号由医院信息科统一开通。</p>
              </div>

              <div className="form-alert" role="alert" hidden={!formError}>{formError}</div>
              <button className="primary-action login-submit" type="submit" disabled={pending}>
                <span>{pending ? '正在验证身份' : '安全登录'}</span>
                {pending ? <span className="spinner" aria-hidden="true" /> : <span className="action-arrow" aria-hidden="true">↗</span>}
              </button>
              <p className="login-status" role="status" aria-live="polite">{status}</p>
            </form>

            <section className="demo-block" aria-labelledby="demo-title" data-reveal style={{ '--delay': '.16s' } as CSSProperties}>
              <div className="demo-head">
                <strong id="demo-title">演示身份</strong>
                <span>点一下自动填入 · 密码 <code>{DEMO_PASSWORD}</code></span>
              </div>
              <div className="demo-list">
                {DEMO_META.map((meta) => {
                  const demo = DEMO_ACCOUNTS.find((item) => item.account === meta.account)
                  if (!demo) return null
                  return (
                    <button
                      key={meta.account}
                      className="demo-option"
                      type="button"
                      aria-pressed={selectedDemo === meta.account}
                      onClick={() => selectDemo(meta.account)}
                    >
                      <span className="demo-avatar" aria-hidden="true">{meta.avatar}</span>
                      <span className="demo-copy">
                        <strong>{demo.displayName}</strong>
                        <small>{meta.note}</small>
                      </span>
                      <span className="demo-account">{demo.account}</span>
                    </button>
                  )
                })}
              </div>
            </section>

            <div className="login-meta">
              <div className="login-foot">
                <svg viewBox="0 0 16 19" fill="none" aria-hidden="true"><path d="M8 1.2 14 3.6v4.9c0 4-2.7 6.9-6 8.3-3.3-1.4-6-4.3-6-8.3V3.6L8 1.2Z" stroke="currentColor" strokeWidth="1.2" /><path d="m5.3 8.7 1.8 1.8 3.6-3.9" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                <span>登录令牌由服务端签发。界面不保存明文口令，会话在关闭标签页后结束。</span>
              </div>
              <p className="login-disclaimer">演示账号与数据均为虚构，不含真实患者信息。<br />平台不替代执业医师的诊断与紧急医疗服务。</p>
              <button
                className="motion-toggle"
                type="button"
                aria-pressed={paused}
                onClick={() => setPaused((value) => !value)}
              >
                {paused ? '恢复背景动效' : '暂停背景动效'}
              </button>
            </div>
          </div>
        </section>
      </main>
    </div>
  )
}
