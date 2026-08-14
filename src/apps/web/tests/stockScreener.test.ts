import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  alertDraftUrl,
  decodeStockScreenerPayload,
  filterAndSortScreenerRows,
  pagedScreenerRows,
  personalPaperPrefillUrl,
  researchUrl,
  type StockScreenerRow,
} from '../src/domain/stockScreener.ts'

const rows: StockScreenerRow[] = [
  { symbol: 'NVDA', name: 'NVIDIA', market: 'US', trend: 'rising', risk: 'high', marketCap: 'large', score: 76 },
  { symbol: 'AAPL', name: 'Apple', market: 'US', trend: 'rising', risk: 'low', marketCap: 'large', score: 82 },
  { symbol: '600519', name: '贵州茅台', market: 'CN', trend: 'neutral', risk: 'medium', marketCap: 'large', score: 74 },
]

test('stock screener decoder accepts an exact connected payload and rejects malformed or unverified inputs', () => {
  const payload = { state: 'ready', source: 'connected', dataAsOf: '2026-08-14T08:00:00Z', rows }
  assert.deepEqual(decodeStockScreenerPayload(payload), payload)
  assert.equal(decodeStockScreenerPayload({ ...payload, source: 'example' }), null)
  assert.equal(decodeStockScreenerPayload({ ...payload, rows: [{ ...rows[0], score: Number.NaN }] }), null)
  assert.equal(decodeStockScreenerPayload({ ...payload, extra: true }), null)
  assert.equal(decodeStockScreenerPayload({ ...payload, dataAsOf: null }), null)
})

test('filters, deterministic sorting and page bounds stay local and predictable', () => {
  const filtered = filterAndSortScreenerRows(rows, { market: 'US', trend: 'rising', risk: 'all', marketCap: 'all' }, 'risk-asc')
  assert.deepEqual(filtered.map((row) => row.symbol), ['AAPL', 'NVDA'])
  assert.deepEqual(filterAndSortScreenerRows(rows, { market: 'all', trend: 'all', risk: 'all', marketCap: 'all' }, 'symbol-asc').map((row) => row.symbol), ['600519', 'AAPL', 'NVDA'])
  const page = pagedScreenerRows(rows, 8, 2)
  assert.equal(page.page, 1)
  assert.equal(page.pageCount, 2)
  assert.deepEqual(page.rows.map((row) => row.symbol), ['600519'])
})

test('research and draft links preserve symbols but never encode an execution request', () => {
  assert.equal(researchUrl(rows[1]), '/research?market=US&symbol=AAPL')
  assert.equal(alertDraftUrl(rows[1]), '/notifications?market=US&symbol=AAPL&draft=alert')
  assert.equal(personalPaperPrefillUrl(rows[1]), '/paper?market=US&symbol=AAPL&side=BUY')
  for (const url of [researchUrl(rows[1]), alertDraftUrl(rows[1]), personalPaperPrefillUrl(rows[1])]) assert.doesNotMatch(url, /submit|execute|quantity|price/i)
})

test('screener is mounted only for its discover tool route and makes the disconnected example state explicit', async () => {
  const [page, panel] = await Promise.all([
    readFile(new URL('../src/pages/OpportunitiesPage.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/StockScreenerPanel.tsx', import.meta.url), 'utf8'),
  ])
  assert.match(page, /searchParams\.get\('tool'\) === 'screener'.*<StockScreenerPanel/)
  assert.match(panel, /示例筛选结果 · 非实时 · 不可交易/)
  assert.match(panel, /数据服务未接入/)
  assert.match(panel, /预填个人模拟/)
  assert.doesNotMatch(panel, /submitPersonalPaperStockOrder|fetch\(|from ['"]\.\.\/api\/client/)
})
