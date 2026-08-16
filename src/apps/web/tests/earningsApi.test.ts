import assert from 'node:assert/strict'
import test from 'node:test'

import { login } from '../src/api/client.ts'
import {
  createEarningsApi,
  EarningsApiError,
} from '../src/api/earnings.ts'
import {
  decodeEarningsDetail,
  decodeEarningsStatistics,
  EarningsDecodeError,
} from '../src/domain/earningsForecast.ts'


const EVENT_ID = 'AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ'
const OPTION_ID = 'AgICAgICAgICAgICAgICAgICAgICAgICAg'

const LOCKED_OVERVIEW = {
  state: 'locked',
  feature: 'earnings_forecast',
  required_capability: 'earnings_forecast',
  window_days: 7,
  confirmed_event_count: 1,
  reason_code: 'legacy_entitlement_required',
  description: '会员权限不足。',
  upgrade_path: null,
}

function forecast(optionResearch: unknown = {
  state: 'available',
  items: [{ option_id: OPTION_ID, structure_type: 'LONG_STRADDLE' }],
}) {
  return {
    countdown_day: 7,
    decision_at: '2026-08-12T00:00:00Z',
    available_cutoff_at: '2026-08-12T00:00:00Z',
    p_up: 0.5,
    p_down: 0.3,
    p_flat: 0.2,
    flat_band_pct: 1,
    confidence: 0.58,
    calibration_sample_size: 200,
    reference_price: 200,
    currency: 'USD',
    price_p10: 180,
    price_p50: 202,
    price_p90: 228,
    estimated_mfe_pct: 14,
    estimated_mae_pct: -10,
    simulated_action: 'OBSERVE',
    narrative: {
      summary: 'Research estimate only.',
      changed_since_previous: [],
      supporting_evidence: ['estimate revisions'],
      counter_evidence: ['valuation'],
    },
    causal_graph: { claims: [{
      kind: 'mechanism_hypothesis',
      claim: 'Revisions may support the reaction.',
      confidence: 0.55,
      evidence_count: 1,
      confounders: ['macro shock'],
    }] },
    risk: {
      defined_risk: true,
      max_loss_amount: 0,
      currency: 'USD',
      invalidation_condition: 'Schedule changes.',
    },
    evidence_count: 1,
    evidence_sha256: ['b'.repeat(64)],
    model_artifact_sha256: 'a'.repeat(64),
    evidence_manifest_sha256: 'c'.repeat(64),
    research_only: true,
    execution_eligible: false,
    automatic_ordering: false,
    action_contract: {
      structure: 'OBSERVE',
      entry: { limit_price: null, quantity: null },
      stop: null,
      targets: [],
      max_loss: 0,
      max_account_pct: null,
      breakeven: null,
      invalidation: 'Schedule changes.',
      exit: 'Manual review.',
      roll: 'No automatic roll.',
      quote_at: '2026-08-12T00:00:00Z',
      model_artifact_sha256: 'a'.repeat(64),
      evidence_manifest_sha256: 'c'.repeat(64),
      execution_eligible: false,
      automatic_ordering: false,
    },
    option_research: optionResearch,
  }
}

function detail(timeline: unknown[] = [forecast()]) {
  return {
    state: 'research',
    event_id: EVENT_ID,
    market: 'US',
    symbol: 'AAPL',
    fiscal_period: '2026Q3',
    scheduled_at: '2026-08-18T20:15:00Z',
    exchange_timezone: 'America/New_York',
    timing: 'AMC',
    status: 'CONFIRMED',
    research_only: true,
    execution_eligible: false,
    automatic_ordering: false,
    timeline,
    outcomes: [],
    postmortems: [],
  }
}

