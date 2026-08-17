const { chromium } = require('playwright')

const BASE = process.env.CICLO_PREVIEW_URL || 'http://localhost:5175'
const MOCK = process.env.CICLO_MOCK_URL || 'http://localhost:5180'

function assert(condition, message) { if (!condition) throw new Error(message) }

async function openPage(browser, path, viewport) {
  const context = await browser.newContext({ viewport })
  const page = await context.newPage()
  const errors = []
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  await page.goto(`${MOCK}${path}`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('#root')
  await page.waitForFunction(() => (document.querySelector('#root')?.innerHTML.length || 0) > 4000)
  await page.waitForTimeout(900)
  return { context, page, errors }
}

;(async () => {
  const browser = await chromium.launch({ headless: true })
  let passed = 0
  try {
    {
      const { context, page, errors } = await openPage(browser, '/research', { width: 390, height: 844 })
      await page.waitForSelector('.overview-card-grid .overview-quote-card')
      const cards = await page.locator('.overview-card-grid .overview-quote-card').count()
      const strip = await page.locator('.overview-card-grid').evaluate((el) => ({ scrollWidth: el.scrollWidth, clientWidth: el.clientWidth }))
      const status = await page.locator('.overview-mobile-card-status').textContent()
      assert(cards >= 6, `mobile market cards were truncated: ${cards}`)
      assert(strip.scrollWidth > strip.clientWidth * 2, `mobile card strip cannot reach all cards: ${JSON.stringify(strip)}`)
      assert(status && status.includes(String(cards)), `mobile card count status is missing: ${status}`)
      assert(!errors.length, `mobile research console errors: ${errors.join(' | ')}`)
      passed += 3
      await context.close()
    }
    {
      const { context, page, errors } = await openPage(browser, '/discover', { width: 1440, height: 1000 })
      await page.waitForSelector('.discover-market-card .v2-candidate-row')
      const layout = await page.evaluate(() => {
        const left = document.querySelector('.discover-left-rail')
        const coverage = left?.querySelector('.discover-coverage-card')
        const account = left?.querySelector('.discover-account-card')
        const table = document.querySelector('.discover-market-card .v2-candidate-table')
        const rows = [...document.querySelectorAll('.discover-market-card .v2-candidate-row')]
        const tableRect = table?.getBoundingClientRect()
        const visibleRows = tableRect ? rows.filter((row) => { const rect = row.getBoundingClientRect(); return rect.bottom > tableRect.top && rect.top < tableRect.bottom }).length : 0
        const spark = document.querySelector('.discover-market-card .discover-sparkline')?.getBoundingClientRect()
        return {
          sameLeft: Boolean(left && coverage?.parentElement === left && account?.parentElement === left),
          ordered: Boolean(coverage && account && coverage.compareDocumentPosition(account) & Node.DOCUMENT_POSITION_FOLLOWING),
          table: table ? { clientHeight: table.clientHeight, scrollHeight: table.scrollHeight, overflowY: getComputedStyle(table).overflowY } : null,
          visibleRows,
          sparkHeight: spark?.height || 0,
        }
      })
      assert(layout.sameLeft && layout.ordered, `account snapshot is not below coverage: ${JSON.stringify(layout)}`)
      assert(layout.table && layout.table.overflowY === 'auto' && layout.table.scrollHeight > layout.table.clientHeight, `candidate matrix is not scrollable: ${JSON.stringify(layout)}`)
      assert(layout.visibleRows >= 2 && layout.visibleRows <= 4, `candidate viewport should show about three rows: ${JSON.stringify(layout)}`)
      assert(layout.sparkHeight >= 55, `Mini K is still too small: ${layout.sparkHeight}`)
      await page.locator('.discover-command-strip .v2-inspector-toggle').click()
      await page.waitForSelector('.discover-right-rail.is-open')
      const search = await page.evaluate(() => {
        const field = document.querySelector('.discover-filter-search .v2-search-field')
        const icon = field?.querySelector('svg')?.getBoundingClientRect()
        const input = field?.querySelector('input')?.getBoundingClientRect()
        return { display: field ? getComputedStyle(field).display : '', iconY: icon ? icon.y + icon.height / 2 : 0, inputY: input ? input.y + input.height / 2 : 0, accountInRight: Boolean(document.querySelector('.discover-right-rail .discover-account-card')) }
      })
      assert(search.display === 'flex' && Math.abs(search.iconY - search.inputY) < 3, `search icon/input are not aligned: ${JSON.stringify(search)}`)
      assert(!search.accountInRight, 'account snapshot still duplicates inside inspector')
      assert(!errors.length, `discover console errors: ${errors.join(' | ')}`)
      passed += 6
      await context.close()
    }
    {
      const { context, page, errors } = await openPage(browser, '/recommendations', { width: 1440, height: 1000 })
      await page.waitForSelector('.recommendation-preview-card')
      const scroll = await page.locator('.recommendation-preview-scroll').evaluate((el) => ({ clientHeight: el.clientHeight, scrollHeight: el.scrollHeight, overflowY: getComputedStyle(el).overflowY, cards: el.querySelectorAll('.recommendation-preview-card').length }))
      assert(scroll.cards >= 8 && scroll.overflowY === 'auto', `recommendation scroll contract failed: ${JSON.stringify(scroll)}`)
      if (scroll.cards > 8) assert(scroll.scrollHeight > scroll.clientHeight, `more than eight recommendations do not scroll: ${JSON.stringify(scroll)}`)
      await page.locator('.recommendation-expand').first().click()
      await page.waitForSelector('.recommendation-detail-drawer')
      const detail = await page.evaluate(() => {
        const context = document.querySelector('.recommendation-context-panel')
        const drawer = document.querySelector('.recommendation-detail-drawer')
        const c = context?.getBoundingClientRect(); const d = drawer?.getBoundingClientRect()
        return { contextVisible: Boolean(c && c.width > 0 && c.height > 0), separated: Boolean(c && d && c.right <= d.left), text: context?.textContent || '' }
      })
      assert(detail.contextVisible && detail.separated, `real comparison panel does not use left detail space: ${JSON.stringify(detail)}`)
      assert(/同类研判对照|同類研判對照/.test(detail.text) && /不生成/.test(detail.text) && !/(?:胜率|勝率|AI评分|AI評分)\s*[:：]?\s*\d/.test(detail.text), `comparison panel contains the wrong content: ${detail.text}`)
      assert(!errors.length, `recommendations console errors: ${errors.join(' | ')}`)
      passed += 4
      await context.close()
    }
    {
      const { context, page, errors } = await openPage(browser, '/account', { width: 1440, height: 1000 })
      await page.waitForSelector('.profile-agent-card')
      const account = await page.evaluate(() => {
        const agent = document.querySelector('.profile-agent-card')?.getBoundingClientRect()
        const shortcuts = document.querySelector('.profile-overview-shortcuts')?.getBoundingClientRect()
        const main = document.querySelector('.profile-overview-main')?.getBoundingClientRect()
        const side = document.querySelector('.profile-side-stack')?.getBoundingClientRect()
        const lower = document.querySelector('.profile-lower-grid')?.textContent || ''
        return { agent, shortcuts, main, side, lower }
      })
      assert(account.agent && account.shortcuts && account.shortcuts.top >= account.agent.bottom - 1, `shortcuts are not below the agent: ${JSON.stringify(account)}`)
      assert(account.main && account.side && account.agent.height < account.side.height, `agent is still stretched to the whole side stack: ${JSON.stringify(account)}`)
      assert(!account.lower.includes('消息与设置') && !account.lower.includes('MY CONTENT'), 'old shortcut cards remain in the lower grid')
      assert(!errors.length, `account console errors: ${errors.join(' | ')}`)
      passed += 4
      await context.close()
    }
    console.log(`PASS=${passed}/17 ROUND3_LAYOUT`)
  } finally {
    await browser.close()
  }
})().catch((error) => { console.error(error.stack || error); process.exit(1) })
