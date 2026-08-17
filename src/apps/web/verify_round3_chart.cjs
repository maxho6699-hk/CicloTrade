const { chromium } = require('playwright')

const BASE = process.env.CICLO_PREVIEW_URL || 'http://localhost:5175'
const MOCK = process.env.CICLO_MOCK_URL || 'http://localhost:5180'

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

async function openPage(browser, viewport, route) {
  const context = await browser.newContext({ viewport })
  const page = await context.newPage()
  const bootstrap = await fetch(`${MOCK}/api/rewrite/v1/bootstrap`).then((response) => response.json())
  bootstrap.me = { ...(bootstrap.me || {}), admin_role: 'user' }
  await page.route('**/api/**', async (handler) => {
    const url = new URL(handler.request().url())
    if (url.pathname.endsWith('/session/refresh')) return handler.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ authenticated: true, access_token: 'x' }) })
    if (url.pathname.endsWith('/bootstrap')) return handler.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bootstrap) })
    const response = await handler.fetch({ url: `${MOCK}${url.pathname}${url.search}` })
    return handler.fulfill({ response })
  })
  await page.goto(`${BASE}${route}`, { waitUntil: 'networkidle', timeout: 40_000 })
  await page.waitForTimeout(500)
  return { context, page }
}

;(async () => {
  const browser = await chromium.launch({ headless: true })
  let passed = 0
  try {
    {
      const { context, page } = await openPage(browser, { width: 1440, height: 900 }, '/research?symbol=AAPL&market=US')
      const dimensions = await page.locator('.chart-tool-panel').evaluate((panel) => {
        const content = panel.querySelector('.chart-tool-panel-content')
        return { panel: panel.getBoundingClientRect().height, content: content.getBoundingClientRect().height, scroll: content.scrollHeight }
      })
      assert(dimensions.content >= dimensions.panel * 0.9, `research tools occupy only ${dimensions.content}/${dimensions.panel}px`)
      assert(dimensions.scroll >= dimensions.content, 'research tools lost scrollable content')
      passed += 1

      await page.locator('button[title*="全屏"],button[title*="全螢幕"]').last().click()
      await page.waitForTimeout(120)
      const fullscreenPicker = await page.evaluate(() => {
        const shell = document.querySelector('.chart-workspace-shell')
        const trigger = shell?.querySelector('.chart-symbol-trigger')
        return {
          shellClass: shell?.className || '',
          fullscreen: Boolean(document.fullscreenElement),
          triggerCount: shell?.querySelectorAll('.chart-symbol-trigger').length || 0,
          triggerDisplay: trigger ? getComputedStyle(trigger).display : 'missing',
          toolbarDisplay: trigger?.closest('.chart-slot-toolbar') ? getComputedStyle(trigger.closest('.chart-slot-toolbar')).display : 'missing',
        }
      })
      assert(fullscreenPicker.triggerCount > 0 && fullscreenPicker.triggerDisplay !== 'none' && fullscreenPicker.toolbarDisplay !== 'none', `fullscreen chart has no stock picker: ${JSON.stringify(fullscreenPicker)}`)
      await page.locator('.chart-workspace-shell.is-workbench-open .chart-symbol-trigger').first().click()
      assert(await page.locator('.chart-symbol-popover').isVisible(), 'fullscreen stock picker did not open')
      passed += 1
      await context.close()
    }

    {
      const { context, page } = await openPage(browser, { width: 1440, height: 900 }, '/research?symbol=AAPL&market=US')
      await page.locator('.layout-picker-trigger').click()
      await page.getByRole('button', { name: /左右双图|左右雙圖/ }).click()
      const secondSlot = page.locator('.chart-slot').nth(1)
      await secondSlot.locator('.timeframe-dropdown-trigger').click()
      await page.getByRole('option', { name: '1 日' }).click()
      await page.waitForFunction(() => document.querySelectorAll('.chart-slot:not(.is-layout-hidden) canvas').length >= 2, null, { timeout: 15_000 })
      const firstCanvas = await page.locator('.chart-slot:not(.is-layout-hidden) .chart-slot-canvas').first().boundingBox()
      assert(firstCanvas, 'first chart canvas missing')
      await page.mouse.move(firstCanvas.x + firstCanvas.width * 0.55, firstCanvas.y + firstCanvas.height * 0.5)
      await page.mouse.wheel(0, -640)
      await page.waitForTimeout(900)
      const viewports = await page.evaluate(() => JSON.parse(localStorage.getItem('ciclotrade:chart-workspace:v2') || '{}').slots?.slice(0, 2).map((slot) => slot.viewport))
      assert(viewports?.length === 2 && viewports.every(Boolean), 'synchronized chart viewports were not persisted')
      assert(Math.abs(viewports[0].from - viewports[1].from) < 0.75 && Math.abs(viewports[0].to - viewports[1].to) < 0.75, `same-stock viewports diverged: ${JSON.stringify(viewports)}`)
      passed += 1
      await context.close()
    }

    {
      const { context, page } = await openPage(browser, { width: 1440, height: 900 }, '/research?symbol=AAPL&market=US')
      await page.getByRole('button', { name: /线段|線段/ }).click()
      const overlay = page.locator('.drawing-overlay.active')
      const box = await overlay.boundingBox()
      assert(box, 'active drawing overlay missing')
      await page.mouse.click(box.x + box.width - 2, box.y + box.height * 0.42)
      await page.mouse.click(box.x + box.width * 0.52, box.y + box.height * 0.25)
      await page.waitForTimeout(80)
      const group = page.locator('.drawing-shape-group').first()
      assert(await group.count() === 1, 'edge-tolerant segment was not created')
      const before = await group.boundingBox()
      assert(before, 'created drawing has no geometry')
      await page.mouse.move(before.x + before.width * 0.5, before.y + before.height * 0.5)
      await page.mouse.down()
      await page.mouse.move(before.x + before.width * 0.5 + 34, before.y + before.height * 0.5 + 18, { steps: 5 })
      await page.mouse.up()
      await page.waitForTimeout(100)
      const after = await group.boundingBox()
      assert(after && (Math.abs(after.x - before.x) > 8 || Math.abs(after.y - before.y) > 8), `drawing did not move: ${JSON.stringify({ before, after })}`)
      passed += 2
      await context.close()
    }

    {
      const { context, page } = await openPage(browser, { width: 1100, height: 820 }, '/deliberation?market=US&symbol=TSLA')
      const draft = await page.locator('.deliberation-side-operation.is-draft .deliberation-secondary-action').evaluate((element) => {
        const box = element.getBoundingClientRect()
        return { width: box.width, height: box.height, whiteSpace: getComputedStyle(element).whiteSpace }
      })
      assert(draft.width > draft.height * 2.4 && draft.whiteSpace === 'nowrap', `draft action is vertical/clipped: ${JSON.stringify(draft)}`)
      const tools = await page.locator('.deliberation-evidence-tools').evaluate((element) => {
        const mode = element.querySelector('.deliberation-evidence-mode').getBoundingClientRect()
        const refresh = element.querySelector('.deliberation-evidence-refresh').getBoundingClientRect()
        return { modeY: mode.y + mode.height / 2, refreshY: refresh.y + refresh.height / 2, refreshWidth: refresh.width, text: element.textContent }
      })
      assert(Math.abs(tools.modeY - tools.refreshY) < 4 && tools.refreshWidth > 58 && /刷新|重新整理/.test(tools.text), `evidence toolbar is misaligned: ${JSON.stringify(tools)}`)
      passed += 2
      await context.close()
    }
  } finally {
    await browser.close()
  }
  assert(passed === 7, `expected 7 checks, got ${passed}`)
  console.log(`PASS=${passed}/7 ROUND3_CHART`)
})().catch((error) => {
  console.error(error.stack || error)
  process.exit(1)
})
