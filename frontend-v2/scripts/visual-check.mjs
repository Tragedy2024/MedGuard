/**
 * 视觉核查：按不同身份登录、遍历关键页面并截图，同时收集控制台报错。
 *
 *   npm run visual              → 截图到 ../.visual-out/
 *   npm run visual -- <目录>     → 指定输出目录
 *
 * 用系统已装的 Chrome（channel: 'chrome'），不下载 Playwright 自带浏览器。
 * 录演示视频前跑一遍，能挡住「样式塌了 / 页面白屏 / 接口报错 / 越权能进」
 * 这类翻车。
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const BASE = process.env.BASE_URL ?? 'http://localhost:5173'
const OUT = resolve(process.argv[2] ?? '../.visual-out')
const VIEWPORT = { width: 1440, height: 900 }
const PASSWORD = 'medguard'

mkdirSync(OUT, { recursive: true })

/**
 * @type {{
 *   name: string
 *   path: string
 *   as?: 'admin' | 'doctor' | 'patient'   // 省略则停在登录页
 *   height?: number
 *   act?: (p: import('playwright').Page) => Promise<void>
 *   mock?: Record<string, unknown>        // 按 pathname 精确 mock
 * }[]}
 */
const SHOTS = [
  { name: '00-login', path: '/' },
  { name: '01-scope-doctor', path: '/', as: 'doctor', height: 1400 },
  { name: '02-scope-patient', path: '/', as: 'patient', height: 1400 },
  { name: '03-datasource-admin', path: '/', as: 'admin', height: 1500 },
  { name: '04-policy', path: '/policy', as: 'admin', height: 1300 },
  {
    name: '04b-policy-rules',
    path: '/policy',
    as: 'admin',
    height: 1000,
    act: async (p) => {
      await p.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
      await p.waitForTimeout(250)
    },
  },
  // 路由守卫：医生直接敲 /policy 应被挡住，而不是渲染出策略页
  { name: '05-forbidden', path: '/policy', as: 'doctor' },

  // 控制台现在只呈现问题与结果；审计明细搬到安全报告页
  {
    name: '06-console-result',
    path: '/console',
    as: 'doctor',
    height: 1000,
    act: (p) => p.getByRole('button', { name: '糖尿病患者产生了多少费用' }).click(),
  },
  {
    // 自由提问：用预热过的问法（缓存命中，瞬时）。未命中的话要等模型
    // 翻译约 70 秒，截图脚本不该卡在那里。
    name: '06b-console-freetext',
    path: '/console',
    as: 'doctor',
    height: 1000,
    act: async (p) => {
      await p.fill('.ask-bar input', '全院各科室的门诊量分别是多少')
      await p.click('.ask-bar button')
      await p.waitForTimeout(900)
    },
  },
  {
    name: '06c-console-denied',
    path: '/console',
    as: 'patient',
    height: 900,
    act: (p) => p.getByRole('button', { name: '得这个病的有多少人' }).click(),
  },

  // 安全报告：左列表 + 右详情（首次进入自动选中最新一条）
  { name: '07-reports', path: '/reports', as: 'admin', height: 1600 },
  {
    name: '07b-reports-detail-sql',
    path: '/reports',
    as: 'admin',
    height: 1900,
    act: async (p) => {
      // 选一条**有分析内容**的记录——最新一条可能是 L3 拒绝（无分解方案），
      // 那样展开 SQL 会扑空，截不到东西。
      await p.waitForTimeout(700)
      const item = p
        .locator('.report-list button')
        .filter({ hasText: '糖尿病患者产生了多少费用' })
        .first()
      if (await item.count()) {
        await item.click()
        await p.waitForTimeout(600)
      }
      const sql = p.locator('.sql-diff > summary').first()
      if (await sql.count()) {
        await sql.click()
        await p.waitForTimeout(300)
      }
    },
  },
  // 未知地址应是「页面不存在」，而不是误报成权限问题
  { name: '08-not-found', path: '/no-such-page', as: 'admin' },
]

async function signIn(page, account) {
  await page.goto(BASE + '/', { waitUntil: 'networkidle' })
  await page.evaluate(() => sessionStorage.clear())
  await page.reload({ waitUntil: 'networkidle' })
  await page.fill('input[autocomplete="username"]', account)
  await page.fill('input[autocomplete="current-password"]', PASSWORD)
  await page.click('button[type="submit"]')
  await page.waitForTimeout(300)
}

const browser = await chromium.launch({ channel: 'chrome' })
const errors = []

for (const shot of SHOTS) {
  // 每个截屏独立 context：sessionStorage 天然隔离，身份不会串场
  const ctx = await browser.newContext({
    viewport: { ...VIEWPORT, height: shot.height ?? VIEWPORT.height },
    deviceScaleFactor: 2,
  })
  const page = await ctx.newPage()

  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`[${shot.name}] console: ${m.text()}`)
  })
  page.on('pageerror', (e) => errors.push(`[${shot.name}] pageerror: ${e.message}`))

  // 精确匹配 pathname，避免 /api/datasources 的规则误伤 /{id}/schema
  for (const [pathname, body] of Object.entries(shot.mock ?? {})) {
    await page.route(
      (url) => url.pathname === pathname,
      (route) => route.fulfill({ json: body }),
    )
  }

  if (shot.as) {
    await signIn(page, shot.as)
    if (shot.path !== '/') {
      await page.goto(BASE + shot.path, { waitUntil: 'networkidle' })
    }
  } else {
    await page.goto(BASE + shot.path, { waitUntil: 'networkidle' })
  }

  if (shot.act) await shot.act(page)
  await page.waitForTimeout(400) // 等入场动效落定
  await page.screenshot({ path: `${OUT}/${shot.name}.png` })
  console.log(`  ✓ ${shot.name}.png`)
  await ctx.close()
}

await browser.close()

console.log(`\n输出目录：${OUT}`)
if (errors.length) {
  console.log(`\n发现 ${errors.length} 条报错：`)
  for (const e of errors) console.log('  ✗ ' + e)
  process.exitCode = 1
} else {
  console.log('\n无控制台报错。')
}
