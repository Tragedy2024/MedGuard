/**
 * 智慧医生——面向患者的「可信就医助手」（智慧医生.docx）。
 *
 * 布局按需求文档：左侧约 70% 多轮问答与结果，右侧约 30% 当前使用的数据、
 * 建议操作、信息来源。患者登录后的默认页（见 App.tsx 路由）。
 *
 * 每一块回答都带**来源标注**，这是产品差异点：
 *   院内数据   → 「来源：医院主库」（经医盾层一准入 + 层二审计，只读本人）
 *   通俗解读   → 「来源：医院审核知识库」
 *   下一步建议 → 「来源：医院审核知识库」
 */
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { askSmartDoctor } from '../api/smartDoctor'
import type { SmartDoctorResponse } from '../api/models'
import { AliasProvider, useAliases } from '../store/aliases'
import { useAuth } from '../store/auth'
import { errorText } from '../api/client'
import { DegradationBadge } from '../components/DegradationBadge'

const DATASOURCE = 'regional_health'

/**
 * 四个任务入口（docx 原文）。
 *
 * 任务分两类：
 * - 直接可查的任务（看懂报告 / 用药）→ 点击即发送问法；
 * - 需要患者提供信息才能回答的任务（挂什么科 / 了解疾病）→ 点击后
 *   聚焦输入框并提示示例，由患者**自己**描述症状或疾病——不能替患者
 *   编一个症状（"我不舒服"点下去就直接报心血管内科是假演示）。
 */
interface Task {
  key: string
  label: string
  /** 可直接发送的问法；与 prompt 二选一 */
  question?: string
  /** 需患者自己描述时的示例提示；与 question 二选一 */
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
const SOURCE_KB = '医院审核知识库'
const SOURCE_AI = 'AI 智能导诊'
/** 症状导诊与疾病条目的事实来源——开源数据集，非本项目自撰。 */
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
  const endRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (!session) return null

