import { useEffect, useRef, useState, type FormEvent } from 'react'
import { runDirectQuery, runQuery } from '../api/query'
import type { QueryResponse } from '../api/types'
import { AliasProvider } from '../store/aliases'
import { useAuth } from '../store/auth'
import { presetsFor } from '../store/presets'
import { AdmissionPanel } from '../components/AdmissionPanel'
import { DegradationBadge } from '../components/DegradationBadge'
import { ResultTable } from '../components/ResultTable'

const DATASOURCE = 'regional_health'

export function ConsolePage() {
  return (
    <AliasProvider datasourceId={DATASOURCE}>
      <Console />
    </AliasProvider>
  )
}

/** 提问后多久还没回来，就提示"可能在翻译新问法"。
 *  首次提问要调模型，实测约 70 秒——不说明的话用户会以为卡死了。 */
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
  const inputRef = useRef<HTMLInputElement>(null)

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

  if (!session) return null
  const { token } = session
  const presets = presetsFor(token.type)

  const send = async (fn: () => Promise<QueryResponse>) => {
    setRunning(true)
    setError(null)
    setData(null)
    try {
      setData(await fn())
    } catch (e) {
      setError(String(e))
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

  const denied = data !== null && !data.admission.passed

  return (
    <div className="page console">
      <h2>查询控制台</h2>

      <p className="hint">
        用大白话提问即可。全过程<strong>零大模型调用、零数据库访问</strong>完成安全审计，
        审计通过后才执行查询。
      </p>

      <form className="ask-bar" onSubmit={askFreeText}>
        <input
          ref={inputRef}
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="例如：糖尿病患者产生了多少费用"
          aria-label="提问"
          disabled={running}
        />
        <button type="submit" disabled={running || !text.trim()}>
          提问
        </button>
      </form>

      <div className="preset-bar">
        <span className="preset-label">常用问题</span>
        {presets.map((p) => (
          <button key={p.id} onClick={() => askPreset(p.id)} disabled={running}>
            {p.question}
          </button>
        ))}
      </div>

      {showBusy && (
        <div className="loading" role="status" aria-live="polite">
          {slow
            ? '正在翻译这个新问法……首次提问约需一分钟，之后就快了。'
            : '处理中…'}
        </div>
      )}
      {error && (
        <div className="alert-error" role="alert">
          {error}
        </div>
      )}

      {data && (
        <>
          <div className="console-head">
            <h3>{data.question}</h3>
            <DegradationBadge degradation={data.degradation} />
          </div>

          {denied ? (
            <>
              {/* 被拒绝时，拒答文案本身就是答案 */}
              <AdmissionPanel admission={data.admission} />
              <div className="deny-note">
                查询在执行前被拒绝——未访问数据库，未产生结果集。
              </div>
            </>
          ) : (
            <>
              {data.result && (
                <section className="panel panel-result">
                  <h4>查询结果</h4>
                  <ResultTable result={data.result} />
                </section>
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

          {/* 审计明细（准入判定 / 分解方案 / 安全事件 / SQL 改写）不在这里——
              它们的去处是「安全报告」页，那里可以逐条回看每一次查询的完整证据。 */}
          <p className="hint console-footnote">
            本次查询的安全审计明细已记入<strong>安全报告</strong>，可随时回看。
          </p>

          <div className="metrics-bar">
            <span>
              审计耗时 <strong>{data.metrics.elapsed_ms} ms</strong>
            </span>
            <span>
              LLM 调用 <strong>{data.metrics.llm_calls}</strong>
            </span>
            <span>
              数据库访问 <strong>{data.metrics.db_access}</strong>
            </span>
            <span className="metrics-note">审计阶段零模型调用、零数据库访问</span>
          </div>
        </>
      )}
    </div>
  )
}
