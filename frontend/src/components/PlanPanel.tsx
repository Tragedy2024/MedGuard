/**
 * 分解方案面板——逐个子查询列出，违规处高亮。
 *
 * 主体走中文业务语言（违规类型 + 业务字段名来自事件），算法返回的英文
 * description 折叠进「技术详情」。理由同 SqlDiff。
 */
import type { PlanItem, SecurityEvent } from '../api/models'
import { useAliases } from '../store/aliases'
import { SqlDiff } from './SqlDiff'

export function PlanPanel({
  plan,
  events,
}: {
  plan: PlanItem[]
  events: SecurityEvent[]
}) {
  const { aliasOf } = useAliases()

  if (plan.length === 0) return null

  // events[].sub_query_id 匹配的是**原始**子查询 id（字符串），与 plan[].id
  // 在 Rule C 拆分/消除时不一定一一对应——对不上属正常，不是 bug。
  const eventsOf = (id: number) => events.filter((e) => e.sub_query_id === String(id))

  return (
    <div className="plan-panel">
      {plan.map((sq) => {
        const hits = eventsOf(sq.id)
        return (
          <div
            key={sq.id}
            className={`plan-item ${hits.length > 0 ? 'has-violation' : ''}`}
          >
            <div className="plan-head">
              <span className="plan-id">#{sq.id}</span>
              {sq.is_final && <span className="plan-final">最终答案</span>}
              {hits.length > 0 && (
                <span className="plan-violation-count">{hits.length} 项违规</span>
              )}
            </div>

            {hits.length > 0 && (
              <ul className="plan-hits">
                {hits.map((h, i) => (
                  <li key={i}>
                    <span className="plan-hit-type">{h.type_label}</span>
                    <strong>{aliasOf(h.column)}</strong>
                  </li>
                ))}
              </ul>
            )}

            <SqlDiff
              before={sq.sql_before}
              after={sq.sql_after}
              violations={hits.map((h) => ({ column: h.column, label: aliasOf(h.column) }))}
            />

            {sq.description && (
              <details className="tech-detail">
                <summary>技术详情</summary>
                <pre>{sq.description}</pre>
              </details>
            )}
          </div>
        )
      })}
    </div>
  )
}
