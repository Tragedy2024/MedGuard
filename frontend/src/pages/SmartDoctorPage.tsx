/**
 * 智慧医生——面向患者的「可信就医助手」。
 *
 * 布局沿用静态稿：左侧多轮问答与结果，右侧当前使用的数据、来源与能力边界。
 * 患者登录后的默认页（见 App.tsx 路由）。
 *
 * 每一块回答都带**来源标注**，这是产品差异点：
 *   院内数据   → 「来源：医院主库」（经医盾层一准入 + 层二审计，只读本人）
 *   通俗解读   → 「来源：医院审核知识库」
 *   下一步建议 → 「来源：医院审核知识库」
 */
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { askSmartDoctor } from '../api/smartDoctor'
import type { SmartDoctorResponse } from '../api/models'
import { AliasProvider, useAliases } from '../store/aliases'
import { useAuth } from '../store/auth'
import { errorText } from '../api/client'
import { AdmissionPanel } from '../components/AdmissionPanel'
import { DegradationBadge } from '../components/DegradationBadge'

const DATASOURCE = 'regional_health'

interface Task {
  key: string
  label: string
  question?: string
  prompt?: string
}

const TASKS: Task[] = [
  {
    key: 'triage',
    label: '我不舒服，不知道挂什么科',
    prompt: '请描述您的具体症状，例如：我胸痛、我头晕、我胃痛',
  },
  { key: 'lab', label: '帮我看懂检查报告', question: '帮我看懂检查报告' },
  { key: 'med', label: '医生开的药怎么吃', question: '医生给我开的药怎么吃' },
  {
    key: 'disease',
    label: '我想了解一种疾病',
    prompt: '请输入您想了解的疾病，例如：我想了解糖尿病',
  },
]

const SOURCE_DATA = '医院主库'
const SOURCE_AI = 'AI 智能导诊'
const SOURCE_DATASET = 'OpenCMKG 开源数据集'

interface Message {
  role: 'user' | 'doctor'
  text: string
  resp?: SmartDoctorResponse
}

export function SmartDoctorPage() {
  return (
    <AliasProvider datasourceId={DATASOURCE}>
      <SmartDoctor />
    </AliasProvider>
  )
}

