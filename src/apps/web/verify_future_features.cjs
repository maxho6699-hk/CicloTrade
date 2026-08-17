const { chromium } = require('playwright')

const MOCK = process.env.CICLO_MOCK_URL || 'http://localhost:5180'

async function openPage(browser, width, height) {
  const context = await browser.newContext({ viewport: { width, height } })
  const bootstrap = await (await context.request.get(`${MOCK}/api/rewrite/v1/bootstrap`)).json()
  await context.addInitScript((payload) => {
    localStorage.setItem('ciclotrade_session', JSON.stringify({ authenticated: true, access_token: 'x', bootstrap: payload }))
    localStorage.setItem('ciclotrade_theme', 'dark')
  }, bootstrap)
  return { context, page: await context.newPage(), bootstrap }
}

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

;(async () => {
  const browser = await chromium.launch({ headless: true })
  let passed = 0
  try {
    const desktop = await openPage(browser, 1440, 900)
    await desktop.page.goto(`${MOCK}/discover`, { waitUntil: 'networkidle' })
    const resources = await desktop.page.evaluate(() => performance.getEntriesByType('resource').map((entry) => entry.name))
    assert(resources.some((url) => /DiscoverV2Page-[^/]+\.js/.test(url)), 'discover route chunk not loaded')
    assert(!resources.some((url) => /AdminPage-[^/]+\.js/.test(url)), 'unvisited admin chunk was eagerly loaded')
    passed += 2

    const weeklyRequest = desktop.page.waitForRequest((request) => request.url().includes('/market/candles') && request.url().includes(encodeURIComponent('周线')))
    await desktop.page.getByRole('tab', { name: '1W', exact: true }).click()
    await weeklyRequest
    assert(new URL(desktop.page.url()).searchParams.get('mini') === '1W', '1W URL state missing')
    assert(await desktop.page.getByRole('tab', { name: '1W', exact: true }).getAttribute('aria-selected') === 'true', '1W aria selection missing')
    passed += 3

    const monthlyRequest = desktop.page.waitForRequest((request) => request.url().includes('/market/candles') && request.url().includes(encodeURIComponent('月线')))
    await desktop.page.getByRole('tab', { name: '1M', exact: true }).click()
    await monthlyRequest
    assert(new URL(desktop.page.url()).searchParams.get('mini') === '1M', '1M URL state missing')
    assert(await desktop.page.getByRole('tab', { name: '1M', exact: true }).getAttribute('aria-selected') === 'true', '1M aria selection missing')
    passed += 3
    await desktop.context.close()

    const mobile = await openPage(browser, 390, 844)
    await mobile.page.goto(`${MOCK}/discover?mini=1W`, { waitUntil: 'networkidle' })
    const metrics = await mobile.page.locator('.discover-periods').evaluate((element) => ({
      overflow: element.scrollWidth - element.clientWidth,
      heights: [...element.querySelectorAll('button')].map((button) => button.getBoundingClientRect().height),
      labels: [...element.querySelectorAll('button')].map((button) => button.textContent?.trim()),
    }))
    assert(metrics.overflow <= 1, `mobile Mini K tabs overflow by ${metrics.overflow}px`)
    assert(metrics.heights.length === 3 && metrics.heights.every((height) => height >= 44), 'mobile Mini K tabs are below 44px')
    assert(metrics.labels.join(',') === '1D,1W,1M', 'Mini K labels are incomplete')
    passed += 3
    await mobile.context.close()

    const race = await openPage(browser, 1440, 900)
    const selectedId = race.bootstrap.recommendations.items.find((item) => item.instrument_type === 'stock')?.event_id
    assert(Number.isSafeInteger(selectedId), 'missing selected recommendation fixture')
    const requestedTimeframes = []
    let abortedWeekly = 0
    race.page.on('requestfailed', (request) => {
      const requestUrl = new URL(request.url())
      if (requestUrl.pathname.includes('/market/candles') && requestUrl.searchParams.get('timeframe') === '周线') abortedWeekly += 1
    })
    await race.page.route('**/api/rewrite/v1/market/candles**', async (route) => {
      const requestUrl = new URL(route.request().url())
      const timeframe = requestUrl.searchParams.get('timeframe')
      requestedTimeframes.push(timeframe)
      const response = await route.fetch()
      const payload = await response.json()
      if (timeframe === '周线') await new Promise((resolve) => setTimeout(resolve, 300))
      if (timeframe === '周线' || timeframe === '月线') {
        const rising = timeframe === '周线'
        payload.items = [
          { time: '2026-07-01', open: rising ? 10 : 20, high: 21, low: 9, close: rising ? 10 : 20, volume: 100 },
          { time: '2026-08-01', open: rising ? 20 : 10, high: 21, low: 9, close: rising ? 20 : 10, volume: 120 },
        ]
      }
      await route.fulfill({ response, json: payload })
    })
    await race.page.goto(`${MOCK}/discover?selected=${selectedId}`, { waitUntil: 'domcontentloaded' })
    await race.page.getByRole('tab', { name: '1D', exact: true }).waitFor()
    await race.page.getByRole('tab', { name: '1W', exact: true }).click()
    await race.page.getByRole('tab', { name: '1M', exact: true }).click()
    await race.page.waitForTimeout(500)
    assert(requestedTimeframes.includes('周线') && requestedTimeframes.includes('月线'), 'rapid timeframe requests missing')
    assert(await race.page.locator('.discover-sparkline[aria-label*="月线"]').count() > 0, 'visible sparklines did not retain monthly timeframe')
    assert(await race.page.locator('.discover-sparkline.is-up[aria-label*="月线"]').count() === 0, 'stale weekly response overwrote monthly sparklines')
    assert(await race.page.locator('.discover-ai-selection .v2-mini-chart[aria-label*="月线"]').count() === 1, 'AI Mini K did not follow monthly timeframe')
    assert(abortedWeekly > 0, 'superseded weekly candle requests were not aborted')
    passed += 6
    await race.context.close()

    const failureBrowser = await chromium.launch({ headless: true })
    const failure = await openPage(failureBrowser, 1440, 900)
    let chunkBlocked = false
    await failure.page.route(/\/assets\/DiscoverV2Page-[^/]+\.js$/, async (route) => {
      chunkBlocked = true
      await route.abort('failed')
    })
    await failure.page.goto(`${MOCK}/discover`, { waitUntil: 'domcontentloaded' })
    const failureAlert = failure.page.getByRole('alert')
    await failureAlert.waitFor()
    const failureText = await failureAlert.textContent()
    assert(chunkBlocked, 'dynamic route chunk was not intercepted')
    assert(failureText?.includes('工作区资源载入失败') || failureText?.includes('工作區資源載入失敗'), 'chunk failure copy is missing')
    assert(await failure.page.locator('.sidebar').count() === 1, 'AppShell disappeared after chunk failure')
    assert(await failure.page.locator('.route-failure button').count() === 1, 'chunk failure has no reload action')
    passed += 4
    await failure.context.close()
    await failureBrowser.close()
  } finally {
    await browser.close()
  }
  assert(passed === 21, `expected 21 checks, got ${passed}`)
  console.log(`PASS=${passed}/21 FUTURE_FEATURES`)
})().catch((error) => {
  console.error(error.stack || error)
  process.exit(1)
})
