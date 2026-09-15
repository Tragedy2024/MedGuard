/**
 * 业务别名解析。
 *
 * 后端的算法输出天然带着物理名——SQL 里是 `clinical_records.diagnosis_name`，
 * 结果列名是 `SUM(amount)`。但界面上不该直接出现这些（会议记录 §1.1 已否决
 * 的开发者工具形态）。本模块把物理名翻译成业务名，供控制台各面板使用。
 *
 * 别名来自策略接口，随策略变化；查不到时**原样返回物理名**——这里允许
 * 回落，因为缺别名的后果由后端测试守卫（tests/test_demo_policy.py 的
 * test_every_demo_column_has_alias），前端只做兜底不再重复告警。
 */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { fetchPolicy } from '../api/policies'
import type { Policy } from '../api/models'

interface AliasCtx {
  policy: Policy | null
  /** 传 "clinical_records.diagnosis_name" 或 (table, column) 皆可 */
  aliasOf: (qualified: string) => string
  tableAliasOf: (table: string) => string
  /** SQL 与结果列名里出现的物理列名 → 中文，用于就地标注 */
  inlineAlias: (text: string) => string | null
}

const Ctx = createContext<AliasCtx | null>(null)

export function AliasProvider({
  datasourceId,
  children,
}: {
  datasourceId: string
  children: ReactNode
}) {
  const [policy, setPolicy] = useState<Policy | null>(null)

  useEffect(() => {
    let alive = true
    fetchPolicy(datasourceId)
      .then((p) => {
        if (alive) setPolicy(p)
      })
      .catch(() => {
        // 别名拿不到不该挡住查询流程——原样显示物理名即可
      })
    return () => {
      alive = false
    }
  }, [datasourceId])

  const value = useMemo<AliasCtx>(() => {
    const lookup = (qualified: string): string | null => {
      const [t, c] = qualified.split('.')

      // 带表限定：直接查
      if (t && c) return policy?.column_aliases[t]?.[c] ?? null

      // 裸列名：跨域规则里的 join_key 就是这样（事件 column 可能是
      // "visit_id" 而非 "visits.visit_id"）。在所有表里找，**只有当
      // 各表译名一致时才采用**——译名有分歧说明真的歧义，宁可显示原文
      // 也不要猜错。
      const names = new Set<string>()
      for (const cols of Object.values(policy?.column_aliases ?? {})) {
        const cn = cols[qualified]
        if (cn) names.add(cn)
      }
      return names.size === 1 ? [...names][0] : null
    }

    /** 在自由文本（SQL 片段、结果列名）里替换出现过的物理列名。 */
    const inlineAlias = (text: string): string | null => {
      if (!policy) return null
      // 先收集所有匹配，命中至少一个才返回，避免无意义的重建字符串
      let out = text
      let hit = false
      for (const [table, cols] of Object.entries(policy.column_aliases)) {
        for (const [col, cn] of Object.entries(cols)) {
          const qualified = `${table}.${col}`
          if (out.includes(qualified)) {
            out = out.split(qualified).join(cn)
            hit = true
          }
          // 裸列名只在词边界处替换，避免误伤（如 id 命中 patient_id）
          const re = new RegExp(`(?<![\\w.])${col}(?![\\w])`)
          if (re.test(out)) {
            out = out.replace(re, cn)
            hit = true
          }
        }
      }
      return hit ? out : null
    }

    return {
      policy,
      aliasOf: (q) => lookup(q) ?? q,
      tableAliasOf: (t) => policy?.table_aliases[t] ?? t,
      inlineAlias,
    }
  }, [policy])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAliases(): AliasCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useAliases 必须在 AliasProvider 内使用')
  return ctx
}
