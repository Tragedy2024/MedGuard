import { useEffect, useState } from 'react'
import { exportReportUrl, fetchReport, fetchReports } from '../api/reports'
import type { ReportDetail, ReportSummary, Token, TokenType } from '../api/models'
import { beijingTime } from '../lib/time'
import { AliasProvider } from '../store/aliases'
import { useAuth } from '../store/auth'
import { AdmissionPanel } from '../components/AdmissionPanel'
import { DegradationBadge } from '../components/DegradationBadge'
import { EventCard } from '../components/EventCard'
import { PlanPanel } from '../components/PlanPanel'
import { ResultTable } from '../components/ResultTable'
import { errorText } from '../api/client'

const DATASOURCE = 'regional_health'
const TOKEN_TEXT: Record<TokenType, string> = { staff: '医护人员', patient: '病患' }

export function ReportPage() {
  return (
    <AliasProvider datasourceId={DATASOURCE}>
      <Reports />
    </AliasProvider>
  )
}

/**
 * 安全事件报告 = **每次查询的安全证据**。
 *
 * 查询控制台只给问题与结果——用户要的是答案。审计过程（准入判定、分解
 * 方案、安全事件、SQL 改写）落在这里，可以逐条回看。
 *
 * 左侧流水、右侧详情，一屏内完成「选记录 → 看证据」，演示时不用跳页。
 */
function Reports() {
  const { session } = useAuth()
  const token = session?.token
  const [reports, setReports] = useState<ReportSummary[]>([])
  const [detail, setDetail] = useState<ReportDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  // 安全报告按令牌身份隔离：列表、详情、导出三处都要带上发起人身份，
  // 后端据此过滤。漏传会 422——身份不是可选项。
  const select = async (id: number) => {
    if (!token) return
    try {
      setDetail(await fetchReport(token, id))
    } catch (e) {
      setError(errorText(e))
    }
  }

  useEffect(() => {
    if (!token) return
    fetchReports(token)
      .then((list) => {
        setReports(list)
        if (list.length > 0) void select(list[0].id)
      })
      .catch((e) => setError(errorText(e)))
    // 只在挂载时拉一次列表（令牌在登录期内不变；换账号会重新登录并重挂载）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  // 未登录时 App 外壳渲染的是登录页，这里只是把类型收窄。
  if (!session) return null

  if (error) {
    return (
      <div className="page">
        <h2>安全事件报告</h2>
        <div className="alert-error">{error}</div>
      </div>
    )
  }

  return (
    <div className="page">
      <h2>安全事件报告</h2>
      <p className="hint">
        每次查询的完整安全证据。查询控制台只呈现问题与结果，审计过程记录在这里。
      </p>

      {reports.length === 0 ? (
        <p className="hint">暂无记录。到查询控制台提一次问试试。</p>
      ) : (
        <div className="report-split">
          <aside className="report-list" aria-label="查询记录">
            {reports.map((r) => (
              <button
                key={r.id}
                type="button"
                className={detail?.id === r.id ? 'active' : ''}
                aria-current={detail?.id === r.id ? 'true' : undefined}
                onClick={() => select(r.id)}
              >
                <span className="report-q">{r.question}</span>
                <span className="report-meta">
                  <DegradationBadge
                    degradation={{
                      level: r.degradation_level,
                      label: '',
                      message: '',
                      message_cn: '',
                    }}
                  />
                  <span className="report-sub">{TOKEN_TEXT[r.token_type] ?? r.token_type}</span>
                  <span className="report-sub">{r.event_count} 事件</span>
                </span>
                <span className="report-time">{beijingTime(r.created_at)}</span>
              </button>
            ))}
          </aside>

          <section className="report-detail" aria-live="polite">
            {detail ? (
              <ReportBody detail={detail} token={session.token} />
            ) : (
              <p className="hint">加载中…</p>
            )}
          </section>
        </div>
      )}
    </div>
  )
}

/** 一次查询的完整证据。组件与控制台同源——同一份 payload，同一套展示。 */
function ReportBody({ detail, token }: { detail: ReportDetail; token: Token }) {
  const d = detail.payload
  const denied = !d.admission.passed

  return (
    <>
      <div className="console-head">
        <h3>{d.question}</h3>
        <DegradationBadge degradation={d.degradation} />
      </div>

      {denied ? (
        <>
          {/* 被拒绝时，拒答文案本身就是答案 */}
          <AdmissionPanel admission={d.admission} />
          <div className="deny-note">
            查询在执行前被拒绝——未访问数据库，未产生结果集。
          </div>
        </>
      ) : (
        <>
          {/* 答案优先：与控制台同一套信息层级 */}
          {d.result && (
            <section className="panel panel-result">
              <h4>查询结果</h4>
              <ResultTable result={d.result} />
            </section>
          )}

          {d.degradation.message_cn && (
            <div className="degradation-message">{d.degradation.message_cn}</div>
          )}

          <h4 className="section-label">安全说明</h4>

          <AdmissionPanel admission={d.admission} />

          <div className="panels">
            <section className="panel">
              <h4>分解方案</h4>
              <PlanPanel plan={d.plan} events={d.events} />
            </section>

            <section className="panel">
              <h4>
                安全事件 <span className="count">{d.events.length}</span>
              </h4>
              {d.events.length === 0 ? (
                <p className="hint">未发现中间结果暴露。</p>
              ) : (
                d.events.map((e, i) => <EventCard key={i} event={e} />)
              )}

              {d.rewrite.applied > 0 && (
                <details className="tech-detail tech-detail-block">
                  <summary>技术详情 · 改写日志（{d.rewrite.applied} 处）</summary>
                  <ul className="rewrite-log">
                    {d.rewrite.log.map((line, i) => (
                      <li key={i}>{line}</li>
                    ))}
                  </ul>
                </details>
              )}
            </section>
          </div>
        </>
      )}

      <div className="metrics-bar">
        <span className="metrics-scope">审计阶段</span>
        <span>
          耗时 <strong>{d.metrics.elapsed_ms} ms</strong>
        </span>
        <span>
          LLM 调用 <strong>{d.metrics.llm_calls}</strong>
        </span>
        <span>
          数据库访问 <strong>{d.metrics.db_access}</strong>
        </span>
        <a className="metrics-export" href={exportReportUrl(token, detail.id)} download>
          导出完整记录
        </a>
      </div>
      <p className="metrics-caveat">
        以上三项只统计<strong>安全审计</strong>那一段：医盾是纯静态分析，
        不调模型、不碰数据库。查询本身仍需访问主库取数——那是执行环节，
        不计入本指标。
      </p>
    </>
  )
}
