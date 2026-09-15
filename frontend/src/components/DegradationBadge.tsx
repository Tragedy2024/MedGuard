/** 降级徽章。L0 绿 / L1·L2 琥珀 / L3 红——严格对齐安全语义三色。 */
import type { DegradationInfo } from '../api/types'

export function DegradationBadge({ degradation }: { degradation: DegradationInfo }) {
  const tone =
    degradation.level === 'L0' ? 'pass'
      : degradation.level === 'L3' ? 'layer1'
        : 'layer2'

  return (
    <div className={`badge badge-${tone}`}>
      <span className="badge-level">{degradation.level}</span>
      <span className="badge-label">{degradation.label}</span>
    </div>
  )
}
