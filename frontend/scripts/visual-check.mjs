/**
 * 视觉核查：把关键页面走一遍并截图，同时收集控制台报错。
 *
 *   npm run visual              → 截图到 ../.visual-out/
 *   npm run visual -- <目录>     → 指定输出目录
 *
 * 用系统已装的 Chrome（channel: 'chrome'），不下载 Playwright 自带浏览器。
 * 录演示视频前跑一遍，能挡住「样式塌了 / 页面白屏 / 接口报错」这类翻车。
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const BASE = process.env.BASE_URL ?? 'http://localhost:5173'
const OUT = resolve(process.argv[2] ?? '../.visual-out')
const VIEWPORT = { width: 1440, height: 900 }

mkdirSync(OUT, { recursive: true })

/**
 * @type {{
 *   name: string
 *   path: string
 *   act?: (p: import('playwright').Page) => Promise<void>
 *   height?: number                  // 内容较长的页面（如展开的库表结构）调高
 *   mock?: Record<string, unknown>   // 按 pathname 精确 mock 接口响应
 * }[]}
 */
const SHOTS = [
  { name: '01-home-staff', path: '/', height: 1500 },
  {
    name: '02-home-patient',
    path: '/',
    act: async (p) => { await p.getByRole('button', { name: '病患' }).click() },
  },
  { name: '03-policy', path: '/policy' },
  { name: '04-console', path: '/console' },
  { name: '05-reports', path: '/reports' },
  // 空态：未载入演示数据。用一个空数组顶掉真实响应即可，无需清库。
  { name: '06-home-empty', path: '/', mock: { '/api/datasources': [] } },
]

const browser = await chromium.launch({ channel: 'chrome' })
const ctx = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 2 })
const errors = []

for (const shot of SHOTS) {
  const page = await ctx.newPage()
  if (shot.height) await page.setViewportSize({ ...VIEWPORT, height: shot.height })
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`[${shot.name}] console: ${m.text()}`)
  })
  page.on('pageerror', (e) => errors.push(`[${shot.name}] pageerror: ${e.message}`))

  // 精确匹配 pathname，避免 /api/datasources 的规则误伤 /api/datasources/{id}/schema
  for (const [pathname, body] of Object.entries(shot.mock ?? {})) {
    await page.route(
      (url) => url.pathname === pathname,
      (route) => route.fulfill({ json: body }),
    )
  }

  await page.goto(BASE + shot.path, { waitUntil: 'networkidle' })
  if (shot.act) await shot.act(page)
  await page.waitForTimeout(400)          // 等入场动效落定
  await page.screenshot({ path: `${OUT}/${shot.name}.png` })
  console.log(`  ✓ ${shot.name}.png`)
  await page.close()
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
