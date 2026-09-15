/**
 * 准入判定面板（层一）。
 *
 * 层一全称规则：病患令牌的查询若引用患者数据表，必须绑定本人。
 * 它不判断聚合结果能否反推个体——那不可判定（需知结果基数）。
 *
 * 视觉上必须与层二的琥珀色明确区分：层一是**执行前拒绝**，红。
 */
import type { AdmissionInfo } from '../api/models'
import { useAliases } from '../store/aliases'

export function AdmissionPanel({ admission }: { admission: AdmissionInfo }) {
  const { tableAliasOf } = useAliases()

  return (
    <div className={`admission ${admission.passed ? 'admission-pass' : 'admission-deny'}`}>
      <div className="admission-head">
        <span className="layer-tag layer-1">层一 · 准入</span>
        <span className="admission-verdict">
          {admission.passed ? '放行' : '拒绝'}
        </span>
      </div>

      {admission.passed ? (
        <div className="admission-body">
          <div className="admission-row">
            <span>涉及数据</span>
            <span>
              {admission.checked_tables.length > 0 ? (
                admission.checked_tables.map((t) => (
                  <span key={t} className="table-chip">
                    {tableAliasOf(t)}
                  </span>
                ))
              ) : (
                <em>无</em>
              )}
            </span>
          </div>
          <div className="admission-row">
            <span>主语绑定</span>
            <span>{admission.bound_to_subject ? '已绑定本人' : '不涉及患者个人数据'}</span>
          </div>
        </div>
      ) : (
        <div className="admission-refusal">{admission.reason}</div>
      )}
    </div>
  )
}
