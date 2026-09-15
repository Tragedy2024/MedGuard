/** 令牌切换器——全局顶部，随时可切。
 *
 * 这是整个演示里最重要的控件：「同一问题、两类令牌、不同结果」的对照
 * 全靠它切换。所以它做得比普通按钮重——分段控件的形态让「当前是哪一侧」
 * 在录像里也一眼可辨。
 */
import { useToken } from '../store/token'

export function TokenSwitcher() {
  const { token, setTokenType } = useToken()

  return (
    <div className="token-switcher">
      <span className="token-label">当前身份</span>
      <div className="token-buttons" role="group" aria-label="切换身份">
        <button
          type="button"
          className={token.type === 'staff' ? 'active' : ''}
          aria-pressed={token.type === 'staff'}
          onClick={() => setTokenType('staff')}
        >
          医护人员
        </button>
        <button
          type="button"
          className={token.type === 'patient' ? 'active' : ''}
          aria-pressed={token.type === 'patient'}
          onClick={() => setTokenType('patient')}
        >
          病患
        </button>
      </div>
      <span className="token-subject">
        {token.type === 'patient' ? `绑定 ${token.subject_id}` : '全院范围'}
      </span>
    </div>
  )
}
