import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import {
  fetchStrategyResearch97History,
  fetchStrategyResearch97Latest,
  fetchStrategyResearch97Status,
  fetchStrategyResearch97Aggregate,
  validStrategyResearch97Aggregate,
  validStrategyResearch97History,
  validStrategyResearch97Latest,
  validStrategyResearch97Status,
} from '../src/api/strategyResearch97.ts'

const authority = {
  publication_ceiling: 'shadow', projection_scope: 'authenticated_research', source_user_visible: false,
  research_only: true, actionable: false, outbound: false,
  execution: false, official: false, live: false,
} as const
const hash = 'a'.repeat(64)
const universe = { key: 'us-liquid-research', version: 'us-liquid-research-2026-08-13-v1', count: 97, sha256: hash } as const

function symbols() {
  return Array.from({ length: 97 }, (_, index) => ({
    market: 'US' as const,
    symbol: `STK${String(index + 1).padStart(2, '0')}`,
    tier: index < 13 ? 'A' as const : 'C' as const,
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
  assert.equal(validStrategyResearch97Aggregate({ status, latest, history }), true)
  assert.equal(status.coverage_count + status.no_data_count, 97)
  assert.equal(latest.cycle.summary.wait_count > 0, true)
})

test('fails closed when authority, universe count, or missing-data signal is unsafe', () => {
  assert.equal(validStrategyResearch97Status({ ...status, authority: { ...authority, actionable: true } }), false)
  assert.equal(validStrategyResearch97Status({ ...status, authority: { ...authority, source_user_visible: true } }), false)
  assert.equal(validStrategyResearch97Status({ ...status, authority: { ...authority, projection_scope: 'public' as never } }), false)
  assert.equal(validStrategyResearch97Status({ ...status, universe: { ...universe, count: 13 } }), false)
  assert.equal(validStrategyResearch97Latest({ ...latest, cycle: { ...latest.cycle, symbols: latest.cycle.symbols.map((item, index) => index === 96 ? { ...item, signal: 'long' } : item) } }), false)
  assert.equal(validStrategyResearch97Aggregate({ status, latest: { ...latest, cycle: { ...latest.cycle, evidence: { ...latest.cycle.evidence, universe_sha256: 'f'.repeat(64) } } }, history }), false)
  assert.equal(validStrategyResearch97Aggregate({ status: { ...status, coverage_count: 95, no_data_count: 2 }, latest, history }), false)
  assert.equal(validStrategyResearch97Aggregate({ status, latest, history: { ...history, items: [{ ...history.items[0], cycle_id: 'different-cycle' }] } }), false)
  assert.equal(validStrategyResearch97Aggregate({ status, latest, history: { ...history, items: [{ ...history.items[0], long_count: 31 }] } }), false)
  assert.equal(validStrategyResearch97Aggregate({ status, latest: { ...latest, cycle: { ...latest.cycle, symbols: latest.cycle.symbols.map((item, index) => index === 13 ? { ...item, tier: 'A' as const } : item) } }, history }), false)
  assert.equal(validStrategyResearch97Aggregate({ status: { ...status, last_result_at: null }, latest, history }), false)
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

test('aggregate loader preserves partial resources instead of converting unavailable data into empty', async () => {
  const originalFetch = globalThis.fetch
  const unavailableStatus = { ...status, available: false, state: 'waiting' as const, last_heartbeat_at: null, last_result_at: null, expires_at: null, coverage_count: 0, no_data_count: 97, spool: null }
  const unavailableHistory = { available: false, authority, limit: 20 as const, items: [] }
  globalThis.fetch = async (input) => {
    const path = String(input)
    const payload = path.endsWith('/status') ? unavailableStatus : path.includes('/history?') ? unavailableHistory : latest
    return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    const result = await fetchStrategyResearch97Aggregate()
    assert.equal(result.phase, 'partial')
    assert.equal(result.reason, 'resource_unavailable')
    assert.equal(result.status.state, 'unavailable')
    assert.equal(result.history.state, 'unavailable')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('aggregate loader preserves status and latest when history fails independently', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (input) => {
    const path = String(input)
    if (path.endsWith('/history?limit=20')) return new Response(JSON.stringify({ error: 'temporarily unavailable' }), { status: 503 })
    const payload = path.endsWith('/status') ? status : latest
    return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    const result = await fetchStrategyResearch97Aggregate()
    assert.equal(result.phase, 'partial')
    assert.equal(result.reason, 'resource_error')
    assert.equal(result.status.state, 'ready')
    assert.equal(result.latest.state, 'ready')
    assert.equal(result.history.state, 'error')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('aggregate loader rejects cross-source hash drift and preserves forbidden as an explicit state', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (input) => {
    const path = String(input)
    const payload = path.endsWith('/status') ? status : path.includes('/history?') ? history : { ...latest, cycle: { ...latest.cycle, evidence: { ...latest.cycle.evidence, universe_sha256: 'f'.repeat(64) } } }
    return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    const mismatch = await fetchStrategyResearch97Aggregate()
    assert.equal(mismatch.phase, 'error')
    assert.equal(mismatch.reason, 'cross_source_mismatch')
  } finally {
    globalThis.fetch = originalFetch
  }

  globalThis.fetch = async () => new Response(JSON.stringify({ error: 'forbidden' }), { status: 403, headers: { 'Content-Type': 'application/json' } })
  try {
    const forbidden = await fetchStrategyResearch97Aggregate()
    assert.equal(forbidden.phase, 'error')
    assert.equal(forbidden.forbidden, true)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('the panel exposes all safety states and contains no execution control', async () => {
  const panel = await readFile(new URL('../src/components/StrategyResearch97Panel.tsx', import.meta.url), 'utf8')
  assert.match(panel, /phase: 'loading'/)
  assert.match(panel, /phase: 'forbidden'/)
  assert.match(panel, /statusData\?\.state === 'stale'/)
  assert.match(panel, /不可执行、不可下单、不可推送 Telegram/)
  assert.doesNotMatch(panel, /自动下单|提交订单|发送Telegram\(/)
})

test('overview card keeps stable and expanded chains distinct and links to research evidence', async () => {
  const card = await readFile(new URL('../src/components/StrategyResearchOverviewCard.tsx', import.meta.url), 'utf8')
  assert.match(card, /13 股稳定 shadow/)
  assert.match(card, /97 标的扩容 research/)
  assert.match(card, /reports\?view=影子策略研究&research_scope=expanded/)
  assert.match(card, /StrategyResearchOverviewLocale/)
  assert.match(card, /expandedCoverage === null \? '—'/)
  assert.doesNotMatch(card, /下单|Telegram/)
})

test('panel owns Tier/status/search pagination and explicit partial/stale states', async () => {
  const panel = await readFile(new URL('../src/components/StrategyResearch97Panel.tsx', import.meta.url), 'utf8')
  assert.match(panel, /PAGE_SIZE = 18/)
  assert.match(panel, /tierFilter/)
  assert.match(panel, /signalFilter/)
  assert.match(panel, /setPage\(1\)/)
  assert.match(panel, /phase: 'partial'/)
  assert.match(panel, /statusData\?\.state === 'stale'/)
  assert.match(panel, /aria-live="polite"/)
  assert.match(panel, /research_query/)
  assert.match(panel, /research_page/)
  assert.match(panel, /strategy-research-97-filter-empty/)
  assert.match(panel, /autoComplete="off"/)
  assert.match(panel, /spellCheck={false}/)
})

test('panel keeps touch-safe focus styles and a zero-result state', async () => {
  const panel = await readFile(new URL('../src/components/StrategyResearch97Panel.tsx', import.meta.url), 'utf8')
  const styles = await readFile(new URL('../src/styles/strategy-research-97.css', import.meta.url), 'utf8')
  assert.match(panel, /text\.noMatches/)
  assert.match(panel, /text\.projection/)
  assert.match(styles, /focus-visible/)
  assert.match(styles, /min-height: 44px/)
  assert.match(styles, /max-width: 390px/)
})
