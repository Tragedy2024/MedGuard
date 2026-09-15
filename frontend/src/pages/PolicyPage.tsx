import { useEffect, useState } from 'react'
import { fetchPolicy, updatePolicy } from '../api/policies'
import type { EclLabel, Policy } from '../api/types'
import { ECL_TEXT, EclTag } from '../components/EclTag'

const CX = 'regional_health'
const LABELS: EclLabel[] = ['free', 'controlled', 'blocked']

export function PolicyPage() {
  const [policy, setPolicy] = useState<Policy | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchPolicy(CX)
      .then(setPolicy)
      .catch((e) => setError(String(e)))
  }, [])

  if (error) {
    return (
      <div className="page">
        <h2>安全策略</h2>
        <div className="alert-error">{error}</div>
      </div>
    )
  }
  if (!policy) {
    return (
      <div className="page">
        <h2>安全策略</h2>
        <div className="loading">加载中…</div>
      </div>
    )
  }

  const change = (table: string, column: string, label: EclLabel) => {
    setPolicy({
      ...policy,
      column_labels: {
        ...policy.column_labels,
        [table]: { ...policy.column_labels[table], [column]: label },
      },
    })
    setDirty(true)
  }

  const save = async () => {
    setSaving(true)
    try {
      await updatePolicy(CX, { column_labels: policy.column_labels })
      setDirty(false)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const counts = LABELS.map((l) => ({
    label: l,
    n: Object.values(policy.column_labels)
      .flatMap((cols) => Object.values(cols))
      .filter((v) => v === l).length,
  }))

  const unlabeled = Object.entries(policy.review_status).flatMap(([t, cols]) =>
    Object.entries(cols)
      .filter(([, s]) => s === 'unlabeled')
      .map(([c]) => `${t}.${c}`),
  )

  const isUnlabeled = (table: string, col: string) =>
    policy.review_status[table]?.[col] === 'unlabeled'

  return (
    <div className="page">
      <h2>安全策略</h2>

      <p className="hint">
        策略是<strong>会腐烂的资产</strong>——新增一列若未标注，医盾会
        <strong>静默放行</strong>该列（SSA 对未标注列默认按「自由」处理）。
        因此本平台把逐列标注做成一等公民的工作流，而非一次性配置。
      </p>

      {unlabeled.length > 0 && (
        <div className="alert-warn">
          <strong>发现 {unlabeled.length} 列未标注，将被静默放行</strong>
          <span className="alert-warn-list">{unlabeled.join('、')}</span>
        </div>
      )}

      <div className="policy-summary">
        {counts.map((c) => (
          <span key={c.label} className="summary-item">
            <EclTag label={c.label} />
            <strong>{c.n}</strong> 列
          </span>
        ))}
      </div>

      {Object.entries(policy.column_labels).map(([table, cols]) => (
        <details key={table} className="schema-table" open>
          <summary>
            <code>{table}</code>
            {policy.table_aliases[table] && (
              <span className="schema-alias">{policy.table_aliases[table]}</span>
            )}
            <span className="schema-count">{Object.keys(cols).length} 列</span>
          </summary>
          <div className="table-scroll">
          <table className="policy-table">
            <thead>
              <tr>
                <th>列名</th>
                <th>标签</th>
                <th>标注理由</th>
                <th>审查</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(cols).map(([col, label]) => (
                <tr key={col} className={isUnlabeled(table, col) ? 'row-unlabeled' : ''}>
                  <td>
                    <code>{col}</code>
                    <span className="col-alias">
                      {policy.column_aliases[table]?.[col] ?? '—'}
                    </span>
                  </td>
                  <td>
                    <EclTag label={label} />
                  </td>
                  <td className="reason-cell">
                    {policy.column_reasons[table]?.[col] ?? '—'}
                  </td>
                  <td>
                    <select
                      value={label}
                      aria-label={`${table}.${col} 的标签`}
                      onChange={(e) => change(table, col, e.target.value as EclLabel)}
                    >
                      {LABELS.map((l) => (
                        <option key={l} value={l}>
                          {ECL_TEXT[l]}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </details>
      ))}

      <h3>跨域规则</h3>
      {policy.cross_domain_rules.length === 0 && (
        <p className="hint">尚未定义跨域规则。</p>
      )}
      {policy.cross_domain_rules.map((r, i) => (
        <div key={i} className="card">
          <div className="rule-head">
            <code>{r.table_pair.join(' ⨝ ')}</code>
            <span className="rule-join">
              on <code>{r.join_key}</code>
            </span>
          </div>
          <p className="hint">{r.reason}</p>
          <div className="rule-flags">
            <span className={r.forbid_personal_level ? 'flag-on' : 'flag-off'}>
              {r.forbid_personal_level ? '✓' : '✗'} 禁止个人级
            </span>
            <span className={r.allow_aggregate_level ? 'flag-on' : 'flag-off'}>
              {r.allow_aggregate_level ? '✓' : '✗'} 允许聚合级
            </span>
          </div>
        </div>
      ))}

      <div className="sticky-actions">
        <button onClick={save} disabled={!dirty || saving}>
          {saving ? '保存中…' : dirty ? '保存策略' : '已保存'}
        </button>
        {dirty && <span className="hint">有未保存的改动</span>}
      </div>
    </div>
  )
}
