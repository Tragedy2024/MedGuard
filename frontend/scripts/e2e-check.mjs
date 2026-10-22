#!/usr/bin/env node
/**
 * 端到端回归核查（**断言式**）—— 与 `npm run visual` 的区别在这里。
 *
 *   npm run visual   截图 + 收控制台报错，**不做断言**（给人看）
 *   npm run e2e      断言 7/7 演示矩阵与关键交互，**失败即 exit 1**（给机器判）
 *
 * 为什么需要它：在这个脚本出现之前，前端**零断言测试**，双令牌矩阵靠人工核对。
 * 而项目里最值钱的声明（层一执行前拒绝、零 LLM 零查库、报告按账号隔离）
 * 全部只在后端 pytest 里守着，**界面上渲染成什么样没人管**。改组件很容易
 * 悄悄弄坏演示效果，且往往等到录视频前才发现。
 *
 * 覆盖：
 *   【API】双令牌矩阵 7/7 · 层一拒绝文案逐字 · 指标恒为 0 · 报告按账号隔离
 *   【界面】登录 · 控制台放行渲染 · 控制台拒绝渲染 · 指标条 · 报告页
 *
 * 用法：
 *   npm run e2e                  自动找后端（没有就自己起一个）
 *   BASE_URL=http://localhost:5173 npm run e2e    指定已有服务（如 Vite 开发服务器）
 *
 * 用系统已装的 Chrome（channel: 'chrome'），不下载 Playwright 自带浏览器。
 */
import { chromium } from 'playwright'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const FRONTEND = resolve(__dirname, '..')
const REPO = resolve(FRONTEND, '..')
const BACKEND = resolve(REPO, 'backend')

const EXPLICIT_BASE = process.env.BASE_URL
const BASE = EXPLICIT_BASE ?? 'http://localhost:8000'
const PASSWORD = 'medguard'
/** 优先系统 Chrome；没装则用系统 Edge（两者都是 Chromium 内核）。 */
const CHANNEL = process.env.PW_CHANNEL || (
  existsSync(`${process.env.PROGRAMFILES}/Google/Chrome/Application/chrome.exe`) ||
  existsSync(`${process.env['PROGRAMFILES(X86)']}/Google/Chrome/Application/chrome.exe`) ||
  existsSync(`${process.env.LOCALAPPDATA}/Google/Chrome/Application/chrome.exe`)
    ? 'chrome'
    : 'msedge'
)

/** 层一拒答的固定文案。产品承诺「统一措辞、不遮掩、不误导」，故逐字断言。 */
const DENY_TEXT = '该查询涉及其他患者信息，无法提供。'

// ── 断言小工具 ────────────────────────────────────────────────
let nPass = 0
const failures = []

function ok(name, detail = '') {
  nPass++
  console.log(`  ✓ ${name}${detail ? '  ' + detail : ''}`)
}
function fail(name, detail) {
  failures.push(`${name} — ${detail}`)
  console.log(`  ✗ ${name}  ${detail}`)
}
function check(name, cond, detail = '') {
  cond ? ok(name, detail) : fail(name, detail || '(断言不成立)')
}
function checkEq(name, actual, expected) {
  const a = JSON.stringify(actual)
  const e = JSON.stringify(expected)
  a === e ? ok(name, `= ${e}`) : fail(name, `期望 ${e}，实际 ${a}`)
}

// ── 后端：探测 → 必要时自己起 ──────────────────────────────────
const PY_CANDIDATES = [
  process.env.MEDGUARD_PYTHON,
  resolve(BACKEND, '.venv/Scripts/python.exe'),
  resolve(BACKEND, '.venv/bin/python'),
  resolve(BACKEND, '../.venv/Scripts/python.exe'),
  'python3',
  'python',
].filter(Boolean)

let serverProc = null
let spawned = false

async function health(base, timeoutMs = 1500) {
  try {
    const ctl = AbortSignal.timeout(timeoutMs)
    const r = await fetch(`${base}/api/health`, { signal: ctl })
    if (!r.ok) return null
    return await r.json()
  } catch {
    return null
  }
}

