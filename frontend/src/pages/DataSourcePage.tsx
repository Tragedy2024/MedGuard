/**
 * 数据源——**管理员视图**。
 *
 * 只有管理员能看到物理库表结构。医护与病患走 ScopePage 的业务视图。
 *
 * 表名与列名同时给出物理名（等宽）与业务别名（中文）：管理员两边都要看
 * 得到——物理名是与接口/数据库核对的锚点，别名是临床用户实际看到的字。
 */
import { useEffect, useState } from 'react'
import { createDemoDatasource, fetchDatasources, fetchSchema } from '../api/datasources'
import { fetchPolicy } from '../api/policies'
import type { DatasourceInfo, Policy, SchemaTable } from '../api/types'

export function DataSourcePage() {
  const [sources, setSources] = useState<DatasourceInfo[]>([])
  const [tables, setTables] = useState<SchemaTable[]>([])
  const [policy, setPolicy] = useState<Policy | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    try {
      setError(null)
      const list = await fetchDatasources()
      setSources(list)
      if (list.length > 0) {
        const id = list[0].id
        const [schema, pol] = await Promise.all([fetchSchema(id), fetchPolicy(id)])
        setTables(schema.tables)
        setPolicy(pol)
      }
    } catch (e) {
      setError(String(e))
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const handleCreate = async () => {
    setLoading(true)
    try {
      await createDemoDatasource()
      await load()
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <h2>数据源</h2>
      {error && <div className="alert-error">{error}</div>}

      {sources.length === 0 ? (
        <div className="empty-state">
          <p>尚未载入演示数据。</p>
          <p className="hint">
            演示库为医院数据仿真版本，全部数据均为虚构（患者001 / TEST-000001），
            不含任何真实个人信息。
          </p>
          <button onClick={handleCreate} disabled={loading}>
            {loading ? '载入中…' : '载入演示数据'}
          </button>
        </div>
      ) : (
        <>
          {sources.map((s) => (
            <div key={s.id} className="card">
              <h3>{s.name}</h3>
              <dl className="kv">
                <div>
                  <dt>标识</dt>
                  <dd>
                    <code>{s.id}</code>
                  </dd>
                </div>
                <div>
                  <dt>表数</dt>
                  <dd>{s.table_count}</dd>
                </div>
                <div>
                  <dt>列数</dt>
                  <dd>{s.column_count}</dd>
                </div>
                <div>
                  <dt>策略状态</dt>
                  <dd className={s.policy_ready ? 'kv-ok' : 'kv-warn'}>
                    {s.policy_ready ? '已标注' : '未标注'}
                  </dd>
                </div>
              </dl>
            </div>
          ))}

          <h3>库表结构</h3>
          <p className="hint">
            共 {tables.length} 张表。等宽字体为物理表名/列名，其后的中文是
            <strong>医护与病患实际看到的业务名称</strong>。
          </p>
          {tables.map((t) => (
            <details key={t.name} className="schema-table" open>
              <summary>
                <code>{t.name}</code>
                {policy?.table_aliases[t.name] && (
                  <span className="schema-alias">{policy.table_aliases[t.name]}</span>
                )}
                <span className="schema-count">{t.columns.length} 列</span>
              </summary>
              <ul className="schema-columns">
                {t.columns.map((c) => (
                  <li key={c.name}>
                    <code>{c.name}</code>
                    <span className="col-alias">
                      {policy?.column_aliases[t.name]?.[c.name] ?? '—'}
                    </span>
                    <span className="col-type">{c.type}</span>
                    {c.pk && <span className="col-pk">PK</span>}
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </>
      )}
    </div>
  )
}
