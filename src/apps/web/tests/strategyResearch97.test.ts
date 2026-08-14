import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import {
  fetchStrategyResearch97History,
  fetchStrategyResearch97Latest,
  fetchStrategyResearch97Status,
  validStrategyResearch97History,
  validStrategyResearch97Latest,
  validStrategyResearch97Status,
} from '../src/api/strategyResearch97.ts'

const authority = {
  publication_ceiling: 'shadow', research_only: true, actionable: false, outbound: false,
  execution: false, official: false, live: false,
} as const
const hash = 'a'.repeat(64)
const universe = { key: 'us-liquid-research', version: 'us-liquid-research-2026-08-13-v1', count: 97, sha256: hash } as const

function symbols() {
  return Array.from({ length: 97 }, (_, index) => ({
    market: 'US' as const,
    symbol: `STK${String(index + 1).padStart(2, '0')}`,
    data_state: index === 96 ? 'missing' as const : 'fresh' as const,
    signal: index === 96 ? 'wait' as const : index % 3 === 0 ? 'long' as const : index % 3 === 1 ? 'flat' as const : 'wait' as const,
    rationale: index === 96 ? null : '研究标签由服务端周期回传。',
    updated_at: index === 96 ? null : '2026-08-14T08:00:00Z',
  }))
}

const status = {
  available: true, state: 'healthy' as const, authority, universe,
  last_heartbeat_at: '2026-08-14T08:01:00Z', last_result_at: '2026-08-14T08:00:00Z', expires_at: '2026-08-14T20:00:00Z',
  coverage_count: 96, no_data_count: 1, spool: { pending: 0, claimed: 0, retryable: 0, delivered: 4 },
}
const latest = {
  available: true, authority, validation_label: '扩容研究 · 只读',
  cycle: {
    cycle_id: 'expanded-2026-08-14-eod', evaluation_date: '2026-08-14', evaluated_at: '2026-08-14T08:00:00Z',
    strategy_key: 'expanded-equity-research', strategy_name: 'US liquid research', strategy_version: 'v1',
    summary: { long_count: 32, flat_count: 32, wait_count: 32, no_data_count: 1 }, symbols: symbols(),
    evidence: { universe_sha256: hash, source_snapshot_sha256: 'b'.repeat(64), code_bundle_sha256: 'c'.repeat(64), result_sha256: 'd'.repeat(64) },
  },
}
const history = {
  available: true, authority, limit: 20 as const,
  items: [{ cycle_id: 'expanded-2026-08-14-eod', evaluation_date: '2026-08-14', evaluated_at: '2026-08-14T08:00:00Z', received_at: '2026-08-14T08:01:00Z', coverage_count: 96, no_data_count: 1, long_count: 32, flat_count: 32, wait_count: 32 }],
}

test('accepts the strict expanded-research authority and 97-symbol coverage', () => {
  assert.equal(validStrategyResearch97Status(status), true)
  assert.equal(validStrategyResearch97Latest(latest), true)
  assert.equal(validStrategyResearch97History(history), true)
  assert.equal(status.coverage_count + status.no_data_count, 97)
  assert.equal(latest.cycle.summary.wait_count > 0, true)
})

test('fails closed when authority, universe count, or missing-data signal is unsafe', () => {
  assert.equal(validStrategyResearch97Status({ ...status, authority: { ...authority, actionable: true } }), false)
  assert.equal(validStrategyResearch97Status({ ...status, universe: { ...universe, count: 13 } }), false)
  assert.equal(validStrategyResearch97Latest({ ...latest, cycle: { ...latest.cycle, symbols: latest.cycle.symbols.map((item, index) => index === 96 ? { ...item, signal: 'long' } : item) } }), false)
})

test('fetches only the three read-only expanded-research endpoints', async () => {
  const originalFetch = globalThis.fetch
  const paths: string[] = []
  const responses = [status, latest, history]
  globalThis.fetch = async (input) => {
    paths.push(String(input))
    return new Response(JSON.stringify(responses[paths.length - 1]), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    await Promise.all([fetchStrategyResearch97Status(), fetchStrategyResearch97Latest(), fetchStrategyResearch97History()])
    assert.deepEqual(paths.sort(), [
      '/api/rewrite/v1/strategy-research/expanded/history?limit=20',
      '/api/rewrite/v1/strategy-research/expanded/latest',
      '/api/rewrite/v1/strategy-research/expanded/status',
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('the panel exposes all safety states and contains no execution control', async () => {
  const panel = await readFile(new URL('../src/components/StrategyResearch97Panel.tsx', import.meta.url), 'utf8')
  assert.match(panel, /phase: 'loading'/)
  assert.match(panel, /phase: 'forbidden'/)
  assert.match(panel, /status\.state === 'stale'/)
  assert.match(panel, /不可执行、不可下单、不可推送 Telegram/)
  assert.doesNotMatch(panel, /自动下单|提交订单|发送Telegram\(/)
})

test('overview card keeps stable and expanded chains distinct and links to research evidence', async () => {
  const card = await readFile(new URL('../src/components/StrategyResearchOverviewCard.tsx', import.meta.url), 'utf8')
  assert.match(card, /13 股稳定 shadow/)
  assert.match(card, /97 标的扩容 research/)
  assert.match(card, /reports\?view=影子策略研究/)
  assert.match(card, /expandedCoverage === null \? '—'/)
  assert.doesNotMatch(card, /下单|Telegram/)
})
