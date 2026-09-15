import { useEffect, useState } from 'react'
import {
  exportReportUrl,
  fetchDetectionMetrics,
  fetchReports,
} from '../api/reports'
import type { DetectionMetrics, ReportSummary, TokenType } from '../api/types'
import { DegradationBadge } from '../components/DegradationBadge'

const TOKEN_TEXT: Record<TokenType, string> = {
  staff: '医护人员',
  patient: '病患',
}

export function ReportPage() {
  const [reports, setReports] = useState<ReportSummary[]>([])
  const [metrics, setMetrics] = useState<DetectionMetrics | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchReports()
      .then(setReports)
      .catch((e) => setError(String(e)))
    fetchDetectionMetrics()
      .then(setMetrics)
      .catch((e) => setError(String(e)))
  }, [])

  return (
    <div className="page">
      <h2>安全事件报告</h2>
      {error && <div className="alert-error">{error}</div>}

      {metrics && (
        <section className="panel">
          <h4>检测效能</h4>
          <div className="metrics-grid">
            <Metric
              value={metrics.precision.value}
              name="精确率"
              detail={metrics.precision.detail}
            />
            <Metric
              value={metrics.recall.value}
              name="召回率"
              detail={metrics.recall.detail}
            />
            <Metric
              value={metrics.blocked_detection.value}
              name="禁止列检出"
              detail={metrics.blocked_detection.detail}
            />
          </div>
          <p className="hint">
            数据来源：<code>{metrics.source}</code>——论文仓库受控注入实验实测，
            非本产品自测，亦非杜撰。
          </p>
        </section>
      )}

      <section className="panel">
        <h4>历史记录</h4>
        {reports.length === 0 ? (
          <p className="hint">暂无记录。到查询控制台提一次问试试。</p>
        ) : (
          <div className="table-scroll">
          <table className="report-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>问题</th>
                <th>令牌</th>
                <th>降级</th>
                <th>事件</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.id}>
                  <td className="report-time">{r.created_at}</td>
                  <td>{r.question}</td>
                  <td>{TOKEN_TEXT[r.token_type] ?? r.token_type}</td>
                  <td>
                    <DegradationBadge
                      degradation={{
                        level: r.degradation_level,
                        label: '',
                        message: '',
                        message_cn: '',
                      }}
                    />
                  </td>
                  <td className="report-count">{r.event_count}</td>
                  <td>
                    <a className="report-export" href={exportReportUrl(r.id)} download>
                      导出
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </section>
    </div>
  )
}

/** 效能指标卡。0–100% 的比率，故用百分数呈现；数值由后端给出，前端不换算口径。 */
function Metric({
  value,
  name,
  detail,
}: {
  value: number
  name: string
  detail: string
}) {
  return (
    <div className="metric">
      <div className="metric-value">{(value * 100).toFixed(0)}%</div>
      <div className="metric-name">{name}</div>
      <div className="metric-detail">{detail}</div>
    </div>
  )
}
