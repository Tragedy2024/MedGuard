/** ECL 标签：free / 受控 / 禁止。
 *
 * ECL（Exposure Control Label）本身就是**安全等级**，所以这三个色直接
 * 取全站的安全语义三色——与层级判定同源，不另起一套配色。
 * 模块②（策略页）与模块③（控制台）复用本组件。
 */
import type { EclLabel } from '../api/types'

/** ECL 的中文名。下拉框等处也应显示这套文案，不要把 free/controlled/blocked
 *  这类内部字符串暴露给非数据库专业的用户。 */
export const ECL_TEXT: Record<EclLabel, string> = {
  free: '自由',
  controlled: '受控',
  blocked: '禁止',
}

export function EclTag({ label }: { label: EclLabel }) {
  return <span className={`ecl ecl-${label}`}>{ECL_TEXT[label]}</span>
}
