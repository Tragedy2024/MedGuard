/**
 * 改写前后 SQL 对比（行级 diff）。
 *
 * 呈现方式为「1+2」：
 *   ① 涉及的字段**就地标注中文业务名**
 *   ② SQL **默认折叠**，需要时展开
 *
 * **为什么保留 SQL**：它是算法创新唯一的直接证据——不给人看 SQL，就证明
 * 不了医盾真的移除了那一列。藏起来，产品就只剩一句无法验证的主张。
 *
 * **为什么默认折叠**：本产品的用户是医护与病患（设计文档 §1.2）。一屏 SQL
 * 对他们只是噪音，也会把产品拉回「开发者工具」的观感——需求分析阶段已否决
 * 的形态。折叠让两类观众各取所需：医生看中文摘要，技术评委展开核对。
 */
import { useMemo } from 'react'

export interface SqlViolation {
  /** 物理限定名，如 clinical_records.diagnosis_name */
  column: string
  /** 中文业务名，如 诊断名称 */
  label: string
}

/** 文本是否引用了某个列（按裸列名做词边界匹配，避免 id 命中 patient_id）。 */
function hitsIn(text: string, violations: SqlViolation[]): SqlViolation[] {
  return violations.filter((v) => {
    const bare = v.column.split('.').pop() ?? v.column
    return new RegExp(`(?<![\\w.])${bare}(?![\\w])`).test(text)
  })
}

export function SqlDiff({
  before,
  after,
  violations = [],
}: {
  before: string
  after: string
  violations?: SqlViolation[]
}) {
  const rows = useMemo(() => {
    const b = before.split('\n')
    const a = after.split('\n')
    const n = Math.max(b.length, a.length)
    return Array.from({ length: n }, (_, i) => ({
      before: b[i] ?? '',
      after: a[i] ?? '',
      changed: (b[i] ?? '') !== (a[i] ?? ''),
    }))
  }, [before, after])

  if (before === after) {
    return <div className="diff-unchanged">未改写</div>
  }

  // 只列出真正在改写前 SQL 里出现过的违规列
  const touched = violations.filter((v) =>
    rows.some((r) => hitsIn(r.before, [v]).length > 0),
  )

  return (
    <details className="sql-diff">
      <summary>
        <span className="diff-summary">已改写</span>
        {touched.length > 0 && (
          <span className="diff-touched">
            {touched.map((v) => v.label).join('、')}
          </span>
        )}
        <span className="diff-hint">展开查看 SQL</span>
      </summary>

      {touched.length > 0 && (
        <div className="diff-legend">
          <span className="diff-legend-title">本次改写涉及的字段</span>
          <ul>
            {touched.map((v) => (
              <li key={v.column}>
                <strong>{v.label}</strong>
                <code>{v.column}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      <table>
        <thead>
          <tr>
            <th>改写前</th>
            <th>改写后</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={r.changed ? 'diff-changed' : ''}>
              <td>
                <code>{r.before}</code>
              </td>
              <td>
                <code>{r.after}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}
