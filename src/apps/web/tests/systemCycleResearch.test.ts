import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  BrowserApiError,
  fetchSystemCycleResearchHistory,
  fetchSystemCycleResearchLatest,
  fetchSystemCycleResearchStatus,
  validSystemCycleResearchHistory,
  validSystemCycleResearchLatest,
  validSystemCycleResearchStatus,
} from '../src/api/client.ts'

const STOCKS = Array.from({ length: 13 }, (_, index) => ({
  market: index < 8 ? 'US' : 'CN',
  symbol: `STOCK${index + 1}`,
  status: 'coverage',
  rows: 100,
  dataset_end: '2026-08-12',
  selected: index === 0,
  signal_state: index === 0 ? 'long' : 'flat',
  latest_price: 100 + index,
  target_quantity: index === 0 ? 1 : 0,
}))

const status = {
  available: true,
  state: 'healthy',
  research_only: true,
  actionable: false,
  last_heartbeat_at: '2026-08-12T01:00:00Z',
  last_result_at: '2026-08-12T00:50:00Z',
  last_cycle_id: 'system-cycle-2026-08-12-after_close',
  stock_count: 13,
  coverage_count: 13,
  no_data_count: 0,
  spool: { pending: 0, claimed: 0, retryable: 0, delivered: 1 },
}

const latest = {
  available: true,
  research_only: true,
  actionable: false,
  validation_label: '历史规则回放与状态扫描',
  cycle: {
    cycle_id: 'system-cycle-2026-08-12-after_close',
    evaluation_date: '2026-08-12',
    cycle_slot: 'after_close',
    strategy_key: 'system-cycle',
    strategy_name: 'System Cycle',
    strategy_version: 'v1',
    evaluated_at: '2026-08-12T00:50:00Z',
    coverage_count: 13,
    no_data_count: 0,
    selected_symbols: ['STOCK1'],
    stocks: STOCKS,
    evidence: {
      universe_sha256: 'a'.repeat(64),
      source_snapshot_sha256: 'b'.repeat(64),
      catalog_snapshot_sha256: 'c'.repeat(64),
      code_bundle_sha256: 'd'.repeat(64),
      result_sha256: 'e'.repeat(64),
    },
  },
}

const history = {
  available: true,
  research_only: true,
  actionable: false,
  limit: 20,
  items: [{
    cycle_id: 'system-cycle-2026-08-12-after_close',
    evaluation_date: '2026-08-12',
    cycle_slot: 'after_close',
    strategy_key: 'system-cycle',
    strategy_name: 'System Cycle',
    strategy_version: 'v1',
    evaluated_at: '2026-08-12T00:50:00Z',
    received_at: '2026-08-12T00:51:00Z',
    coverage_count: 13,
    no_data_count: 0,
    selected_count: 1,
  }],
}

test('system cycle research client calls only the frozen read endpoints', async () => {
  const originalFetch = globalThis.fetch
  const paths: string[] = []
  globalThis.fetch = async (input) => {
    const path = String(input)
    paths.push(path)
    const payload = path.endsWith('/status') ? status : path.endsWith('/latest') ? latest : history
    return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    await Promise.all([fetchSystemCycleResearchStatus(), fetchSystemCycleResearchLatest(), fetchSystemCycleResearchHistory()])
    assert.deepEqual(paths, [
      '/api/rewrite/v1/system-cycle-research/status',
      '/api/rewrite/v1/system-cycle-research/latest',
      '/api/rewrite/v1/system-cycle-research/history?limit=20',
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('system cycle research decoders fail closed on unexpected fields, missing authority, and non-finite quantities', async () => {
  assert.equal(validSystemCycleResearchStatus(status), true)
  assert.equal(validSystemCycleResearchLatest(latest), true)
  assert.equal(validSystemCycleResearchHistory(history), true)
  assert.equal(validSystemCycleResearchStatus({ ...status, source: 'private' }), false)
  assert.equal(validSystemCycleResearchLatest({ ...latest, actionable: true }), false)
  assert.equal(validSystemCycleResearchHistory({ ...history, limit: 10 }), false)
  const malformed = structuredClone(latest)
  malformed.cycle.stocks[0].target_quantity = Number.NaN
  assert.equal(validSystemCycleResearchLatest(malformed), false)

  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify({ ...latest, research_only: false }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  try {
    await assert.rejects(() => fetchSystemCycleResearchLatest(), (error: unknown) => error instanceof BrowserApiError && error.status === 502)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('research tab mounts the request-owning panel only when active and preserves the non-executable copy', async () => {
  const reports = await readFile(new URL('../src/pages/ReportsPage.tsx', import.meta.url), 'utf8')
  const panel = await readFile(new URL('../src/components/SystemCycleResearchPanel.tsx', import.meta.url), 'utf8')
  assert.match(reports, /researchView && <SystemCycleResearchPanel/)
  assert.match(panel, /useEffect\(\(\) =>/)
  assert.match(panel, /cycle\.stocks\.map/)
  assert.match(panel, /历史规则回放与状态扫描，不是严格样本外验证；研究结果不可执行，不会发送Telegram或订单。/)
  assert.doesNotMatch(panel, /正式建议|已验证OOS|自动发布|买入|卖出|止盈|止损/)
})