test('earnings browser transport reuses the in-memory Bearer session without exposing the token', async () => {
  const originalFetch = globalThis.fetch
  const calls: Array<{ path: string; init?: RequestInit }> = []
  globalThis.fetch = async (input, init) => {
    const path = String(input)
    calls.push({ path, init })
    if (path === '/api/rewrite/v1/session') {
      return new Response(JSON.stringify({
        access_token: 'memory-only-access-token',
        user: { id: 1 },
        new_ip: false,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    return new Response(JSON.stringify(LOCKED_OVERVIEW), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  try {
    await login('owner@example.com', 'correct horse battery staple')
    const payload = await createEarningsApi().fetchOverview()
    assert.equal(payload.state, 'locked')
    const request = calls.at(-1)
    assert.equal(request?.path, '/api/rewrite/v1/earnings-forecasts?window_days=7&limit=100')
    const headers = new Headers(request?.init?.headers)
    assert.equal(headers.get('Authorization'), 'Bearer memory-only-access-token')
    assert.equal(headers.get('Accept'), 'application/json')
    assert.equal(request?.init?.credentials, 'same-origin')
    assert.equal(request?.init?.cache, 'no-store')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('earnings statistics preserve unavailable paper performance instead of fake zeroes', () => {
  const decoded = decodeEarningsStatistics({
    state: 'research',
    metrics: {
      sample_size: 3,
      direction_accuracy: 0.5,
      multiclass_brier_score: 0.4,
      log_loss: 0.8,
      expected_calibration_error: 0.1,
      average_confidence_gap: 0.05,
      interval_coverage: 0.7,
      average_interval_width: 12,
      overconfidence_rate: 0.2,
      high_confidence_sample_size: 1,
      paper_total_pnl: null,
      paper_max_drawdown: null,
    },
  })
  assert.equal(decoded.state, 'research')
  if (decoded.state !== 'research') return
  assert.equal(decoded.metrics.paper_total_pnl, null)
  assert.equal(decoded.metrics.paper_max_drawdown, null)
})

test('earnings transport preserves an authoritative 401 error', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(
    JSON.stringify({ error: '缺少 Bearer Access Token。' }),
    { status: 401, headers: { 'Content-Type': 'application/json' } },
  )
  try {
    await assert.rejects(
      () => createEarningsApi().fetchOverview(),
      (error: unknown) => error instanceof EarningsApiError
        && error.status === 401
        && error.message === '缺少 Bearer Access Token。',
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('opaque option references are decoded and mapped to the existing detail endpoint', async () => {
  const paths: string[] = []
  const api = createEarningsApi(async (path) => {
    paths.push(path)
    return {
      state: 'locked',
      feature: 'earnings_option_research',
      required_capability: 'earnings_option_defined_risk',
      reason_code: 'legacy_entitlement_required',
      upgrade_path: null,
    }
  })
  const response = await api.fetchOptionDetail(EVENT_ID, OPTION_ID)
  assert.equal(response.state, 'locked')
  assert.deepEqual(paths, [`/api/rewrite/v1/earnings-forecasts/${EVENT_ID}/options/${OPTION_ID}`])

  await assert.rejects(() => api.fetchOptionDetail('bad-id', OPTION_ID), EarningsApiError)
  assert.equal(paths.length, 1)
})

test('detail decoder accepts capability-safe option references and rejects duplicates', () => {
  const decoded = decodeEarningsDetail(detail())
  assert.equal(decoded.state, 'research')
  if (decoded.state !== 'research') return
  assert.equal(decoded.timeline[0].option_research.state, 'available')

  assert.throws(
    () => decodeEarningsDetail(detail([forecast(), forecast()])),
    EarningsDecodeError,
  )
  assert.throws(
    () => decodeEarningsDetail(detail([forecast({
      state: 'available',
      items: [
        { option_id: OPTION_ID, structure_type: 'LONG_CALL' },
        { option_id: OPTION_ID, structure_type: 'LONG_PUT' },
      ],
    })])),
    EarningsDecodeError,
  )
  assert.throws(
    () => decodeEarningsDetail(detail([forecast({
      state: 'no_data',
      items: [{ option_id: OPTION_ID, structure_type: 'LONG_CALL' }],
    })])),
    EarningsDecodeError,
  )
})