async function ensureServer() {
  const existing = await health(BASE)
  if (existing) {
    console.log(`后端：${BASE}（复用已有服务）\n`)
    return
  }
  if (EXPLICIT_BASE) {
    console.error(`[错误] BASE_URL 指定的服务不可达：${EXPLICIT_BASE}`)
    process.exit(1)
  }

  let py = null
  for (const c of PY_CANDIDATES) {
    const probe = spawn(c, ['-c', 'import fastapi, uvicorn, sqlglot'], { stdio: 'ignore' })
    // 候选路径不存在时 spawn 只发 'error' 不发 'close'——两种都结算，
    // 否则 Node 24 会直接抛 unhandled error 终止整个脚本。
    const code = await new Promise((res) => {
      probe.on('close', res)
      probe.on('error', () => res(-1))
    })
    if (code === 0) { py = c; break }
  }
  if (!py) {
    console.error(
      '[错误] 找不到装了依赖（fastapi / uvicorn / sqlglot）的 Python。\n' +
      '       请先建环境，或指定解释器：MEDGUARD_PYTHON=<路径> npm run e2e',
    )
    process.exit(1)
  }

  console.log(`后端：${BASE}（本次由脚本启动，用 ${py}）`)
  serverProc = spawn(
    py,
    ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8000',
     '--no-access-log'],
    { cwd: BACKEND, stdio: 'ignore' },
  )
  spawned = true

  const deadline = Date.now() + 45_000
  while (Date.now() < deadline) {
    if (await health(BASE)) { console.log('后端已就绪\n'); return }
    await new Promise((r) => setTimeout(r, 400))
  }
  console.error('[错误] 后端启动超时（45s）')
  cleanup()
  process.exit(1)
}

function cleanup() {
  if (serverProc && !serverProc.killed) {
    try { serverProc.kill() } catch { /* 忽略 */ }
  }
}

// ── HTTP ──────────────────────────────────────────────────────
async function login(account) {
  const r = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ account, password: PASSWORD }),
  })
  if (!r.ok) throw new Error(`登录失败 ${account}: HTTP ${r.status}`)
  return (await r.json()).token
}

async function runQuery(token, questionId) {
  const r = await fetch(`${BASE}/api/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      token, datasource_id: 'regional_health', question_id: questionId,
    }),
  })
  return { status: r.status, body: await r.json().catch(() => null) }
}

/** 智慧医生一问。AI 建议可能走模型（慢），故有界超时；断言只碰确定性块。 */
async function smartAsk(token, question, timeoutMs = 60_000) {
  const ctl = AbortSignal.timeout(timeoutMs)
  const r = await fetch(`${BASE}/api/smart-doctor/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal: ctl,
    body: JSON.stringify({
      token, datasource_id: 'regional_health', question,
    }),
  })
  return { status: r.status, body: await r.json().catch(() => null) }
}

/** 令牌展平成查询串——GET 端点带不了请求体（同 src/api/scope.ts）。 */
function tokenQuery(t) {
  const p = new URLSearchParams()
  p.set('token_type', t.type)
  if (t.subject_id) p.set('subject_id', t.subject_id)
  if (t.account) p.set('account', t.account)
  p.set('exp', String(t.exp))
  p.set('sig', t.sig)
  return p.toString()
}

// ── 演示矩阵（设计文档 §6.5 / TestPlan §5）─────────────────────
const MATRIX = [
  { q: 'patient_my_lab',         as: 'patient', label: '我上次的血糖是多少',
    admission: true,  level: 'L0', events: 0, hasResult: true },
  { q: 'patient_doctors',        as: 'patient', label: '心内科有哪些医生',
    admission: true,  level: 'L0', events: 0, hasResult: true },
  { q: 'patient_others_count',   as: 'patient', label: '得这个病的有多少人',
    admission: false, level: 'L3', events: 0, hasResult: false },
  { q: 'doctor_dept_visits',     as: 'staff',   label: '统计各科室接诊量',
    admission: true,  level: 'L0', events: 0, hasResult: true },
  { q: 'doctor_diabetes_cost',   as: 'staff',   label: '糖尿病患者产生了多少费用',
    admission: true,  level: 'L2', events: 3, hasResult: true },
  { q: 'doctor_diagnosis_stats', as: 'staff',   label: '按诊断结果分类统计患者数',
    admission: true,  level: 'L2', events: 2, hasResult: true },
  { q: 'doctor_export_roster',   as: 'staff',   label: '导出患者基本信息核对表',
    admission: true,  level: 'L2', events: 1, hasResult: true },
]

