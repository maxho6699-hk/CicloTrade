import assert from 'node:assert/strict'
import test from 'node:test'
import { createDeliberationApi, deliberationBindingFromRecommendation, type DeliberationBinding } from '../src/api/deliberation.ts'

const binding: DeliberationBinding = { market: 'US', symbol: 'AAPL', timeframe: '1d', question: '资料审阅', source_event_id: 'event_1', source_event_version: 1, source_event_sha256: 'a'.repeat(64) }
const seat = (name: string) => ({ seat: name, status: 'ready', support_strength: 60, counter_evidence_strength: 40, weight_bps: 2500, contribution: { support: 15, counter: 10 }, coverage: 1, source: 'source-v1', citation: 'citation-v1', missing: [], invalidated_reason: null })
const result = { ...binding, deliberation_public_id: 'dlb_1', task_public_id: 'wfl_1', status: 'succeeded', method_version: 'deliberation.v1', evidence_version: 'evidence.v1', research_version: 'research.v1', support_strength: 60, counter_evidence_strength: 40, coverage: 1, missing: [], seats: { market_structure: seat('market_structure'), fundamentals: seat('fundamentals'), news_macro: seat('news_macro'), risk: seat('risk') }, observed_at: '2026-08-16T00:00:00Z', available_at: '2026-08-16T00:00:00Z', as_of: '2026-08-16T00:00:00Z', calculated_at: '2026-08-16T00:00:00Z', invalidated_reason: null, evidence_snapshot_sha256: 'b'.repeat(64), result_sha256: 'c'.repeat(64) }

test('deliberation API uses readiness query and rejects malformed results', async () => {
  const calls: string[] = []
  const api = createDeliberationApi(async (path) => { calls.push(path); return { ...binding, ready: false, status: 'blocked', missing: ['risk'], reason: 'evidence_snapshot_missing' } })
  const readiness = await api.readiness(binding)
  assert.equal(readiness.status, 'blocked')
  assert.match(calls[0], /\/deliberations\/readiness\?/)
  await assert.rejects(() => createDeliberationApi(async () => ({ ...result, owner_id: 1 })).get('dlb_1'), /审议结果响应格式无效/)
})

test('deliberation API maps list and detail routes to server DTOs', async () => {
  const calls: string[] = []
  const api = createDeliberationApi(async (path) => { calls.push(path); return path.includes('?limit=') ? { items: [result] } : result })
  assert.equal((await api.list())[0].task_public_id, 'wfl_1')
  assert.equal((await api.get('dlb_1')).status, 'succeeded')
  assert.deepEqual(calls, ['/api/rewrite/v1/deliberations?limit=100', '/api/rewrite/v1/deliberations/dlb_1'])
})

test('deliberation API exposes create/cancel/retry and binds a real recommendation event', async () => {
  const calls: Array<{ path: string; init?: RequestInit }> = []
  const api = createDeliberationApi(async (path, init) => {
    calls.push({ path, init })
    return result
  })
  const recommendation = {
    event_id: 7,
    state: 'official' as const,
    action: 'BUY' as const,
    position_action: 'open_long' as const,
    market: 'US',
    instrument_type: 'stock' as const,
    symbol: 'AAPL',
    reference_price: 200,
    current_price: null,
    quote_at: null,
    stop_price: null,
    target_price: null,
    max_loss: null,
    rationale: '资料审阅',
    strategy_name: 'trend',
    strategy_version: 'v1',
    occurred_at: '2026-08-16T00:00:00Z',
    recorded_at: '2026-08-16T00:00:00Z',
    available_at: null,
    contract_status: 'complete' as const,
    missing_fields: [],
  }
  const bound = await deliberationBindingFromRecommendation(recommendation)
  assert.equal(bound.source_event_id, 'qevt_7')
  assert.match(bound.source_event_sha256, /^[0-9a-f]{64}$/)
  assert.equal((await api.create(bound)).deliberation_public_id, 'dlb_1')
  assert.equal((await api.cancel('dlb_1')).status, 'succeeded')
  assert.equal((await api.retry('dlb_1')).task_public_id, 'wfl_1')
  assert.deepEqual(calls.map(({ path }) => path), ['/api/rewrite/v1/deliberations', '/api/rewrite/v1/deliberations/dlb_1/cancel', '/api/rewrite/v1/deliberations/dlb_1/retry'])
  assert.equal(calls[0].init?.method, 'POST')
})
