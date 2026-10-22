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
      <section className="view is-active">
        <div className="view-head">
          <div className="view-head-copy">
            <h2>安全事件报告</h2>
            <p className="view-sub">每次查询的完整安全证据。审计过程记录在这里。</p>
          </div>
        </div>
        <div className="alert-error">{error}</div>
      </section>
    )
  }

  return (
    <section className="view is-active" aria-labelledby="reportsTitle">
      <div className="view-head">
        <div className="view-head-copy">
          <h2 id="reportsTitle">审计证据，可以逐条回看。</h2>
          <p className="view-sub">
            查询控制台只呈现问题与结果；准入判定、分解方案、安全事件与 SQL 改写记录在这里。
          </p>
        </div>
        <div className="view-head-aside">
          <span className="quiet-badge">{reports.length} 份记录 · 只读</span>
        </div>
      </div>

      {reports.length === 0 ? (
        <div className="result-empty">
          <p>暂无记录。到可信问答提一次问，这里会留下对应的安全证据。</p>
        </div>
      ) : (
        <div className="reports-layout">
          <aside className="report-list-card">
            <div className="report-list-head">
              <strong>查询记录</strong>
              <span>最新在前 · {reports.length} 条</span>
            </div>
            <div className="report-list" aria-label="查询记录">
              {reports.map((r) => (
                <div className="report-item" key={r.id}>
                  <button
                    type="button"
                    className="report-select"
                    aria-pressed={detail?.id === r.id}
                    onClick={() => select(r.id)}
                  >
                    <span className="report-item-top">
                      <span className="report-id">#{String(r.id).padStart(4, '0')}</span>
                      <span className="report-time">{beijingTime(r.created_at)}</span>
                    </span>
                    <span className="report-q">{r.question}</span>
                    <span className="report-item-bottom">
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
                  </button>
                </div>
              ))}
            </div>
          </aside>

          {detail ? (
            <ReportBody detail={detail} token={session.token} />
          ) : (
            <section className="report-detail" aria-live="polite">
              <div className="report-detail-body">
                <p className="hint">正在读取审计证据…</p>
              </div>
            </section>
          )}
        </div>
      )}
    </section>
  )
}

/** 一次查询的完整证据。组件与控制台同源——同一份 payload，同一套展示。 */
function ReportBody({ detail, token }: { detail: ReportDetail; token: Token }) {
  const d = detail.payload
  const denied = !d.admission.passed

  return (
    <section className="report-detail" aria-live="polite">
      <header className="report-detail-head">
        <div className="report-detail-top">
          <div>
            <p className="eyebrow">SECURITY RECORD / {String(detail.id).padStart(4, '0')}</p>
            <h2>{d.question}</h2>
            <div className="report-detail-meta">
              <span>{TOKEN_TEXT[detail.token_type] ?? detail.token_type} 发起</span>
              <span>{beijingTime(detail.created_at)}</span>
              <span>{d.events.length} 个安全事件</span>
              <DegradationBadge degradation={d.degradation} />
            </div>
          </div>
          <a className="report-export" href={exportReportUrl(token, detail.id)} download>
            导出完整记录
          </a>
        </div>
      </header>

      <div className="report-detail-body">
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
              <section className="report-section">
                <h3>查询结果</h3>
                <ResultTable result={d.result} />
              </section>
            )}

            {d.degradation.message_cn && (
              <div className="degradation-message">{d.degradation.message_cn}</div>
            )}

            <section className="report-section">
              <h3>安全说明</h3>
              <AdmissionPanel admission={d.admission} />
            </section>

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

        <div className="audit-grid">
          <div className="audit-cell">
            <span>审计耗时</span>
            <strong>{d.metrics.elapsed_ms} ms</strong>
          </div>
          <div className="audit-cell">
            <span>LLM 调用</span>
            <strong className={d.metrics.llm_calls === 0 ? 'is-zero' : undefined}>{d.metrics.llm_calls}</strong>
          </div>
          <div className="audit-cell">
            <span>数据库访问</span>
            <strong className={d.metrics.db_access === 0 ? 'is-zero' : undefined}>{d.metrics.db_access}</strong>
          </div>
          <div className="audit-cell">
            <span>审计结论</span>
            <strong>{denied ? '已拒绝' : '已通过'}</strong>
          </div>
        </div>

        <p className="metrics-caveat">
          以上指标只统计<strong>安全审计</strong>那一段：医盾是纯静态分析，不调模型、不碰数据库。
          查询本身仍需以只读方式访问主库取数——那是执行环节，不计入本指标。
        </p>
      </div>
    </section>
  )
}
