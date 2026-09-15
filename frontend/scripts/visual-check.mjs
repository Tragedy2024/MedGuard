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

  // 控制台三条关键路径：层二跨域拦截 / 展开 SQL / 层一拒绝
  {
    name: '06-console-audit',
    path: '/console',
    as: 'doctor',
    height: 1500,
    act: (p) => p.getByRole('button', { name: '糖尿病患者产生了多少费用' }).click(),
  },
  {
    name: '06b-console-sql-expanded',
    path: '/console',
    as: 'doctor',
    height: 1400,
    act: async (p) => {
      await p.getByRole('button', { name: '糖尿病患者产生了多少费用' }).click()
      await p.waitForTimeout(500)
      await p.locator('.sql-diff > summary').first().click()
      await p.waitForTimeout(250)
    },
  },
  {
    name: '06c-console-denied',
    path: '/console',
    as: 'patient',
    height: 1100,
    act: (p) => p.getByRole('button', { name: '得这个病的有多少人' }).click(),
  },

  { name: '07-reports', path: '/reports', as: 'admin' },
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
