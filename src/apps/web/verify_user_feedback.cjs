const { chromium } = require('playwright')

const BASE = process.env.CICLO_PREVIEW_URL || 'http://localhost:5175'
const MOCK = process.env.CICLO_MOCK_URL || 'http://localhost:5180'

async function openPage(browser, viewport, route, theme = 'dark') {
  const context = await browser.newContext({ viewport })
  const page = await context.newPage()
  const bootstrap = await fetch(`${MOCK}/api/rewrite/v1/bootstrap`).then((response) => response.json())
  bootstrap.me = { ...(bootstrap.me || {}), admin_role: 'user' }
  await page.addInitScript((value) => localStorage.setItem('ciclotrade.theme', value), theme)
  await page.route('**/api/**', async (request) => {
    const url = new URL(request.request().url())
    if (url.pathname.endsWith('/session/refresh')) return request.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ authenticated: true, access_token: 'x' }) })
    if (url.pathname.endsWith('/bootstrap')) return request.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bootstrap) })
    const response = await request.fetch({ url: `${MOCK}${url.pathname}${url.search}` })
    return request.fulfill({ response })
  })
  await page.goto(`${BASE}${route}`, { waitUntil: 'networkidle', timeout: 40_000 })
  await page.waitForTimeout(500)
  return { context, page }
}

async function rect(page, selector) {
  return page.locator(selector).first().evaluate((element) => {
    const box = element.getBoundingClientRect()
    const style = getComputedStyle(element)
    return { x: box.x, y: box.y, width: box.width, height: box.height, right: box.right, bottom: box.bottom, position: style.position, overflow: style.overflow }
  })
}

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

