/**
 * 结果表格。
 *
 * 结果列名由算法从 SQL 里带出（如 `SUM(amount)`、`test_name`），是物理形态。
 * 这里就地翻译成业务名，并把原始表达式折叠在表头下方——技术观众仍能核对，
 * 医生看到的是中文。
 */
import type { ResultSet } from '../api/models'
import { useAliases } from '../store/aliases'

export function ResultTable({ result }: { result: ResultSet | null }) {
  const { inlineAlias } = useAliases()

  if (!result) return null
  if (result.rows.length === 0) {
    return <div className="result-empty">查询已执行，无匹配数据。</div>
  }

  return (
    <table className="result-table">
      <thead>
        <tr>
          {result.columns.map((c) => {
            const cn = inlineAlias(c)
            return (
              <th key={c}>
                <span className="result-col">{cn ?? c}</span>
                {cn && <code className="result-col-raw">{c}</code>}
              </th>
            )
          })}
        </tr>
      </thead>
      <tbody>
        {result.rows.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) => (
              <td key={j}>{String(cell)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