function SmartDoctor() {
  const { session } = useAuth()
  const { aliasOf, inlineAlias } = useAliases()
  const [messages, setMessages] = useState<Message[]>([])
  const [text, setText] = useState('')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [taskHint, setTaskHint] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [messages, running])

  useEffect(() => {
    const field = inputRef.current
    if (!field) return
    field.style.height = 'auto'
    field.style.height = `${Math.min(field.scrollHeight, 110)}px`
  }, [text])

  if (!session) return null

  const ask = async (question: string) => {
    if (running) return
    setRunning(true)
    setError(null)
    setTaskHint(null)
    // 把**上一轮的识别结果**交给后端，用来解析「它」「那个」这类指代。
    const prev = messages.filter((m) => m.role === 'doctor' && m.resp).at(-1)?.resp
    const context = prev
      ? { intent: prev.intent, entity: prev.entity ?? null }
      : undefined

    setMessages((m) => [...m, { role: 'user', text: question }])
    try {
      const resp = await askSmartDoctor({
        token: session.token,
        datasource_id: DATASOURCE,
        question,
        context,
      })
      setMessages((m) => [...m, { role: 'doctor', text: question, resp }])
    } catch (e) {
      setError(errorText(e))
    } finally {
      setRunning(false)
      setText('')
    }
  }

  /** 任务按钮：可直答的任务直接问；需要患者提供信息的任务 →
   *  聚焦输入框并提示示例，由患者自己描述。 */
  const onTask = (t: Task) => {
    if (t.question) {
      void ask(t.question)
      return
    }
    setTaskHint(t.prompt ?? '')
    inputRef.current?.focus()
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const q = text.trim()
    if (!q) return
    void ask(q)
  }

  const submitOnEnter = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    event.currentTarget.form?.requestSubmit()
  }

  const lastResp = messages.filter((m) => m.role === 'doctor').at(-1)?.resp
  const lastRows = lastResp?.data?.rows ?? []
  const lastColumns = lastResp?.data?.columns ?? []
  const lastActions = lastResp?.advice?.actions ?? []

  return (
    <section className="view is-active" aria-labelledby="doctorTitle">
      <div className="view-head">
        <div className="view-head-copy">
          <h2 id="doctorTitle">记录与解释，分开呈现。</h2>
          <p className="view-sub">
            医院记录、审核知识库与导诊建议分开显示；通俗解释不会被写成诊断，也不会改动处方。
          </p>
        </div>
        <div className="view-head-aside"><span className="quiet-badge">来源分离 · 演示患者</span></div>
      </div>

      <div className="doctor-layout">
        <section className="chat-card" aria-labelledby="chatTitle">
          <div className="chat-head">
            <div className="chat-id">
              <span className="chat-avatar" aria-hidden="true">盾</span>
              <div>
                <strong id="chatTitle">MedGuard 智慧医生</strong>
                <small>医院审核知识库 · 回答可回到来源</small>
              </div>
            </div>
            <span className="chat-subject">{session.displayName} · 演示</span>
          </div>

          <div className="chat-scroll" ref={scrollRef} role="log" aria-live="polite" aria-label="智慧医生对话">
            <p className="chat-date">演示会话 · 所有记录均为虚构测试数据</p>

            {messages.length === 0 && (
              <div className="sd-welcome">
                <p className="sd-welcome-title">您可以从下面几个任务开始，或直接输入您的问题：</p>
                <div className="sd-tasks chat-start-grid">
                  {TASKS.map((t) => (
                    <button key={t.key} className="sd-task chat-chip" type="button"
                            onClick={() => onTask(t)} disabled={running}>
                      {t.label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {taskHint && <div className="sd-task-hint" role="status">{taskHint}</div>}

            {messages.map((m, i) =>
              m.role === 'user' ? (
                <div key={i} className="msg is-user">
                  <span className="msg-avatar" aria-hidden="true">我</span>
                  <div className="msg-main">
                    <div className="msg-who">您</div>
                    <div className="msg-bubble"><p>{m.text}</p></div>
                  </div>
                </div>
              ) : m.resp ? (
                <div key={i} className="msg">
                  <span className="msg-avatar" aria-hidden="true">盾</span>
                  <div className="msg-main">
                    <div className="msg-who">智慧医生</div>
                    <div className="msg-bubble">
                      <AnswerView resp={m.resp} aliasOf={aliasOf} inlineAlias={inlineAlias} />
                    </div>
                  </div>
                </div>
              ) : null,
            )}

            {running && (
              <div className="msg" role="status" aria-live="polite">
                <span className="msg-avatar" aria-hidden="true">盾</span>
                <div className="msg-main">
                  <div className="msg-bubble">
                    <span className="typing" aria-label="正在为您解读">
                      <i /><i /><i />
                    </span>
                  </div>
                </div>
              </div>
            )}

            {error && <div className="alert-error">{error}</div>}
          </div>

          {messages.length > 0 && (
            <div className="sd-followup chat-suggestions">
              <span className="sd-followup-label">还可以问我</span>
              {TASKS.map((t) => (
                <button key={t.key} className="sd-task chat-chip" type="button"
                        onClick={() => onTask(t)} disabled={running}>
                  {t.label}
                </button>
              ))}
            </div>
          )}

          <form className="chat-composer sd-ask" onSubmit={submit}>
            <label className="sr-only" htmlFor="chatInput">向智慧医生提问</label>
            <textarea
              ref={inputRef}
              id="chatInput"
              rows={1}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={submitOnEnter}
              placeholder={taskHint ?? '例如：我的血糖结果正常吗？接下来怎么办？'}
              disabled={running}
            />
            <button className="chat-send" type="submit" disabled={running || !text.trim()} aria-label="发送问题">↗</button>
          </form>
          <p className="chat-note">回答只基于演示记录与审核知识库，不替代执业医师的诊断、处方和紧急医疗服务。</p>
        </section>

        <aside className="doctor-aside" aria-label="回答来源与任务">
          <section className="aside-card">
            <h3>本次使用的数据</h3>
            {lastResp?.data && lastRows.length > 0 ? (
              <>
                <p>
                  <span className="sd-source-tag">{SOURCE_DATA}</span>
                  共 {lastRows.length} 条本人记录
                </p>
                <DegradationBadge degradation={lastResp.degradation} />
                {lastResp.degradation.message_cn && <p>{lastResp.degradation.message_cn}</p>}
                <p>列名：{lastColumns.map((c) => aliasOf(c)).join('、')}</p>
              </>
            ) : (
              <p>本回答不涉及院内数据，仅使用知识库内容。</p>
            )}
          </section>

          <section className="aside-card">
            <h3>建议操作</h3>
            {lastActions.length > 0 ? (
              <ul className="sd-actions">
                {lastActions.map((a, i) => <li key={i}>{a}</li>)}
              </ul>
            ) : (
              <p>暂无特殊建议。</p>
            )}
          </section>

          <section className="aside-card">
            <h3>回答从哪里来</h3>
            <p>每个回答都按来源拆分，方便你分辨“记录写了什么”和“这意味着什么”。</p>
            <ul className="source-list">
              <li className="source-item"><span>医院主库 · 本人记录</span><small>限定</small></li>
              <li className="source-item"><span>医院审核知识库</span><small>91 条核心</small></li>
              <li className="source-item"><span>{SOURCE_DATASET}</span><small>可追溯</small></li>
              <li className="source-item"><span>{SOURCE_AI}</span><small>仅作提示</small></li>
            </ul>
            <p className="source-caption">未命中审核知识的问题会明确说“不知道”，不会用相似文本拼出确定答案。</p>
          </section>

          <section className="aside-card aside-boundary">
            <h3>能力边界</h3>
            <p>智慧医生不处理急诊分诊，不判断未上传的检查，不修改处方。胸痛、呼吸困难、意识改变等情况请立即拨打急救电话或前往急诊。</p>
          </section>
        </aside>
      </div>
    </section>
  )
}

/** 助手回答：三块卡片（院内数据 / 通俗解读 / 下一步建议），每块标来源。 */
function AnswerView({
  resp,
  aliasOf,
  inlineAlias,
}: {
  resp: SmartDoctorResponse
  aliasOf: (q: string) => string
  inlineAlias: (t: string) => string | null
}) {
  if (!resp.admission.passed) {
    return <AdmissionPanel admission={resp.admission} />
  }

  const rows = resp.data?.rows ?? []
  const columns = resp.data?.columns ?? []
  const items = resp.interpretation?.items ?? []
  const actions = resp.advice?.actions ?? []

  return (
    <div className="answer-block sd-answer">
      {resp.data && rows.length > 0 && (
        <div className="answer-section source-hospital sd-block">
          <h5>您的院内数据 <span className="sd-source-tag">{SOURCE_DATA}</span></h5>
          <div className="data-table-wrap">
            <table className="result-table sd-table">
              <thead>
                <tr>{columns.map((c) => <th key={c}>{aliasOf(c)}</th>)}</tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i}>{row.map((cell, j) => <td key={j}>{String(cell)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
          {resp.data.sql_after && (
            <details className="sd-sql">
              <summary>查看本次查询（经医盾审计）</summary>
              <pre>{inlineAlias(resp.data.sql_after) ?? resp.data.sql_after}</pre>
            </details>
          )}
        </div>
      )}

      {resp.interpretation && (
        <div className="answer-section source-plain sd-block">
          <h5>{resp.interpretation.title} <span className="sd-source-tag">{resp.interpretation.source}</span></h5>
          {resp.interpretation.text && <p>{resp.interpretation.text}</p>}
          {items.length > 0 && (
            <ul className="sd-items">
              {items.map((it, i) => (
                <li key={i} className="sd-item">
                  <InterpretationRow item={it} aliasOf={aliasOf} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {resp.advice && (
        <div className="answer-section source-next sd-block">
          <h5>下一步建议 <span className="sd-source-tag">{resp.advice.source}</span></h5>
          {resp.advice.urgent && (
            <div className="sd-urgent">⚠ 请留意紧急情况：出现文中描述的重症信号请立即就医</div>
          )}
          {resp.advice.text && <p>{resp.advice.text}</p>}
          {actions.length > 0 && (
            <ul className="sd-actions">
              {actions.map((a, i) => <li key={i}>{a}</li>)}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

/** 一条解读/用药条目：检验项（或药名）+ 分档 + 通俗说明。 */
function InterpretationRow({
  item,
  aliasOf,
}: {
  item: Record<string, unknown>
  aliasOf: (q: string) => string
}) {
  if (item.ref_title !== undefined) {
    return (
      <>
        <div className="sd-item-line">
          <strong>{String(item.ref_title)}</strong>
          {item.ref_kind ? <span className="sd-item-label">{String(item.ref_kind)}</span> : null}
        </div>
        <div className="sd-item-text sd-ref">
          {item.ref_excerpt ? <span>{String(item.ref_excerpt)}</span> : null}
          {item.ref_source ? <span className="sd-ref-source">出处：{String(item.ref_source)}</span> : null}
        </div>
      </>
    )
  }

  if (item.test_name !== undefined) {
    const name = String(item.test_name)
    const label = item.label ? String(item.label) : ''
    return (
      <>
        <div className="sd-item-line">
          <strong>{aliasOf(name)}</strong>
          <span className="sd-item-value">
            {String(item.result_value ?? '')}
            {item.unit ? ` ${String(item.unit)}` : ''}
          </span>
          {label && <span className="sd-item-label">{label}</span>}
        </div>
        {item.text ? <div className="sd-item-text">{String(item.text)}</div> : null}
      </>
    )
  }

  if (item.drug_name !== undefined) {
    const drug = String(item.drug_name)
    return (
      <>
        <div className="sd-item-line">
          <strong>{aliasOf(drug)}</strong>
          <span className="sd-item-value">
            {String(item.dosage ?? '')} · {String(item.frequency ?? '')}
            {item.route ? ` · ${String(item.route)}` : ''}
          </span>
        </div>
        {item.purpose ? <div className="sd-item-text">用途：{String(item.purpose)}</div> : null}
        {item.usage ? <div className="sd-item-text">用法：{String(item.usage)}</div> : null}
        {item.caution ? <div className="sd-item-text sd-caution">注意：{String(item.caution)}</div> : null}
        {item.note ? <div className="sd-item-text sd-caution">提醒：{String(item.note)}</div> : null}
      </>
    )
  }

  const entries = Object.entries(item).filter(([, v]) => v !== '' && v != null)
  return (
    <>
      {entries.map(([k, v]) => (
        <div key={k} className="sd-item-line">
          <span className="sd-item-value">{k}：{String(v)}</span>
        </div>
      ))}
    </>
  )
}
