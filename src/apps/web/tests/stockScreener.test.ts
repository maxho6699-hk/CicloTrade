import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  alertPrefillUrl,
  decodeStockScreenerDraft,
  decodeStockScreenerPayload,
  decodeStockScreenerPreset,
  decodeStockScreenerRequest,
  paperPrefillUrl,
  screenerViewState,
  stockScreenerRequestError,
} from '../src/domain/stockScreener.ts'

function item(overrides: Record<string, unknown> = {}) {
  return {
    symbol: 'NVDA', name: 'NVIDIA', state: 'official', action: 'buy', score: null, price: 180.25, change_pct: 1.2,
    reasons: ['突破确认'], counter_evidence: ['成交量仍需核对'], risk: '波动扩大', invalidation: '跌破风险线',
    data_state: 'fresh', health: 'healthy', updated_at: '2026-08-14T08:00:00+08:00', hong_kong_time: '2026-08-14T08:00:00+08:00',
    research_url: '/discover?tool=screener&symbol=NVDA', alert_prefill: { market: 'US', symbol: 'NVDA' }, paper_prefill: { market: 'US', symbol: 'NVDA', side: 'BUY' }, blocked_reason: null, actionable: true,
    ...overrides,
  }
}

function payload(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1, preset: 'all', filters: {}, sort: { field: 'updated_at', direction: 'desc' }, page: 1, page_size: 20, total: 1, items: [item()],
    ...overrides,
  }
}

test('stock screener decoder accepts the exact server contract including nullable score and Hong Kong time', () => {
  const response = payload()
  assert.deepEqual(decodeStockScreenerPayload(response), response)
  const decoded = decodeStockScreenerPayload(response)!
  assert.equal(decoded.items[0].score, null)
  assert.equal(decoded.items[0].hong_kong_time, '2026-08-14T08:00:00+08:00')
  assert.equal(screenerViewState(decoded), 'success')
})

test('screener decoder fails closed for duplicate symbols, unbounded pages, invalid data health, and unsafe paper actions', () => {
  assert.equal(decodeStockScreenerPayload(payload({ items: [item(), item({ name: 'duplicate' })], total: 2 })), null)
  assert.equal(decodeStockScreenerPayload(payload({ page: 2 })), null)
  assert.equal(decodeStockScreenerPayload(payload({ page_size: 101 })), null)
  assert.equal(decodeStockScreenerPayload(payload({ items: [item({ health: 'unknown' })] })), null)
  assert.equal(decodeStockScreenerPayload(payload({ items: [item({ paper_prefill: { market: 'US', symbol: 'NVDA', side: 'BUY' }, actionable: false, blocked_reason: 'candidate_action_not_tradeable' })] })), null)
  assert.equal(decodeStockScreenerPayload(payload({ items: [item({ paper_prefill: { market: 'US', symbol: 'NVDA', side: 'SHORT' } })] })), null)
  for (const overrides of [{ action: 'hold' }, { data_state: 'stale' }, { health: 'degraded' }]) assert.equal(decodeStockScreenerPayload(payload({ items: [item(overrides)] })), null)
  assert.equal(decodeStockScreenerPayload(payload({ items: [item({ research_url: '/paper?symbol=NVDA' })] })), null)
})

test('status keeps stale, offline, empty, pending, and unknown states distinct', () => {
  assert.equal(screenerViewState(null), 'unknown')
  assert.equal(screenerViewState(null, true), 'pending')
  assert.equal(screenerViewState(decodeStockScreenerPayload(payload({ total: 0, items: [] }))), 'empty')
  assert.equal(screenerViewState(decodeStockScreenerPayload(payload({ items: [item({ data_state: 'stale', actionable: false, paper_prefill: null, blocked_reason: 'market_data_not_fresh' })] }))), 'stale')
  assert.equal(screenerViewState(decodeStockScreenerPayload(payload({ items: [item({ health: 'unavailable', actionable: false, paper_prefill: null, blocked_reason: 'candidate_health_not_healthy' })] }))), 'offline')
})

test('versioned server presets and local drafts require the same schema', () => {
  const preset = { schema_version: 1, version: 4, name: '我的动量', filters: { min_score: 50 }, sort: { field: 'score', direction: 'desc' } }
  assert.deepEqual(decodeStockScreenerPreset(preset), preset)
  assert.deepEqual(decodeStockScreenerDraft(JSON.stringify(preset)), preset)
  assert.equal(decodeStockScreenerDraft('{bad json'), null)
  assert.equal(decodeStockScreenerPreset({ ...preset, schema_version: 2 }), null)
  assert.equal(decodeStockScreenerPreset({ ...preset, version: -1 }), null)
})

