import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { errorText } from '../api/client'
import { MouseParticles } from '../components/MouseParticles'
import { DEMO_ACCOUNTS, DEMO_PASSWORD, useAuth } from '../store/auth'

type FieldErrors = { account?: string; password?: string }

function StoryReveal({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`story-reveal ${className}`} data-reveal>{children}</div>
}

/** 未登录首页：先讲清楚“为什么需要医盾”，登录是故事的终点。 */
export function LoginPage() {
  const { signIn } = useAuth()
  const navigate = useNavigate()
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [pending, setPending] = useState(false)

  useEffect(() => {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const elements = Array.from(document.querySelectorAll<HTMLElement>('[data-reveal]'))
    if (reduceMotion || !('IntersectionObserver' in window)) {
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
    }, { threshold: 0.18 })
    elements.forEach((element) => observer.observe(element))
    return () => observer.disconnect()
  }, [])

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
    <div className="story-home">
      <MouseParticles />
      <a className="skip-link" href="#story-login">跳到登录</a>

      <header className="story-nav">
        <a className="story-brand" href="#story-top" aria-label="医盾首页顶部">
          <span className="story-brand-mark" aria-hidden="true">盾</span>
          <span><strong>MedGuard 医盾</strong><small>医患信息数据服务云平台</small></span>
        </a>
        <a className="story-login-link" href="#story-login">登录平台 <span aria-hidden="true">↓</span></a>
      </header>

      <main>
        <section className="story-hero story-background" id="story-top">
          <div className="story-hero-glow" aria-hidden="true" />
          <StoryReveal className="story-hero-copy">
            <p className="story-kicker">01 / 背景</p>
            <h1>当健康出现疑问，<br />查询是第一反应。</h1>
            <p className="story-lead">
              从身体不适、报告指标到就医准备，人们越来越习惯先在线寻找答案。
              信息触手可及，但医疗问题需要的不只是“搜得到”，更是可信、清楚和及时。
            </p>
          </StoryReveal>

          <div className="search-chaos" aria-hidden="true">
            <span className="search-query">这个症状严重吗？</span>
            <span className="search-fragment fragment-1">可能只是休息不足</span>
            <span className="search-fragment fragment-2">建议立即就医</span>
            <span className="search-fragment fragment-3">相似经历分享</span>
            <span className="search-fragment fragment-4">相关内容 10,000+</span>
            <span className="search-fragment fragment-5">推荐治疗方案</span>
          </div>

          <a className="story-scroll" href="#story-problem">向下，看看问题从哪里开始 <span aria-hidden="true">↓</span></a>
        </section>

        <section className="story-section story-problem" id="story-problem">
          <StoryReveal className="story-section-copy">
            <p className="story-index">02 / 问题引出</p>
            <h2>信息很多，<br />有效帮助却很分散。</h2>
            <p>医学名词、患者经验、营销内容与过时资料混在一起；疾病知识、报告理解、科室方向与院内服务又散落在不同入口。用户反复搜索、切换和判断，仍然不知道下一步该做什么。</p>
          </StoryReveal>
          <StoryReveal className="problem-grid">
            <article><small>01</small><strong>信息过于冗杂</strong><p>相似内容不断重复，关键结论被噪声淹没。</p></article>
            <article><small>02</small><strong>权威来源难找</strong><p>来源、时间与适用边界不清，用户难以判断可信度。</p></article>
            <article><small>03</small><strong>医院联系不稳定</strong><p>知识查询和院内服务彼此割裂，信息获取不够及时。</p></article>
          </StoryReveal>
        </section>

        <section className="story-question story-thinking">
          <StoryReveal>
            <p>03 / 我们的思考</p>
            <h2>如果把寻找答案的过程，重新连成一条路？</h2>
            <div className="thinking-questions">
              <span>先找到依据，再组织回答</span>
              <span>在一个入口连接知识与服务</span>
              <span>让每次数据访问先经过安全判断</span>
            </div>
          </StoryReveal>
          <div className="converge-lines" aria-hidden="true"><span /><span /><span /><span /><b>医盾</b></div>
        </section>

        <section className="story-section story-purpose">
          <StoryReveal className="story-section-copy">
            <p className="story-index">04 / 网站目的</p>
            <h2>建立一个统一、可信、<br />安全的医疗信息入口。</h2>
            <p>MedGuard 医盾不是简单地再做一个搜索框。我们希望把医学知识查询、报告理解、就医方向和院内数据服务集中起来，让用户用自然语言提出问题，也能获得有来源、有边界、可继续行动的结果。</p>
          </StoryReveal>
          <StoryReveal className="purpose-path">
            <div><span>用户问题</span><small>症状 · 疾病 · 报告 · 院内信息</small></div>
            <i aria-hidden="true">↓</i>
            <div className="purpose-core"><span>MedGuard 医盾</span><small>统一理解、检索、审计与呈现</small></div>
            <i aria-hidden="true">↓</i>
            <div><span>清晰结果</span><small>依据可见 · 权限受控 · 下一步明确</small></div>
          </StoryReveal>
        </section>

        <section className="story-overview" id="story-overview">
          <StoryReveal className="overview-heading">
            <p className="story-index">05 / 网站简介 · 工作量</p>
            <h2>一个入口背后，<br />是数据、检索、服务与安全的协同。</h2>
            <p>我们没有从空白生成医学答案，而是对开放数据进行清洗、结构化和产品适配，再把检索增强、院内服务与隐私审计接入同一条查询链路。</p>
          </StoryReveal>

          <StoryReveal className="workload-stats">
            <div><strong>7,495</strong><span>疾病事实条目</span><small>OpenCMKG 衍生结构化数据</small></div>
            <div><strong>6,227</strong><span>症状—科室映射</span><small>用于导诊候选与覆盖扩展</small></div>
            <div><strong>15,305</strong><span>可选扩展检索文档</span><small>OpenCMKG 第二知识源，默认关闭</small></div>
            <div><strong>313</strong><span>后端自动化测试</span><small>覆盖查询、安全与服务链路</small></div>
          </StoryReveal>

          <StoryReveal className="dataset-volume-panel">
            <div className="dataset-volume-copy">
              <p className="story-index">DATASET VOLUME / 数据资产</p>
              <h3>开源数据经过清洗与适配，<br />形成四层知识规模。</h3>
              <p>柱体高度使用对数刻度，解决 91 到 15,305 的数量跨度；精确数量以每根柱体旁标注为准。</p>
              <div className="dataset-legend"><span>结构化数据</span><span>检索语料</span><span>默认核心</span></div>
            </div>

            <div className="dataset-3d-scene" role="img" aria-label="数据集规模三维柱状图：核心知识91条，症状科室映射6227条，疾病事实7495条，可选扩展检索文档15305条。柱高使用对数刻度。">
              <div className="dataset-grid-floor" aria-hidden="true" />
              <div className="dataset-column dataset-column-core">
                <div className="dataset-bar" aria-hidden="true" />
                <strong>91</strong><span>核心知识</span><small>YAML 条目</small>
              </div>
              <div className="dataset-column dataset-column-symptom">
                <div className="dataset-bar" aria-hidden="true" />
                <strong>6,227</strong><span>症状映射</span><small>结构化数据</small>
              </div>
              <div className="dataset-column dataset-column-disease">
                <div className="dataset-bar" aria-hidden="true" />
                <strong>7,495</strong><span>疾病事实</span><small>结构化数据</small>
              </div>
              <div className="dataset-column dataset-column-open">
                <div className="dataset-bar" aria-hidden="true" />
                <strong>15,305</strong><span>扩展文档</span><small>可选检索语料</small>
              </div>
            </div>
          </StoryReveal>

          <div className="workload-grid">
            <StoryReveal className="workload-card">
              <small>DATA / 数据适配</small><h3>从开放数据到可查询知识</h3>
              <p>完成抽取、污染清洗、别名归一、结构化索引与来源标记，让数据能够被产品稳定使用。</p>
            </StoryReveal>
            <StoryReveal className="workload-card">
              <small>RAG / 检索增强</small><h3>先检索依据，再组织回答</h3>
              <p>组合 BGE 向量检索、BM25 与 RRF 融合；模型不可用时可降级到离线 BM25，避免查询链路完全中断。</p>
            </StoryReveal>
            <StoryReveal className="workload-card">
              <small>SERVICE / 集中服务</small><h3>把四类需求放进一个入口</h3>
              <p>覆盖疾病知识、症状导诊、报告理解与院内数据查询，并区分知识库、院内记录和 AI 生成内容。</p>
            </StoryReveal>
            <StoryReveal className="workload-card workload-card-security">
              <small>SECURITY / 隐私保护</small><h3>查询执行前先过安全门</h3>
              <p>安全审计阶段不调用 LLM、不访问数据库；通过访问准入、静态审计、执行绑定与审计报告约束敏感数据访问。</p>
            </StoryReveal>
          </div>

          <StoryReveal className="overview-boundary">
            <strong>诚实的边界</strong>
            <p>平台提供医学信息检索与就医辅助，不替代执业医师诊断。OpenCMKG 扩展语料属于可选学术数据源，需完成医学安全评估后再决定是否默认启用。</p>
          </StoryReveal>
        </section>

        <section className="story-login" id="story-login">
          <StoryReveal className="story-login-intro">
            <span className="story-brand-mark story-brand-mark-large" aria-hidden="true">盾</span>
            <p className="story-kicker">06 / 登录平台</p>
            <h2>你的问题，<br />值得一个可靠的入口。</h2>
            <p>登录平台，获得集中、清晰、可追溯且受到安全保护的医疗信息服务。</p>
            <p className="medical-disclaimer">医盾提供医学信息检索与就医辅助，不替代执业医师的诊断、处方和紧急医疗服务。</p>
          </StoryReveal>

          <StoryReveal className="story-login-card">
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
          </StoryReveal>
        </section>
      </main>
    </div>
  )
}