async function assertVisibleTypography(page, label) {
  const violations = await page.evaluate(() => {
    const visible = (element) => {
      const style = getComputedStyle(element)
      const box = element.getBoundingClientRect()
      return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > 0 && box.width > 0 && box.height > 0
    }
    return [...document.querySelectorAll('body *')].filter(visible).filter((element) => {
      const ownsText = [...element.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim())
      const control = ['BUTTON', 'INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName)
      if (!ownsText && !control) return false
      const size = Number.parseFloat(getComputedStyle(element).fontSize)
      const required = ['P', 'BUTTON', 'INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName) ? 13 : 11
      return Number.isFinite(size) && size + 0.01 < required
    }).map((element) => `${element.tagName}.${String(element.className).slice(0, 60)}=${getComputedStyle(element).fontSize}`).slice(0, 8)
  })
  assert(violations.length === 0, `${label} has undersized visible text: ${violations.join(', ')}`)
}

;(async () => {
  const browser = await chromium.launch({ headless: true })
  let checks = 0

  {
    const { context, page } = await openPage(browser, { width: 1917, height: 1013 }, '/discover')
    const left = await rect(page, '.discover-left-rail')
    const center = await rect(page, '.discover-center-column')
    const right = await rect(page, '.discover-right-rail')
    assert(center.width > 700, 'Discover candidate matrix collapsed')
    assert(left.right <= center.x + 1 && center.right <= right.x + 1, 'Discover columns overlap')
    await assertVisibleTypography(page, 'Discover')
    checks += 2
    await context.close()
  }

  {
    const { context, page } = await openPage(browser, { width: 1114, height: 841 }, '/research?symbol=AAPL&market=US')
    const tools = await rect(page, '.chart-tool-panel')
    const drawing = await rect(page, '.drawing-tools-root')
    const chart = await rect(page, '.multi-chart-grid')
    const layout = await rect(page, '.layout-picker-trigger')
    const star = await page.locator('.workbench-watchlist-toggle').evaluate((element) => getComputedStyle(element).color)
    assert(chart.width > 500, 'K-line lost its primary width')
    assert(tools.right <= drawing.x + 1 && drawing.right <= chart.x + 1, 'Research/drawing/chart order is wrong')
    assert(layout.x < chart.x, 'Layout picker is not beside the left workbench controls')
    assert(star === 'rgb(250, 204, 21)', 'Dark saved star is not yellow')
    await page.locator('.layout-picker-trigger').click()
    const picker = await rect(page, '.layout-picker-popover')
    assert(picker.x >= 0 && picker.right <= 1114, 'Layout picker is clipped')
    await assertVisibleTypography(page, 'Research layout picker')
    checks += 5
    await context.close()
  }

  {
    const { context, page } = await openPage(browser, { width: 1420, height: 700 }, '/research?symbol=AAPL&market=US', 'light')
    const star = await page.locator('.workbench-watchlist-toggle').evaluate((element) => getComputedStyle(element).color)
    assert(star === 'rgb(220, 38, 38)', 'Light saved star is not red')
    checks += 1
    await context.close()
  }

  {
    const { context, page } = await openPage(browser, { width: 1062, height: 599 }, '/research?symbol=AAPL&market=US')
    await page.locator('.layout-picker-trigger').click()
    await page.getByRole('button', { name: /上下雙圖|上下双图/ }).click()
    const split = await page.evaluate(() => {
      const box = (element) => { const value = element.getBoundingClientRect(); return { x: value.x, width: value.width, height: value.height, right: value.right, bottom: value.bottom } }
      const grid = document.querySelector('.multi-chart-grid')
      return { grid: box(grid), slots: [...document.querySelectorAll('.chart-slot:not(.is-layout-hidden)')].map(box) }
    })
    assert(split.slots.length === 2 && split.slots.every((slot) => slot.height >= 220), 'Split charts are vertically clipped')
    assert(split.slots.at(-1).bottom <= split.grid.bottom + 1, 'Lower split chart escapes its grid')
    await page.locator('.chart-symbol-trigger').first().click()
    const symbolPicker = await page.locator('.chart-symbol-popover').evaluate((element) => {
      const box = element.getBoundingClientRect()
      const grid = document.querySelector('.multi-chart-grid').getBoundingClientRect()
      return { x: box.x, right: box.right, gridLeft: grid.x, gridRight: grid.right, slotOverflow: getComputedStyle(element.closest('.chart-slot')).overflow, gridOverflow: getComputedStyle(document.querySelector('.multi-chart-grid')).overflow }
    })
    assert(symbolPicker.x >= symbolPicker.gridLeft - 1 && symbolPicker.right <= symbolPicker.gridRight + 1, 'Symbol picker is horizontally clipped')
    assert(symbolPicker.slotOverflow === 'visible' && symbolPicker.gridOverflow === 'visible', 'Symbol picker is clipped by split containers')
    await assertVisibleTypography(page, 'Split chart symbol picker')
    checks += 4
    await context.close()
  }

  {
    const { context, page } = await openPage(browser, { width: 813, height: 798 }, '/recommendations')
    const firstBefore = await rect(page, '.recommendation-preview-card:nth-child(1)')
    const secondBefore = await rect(page, '.recommendation-preview-card:nth-child(2)')
    await page.locator('.recommendation-preview-card').first().locator('.recommendation-expand').click()
    const firstAfter = await rect(page, '.recommendation-preview-card:nth-child(1)')
    const secondAfter = await rect(page, '.recommendation-preview-card:nth-child(2)')
    const drawer = await rect(page, '.recommendation-detail-drawer')
    assert(Math.abs(firstBefore.height - firstAfter.height) < 1, 'Recommendation card stretches when detail opens')
    assert(Math.abs((firstBefore.y - secondBefore.y) - (firstAfter.y - secondAfter.y)) < 1, 'Recommendation grid loses row alignment')
    assert(drawer.position === 'fixed' && drawer.right <= 813.5, 'Desktop recommendation drawer is outside viewport')
    await assertVisibleTypography(page, 'Recommendation drawer')
    checks += 3
    await context.close()
  }

  {
    const { context, page } = await openPage(browser, { width: 390, height: 844 }, '/recommendations')
    await page.locator('.recommendation-preview-card').first().locator('.recommendation-expand').click()
    const sheet = await rect(page, '.recommendation-detail-drawer')
    assert(sheet.position === 'fixed' && sheet.right <= 390.5 && sheet.bottom <= 844.5 && sheet.y > 0, 'Mobile recommendation detail is not a bottom sheet')
    await assertVisibleTypography(page, 'Recommendation bottom sheet')
    checks += 1
    await context.close()
  }

  {
    const { context, page } = await openPage(browser, { width: 841, height: 626 }, '/research?symbol=AAPL&market=US')
    await page.locator('button[title*="全屏"],button[title*="全螢幕"]').last().click()
    await page.waitForTimeout(150)
    const shell = await rect(page, '.chart-workspace-shell.is-workbench-open')
    assert(Boolean(await page.evaluate(() => document.fullscreenElement)), 'Fullscreen API did not activate')
    assert(shell.x === 0 && shell.y === 0 && Math.abs(shell.width - 841) < 1 && Math.abs(shell.height - 626) < 1, 'Fullscreen K-line does not fill viewport')
    await assertVisibleTypography(page, 'Fullscreen K-line')
    checks += 2
    await context.close()
  }

  {
    const { context, page } = await openPage(browser, { width: 841, height: 626 }, '/research?symbol=AAPL&market=US')
    await page.evaluate(() => { Element.prototype.requestFullscreen = () => Promise.reject(new Error('blocked by test')) })
    await page.locator('button[title*="全屏"],button[title*="全螢幕"]').last().click()
    await page.waitForTimeout(80)
    assert(await page.locator('.chart-workspace-shell.is-workbench-open').isVisible(), 'Fullscreen rejection should retain CSS workbench fallback')
    assert(!await page.evaluate(() => document.fullscreenElement), 'Rejected Fullscreen API unexpectedly activated')
    checks += 2
    await context.close()
  }

  {
    const { context, page } = await openPage(browser, { width: 1690, height: 700 }, '/ai')
    const header = await rect(page, '.ai-workspace-header')
    const status = await page.locator('.ai-workspace-header .intelligence-status').textContent()
    const title = await page.locator('.ai-workspace-header h1').evaluate((element) => { const style = getComputedStyle(element); return { color: style.color, fill: style.webkitTextFillColor, background: style.backgroundImage } })
    assert(header.height >= 120 && header.height <= 180, 'AI header has unbalanced height')
    assert(!status.includes('unavailable'), 'AI header leaks raw backend status')
    assert(title.color !== 'rgba(0, 0, 0, 0)' && title.fill !== 'rgba(0, 0, 0, 0)' && title.background === 'none', 'AI title is transparent or low-contrast')
    await assertVisibleTypography(page, 'AI workspace')
    checks += 3
    await context.close()
  }

  {
    const { context, page } = await openPage(browser, { width: 898, height: 768 }, '/deliberation?market=US&symbol=TSLA')
    const disabled = page.locator('.deliberation-secondary-action.is-disabled')
    assert(await disabled.isVisible() && (await disabled.textContent()).includes('等待'), 'Deliberation disabled action has no readable reason')
    const banner = await page.locator('.deliberation-compliance-banner').evaluate((element) => ({ scrollWidth: element.scrollWidth, clientWidth: element.clientWidth }))
    assert(banner.scrollWidth <= banner.clientWidth + 1, 'Deliberation compliance banner is clipped')
    checks += 2
    await context.close()
  }

  await browser.close()
  console.log(`PASS=${checks}/25 USER_FEEDBACK_GEOMETRY`)
})().catch((error) => {
  console.error(error)
  process.exit(1)
})
