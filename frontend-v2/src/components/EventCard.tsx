/**
 * 安全事件卡——层二审计发现的每一次中间结果暴露。
 *
 * 卡片主体走**业务语言**：类型标签、严重度、业务字段名都是中文。
 * 算法返回的 `detail` 是英文且含物理列名（如
 * "patients.name (ECL=controlled) is SELECTed but not consumed by any"），
 * 对医护与病患只是噪音，故折叠进「技术详情」，默认不展开。
 */
import type { SecurityEvent } from '../api/types'
import { useAliases } from '../store/aliases'

export function EventCard({ event }: { event: SecurityEvent }) {
  const { aliasOf } = useAliases()
  const cn = aliasOf(event.column)
  const hasAlias = cn !== event.column

  return (
    <div className={`event-card sev-${event.severity}`}>
      <div className="event-head">
        <span className="event-type">{event.type_label}</span>
        <span className="event-sev">{event.severity_label}</span>
        <span className="event-sub">子查询 #{event.sub_query_id}</span>
      </div>

      <div className="event-column">
        {hasAlias ? <strong>{cn}</strong> : <code>{event.column}</code>}
      </div>

      {event.detail && (
        <details className="tech-detail">
          <summary>技术详情</summary>
          <pre>{event.detail}</pre>
        </details>
      )}
    </div>
  )
}
