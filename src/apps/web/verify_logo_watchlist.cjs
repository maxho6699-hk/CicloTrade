const { chromium } = require('playwright')

const MOCK = process.env.CICLO_MOCK_URL || 'http://localhost:5180'
function assert(condition, message) { if (!condition) throw new Error(message) }

;(async () => {
  const baseBootstrap = await fetch(`${MOCK}/api/rewrite/v1/bootstrap`).then((response) => response.json())
  let watchlists = structuredClone(baseBootstrap.settings.watchlists)
  let pins = structuredClone(baseBootstrap.settings.watchlist_pins)
  let failNextWatchlist = false
  let bootstrapCalls = 0

  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  const consoleErrors = []
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname.endsWith('/bootstrap')) {
      bootstrapCalls += 1
      const payload = structuredClone(baseBootstrap)
      payload.settings.watchlists = structuredClone(watchlists)
      payload.settings.watchlist_pins = structuredClone(pins)
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) })
    }
    if (url.pathname.endsWith('/watchlist')) {
      if (failNextWatchlist) {
        failNextWatchlist = false
        return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'forced watchlist failure' }) })
      }
      const body = request.postDataJSON()
      const list = body.market === 'CN' ? watchlists.a_share : watchlists.us
      if (request.method() === 'DELETE') {
        const next = list.filter((symbol) => symbol !== body.symbol)
        if (body.market === 'CN') watchlists.a_share = next
        else watchlists.us = next
      } else if (request.method() === 'POST' && !list.includes(body.symbol)) {
        list.push(body.symbol)
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ watchlists, pins }) })
    }
    return route.continue()
  })

  const allLogos = new Map()
  let totalImages = 0
  async function inspectLogos(path) {
    await page.goto(`${MOCK}${path}`, { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('#root')
    await page.waitForFunction(() => (document.querySelector('#root')?.innerHTML.length || 0) > 4000)
    await page.waitForTimeout(900)
    const result = await page.evaluate(async () => {
      const logos = [...document.querySelectorAll('.stock-company-logo')]
      for (const logo of logos) logo.querySelector('img')?.setAttribute('loading', 'eager')
      await Promise.all([...document.querySelectorAll('.stock-company-logo img')].map((img) => img.decode().catch(() => undefined)))
      return {
        oldFallbacks: document.querySelectorAll('.stock-company-logo b,.discover-company-logo,.v2-stock-logo').length,
        missing: logos.filter((logo) => logo.getAttribute('data-logo-status') !== 'ready').map((logo) => logo.getAttribute('aria-label') || logo.textContent),
        images: [...document.querySelectorAll('.stock-company-logo img')].map((img) => ({ alt: img.alt, src: img.getAttribute('src'), complete: img.complete, naturalWidth: img.naturalWidth, naturalHeight: img.naturalHeight })),
      }
    })
    assert(result.oldFallbacks === 0, `${path} still renders old letter fallbacks`)
    assert(result.missing.length === 0, `${path} missing logos: ${result.missing.join(', ')}`)
    for (const image of result.images) {
      assert(image.complete && image.naturalWidth > 0 && image.naturalHeight > 0, `${path} broken logo: ${JSON.stringify(image)}`)
      assert(image.src && image.src.startsWith('/stock-logos/'), `${path} logo is not controlled local asset: ${image.src}`)
      const symbol = image.alt.split(' ')[0]
      const previous = allLogos.get(symbol)
      if (previous) assert(previous === image.src, `${symbol} resolves differently across routes: ${previous} vs ${image.src}`)
      allLogos.set(symbol, image.src)
      totalImages += 1
    }
  }

  for (const path of ['/today', '/discover', '/recommendations', '/research', '/research?market=US&symbol=AAPL', '/portfolio', '/paper']) await inspectLogos(path)
  assert(allLogos.size >= 16, `logo route coverage too small: ${allLogos.size} unique, ${totalImages} rendered`)

  await page.goto(`${MOCK}/discover`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.discover-market-card .v2-candidate-row')
  const candidate = () => page.locator('.discover-market-card .v2-candidate-row').filter({ hasText: 'AAPL' }).first()
  const watchlistAapl = () => page.locator('.discover-watchlist-list > div').filter({ hasText: 'AAPL' })
  assert(await candidate().locator('.discover-row-watch').getAttribute('aria-pressed') === 'true', 'AAPL should start saved')
  await candidate().locator('.discover-row-watch').click()
  await page.waitForTimeout(250)
  assert(await candidate().isVisible(), 'ordinary candidate disappeared after watchlist removal')
  assert(await candidate().locator('.discover-row-watch').getAttribute('aria-pressed') === 'false', 'ordinary candidate star did not turn off')
  assert(await watchlistAapl().count() === 0, 'watchlist-only AAPL entry did not disappear')

  await page.evaluate(() => { history.pushState({}, '', '/research'); window.dispatchEvent(new PopStateEvent('popstate')) })
  await page.waitForURL('**/research')
  await page.waitForSelector('.overview-quote-card')
  const researchAapl = page.locator('.overview-quote-card').filter({ hasText: 'AAPL' }).first()
  assert(await researchAapl.locator('.overview-watch-action').getAttribute('aria-pressed') === 'false', 'research did not receive the account-level removal')
  await researchAapl.locator('.overview-watch-action').click()
  await page.waitForTimeout(250)
  assert(await researchAapl.locator('.overview-watch-action').getAttribute('aria-pressed') === 'true', 'research add did not update the shared store')

  await page.evaluate(() => { history.pushState({}, '', '/discover'); window.dispatchEvent(new PopStateEvent('popstate')) })
  await page.waitForURL('**/discover')
  await page.waitForSelector('.discover-market-card .v2-candidate-row')
  assert(await watchlistAapl().count() === 1, 'discover watchlist did not receive the cross-route add')
  assert(await candidate().locator('.discover-row-watch').getAttribute('aria-pressed') === 'true', 'discover candidate did not receive the cross-route add')

  assert(!consoleErrors.length, `unexpected console errors before failure injection: ${consoleErrors.join(' | ')}`)
  consoleErrors.length = 0
  const bootstrapBeforeFailure = bootstrapCalls
  failNextWatchlist = true
  await candidate().locator('.discover-row-watch').click()
  await page.waitForTimeout(500)
  assert(bootstrapCalls > bootstrapBeforeFailure, 'failed watchlist write did not refresh authoritative bootstrap')
  assert(await candidate().isVisible(), 'failed removal hid ordinary candidate')
  assert(await candidate().locator('.discover-row-watch').getAttribute('aria-pressed') === 'true', 'failed removal left a false local state')
  assert(await watchlistAapl().count() === 1, 'failed removal deleted the watchlist-only entry')
  const unexpectedErrors = consoleErrors.filter((message) => !message.includes('500 (Internal Server Error)'))
  assert(!unexpectedErrors.length, `unexpected console errors: ${unexpectedErrors.join(' | ')}`)

  console.log(`PASS=*** LOGO_WATCHLIST unique=${allLogos.size} rendered=${totalImages} bootstrap=${bootstrapCalls}`)
  await context.close()
  await browser.close()
})().catch((error) => { console.error(error.stack || error); process.exit(1) })
