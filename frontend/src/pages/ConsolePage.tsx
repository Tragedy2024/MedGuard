import { useState } from 'react'
import { runQuery } from '../api/query'
import type { QueryResponse } from '../api/types'
import { AliasProvider } from '../store/aliases'
import { useAuth } from '../store/auth'
import { presetsFor } from '../store/presets'
import { AdmissionPanel } from '../components/AdmissionPanel'
import { PlanPanel } from '../components/PlanPanel'
import { EventCard } from '../components/EventCard'
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

function Console() {
  const { session } = useAuth()
  const [data, setData] = useState<QueryResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!session) return null
  const { token } = session
  const presets = presetsFor(token.type)

  const ask = async (questionId: string) => {
    setRunning(true)
    setError(null)
    setData(null)
    try {
      setData(
        await runQuery({ token, datasource_id: DATASOURCE, question_id: questionId }),
      )
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  const denied = data !== null && !data.admission.passed

  return (
    <div className="page console">
      <h2>查询控制台</h2>

      <p className="hint">
        选择一个问题发起查询。全过程<strong>零大模型调用、零数据库访问</strong>，
        由医盾引擎静态审计后再执行。
      </p>

      <div className="preset-bar">
        {presets.map((p) => (
          <button key={p.id} onClick={() => ask(p.id)} disabled={running}>
            {p.question}
          </button>
        ))}
      </div>

      {running && <div className="loading">审计中…</div>}
      {error && <div className="alert-error">{error}</div>}

      {data && (
        <>
          <div className="console-head">
            <h3>{data.question}</h3>
            <DegradationBadge degradation={data.degradation} />
          </div>

          <AdmissionPanel admission={data.admission} />

          {denied ? (
            <div className="deny-note">
              查询在执行前被拒绝——未访问数据库，未产生结果集。
            </div>
          ) : (
            <div className="panels">
              <section className="panel">
                <h4>分解方案</h4>
                <PlanPanel plan={data.plan} events={data.events} />
              </section>

              <section className="panel">
                <h4>
                  安全事件 <span className="count">{data.events.length}</span>
                </h4>
                {data.events.length === 0 ? (
                  <p className="hint">未发现中间结果暴露。</p>
                ) : (
                  data.events.map((e, i) => <EventCard key={i} event={e} />)
                )}

                {/* 改写日志是算法层原文（英文 + 物理列名），对医护与病患
                    只是噪音；改动内容已由上方事件卡与左栏方案用中文表达。
                    故整段折叠进「技术详情」，技术观众仍可核对。 */}
                {data.rewrite.applied > 0 && (
                  <details className="tech-detail tech-detail-block">
                    <summary>技术详情 · 改写日志（{data.rewrite.applied} 处）</summary>
                    <ul className="rewrite-log">
                      {data.rewrite.log.map((line, i) => (
                        <li key={i}>{line}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </section>
            </div>
          )}

          {/* 层一拒绝时准入面板已经给出拒答文案（L3 的 message_cn 就是它），
              再重复一遍只是噪音。 */}
          {!denied && data.degradation.message_cn && (
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

          {data.result && (
            <section className="panel">
              <h4>查询结果</h4>
              <ResultTable result={data.result} />
            </section>
          )}

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
