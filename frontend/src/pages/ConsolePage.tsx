import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { runDirectQuery, runQuery } from '../api/query'
import type { PresetQuery, QueryResponse } from '../api/models'
import { AliasProvider } from '../store/aliases'
import { useAuth } from '../store/auth'
import { presetsFor } from '../store/presets'
import { AdmissionPanel } from '../components/AdmissionPanel'
import { DegradationBadge } from '../components/DegradationBadge'
import { ResultTable } from '../components/ResultTable'
import { errorStatus, errorText } from '../api/client'

const DATASOURCE = 'regional_health'

export function ConsolePage() {
  return (
    <AliasProvider datasourceId={DATASOURCE}>
      <Console />
    </AliasProvider>
  )
}

/** 可用问法按钮组。常驻工具条与撞墙后的出路共用，避免两处漂移。 */
function PresetButtons({ presets, running, onPick }: {
  presets: PresetQuery[]
  running: boolean
  onPick: (id: string) => void
}) {
  return (
    <div className="preset-list">
      {presets.map((p) => (
        <button key={p.id} className="preset-chip" type="button"
                onClick={() => onPick(p.id)} disabled={running}>
          {p.question}
        </button>
      ))}
    </div>
  )
}

/** 提问后多久还没回来，就提示"可能在翻译新问法"。 */
const SLOW_HINT_MS = 5000
/** 快查询（1–9ms）不该闪一下加载态，那比不显示更糟。 */
const BUSY_DELAY_MS = 150

