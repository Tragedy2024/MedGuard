/**
 * 通用查询回答卡（患者单输入框路由到 `general_query` 时使用）。
 *
 * 与查询控制台（ConsolePage）共用同一套审计件：结果表、层一准入、降级徽章、
 * 审计指标。区别只在承载方式——这里嵌在智慧医生的对话气泡里，是「一个回答」，
 * 不是一台需要用户自己操作的查询台。
 *
 * 患者令牌问「得这个病的有多少人」这类问题时，层一会拒绝：此时不渲染结果表，
 * 只给拒答文案与「未访问数据库」的说明；审计指标仍然展示，因为「没有执行」
 * 本身也是可核对的证据。
 */
import { Link } from 'react-router-dom'
import type { QueryResponse } from '../api/models'
import { AdmissionPanel } from './AdmissionPanel'
import { DegradationBadge } from './DegradationBadge'
import { ResultTable } from './ResultTable'

export function QueryAnswer({
  query,
  error,
}: {
  query: QueryResponse | null
  error?: string | null
}) {
  const denied = query !== null && !query.admission.passed
  const rows = query?.result?.rows.length ?? 0

  return (
    <section className="query-answer" aria-label="通用查询回答">
      <header className="query-answer-head">
        <div className="query-answer-heading">
          <p className="query-answer-kicker">QUERY / 医院主库查询</p>
          <h4 className="query-answer-question">
            {query?.question ?? '本次查询没有完成'}
          </h4>
        </div>
        {query && (
          <div className="query-answer-badges">
            <DegradationBadge degradation={query.degradation} />
            <span className={`query-answer-status ${denied ? 'is-denied' : 'is-ok'}`}>
              {denied ? '层一拒绝' : '已通过审计'}
            </span>
          </div>
        )}
      </header>

      {error && (
        <div className="alert-error query-answer-error" role="alert">
          <strong>这次查询没有完成。</strong>
          <span>{error}</span>
        </div>
      )}

      {query && (
        <>
          <div className="query-answer-body">
            {denied ? (
              <>
                <AdmissionPanel admission={query.admission} />
                <p className="query-answer-note">
                  查询在执行前被拒绝：未访问数据库，未产生结果集。
                </p>
              </>
            ) : (
              <>
                {query.result ? (
                  <div className="data-table-wrap">
                    <ResultTable result={query.result} />
                  </div>
                ) : (
                  <div className="result-empty">查询已执行，无匹配数据。</div>
                )}
                {query.degradation.message_cn && (
                  <div className="degradation-message">
                    {query.degradation.message_cn}
                    {query.degradation.message &&
                      query.degradation.message !== query.degradation.message_cn && (
                        <details className="tech-detail">
                          <summary>技术详情</summary>
                          <pre>{query.degradation.message}</pre>
                        </details>
                      )}
                  </div>
                )}
              </>
            )}
          </div>

          <div className="metrics-bar query-answer-metrics">
            <span className="metrics-scope">审计阶段</span>
            <span>耗时 <strong>{query.metrics.elapsed_ms} ms</strong></span>
            <span>LLM 调用 <strong>{query.metrics.llm_calls}</strong></span>
            <span>数据库访问 <strong>{query.metrics.db_access}</strong></span>
            <span className="metrics-note">
              {denied
                ? '执行前拦截 · 零 LLM、零查库'
                : `已执行 · ${rows} 行结果`}
            </span>
          </div>

          <p className="query-answer-report">
            本次查询的审计明细已记入
            <Link to="/reports"> 安全报告 </Link>
            ，可随时回看准入判定与执行证据。
          </p>
        </>
      )}
    </section>
  )
}
