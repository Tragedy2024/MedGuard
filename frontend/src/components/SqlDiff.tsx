/**
 * 改写前后 SQL 对比。
 *
 * 呈现方式为「1+2」：
 *   ① 涉及的字段**就地标注中文业务名**，并在 SQL 里**就地标出列名**
 *   ② SQL **默认折叠**，需要时展开
 *
 * **为什么保留 SQL**：它是算法创新唯一的直接证据——不给人看 SQL，就证明
 * 不了医盾真的移除了那一列。藏起来，产品就只剩一句无法验证的主张。
 *
 * **为什么默认折叠**：本产品的用户是医护与病患（设计文档 §1.2）。一屏 SQL
 * 对他们只是噪音，也会把产品拉回「开发者工具」的观感。
 *
 * ── 与计划书的两处偏离（都是实测后改的）────────────────────────
 *
 * ① 不用「左右两栏按行号对齐」的表格。实测那样读不通：两侧宽度只有一半，
 *    长 SQL 各自折行，看起来像断掉的代码。改为两侧各成连续的代码块。
 *
 * ② 不做「整行标色」。原以为需要按行 diff，实测发现**算法的 SQL 常常是
 *    一整行**（没有换行符）——一行就是整条语句，标了等于没标。所以高亮
 *    精确到**列名本身**：读者要找的正是「哪个字段被处理了」。
 */
import { useMemo, type ReactNode } from 'react'

export interface SqlViolation {
  /** 物理限定名，如 clinical_records.diagnosis_name */
  column: string
  /** 中文业务名，如 诊断名称 */
  label: string
}

/**
 * 文本是否引用了某个列。
 *
 * 匹配裸列名，但**允许前面是点号**——SQL 里的列几乎总是限定形式
 * （`b.amount`、`c.diagnosis_name`）。早先写成 `(?<![\w.])` 把点号也排除
 * 了，结果一条都匹配不上，高亮整块静默失效。防 `total_amount` 命中
 * `amount` 靠的是 `\w`，不需要排除点号。
 */
function hitsIn(text: string, violations: SqlViolation[]): SqlViolation[] {
  return violations.filter((v) => {
    const bare = v.column.split('.').pop() ?? v.column
    return new RegExp(`(?<!\\w)${bare}(?!\\w)`).test(text)
  })
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** 把一行 SQL 里出现的违规列名就地包起来。 */
function markColumns(line: string, violations: SqlViolation[]): ReactNode {
  const names = [
    ...new Set(violations.map((v) => v.column.split('.').pop() ?? v.column)),
  ]
  if (names.length === 0 || !line) return line

  const re = new RegExp(`(?<!\\w)(${names.map(escapeRe).join('|')})(?!\\w)`, 'g')
  return line.split(re).map((part, i) =>
    names.includes(part) ? (
      <mark key={i} className="diff-col-mark">
        {part}
      </mark>
    ) : (
      part
    ),
  )
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
  const beforeLines = useMemo(() => before.split('\n'), [before])
  const afterLines = useMemo(() => after.split('\n'), [after])

  if (before === after) {
    return <div className="diff-unchanged">未改写</div>
  }

  // 只列出真正在改写前 SQL 里出现过的违规列
  const touched = violations.filter((v) =>
    beforeLines.some((l) => hitsIn(l, [v]).length > 0),
  )

  const Side = ({ title, lines }: { title: string; lines: string[] }) => (
    <div className="diff-side">
      <div className="diff-side-title">{title}</div>
      <pre className="diff-side-body">
        {lines.map((line, i) => (
          <div key={i}>{markColumns(line, touched) || ' '}</div>
        ))}
      </pre>
    </div>
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

      <div className="diff-sides">
        <Side title="改写前" lines={beforeLines} />
        <Side title="改写后" lines={afterLines} />
      </div>
    </details>
  )
}
