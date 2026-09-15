/**
 * 可查范围——面向医护与病患的**业务视图**。
 *
 * 这一页刻意不出现任何物理表名、列名与数据类型。用户是非数据库专业人员
 * （设计文档 §1.2），展示 `clinical_records.impression TEXT` 对他们毫无
 * 意义，只会把产品变成开发者工具——会议记录 §1.1 已否决的形态。
 *
 * 数据全部由策略接口派生：table_aliases / column_aliases 给中文名，
 * column_labels 给可用性分级。所以这一页天然随策略变化，不会说谎。
 */
import { useEffect, useState } from 'react'
import { fetchPolicy } from '../api/policies'
import type { EclLabel, Policy } from '../api/models'
import { EclTag } from '../components/EclTag'
import { useAuth } from '../store/auth'
import { errorText } from '../api/client'

const CX = 'regional_health'

interface Domain {
  table: string
  name: string
  total: number
  queryable: string[]
  counts: Record<EclLabel, number>
}

function buildDomains(policy: Policy): Domain[] {
  return Object.entries(policy.column_labels).map(([table, cols]) => {
    const entries = Object.entries(cols)
    const counts: Record<EclLabel, number> = { free: 0, controlled: 0, blocked: 0 }
    const queryable: string[] = []
    for (const [col, label] of entries) {
      counts[label] = (counts[label] ?? 0) + 1
      if (label === 'free') {
        queryable.push(policy.column_aliases[table]?.[col] ?? col)
      }
    }
    return {
      table,
      name: policy.table_aliases[table] ?? table,
      total: entries.length,
      queryable,
      counts,
    }
  })
}

/** 只列出数量大于 0 的等级，避免「禁止 0」这种噪音。 */
const present = (counts: Record<EclLabel, number>): EclLabel[] =>
  (['free', 'controlled', 'blocked'] as EclLabel[]).filter((l) => counts[l] > 0)

export function ScopePage() {
  const { session } = useAuth()
  const [policy, setPolicy] = useState<Policy | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchPolicy(CX)
      .then(setPolicy)
      .catch((e) => setError(errorText(e)))
  }, [])

  if (error) {
    return (
      <div className="page">
        <h2>可查范围</h2>
        <div className="alert-error">{error}</div>
      </div>
    )
  }
  if (!policy) {
    return (
      <div className="page">
        <h2>可查范围</h2>
        <div className="loading">加载中…</div>
      </div>
    )
  }

  const domains = buildDomains(policy)
  const isPatient = session?.role === 'patient'

  return (
    <div className="page">
      <h2>可查范围</h2>
      <p className="hint">
        {isPatient
          ? '以下为您本人病历涵盖的数据内容。'
          : '以下为本平台可供查询的数据内容。'}
        <strong>受控</strong>与<strong>禁止</strong>的信息不会出现在任何查询结果中，
        包括查询的中间步骤。
      </p>

      {domains.map((d) => (
        <div key={d.table} className="card domain">
          <div className="domain-head">
            <h3>{d.name}</h3>
            <span className="domain-total">{d.total} 项</span>
            <span className="domain-levels">
              {present(d.counts).map((l) => (
                <span key={l} className="domain-level">
                  <EclTag label={l} />
                  <strong>{d.counts[l]}</strong>
                </span>
              ))}
            </span>
          </div>

          {d.queryable.length > 0 ? (
            <ul className="domain-fields">
              {d.queryable.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          ) : (
            <p className="hint">该数据域没有可自由查询的信息。</p>
          )}

          {d.counts.controlled + d.counts.blocked > 0 && (
            <p className="domain-note">
              其中 {d.total - d.queryable.length} 项受保护，不会出现在查询结果中。
            </p>
          )}
        </div>
      ))}
    </div>
  )
}