// ── 【API】断言 ───────────────────────────────────────────────
async function apiChecks() {
  const tok = { patient: await login('patient'), staff: await login('doctor') }

  console.log('【双令牌矩阵】（设计文档 §6.5）')
  const seen = {}
  for (const m of MATRIX) {
    const { body: r } = await runQuery(tok[m.as], m.q)
    seen[m.q] = r
    if (!r) { fail(`${m.as} · ${m.label}`, '无响应'); continue }
    const got = {
      admission: r.admission.passed,
      level: r.degradation.level,
      events: r.events.length,
      hasResult: r.result != null,
    }
    const want = {
      admission: m.admission, level: m.level,
      events: m.events, hasResult: m.hasResult,
    }
    checkEq(`${m.as.padEnd(7)} · ${m.label}`, got, want)
  }

  console.log('\n【层一拒绝：文案与形状】')
  const denied = seen['patient_others_count']
  if (!denied) {
    fail('层一拒绝响应', '未取到')
  } else {
    checkEq('拒答文案逐字匹配', denied.degradation.message_cn, DENY_TEXT)
    checkEq('拒绝时 plan 为空', denied.plan.length, 0)
    checkEq('拒绝时无结果集', denied.result, null)
    check('拒绝时准入判定为未通过', denied.admission.passed === false)
  }

  console.log('\n【零 LLM、零查库（可验证的安全声明）】')
  const dirty = MATRIX.filter((m) => {
    const r = seen[m.q]
    return !r || r.metrics.llm_calls !== 0 || r.metrics.db_access !== 0
  })
  check(
    `${MATRIX.length}/${MATRIX.length} 条 llm_calls=0 且 db_access=0`,
    dirty.length === 0,
    dirty.length ? `不合规：${dirty.map((d) => d.label).join('、')}` : '',
  )

  console.log('\n【安全报告按账号隔离】')
  const pr = await runQuery(tok.patient, 'patient_my_lab')   // 确保患者有一条报告
  check('患者查询成功（用于生成报告）', pr.status === 200)
  const listRes = await fetch(`${BASE}/api/reports?${tokenQuery(tok.patient)}`)
  const list = listRes.ok ? await listRes.json() : []
  const items = Array.isArray(list) ? list : (list.items ?? [])
  check('患者能列出自己的报告', items.length > 0, `${items.length} 条`)
  if (items.length) {
    const rid = items[0].id
    const asDoctor = await fetch(
      `${BASE}/api/reports/${rid}?${tokenQuery(tok.staff)}`)
    checkEq(`医护读患者的报告 id=${rid} → 404`, asDoctor.status, 404)
    const asOwner = await fetch(
      `${BASE}/api/reports/${rid}?${tokenQuery(tok.patient)}`)
    checkEq(`患者读自己的报告 id=${rid} → 200`, asOwner.status, 200)
  }

  console.log('\n【A1 · 数据集疾病可达（评审 A1）】')
  {
    const { status, body } = await smartAsk(tok.patient, '我想了解帕金森病')
    checkEq('智慧医生 · 帕金森病请求 200', status, 200)
    if (status === 200) {
      checkEq('意图解析为 disease', body.intent, 'disease')
      check('解读标题含「帕金森病」',
        (body.interpretation?.title ?? '').includes('帕金森病'))
      check('事实卡条目非空（不依赖模型）',
        (body.interpretation?.items ?? []).length > 0)
      check('出处标注为开源知识图谱',
        (body.interpretation?.source ?? '').includes('OpenCMKG'))
    }
  }

  console.log('\n【A3 · 跨域个人级 JOIN 检出（评审 A3）】')
  {
    const { body: r } = await runQuery(tok.staff, 'doctor_diabetes_cost')
    if (r) {
      const types = (r.events ?? []).map((e) => e.type)
      check('费用查询事件含 cross_domain_personal_join',
        types.includes('cross_domain_personal_join'), types.join('、'))
    } else {
      fail('A3 跨域事件', '费用查询无响应')
    }
  }

  console.log('\n【B5 · 审计—执行绑定（评审 B5）】')
  {
    // 先跑一条必执行的查询（L0 直通），保证最新报告有真实执行哈希
    await runQuery(tok.staff, 'doctor_dept_visits')
    const listRes = await fetch(`${BASE}/api/reports?${tokenQuery(tok.staff)}`)
    const list = listRes.ok ? await listRes.json() : []
    const rid = list?.[0]?.id
    if (!rid) {
      fail('B5 绑定证据', '取不到医护报告')
    } else {
      const detail = await (
        await fetch(`${BASE}/api/reports/${rid}?${tokenQuery(tok.staff)}`)).json()
      const d = detail.decision ?? {}
      check('报告含 decision_id', Boolean(d.decision_id))
      check('报告含改写后计划哈希', Boolean(d.plan_hashes && Object.keys(d.plan_hashes).length))
      check('报告含实际执行哈希（确实执行了）', Boolean(d.executed_hash))
      check('报告含执行时刻', Boolean(d.executed_at))
      const last = Object.values(d.plan_hashes ?? {}).pop()
      checkEq('实际执行哈希 == 最终子查询哈希（执行的就是审计通过的 SQL）',
        d.executed_hash, last)
    }
  }

  console.log('\n【B6 · 数据源就绪门禁（评审 B6）】')
  {
    const r = await fetch(`${BASE}/api/datasources`)
    const list = r.ok ? await r.json() : []
    const demo = (list ?? []).find((d) => d.id === 'regional_health')
    check('演示数据源存在', Boolean(demo))
    if (demo) {
      checkEq('演示库兼容模式：ready', demo.ready, true)
      checkEq('演示库无未标注列', (demo.unlabeled_columns ?? []).length, 0)
      checkEq('默认非严格模式', demo.strict, false)
    }
  }
}