  const ask = async (question: string) => {
    if (running) return
    setRunning(true)
    setError(null)
    setTaskHint(null)
    // 把**上一轮的识别结果**交给后端，用来解析「它」「那个」这类指代。
    //
    // 传的是已解析的 {intent, entity} 而不是上一轮的原话：链式追问才不会
    // 一轮轮退化——「我的血糖结果正常吗」→「它正常吗」→「严重吗」，
    // 若每轮都传原话，第二轮回解析「它正常吗」同样拿不到实体，第三轮就断了。
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

  const lastResp = messages.filter((m) => m.role === 'doctor').at(-1)?.resp
  const denied = lastResp !== undefined && !lastResp.admission.passed
  // 契约里 rows / columns / actions 是**可选**字段（后端用 default_factory
  // 声明，OpenAPI 就不把它们标成必填）。统一在这里兜底，免得每处调用都写
  // 一遍 `?? []`，也免得漏掉一处就白屏。
  const lastRows = lastResp?.data?.rows ?? []
  const lastColumns = lastResp?.data?.columns ?? []
  const lastActions = lastResp?.advice?.actions ?? []

  return (
    <div className="page smart-doctor">
      <div className="sd-head">
        <h2>智慧医生</h2>
        <p className="hint">
          面向患者的可信就医助手：回答由两块合成——<strong>医院主库的本人数据</strong>
          （经医盾安全审计，只读取您本人的记录）与<strong>医院审核知识库</strong>的通俗解读。
          两个来源分开标注，不混为一谈。
        </p>
      </div>

      <div className="sd-layout">
        {/* ── 左栏：多轮问答与结果 ── */}
        <div className="sd-chat">
          {messages.length === 0 && (
            <div className="sd-welcome">
              <p className="sd-welcome-title">您可以从下面几个任务开始，或直接输入您的问题：</p>
              <div className="sd-tasks">
                {TASKS.map((t) => (
                  <button
                    key={t.key}
                    className="sd-task"
                    onClick={() => onTask(t)}
                    disabled={running}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {taskHint && (
            <div className="sd-task-hint" role="status">
              {taskHint}
            </div>
          )}

          {messages.map((m, i) =>
            m.role === 'user' ? (
              <div key={i} className="sd-msg sd-msg-user">
                {m.text}
              </div>
            ) : m.resp ? (
              <div key={i} className="sd-msg sd-msg-doctor">
                <AnswerView resp={m.resp} aliasOf={aliasOf} inlineAlias={inlineAlias} />
              </div>
            ) : null,
          )}

          {running && (
            <div className="loading" role="status" aria-live="polite">
              正在为您解读……
            </div>
          )}

          {/* 追问入口。
              原先这四个任务入口只在 messages.length === 0 时渲染——患者问过
              第一句之后它们就**消失了**，此后再想问别的只能靠打字回忆。
              「还能问什么」是产品该主动告诉患者的事，不该让他去猜；
              所以首轮之后改为一行常驻的紧凑入口。 */}
          {messages.length > 0 && (
            <div className="sd-followup">
              <span className="sd-followup-label">还可以问我</span>
              {TASKS.map((t) => (
                <button
                  key={t.key}
                  className="sd-task"
                  onClick={() => onTask(t)}
                  disabled={running}
                >
                  {t.label}
                </button>
              ))}
            </div>
          )}
          {error && <div className="alert-error">{error}</div>}
          {denied && (
            <div className="admission admission-deny">
              <div className="admission-refusal">
                {lastResp?.admission.reason ?? '该查询涉及其他患者信息，无法提供。'}
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        {/* ── 右栏：数据 / 建议操作 / 信息来源 ── */}
        <aside className="sd-side">
          <section className="sd-card">
            <h4>本次使用的数据</h4>
            {lastResp?.data && lastRows.length > 0 ? (
              <>
                <p className="hint">
                  <span className="sd-source-tag">{SOURCE_DATA}</span>
                  共 {lastRows.length} 条本人记录
                </p>
                <DegradationBadge degradation={lastResp.degradation} />
                {lastResp.degradation.message_cn && (
                  <p className="hint">{lastResp.degradation.message_cn}</p>
                )}
                <p className="hint">
                  列名：{lastColumns.map((c) => aliasOf(c)).join('、')}
                </p>
              </>
            ) : (
              <p className="hint">本回答不涉及院内数据，仅使用知识库内容。</p>
            )}
          </section>

          <section className="sd-card">
            <h4>建议操作</h4>
            {lastActions.length > 0 ? (
              <ul className="sd-actions">
                {lastActions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            ) : (
              <p className="hint">暂无特殊建议。</p>
            )}
          </section>

          <section className="sd-card">
            <h4>信息来源</h4>
            <ul className="sd-sources">
              <li>
                <span className="sd-source-tag">{SOURCE_DATA}</span>
                <span className="sd-source-desc">
                  您的院内数据，查询计划经医盾层一准入与层二安全审计，只读取本人记录。
                </span>
              </li>
              <li>
                <span className="sd-source-tag">{SOURCE_DATASET}</span>
                <span className="sd-source-desc">
                  就诊科室建议与疾病条目取自开源中文医学知识图谱 OpenCMKG，
                  <strong>非本项目自撰</strong>；抽取脚本随仓库交付，可复现、可审计。
                </span>
              </li>
              <li>
                <span className="sd-source-tag">{SOURCE_KB}</span>
                <span className="sd-source-desc">
                  检验数值分档与用药说明来自本项目的仿真知识库（演示数据）。
                </span>
              </li>
              <li>
                <span className="sd-source-tag">{SOURCE_AI}</span>
                <span className="sd-source-desc">
                  通俗解读与下一步建议由大模型生成，<strong>未经人工审核</strong>，仅供参考。
                </span>
              </li>
            </ul>
            <p className="hint">
              对结果有疑问，请携带报告咨询您的主治医生或医院信息科。
            </p>
          </section>
        </aside>
      </div>

      <form className="ask-bar sd-ask" onSubmit={submit}>
        <input
          ref={inputRef}
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={taskHint ?? '例如：我的血糖结果正常吗？接下来怎么办？'}
          aria-label="向智慧医生提问"
          disabled={running}
        />
        <button type="submit" disabled={running || !text.trim()}>
          提问
        </button>
      </form>
    </div>
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
  // 层一拒绝：只展示固定拒绝文案（由父组件兜底渲染，这里也防一手）
  if (!resp.admission.passed) {
    return (
      <div className="sd-block">
        <p className="sd-refusal">
          {resp.admission.reason ?? '该查询涉及其他患者信息，无法提供。'}
        </p>
      </div>
    )
  }

  // 契约里这几个数组是可选字段（后端 default_factory），统一兜底。
  const rows = resp.data?.rows ?? []
  const columns = resp.data?.columns ?? []
  const items = resp.interpretation?.items ?? []
  const actions = resp.advice?.actions ?? []

  return (
    <div className="sd-answer">
      {/* 块一：院内数据 */}
      {resp.data && rows.length > 0 && (
        <div className="sd-block">
          <div className="sd-block-head">
            <span className="sd-block-title">您的院内数据</span>
            <span className="sd-source-tag">{SOURCE_DATA}</span>
          </div>
          <table className="result-table sd-table">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c}>{aliasOf(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j}>{String(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {resp.data.sql_after && (
            <details className="sd-sql">
              <summary>查看本次查询（经医盾审计）</summary>
              <pre>{inlineAlias(resp.data.sql_after) ?? resp.data.sql_after}</pre>
            </details>
          )}
        </div>
      )}

      {/* 块二：通俗解读 */}
      {resp.interpretation && (
        <div className="sd-block">
          <div className="sd-block-head">
            <span className="sd-block-title">{resp.interpretation.title}</span>
            <span className="sd-source-tag">{resp.interpretation.source}</span>
          </div>
          {resp.interpretation.text && <p className="sd-text">{resp.interpretation.text}</p>}
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

      {/* 块三：下一步建议 */}
      {resp.advice && (
        <div className="sd-block">
          <div className="sd-block-head">
            <span className="sd-block-title">下一步建议</span>
            <span className="sd-source-tag">{resp.advice.source}</span>
          </div>
          {resp.advice.urgent && (
            <div className="sd-urgent">⚠ 请留意紧急情况：出现文中描述的重症信号请立即就医</div>
          )}
          {resp.advice.text && <p className="sd-text">{resp.advice.text}</p>}
          {actions.length > 0 && (
            <ul className="sd-actions">
              {actions.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
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
  // 知识库检索来源行（RAG 的检索结果）。
  // 放在最前面判：`ref_` 前缀与下面的 test_name / drug_name 不会冲突，
  // 但先判更直白，也表明「这是另一类行，不是检验项也不是药品」。
  if (item.ref_title !== undefined) {
    return (
      <>
        <div className="sd-item-line">
          <strong>{String(item.ref_title)}</strong>
          {item.ref_kind ? (
            <span className="sd-item-label">{String(item.ref_kind)}</span>
          ) : null}
        </div>
        <div className="sd-item-text sd-ref">
          {item.ref_excerpt ? <span>{String(item.ref_excerpt)}</span> : null}
          {item.ref_source ? (
            <span className="sd-ref-source">出处：{String(item.ref_source)}</span>
          ) : null}
        </div>
      </>
    )
  }

  // 检验解读行
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

  // 用药行
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
        {item.purpose ? (
          <div className="sd-item-text">用途：{String(item.purpose)}</div>
        ) : null}
        {item.usage ? <div className="sd-item-text">用法：{String(item.usage)}</div> : null}
        {item.caution ? (
          <div className="sd-item-text sd-caution">注意：{String(item.caution)}</div>
        ) : null}
        {item.note ? <div className="sd-item-text sd-caution">提醒：{String(item.note)}</div> : null}
      </>
    )
  }

  // 其他（fallback）：原样列出非空字段
  const entries = Object.entries(item).filter(([, v]) => v !== '' && v != null)
  return (
    <>
      {entries.map(([k, v]) => (
        <div key={k} className="sd-item-line">
          <span className="sd-item-value">
            {k}：{String(v)}
          </span>
        </div>
      ))}
    </>
  )
}