test('server query requests reject unknown filters and out-of-bounds pagination', () => {
  const request = { schema_version: 1, preset: 'all', filters: { actions: ['buy'], symbols: ['NVDA'] }, sort: { field: 'score', direction: 'desc' }, page: 1, page_size: 20 }
  assert.deepEqual(decodeStockScreenerRequest(request), request)
  assert.equal(decodeStockScreenerRequest({ ...request, filters: { unknown: true } }), null)
  assert.equal(decodeStockScreenerRequest({ ...request, page: 0 }), null)
  assert.equal(decodeStockScreenerRequest({ ...request, page_size: 101 }), null)
})

test('filter validation returns a stable reason instead of silently dropping invalid input', () => {
  const request = { schema_version: 1 as const, preset: 'all' as const, filters: {}, sort: { field: 'score' as const, direction: 'desc' as const }, page: 1, page_size: 20 }
  assert.equal(stockScreenerRequestError(request), null)
  assert.equal(stockScreenerRequestError({ ...request, filters: { symbols: ['A/PL'] } }), 'invalid_symbol')
  assert.equal(stockScreenerRequestError({ ...request, filters: { min_price: 25, max_price: 10 } }), 'invalid_price_range')
  assert.equal(stockScreenerRequestError({ ...request, filters: { min_price: 0 } }), 'invalid_price_range')
  assert.equal(stockScreenerRequestError({ ...request, filters: { min_score: 101 } }), 'invalid_score_range')
  assert.equal(stockScreenerRequestError({ ...request, filters: { min_score: 80, max_score: 40 } }), 'invalid_score_range')
})

test('only server-supplied actions produce research, alert, and personal-paper navigation', () => {
  const decoded = decodeStockScreenerPayload(payload())!
  const row = decoded.items[0]
  assert.equal(row.research_url, '/discover?tool=screener&symbol=NVDA')
  assert.equal(alertPrefillUrl(row.alert_prefill), '/notifications?market=US&symbol=NVDA&draft=alert')
  assert.ok(row.paper_prefill)
  assert.equal(paperPrefillUrl(row.paper_prefill!), '/paper?market=US&symbol=NVDA&side=BUY&source=screener')
  assert.equal(decodeStockScreenerPayload(payload({ items: [item({ action: 'hold', paper_prefill: null, actionable: false, blocked_reason: 'candidate_action_not_tradeable' })] }))?.items[0].paper_prefill, null)
})

test('screener keeps its Opportunities query slot and wires only through the typed route adapter', async () => {
  const [page, route, client, panel, domain, css] = await Promise.all([
    readFile(new URL('../src/pages/OpportunitiesPage.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/pages/StockScreenerRoute.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/StockScreenerPanel.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/domain/stockScreener.ts', import.meta.url), 'utf8'),
    readFile(new URL('../src/styles/screener.css', import.meta.url), 'utf8'),
  ])
  assert.match(page, /searchParams\.get\('tool'\) === 'screener'.*<StockScreenerRoute/)
  assert.match(route, /fetchStockScreener/)
  assert.match(route, /fetchStockScreenerPreset/)
  assert.match(route, /saveStockScreenerPreset/)
  assert.match(route, /savedPreset \? \{ \.\.\.INITIAL_REQUEST, filters: savedPreset\.filters, sort: savedPreset\.sort \}/)
  assert.match(client, /\/api\/rewrite\/v1\/stock-screener\/query/)
  assert.match(client, /decodeStockScreenerPayload/)
  assert.doesNotMatch(panel, /SAMPLE_ROWS|示例筛选结果|marketCap|trendCol|riskCol/)
  assert.doesNotMatch(panel, /fetch\(|from ['"]\.\.\/api\/client/)
  assert.doesNotMatch(domain, /side: 'BUY' \}\)}/)
  assert.match(panel, /row\.paper_prefill && row\.actionable/)
  assert.match(panel, /onSavePreset/)
  assert.match(panel, /copy\.actions\[row\.action\]/)
  assert.match(panel, /copy\.dataStates\[row\.data_state\]/)
  assert.match(panel, /filters: \{\}, sort: DEFAULT_SORT, page: 1/)
  assert.match(panel, /currentPreset = \{ \.\.\.draft, filters: request\.filters, sort: request\.sort \}/)
  assert.match(panel, /name="preset_name"[\s\S]*?disabled=\{saving\}/)
  assert.match(panel, /stockScreenerRequestError\(query\)/)
  assert.match(panel, /const value = optionalNumber\(event\.currentTarget\); editQuery/)
  assert.match(panel, /role="alert"/)
  assert.doesNotMatch(panel, /state === 'empty' \? <section className="data-panel stock-screener-empty"/)
  assert.match(css, /font-size: 12px/)
  assert.match(css, /min-height: max\(44px, var\(--control-hit-size\)\)/)
  assert.match(css, /@media \(max-width: 440px\)[\s\S]*\.stock-screener-query \{[\s\S]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/)
  assert.match(css, /\.stock-screener-query > :not\(\.stock-screener-preset\)[\s\S]*grid-column: 1 \/ -1/)
  assert.doesNotMatch(css, /(?:font-size:|font:)\s*1[01]px/)
})