// ── 【界面】断言 ─────────────────────────────────────────────
async function signIn(page, account) {
  await page.goto(BASE + '/login', { waitUntil: 'networkidle' })
  await page.evaluate(() => sessionStorage.clear())
  await page.reload({ waitUntil: 'networkidle' })
  await page.fill('input[autocomplete="username"]', account)
  await page.fill('input[autocomplete="current-password"]', PASSWORD)
  await page.click('button[type="submit"]')
  await page.waitForTimeout(400)
}

async function uiChecks(browser) {
  console.log('\n【界面】')
  const errors = []

  const newPage = async (as) => {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    const page = await ctx.newPage()
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
    page.on('pageerror', (e) => errors.push(e.message))
    if (as) await signIn(page, as)
    return { ctx, page }
  }

  // ⓪ 未登录首页 → 点「进入平台」进入登录页（React 路由，不整页刷新）
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    const page = await ctx.newPage()
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
    page.on('pageerror', (e) => errors.push(e.message))
    await page.goto(BASE + '/', { waitUntil: 'networkidle' })
    try {
      await page.getByRole('link', { name: /进入平台/ }).first().click()
      await page.waitForSelector('input[autocomplete="username"]', { timeout: 4000 })
      check('未登录首页进入登录页', new URL(page.url()).pathname === '/login', page.url())
    } catch {
      fail('未登录首页进入登录页', '4s 内未出现登录表单')
    }
    await ctx.close()
  }

  // ① 放行：医护点预置问题 → 出现结果表
  {
    const { ctx, page } = await newPage('doctor')
    await page.goto(BASE + '/console', { waitUntil: 'networkidle' })
    await page.getByRole('button', { name: '糖尿病患者产生了多少费用' }).click()
    try {
      await page.waitForSelector('.panel-result', { timeout: 8000 })
      ok('控制台 · 放行路径渲染出结果表', '（.panel-result）')
    } catch {
      fail('控制台 · 放行路径渲染出结果表', '8s 内未出现 .panel-result')
    }
    await ctx.close()
  }

  // ② 拒绝：病患点涉他查询 → 拒答文案逐字出现 + 无结果表 + 给出出路
  {
    const { ctx, page } = await newPage('patient')
    await page.goto(BASE + '/console', { waitUntil: 'networkidle' })
    await page.getByRole('button', { name: '得这个病的有多少人' }).click()
    try {
      await page.waitForSelector('.admission-refusal', { timeout: 8000 })
      const text = (await page.locator('.admission-refusal').innerText()).trim()
      checkEq('控制台 · 层一拒答文案逐字匹配', text, DENY_TEXT)
      const n = await page.locator('.panel-result').count()
      checkEq('控制台 · 拒绝时不渲染结果表', n, 0)
      // 被拒绝后要给出出路——不能只留一句拒答让用户自己猜还能问什么
      await page.waitForSelector('.next-steps', { timeout: 4000 })
      const btns = await page.locator('.next-steps .preset-chip').count()
      check('控制台 · 拒绝后给出可问清单', btns > 0, `${btns} 条`)
    } catch {
      fail('控制台 · 拒绝路径渲染', '8s 内未出现 .admission-refusal 或 .next-steps')
    }
    await ctx.close()
  }

  // ③ 指标条：断言两个 0 真的显示出来
  {
    const { ctx, page } = await newPage('doctor')
    await page.goto(BASE + '/console', { waitUntil: 'networkidle' })
    await page.getByRole('button', { name: '统计各科室接诊量' }).click()
    try {
      await page.waitForSelector('.metrics-bar', { timeout: 8000 })
      const txt = await page.locator('.metrics-bar').innerText()
      check('指标条显示「LLM 调用 0」', /LLM 调用\s*0/.test(txt), txt.replace(/\s+/g, ' '))
      check('指标条显示「数据库访问 0」', /数据库访问\s*0/.test(txt))
    } catch {
      fail('指标条渲染', '8s 内未出现 .metrics-bar')
    }
    await ctx.close()
  }

  // ④ 安全报告页能渲染
  {
    const { ctx, page } = await newPage('doctor')
    await page.goto(BASE + '/reports', { waitUntil: 'networkidle' })
    try {
      await page.waitForSelector('.report-list', { timeout: 8000 })
      ok('安全报告页渲染出列表', '（.report-list）')
    } catch {
      fail('安全报告页渲染出列表', '8s 内未出现 .report-list')
    }
    await ctx.close()
  }

  // ⑤ 智慧医生：首轮之后追问入口必须还在
  {
    const { ctx, page } = await newPage('patient')   // 病患登录后默认页即智慧医生
    try {
      await page.waitForSelector('.sd-tasks', { timeout: 8000 })
      const before = await page.locator('.sd-followup').count()
      checkEq('智慧医生 · 首屏只出任务网格、不出追问行', before, 0)
      await page.getByRole('button', { name: '帮我看懂检查报告' }).click()
      await page.waitForSelector('.sd-followup', { timeout: 12000 })
      const n = await page.locator('.sd-followup .sd-task').count()
      check('智慧医生 · 首轮之后追问入口仍在', n > 0, `${n} 个`)
    } catch {
      fail('智慧医生 · 追问入口', '12s 内未按预期出现')
    }
    await ctx.close()
  }

  // ⑥ 智慧医生：RAG 检索来源的渲染。
  //    AI 兜底路径要配了 API Key 才走得到，所以这里用**路由 mock**把那个响应
  //    形态固定下来——否则"检索到的条目有没有渲染出来"永远没人验证过。
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1100 } })
    const page = await ctx.newPage()
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
    page.on('pageerror', (e) => errors.push(e.message))
    await page.route('**/api/smart-doctor/ask', (route) => route.fulfill({
      json: {
        intent: 'fallback',
        question: '吃了他汀之后肌肉酸痛',
        admission: { passed: true, reason: null, checked_tables: [], bound_to_subject: false },
        degradation: { level: 'L0', label: '通过', message: '', message_cn: '' },
        data: null,
        interpretation: {
          source: 'AI 智能导诊',
          title: '用药咨询',
          text: '他汀类药物可能引起肌肉酸痛，建议告知医生。',
          items: [{
            ref_title: '阿托伐他汀', ref_kind: '药品',
            ref_source: '医院审核知识库',
            ref_excerpt: '出现不明原因的肌肉酸痛、乏力请及时告知医生。',
            ref_id: 'drug_atorvastatin', ref_score: 6.76,
          }],
        },
        advice: { source: 'AI 智能导诊', text: '请咨询医生。', actions: ['遵医嘱'], urgent: false },
      },
    }))
    await signIn(page, 'patient')
    try {
      await page.fill('.sd-ask textarea', '吃了他汀之后肌肉酸痛')
      await page.click('.sd-ask button')
      await page.waitForSelector('.sd-ref', { timeout: 8000 })
      // 取整个条目（.sd-item）：标题在 .sd-item-line 里，与 .sd-ref 是兄弟节点，
      // 只取 .sd-ref 会漏掉标题——第一版就是这么写错的。
      const txt = await page.locator('.sd-item').first().innerText()
      check('智慧医生 · 渲染出 RAG 检索来源', txt.includes('阿托伐他汀'),
        txt.replace(/\s+/g, ' ').slice(0, 46))
      check('智慧医生 · 逐条标注条目出处', txt.includes('医院审核知识库'))
    } catch {
      fail('智慧医生 · RAG 检索来源渲染', '8s 内未出现 .sd-ref')
    }
    await ctx.close()
  }

  // ⑦ 智慧医生：多轮上下文。
  //    断言**第二轮请求带上了上一轮的 {intent, entity}**——这是「它正常吗」
  //    这类指代能接上的前提。用路由 mock 把两轮的请求体都记下来。
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1100 } })
    const page = await ctx.newPage()
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
    page.on('pageerror', (e) => errors.push(e.message))
    const seen = []
    await page.route('**/api/smart-doctor/ask', async (route) => {
      try { seen.push(JSON.parse(route.request().postData() || '{}')) } catch { seen.push({}) }
      await route.fulfill({
        json: {
          intent: 'lab', entity: '血糖', question: 'x',
          admission: { passed: true, reason: null, checked_tables: ['clinical_records'], bound_to_subject: true },
          degradation: { level: 'L0', label: '通过', message: '', message_cn: '' },
          data: {
            source: '医院主库', columns: ['test_name', 'result_value'],
            rows: [['血糖', '6.3']], sql_after: '', degradation_level: 'L0',
          },
          interpretation: { source: '医院审核知识库', title: '血糖结果解读', text: '……', items: [] },
          advice: { source: '医院审核知识库', text: '遵医嘱。', actions: [], urgent: false },
        },
      })
    })
    await signIn(page, 'patient')
    try {
      await page.fill('.sd-ask textarea', '我的血糖结果正常吗')
      await page.click('.sd-ask button')
      await page.waitForTimeout(500)
      await page.fill('.sd-ask textarea', '它正常吗')
      await page.click('.sd-ask button')
      await page.waitForTimeout(500)
      checkEq('智慧医生 · 首轮不带 context', seen[0]?.context ?? null, null)
      checkEq('智慧医生 · 追问带上上一轮的 intent/entity',
        seen[1]?.context ?? null, { intent: 'lab', entity: '血糖' })
    } catch (e) {
      fail('智慧医生 · 多轮上下文', String(e).slice(0, 80))
    }
    await ctx.close()
  }

  // ⑧ 智慧医生：离线引导形态（无 API Key 时的确定性文案）。
  //    用路由 mock 固定「无 Key 回退」的响应形状，断言引导文案渲染出来
  //    ——否则"没配 Key 的机器上患者看到什么"永远没人验证过。
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1100 } })
    const page = await ctx.newPage()
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
    page.on('pageerror', (e) => errors.push(e.message))
    await page.route('**/api/smart-doctor/ask', (route) => route.fulfill({
      json: {
        intent: 'fallback',
        question: '怎么办理出院手续',
        admission: { passed: true, reason: null, checked_tables: [], bound_to_subject: false },
        degradation: { level: 'L0', label: '通过', message: '', message_cn: '' },
        data: null,
        interpretation: {
          source: '医院审核知识库',
          title: '换个问法试试',
          text: '可以这样问我：……',
          items: [],
        },
        advice: { source: '医院审核知识库', text: '', actions: [], urgent: false },
      },
    }))
    await signIn(page, 'patient')
    try {
      await page.fill('.sd-ask textarea', '怎么办理出院手续')
      await page.click('.sd-ask button')
      await page.waitForFunction(
        () => document.body.innerText.includes('换个问法试试'),
        { timeout: 8000 })
      ok('智慧医生 · 离线引导文案渲染', '（换个问法试试）')
    } catch {
      fail('智慧医生 · 离线引导文案渲染', '8s 内未出现引导文案')
    }
    await ctx.close()
  }

  check('界面无控制台报错', errors.length === 0,
    errors.length ? errors.slice(0, 3).join(' | ') : '')
}

// ── 主流程 ────────────────────────────────────────────────────
console.log('医盾 · 端到端回归核查\n')
await ensureServer()

let browser = null
try {
  await apiChecks()
  browser = await chromium.launch({ channel: CHANNEL })
  await uiChecks(browser)
} catch (e) {
  console.error(`\n[异常] ${e.message}`)
  failures.push(`异常：${e.message}`)
} finally {
  if (browser) await browser.close()
  cleanup()
}

console.log(`\n${'─'.repeat(60)}`)
if (failures.length) {
  console.log(`通过 ${nPass} · 失败 ${failures.length}\n`)
  for (const f of failures) console.log('  ✗ ' + f)
  if (spawned) console.log('\n（本次后端由脚本启动，已停止）')
  process.exit(1)
} else {
  console.log(`全部通过：${nPass} 项`)
  if (spawned) console.log('（本次后端由脚本启动，已停止）')
}
