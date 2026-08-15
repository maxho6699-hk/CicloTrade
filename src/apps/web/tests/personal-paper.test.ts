import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  formatPersonalPaperRiskCheck,
  validPersonalPaperRiskProof,
} from '../src/api/client.ts'

const paperPrimitives = readFileSync(new URL('../src/components/paper/PaperPrimitives.tsx', import.meta.url), 'utf8')

const checks = [
  'buying_power', 'max_loss', 'position_concentration', 'sector_concentration',
  'drawdown', 'event_gap', 'liquidity',
].map((code) => {
  const values = {
    buying_power: [{ required: 100, available: 900 }, { required_max: 900 }],
    max_loss: [{ usd: 100, pct: 1, unbounded: false }, { usd: 1000, pct: 10 }],
    position_concentration: [{ usd: 100, pct: 1 }, { pct: 25 }],
    sector_concentration: [{ industry: 'Technology', usd: 100, pct: 1 }, { pct: 35 }],
    drawdown: [{ pct: 2, peak_usd: 10000, current_usd: 9800 }, { pct: 20 }],
    event_gap: [{ scheduled_at: '2026-08-14T00:00:00Z', revision_id: 1, payload_sha256: 'a'.repeat(64) }, { scheduled_at: 'must_be_known' }],
    liquidity: [{ spread_pct: 0.5 }, { spread_pct: 2 }],
  }[code as keyof typeof values]
  return { code, status: 'pass', title: code, detail: `${code} passed`, value: JSON.stringify(values[0]), limit: JSON.stringify(values[1]), data_state: 'fresh' }
})

const proof = {
  id: 'ppr_1234567890', schema_version: 'r1', season_id: 'pps_1234567890', quote_id: 'ppq_1234567890',
  account_version: 2, draft_sha256: 'a'.repeat(64), proof_sha256: 'b'.repeat(64),
  created_at: '2026-08-14T00:00:00+00:00', computed_at: '2026-08-14T00:00:01+00:00',
  marks_as_of: '2026-08-14T00:00:00+00:00', expires_at: '2026-08-14T00:05:00+00:00',
  decision: 'allow', risk_level: 'low', data_state: 'fresh', checks, blocking_reasons: [], warnings: [],
}

test('personal paper risk proof is fail-closed for every decision branch', () => {
  const failedChecks = checks.map((check, index) => index === 0 ? { ...check, status: 'fail' } : check)
  assert.equal(validPersonalPaperRiskProof({ ...proof, decision: 'review', risk_level: 'moderate', checks: failedChecks }), false)
  assert.equal(validPersonalPaperRiskProof({ ...proof, decision: 'review', risk_level: 'moderate', blocking_reasons: ['需要复核'] }), false)
  assert.equal(validPersonalPaperRiskProof({ ...proof, decision: 'review', risk_level: 'blocked' }), false)
  assert.equal(validPersonalPaperRiskProof({ ...proof, decision: 'allow', risk_level: 'moderate' }), false)
  assert.equal(validPersonalPaperRiskProof({ ...proof, decision: 'reject', risk_level: 'blocked' }), false)
  assert.equal(validPersonalPaperRiskProof({ ...proof, decision: 'reject', risk_level: 'blocked', checks: failedChecks }), false)
  assert.equal(validPersonalPaperRiskProof({ ...proof, decision: 'reject', risk_level: 'blocked', checks: failedChecks, blocking_reasons: ['buying_power passed'] }), true)
  assert.equal(validPersonalPaperRiskProof({ ...proof, decision: 'reject', risk_level: 'blocked', blocking_reasons: ['购买力不足'] }), true)
  const warningChecks = checks.map((check, index) => index === 5 ? { ...check, status: 'unknown', detail: 'event unknown' } : check)
  assert.equal(validPersonalPaperRiskProof({ ...proof, decision: 'review', risk_level: 'moderate', checks: warningChecks, warnings: ['event unknown'] }), true)
  assert.equal(validPersonalPaperRiskProof({ ...proof, decision: 'review', risk_level: 'moderate', checks: warningChecks, warnings: ['wrong order'] }), false)
  assert.equal(validPersonalPaperRiskProof({ ...proof, decision: 'allow', risk_level: 'low', checks: warningChecks }), false)
  assert.equal(validPersonalPaperRiskProof({ ...proof, checks: checks.map((check, index) => index === 0 ? { ...check, value: '{"required":100}' } : check) }), false)
})

test('personal paper risk check formatter rejects malformed JSON without exposing raw payloads', () => {
  const malformed = { ...checks[0], value: '{"required":100}' }
  const formatted = formatPersonalPaperRiskCheck(malformed)
  assert.match(formatted.value, /数据格式异常|需重新获取/)
  assert.doesNotMatch(formatted.value, /required/)
  assert.equal(formatPersonalPaperRiskCheck({ ...checks[3], value: JSON.stringify({ industry: 'Technology', usd: 100, pct: 2 }), limit: JSON.stringify({ pct: 35 }) }).value.includes('Technology'), true)
  assert.doesNotMatch(formatPersonalPaperRiskCheck({ ...checks[5], value: JSON.stringify({ scheduled_at: '2026-08-14T00:00:00Z', revision_id: 1, payload_sha256: 'a'.repeat(64) }), limit: JSON.stringify({ scheduled_at: 'must_be_known' }) }).value, /\{"/)
})

test('personal paper risk check formatter handles all seven fixed schemas', () => {
  const values = [
    { code: 'buying_power', value: { required: 100, available: 900 }, limit: { required_max: 900 } },
    { code: 'max_loss', value: { usd: 100, pct: 1, unbounded: false }, limit: { usd: 1000, pct: 10 } },
    { code: 'position_concentration', value: { usd: 100, pct: 1 }, limit: { pct: 25 } },
    { code: 'sector_concentration', value: { industry: 'Technology', usd: 100, pct: 1 }, limit: { pct: 35 } },
    { code: 'drawdown', value: { pct: 2, peak_usd: 10000, current_usd: 9800 }, limit: { pct: 20 } },
    { code: 'event_gap', value: { scheduled_at: '2026-08-14T00:00:00Z', revision_id: 1, payload_sha256: 'a'.repeat(64) }, limit: { scheduled_at: 'must_be_known' } },
    { code: 'liquidity', value: { spread_pct: 0.5 }, limit: { spread_pct: 2 } },
  ]
  for (const item of values) {
    const formatted = formatPersonalPaperRiskCheck({ ...checks.find((check) => check.code === item.code), value: JSON.stringify(item.value), limit: JSON.stringify(item.limit) } as typeof checks[number])
    assert.doesNotMatch(formatted.value, /\{"/)
    assert.doesNotMatch(formatted.value, /数据格式异常/)
  }
})

test('personal paper preserves legal missing sector data and truthful preview wording', () => {
  const formatted = formatPersonalPaperRiskCheck({ ...checks[3], value: null, limit: JSON.stringify({ pct: 35 }), data_state: 'missing' })
  assert.match(formatted.value, /暂无数据|需重新获取/)
  assert.equal(formatted.limit, '≤ 35.00%')
  assert.match(paperPrimitives, /预计资金占用（含费用）/)
  assert.match(paperPrimitives, /费用未单列/)
  for (const label of ['买入', '卖出', '做空', '回补', '市价', '限价', '止损触发', '止损限价']) assert.match(paperPrimitives, new RegExp(label))
})
