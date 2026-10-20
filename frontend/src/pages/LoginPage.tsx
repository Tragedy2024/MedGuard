import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { errorText } from '../api/client'
import { MouseParticles } from '../components/MouseParticles'
import { DEMO_ACCOUNTS, DEMO_PASSWORD, useAuth } from '../store/auth'

type FieldErrors = { account?: string; password?: string }

const STORY_SCENE_COUNT = 5
const STORY_SCENE_DURATION = 3600

/** 未登录首页：先讲清楚“为什么需要医盾”，登录是故事的终点。 */
export function LoginPage() {
  const { signIn } = useAuth()
  const navigate = useNavigate()
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [pending, setPending] = useState(false)
  const [sceneIndex, setSceneIndex] = useState(0)
  const [introComplete, setIntroComplete] = useState(false)

  useEffect(() => {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduceMotion) setIntroComplete(true)
  }, [])

  useEffect(() => {
    if (introComplete) return
    const timer = window.setTimeout(() => {
      if (sceneIndex >= STORY_SCENE_COUNT - 1) setIntroComplete(true)
      else setSceneIndex((index) => index + 1)
    }, STORY_SCENE_DURATION)
    return () => window.clearTimeout(timer)
  }, [introComplete, sceneIndex])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const errs: FieldErrors = {}
    if (!account.trim()) errs.account = '请输入账号'
    if (!password) errs.password = '请输入密码'
    setFieldErrors(errs)
    if (errs.account || errs.password) return

    setPending(true)
    setFormError(null)
    try {
      await signIn(account, password)
      navigate('/', { replace: true })
    } catch (err) {
      setFormError(errorText(err))
    } finally {
      setPending(false)
    }
  }

  const clearField = (key: keyof FieldErrors) =>
    setFieldErrors((errors) => ({ ...errors, [key]: undefined }))

  return (
    <div className="story-home story-cinematic-home">
      <MouseParticles />
      <button type="button" className="skip-link" onClick={() => setIntroComplete(true)}>跳到登录</button>

      <header className="story-nav">
        <button type="button" className="story-brand story-brand-button" onClick={() => { setSceneIndex(0); setIntroComplete(false) }} aria-label="重新播放医盾故事">
          <span className="story-brand-mark" aria-hidden="true">盾</span>
          <span><strong>MedGuard 医盾</strong><small>医患信息数据服务云平台</small></span>
        </button>
        <button type="button" className="story-login-link story-nav-action" onClick={() => {
          if (introComplete) {
            setSceneIndex(0)
            setIntroComplete(false)
          } else {
            setIntroComplete(true)
          }
        }}>{introComplete ? '重播动画 ↻' : '登录 →'}</button>
      </header>

      <main>
        {!introComplete ? (
          <section className={`cinematic-stage cinematic-stage-${sceneIndex + 1}`} id="story-top" aria-live="polite">
            <div className="cinematic-ambient" aria-hidden="true"><span /><span /><span /></div>
            <div className="cinematic-scene" key={sceneIndex}>
              {sceneIndex === 0 && (
                <div className="cinematic-layout cinematic-background">
                  <div className="cinematic-copy">
                    <p className="story-kicker">01 / 背景</p>
                    <h1>当健康出现疑问，<br />查询是第一反应。</h1>
                    <p>从身体不适、报告指标到就医准备，人们越来越习惯先在线寻找答案。但医疗问题需要的不只是“搜得到”，更是可信、清楚和及时。</p>
                  </div>
                  <div className="search-chaos" aria-hidden="true">
                    <span className="search-query">这个症状严重吗？</span>
                    <span className="search-fragment fragment-1">可能只是休息不足</span>
                    <span className="search-fragment fragment-2">建议立即就医</span>
                    <span className="search-fragment fragment-3">相似经历分享</span>
                    <span className="search-fragment fragment-4">相关内容 10,000+</span>
                    <span className="search-fragment fragment-5">推荐治疗方案</span>
                  </div>
                </div>
              )}

              {sceneIndex === 1 && (
                <div className="cinematic-layout cinematic-problem">
                  <div className="cinematic-copy">
                    <p className="story-index">02 / 问题引出</p>
                    <h2>信息很多，<br />有效帮助却很分散。</h2>
                    <p>来源难辨、内容重复，知识查询与院内服务又彼此割裂。用户反复搜索和切换，仍然不知道下一步该做什么。</p>
                  </div>
                  <div className="problem-grid">
                    <article><small>01</small><strong>信息过于冗杂</strong><p>关键结论被重复内容淹没。</p></article>
                    <article><small>02</small><strong>权威来源难找</strong><p>来源与适用边界难以判断。</p></article>
                    <article><small>03</small><strong>医院联系不稳定</strong><p>知识和院内服务缺少统一入口。</p></article>
                  </div>
                </div>
              )}

              {sceneIndex === 2 && (
                <div className="cinematic-thinking">
                  <p className="story-index">03 / 我们的思考</p>
                  <h2>如果把寻找答案的过程，<br />重新连成一条路？</h2>
                  <div className="thinking-questions">
                    <span>先找到依据，再组织回答</span>
                    <span>在一个入口连接知识与服务</span>
                    <span>让每次数据访问先经过安全判断</span>
                  </div>
                  <div className="converge-lines" aria-hidden="true"><span /><span /><span /><span /><b>医盾</b></div>
                </div>
              )}

              {sceneIndex === 3 && (
                <div className="cinematic-layout cinematic-purpose">
                  <div className="cinematic-copy">
                    <p className="story-index">04 / 网站目的</p>
                    <h2>建立一个统一、可信、<br />安全的医疗信息入口。</h2>
                    <p>把医学知识、报告理解、就医方向和院内数据服务集中起来，让自然语言问题得到有来源、有边界、可继续行动的结果。</p>
                  </div>
                  <div className="purpose-path">
                    <div><span>用户问题</span><small>症状 · 疾病 · 报告 · 院内信息</small></div>
                    <i aria-hidden="true">↓</i>
                    <div className="purpose-core"><span>MedGuard 医盾</span><small>理解 · 检索 · 审计 · 呈现</small></div>
                    <i aria-hidden="true">↓</i>
                    <div><span>清晰结果</span><small>依据可见 · 权限受控 · 下一步明确</small></div>
                  </div>
                </div>
              )}

              {sceneIndex === 4 && (
                <div className="cinematic-data">
                  <div className="cinematic-data-copy">
                    <p className="story-index">05 / 网站简介 · 数据资产</p>
                    <h2>开放数据经过清洗与适配，<br />成为可以查询的知识。</h2>
                    <p>柱高采用对数刻度，精确数量以数字标注为准。OpenCMKG 扩展语料默认关闭，需通过医学安全评估后启用。</p>
                  </div>
                  <div className="dataset-3d-scene cinematic-dataset" role="img" aria-label="数据集规模三维柱状图：核心知识91条，症状科室映射6227条，疾病事实7495条，可选扩展检索文档15305条。柱高使用对数刻度。">
                    <div className="dataset-grid-floor" aria-hidden="true" />
                    <div className="dataset-column dataset-column-core"><div className="dataset-bar" aria-hidden="true" /><strong>91</strong><span>核心知识</span><small>YAML 条目</small></div>
                    <div className="dataset-column dataset-column-symptom"><div className="dataset-bar" aria-hidden="true" /><strong>6,227</strong><span>症状映射</span><small>结构化数据</small></div>
                    <div className="dataset-column dataset-column-disease"><div className="dataset-bar" aria-hidden="true" /><strong>7,495</strong><span>疾病事实</span><small>结构化数据</small></div>
                    <div className="dataset-column dataset-column-open"><div className="dataset-bar" aria-hidden="true" /><strong>15,305</strong><span>扩展文档</span><small>可选检索语料</small></div>
                  </div>
                </div>
              )}
            </div>

            <div className="cinematic-controls">
              <div className="cinematic-progress" aria-label="故事进度">
                {Array.from({ length: STORY_SCENE_COUNT }, (_, index) => (
                  <button
                    type="button"
                    key={index}
                    className={index === sceneIndex ? 'is-active' : index < sceneIndex ? 'is-past' : ''}
                    onClick={() => setSceneIndex(index)}
                    aria-label={`播放第 ${index + 1} 幕`}
                    aria-current={index === sceneIndex ? 'step' : undefined}
                  ><span /></button>
                ))}
              </div>
              <p>{String(sceneIndex + 1).padStart(2, '0')} / 05</p>
              <button type="button" onClick={() => setIntroComplete(true)}>跳过动画</button>
            </div>
            <span className="cinematic-timer" key={`timer-${sceneIndex}`} aria-hidden="true" />
          </section>
        ) : (
          <section className="story-login cinematic-login" id="story-login">
            <div className="story-login-intro cinematic-login-intro">
              <span className="story-brand-mark story-brand-mark-large" aria-hidden="true">盾</span>
              <p className="story-kicker">MEDGUARD / 登录平台</p>
              <h2>你的问题，<br />值得一个可靠的入口。</h2>
              <p>知识、服务与安全已经汇聚。登录平台，获得集中、清晰、可追溯的医疗信息服务。</p>
              <p className="medical-disclaimer">医盾提供医学信息检索与就医辅助，不替代执业医师的诊断、处方和紧急医疗服务。</p>
              <button type="button" className="cinematic-replay" onClick={() => { setSceneIndex(0); setIntroComplete(false) }}>重新播放动画 ↻</button>
            </div>

            <div className="story-login-card cinematic-login-card">
              <form className="login-form" onSubmit={submit}>
                <div className="login-form-head"><p>平台入口</p><h2>登录</h2></div>
                <div className="field">
                  <label className="field-label" htmlFor="login-account">账号</label>
                  <input id="login-account" type="text" value={account} autoComplete="username" aria-invalid={fieldErrors.account ? true : undefined} aria-describedby={fieldErrors.account ? 'login-account-error' : undefined} onChange={(e) => { setAccount(e.target.value); clearField('account') }} placeholder="请输入账号" />
                  {fieldErrors.account && <p className="field-error" id="login-account-error">{fieldErrors.account}</p>}
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="login-password">密码</label>
                  <input id="login-password" type="password" value={password} autoComplete="current-password" aria-invalid={fieldErrors.password ? true : undefined} aria-describedby={fieldErrors.password ? 'login-password-error' : undefined} onChange={(e) => { setPassword(e.target.value); clearField('password') }} placeholder="请输入密码" />
                  {fieldErrors.password && <p className="field-error" id="login-password-error">{fieldErrors.password}</p>}
                </div>
                {formError && <div className="alert-error" role="alert">{formError}</div>}
                <button type="submit" className="login-submit" disabled={pending}>{pending ? '登录中……' : '进入平台'}</button>
              </form>

              <div className="demo-accounts">
                <div className="demo-accounts-head">演示账号 · 密码均为 <code>{DEMO_PASSWORD}</code></div>
                <ul>{DEMO_ACCOUNTS.map((demoAccount) => (
                  <li key={demoAccount.account}><button type="button" onClick={() => { setAccount(demoAccount.account); setPassword(DEMO_PASSWORD); setFormError(null); setFieldErrors({}) }}>
                    <code>{demoAccount.account}</code><span className="demo-role">{demoAccount.displayName}</span><span className="demo-scope">{demoAccount.scope}</span>
                  </button></li>
                ))}</ul>
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  )
}