function Console() {
  const { session } = useAuth()
  const [text, setText] = useState('')
  const [data, setData] = useState<QueryResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [showBusy, setShowBusy] = useState(false)
  const [slow, setSlow] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [errStatus, setErrStatus] = useState<number | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (!running) {
      setShowBusy(false)
      setSlow(false)
      return
    }
    const a = setTimeout(() => setShowBusy(true), BUSY_DELAY_MS)
    const b = setTimeout(() => setSlow(true), SLOW_HINT_MS)
    return () => {
      clearTimeout(a)
      clearTimeout(b)
    }
  }, [running])

  useEffect(() => {
    const field = inputRef.current
    if (!field) return
    field.style.height = 'auto'
    field.style.height = `${Math.min(field.scrollHeight, 164)}px`
  }, [text])

  if (!session) return null
  const { token } = session
  const presets = presetsFor(token.type)

  const send = async (fn: () => Promise<QueryResponse>) => {
    setRunning(true)
    setError(null)
    setErrStatus(null)
    setData(null)
    try {
      setData(await fn())
    } catch (e) {
      setError(errorText(e))
      setErrStatus(errorStatus(e))
    } finally {
      setRunning(false)
    }
  }

  const askFreeText = (e: FormEvent) => {
    e.preventDefault()
    const q = text.trim()
    if (!q) {
      inputRef.current?.focus()
      return
    }
    void send(() => runDirectQuery({ token, datasource_id: DATASOURCE, question: q }))
  }

  const askPreset = (questionId: string) =>
    send(() => runQuery({ token, datasource_id: DATASOURCE, question_id: questionId }))

  const submitOnEnter = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    event.currentTarget.form?.requestSubmit()
  }

  const denied = data !== null && !data.admission.passed
  const showStart = !data && !showBusy && !error

  return (
    <section className="view is-active" aria-labelledby="askTitle">
      <div className="ask-head">
        <div className="ask-head-copy">
          <h2 id="askTitle">查找院内信息</h2>
          <p className="ask-head-note">自然语言提问，系统先审计查询计划，通过后再只读访问主库。</p>
        </div>
        <span className="ask-scope">范围 · <strong>{session.scope}</strong></span>
      </div>

      <form onSubmit={askFreeText} noValidate>
        <div className={`query-composer ${running ? 'is-busy' : ''}`}>
          <span className="composer-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
              <circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 4.2 4.2" />
            </svg>
          </span>
          <div className="composer-field">
            <label className="composer-label" htmlFor="queryInput">
              输入问题 <span>支持自然语言，不必写 SQL</span>
            </label>
            <textarea
              ref={inputRef}
              id="queryInput"
              rows={1}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={submitOnEnter}
              placeholder="例如：统计各科室接诊量"
              aria-describedby="queryHelp"
              disabled={running}
            />
          </div>
          <button className="query-submit" type="submit" disabled={running || !text.trim()}>
            <span>{running ? '处理中' : '开始查询'}</span>
            <span className="action-arrow" aria-hidden="true">↗</span>
          </button>
        </div>
      </form>
      <div className="query-help" id="queryHelp">
        <span>审计阶段不调用模型、不访问数据库；结果查询以只读方式访问主库。</span>
        <span className="kbd">ENTER 提交 · SHIFT + ENTER 换行</span>
      </div>

      <div className="preset-row">
        <span className="preset-label">常用问法</span>
        <PresetButtons presets={presets} running={running} onPick={askPreset} />
      </div>

      <div className="result-shell">
        {showBusy && (
          <div className="processing" role="status" aria-live="polite">
            <div className="processing-ring" aria-hidden="true" />
            <div>
              <h3>{slow ? '正在翻译这个新问法' : '正在审计查询计划'}</h3>
              <p>
                {slow
                  ? '首次提问约需一分钟，之后就快了。审计阶段仍不调用模型、不访问数据库。'
                  : '先判断访问边界，再检查中间结果是否会暴露受控列。'}
              </p>
            </div>
          </div>
        )}

        {error && <div className="alert-error" role="alert">{error}</div>}

        {showStart && (
          <div className="result-empty console-start">
            <p>结果会显示在这里，并附上来源、审计过程与执行状态。</p>
            <p>
              自由提问首次约需一分钟；常用问法毫秒返回。两种方式都会先经过
              <strong> 层一准入 </strong>与<strong> 层二审计 </strong>。
            </p>
          </div>
        )}

        {data && (
          <section className={`result-panel ${denied ? '' : 'panel-result'}`} aria-live="polite">
            <div className="result-head">
              <div>
                <div className="result-kicker">
                  <span>QUERY / 审计记录</span>
                  <span className="dot" />
                  <DegradationBadge degradation={data.degradation} />
                </div>
                <h3>{data.question}</h3>
                <p className="result-meta">
                  {denied
                    ? '查询在执行前被拒绝，未访问数据库，未产生结果集。'
                    : '查询已通过安全审计，并以只读方式访问医院主库。'}
                </p>
              </div>
              <span className={`result-status ${denied ? 'denied' : 'ok'}`}>
                {denied ? '已拒绝' : '已通过'}
              </span>
            </div>

            <div className="result-body">
              {denied ? (
                <>
                  <AdmissionPanel admission={data.admission} />
                  <p className="metrics-caveat">被拒绝的查询不进入执行阶段，也不会在安全报告中留下结果集。</p>
                </>
              ) : (
                <>
                  {data.result && (
                    <div className="data-table-wrap">
                      <ResultTable result={data.result} />
                    </div>
                  )}
                  {data.degradation.message_cn && (
                    <div className="degradation-message">
                      {data.degradation.message_cn}
                      {data.degradation.message &&
                        data.degradation.message !== data.degradation.message_cn && (
                          <details className="tech-detail">
                            <summary>技术详情</summary>
                            <pre>{data.degradation.message}</pre>
                          </details>
                        )}
                    </div>
                  )}
                </>
              )}
            </div>
          </section>
        )}
      </div>

      {data && (
        <>
          <p className="hint console-footnote">
            本次查询的安全审计明细已记入<strong>安全报告</strong>，可随时回看。
          </p>
          <div className="metrics-bar">
            <span className="metrics-scope">审计阶段</span>
            <span>耗时 <strong>{data.metrics.elapsed_ms} ms</strong></span>
            <span>LLM 调用 <strong>{data.metrics.llm_calls}</strong></span>
            <span>数据库访问 <strong>{data.metrics.db_access}</strong></span>
            <span className="metrics-note">纯静态分析，不调模型、不碰数据库</span>
          </div>
        </>
      )}

      {(errStatus === 503 || denied) && presets.length > 0 && (
        <section className="next-steps">
          <h4>{denied ? '你还可以问这些' : '试试这些已收录的问法'}</h4>
          <PresetButtons presets={presets} running={running} onPick={askPreset} />
        </section>
      )}
    </section>
  )
}
