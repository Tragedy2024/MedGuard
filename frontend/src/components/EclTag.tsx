/** ECL 标签：free / control / forbidden。
 *
 * ECL（Exposure Control Label）本身就是**安全等级**，所以这三个色直接
 * 取全站的安全语义三色——与层级判定同源，不另起一套配色。
 * 模块②（策略页）与模块③（控制台）复用本组件。
 */
import type { EclLabel } from '../api/models'

/** 界面使用的 ECL 显示名称。 */
export const ECL_TEXT: Record<EclLabel, string> = {
  free: 'free',
  controlled: 'control',
  blocked: 'forbidden',
}

export function EclTag({ label }: { label: EclLabel }) {
  return <span className={`ecl ecl-${label}`}>{ECL_TEXT[label]}</span>
}
