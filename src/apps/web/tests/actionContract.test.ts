import assert from 'node:assert/strict'
import test from 'node:test'
import type { RecommendationItem } from '../src/api/client.ts'
import { assessRecommendationContract } from '../src/domain/actionContract.ts'

const baseItem: RecommendationItem = {
  event_id: 12,
  state: 'official',
  action: 'BUY',
  position_action: 'open_long',
  market: 'US',
  instrument_type: 'stock',
  symbol: 'AAPL',
  currency: 'USD',
  reference_price: 200,
  current_price: 210,
  quantity_hint: 5,
  actionable: true,
  contract_status: 'complete',
  stop_price: 198,
  target_price: 225,
  max_loss: 60,
  rationale: '价格与风险条件完整。',
  strategy_name: 'test-strategy',
  strategy_version: '1',
  occurred_at: '2026-08-11T01:00:00Z',
}

test('historical reference price is never treated as the current quote', () => {
  const assessment = assessRecommendationContract(baseItem, undefined, Date.parse('2026-08-11T02:00:00Z'))
  assert.equal(assessment.actionable, false)
  assert.equal(assessment.price, 210)
  assert.equal(assessment.blockReason, '没有可验证的当前报价时间')
})

test('a complete contract with a fresh timestamp exposes the controlled quantity', () => {
  const fixedItem = { ...baseItem, quote_at: '2026-08-11T01:55:00Z' }
  const assessment = assessRecommendationContract(fixedItem, undefined, Date.parse('2026-08-11T02:00:00Z'))

  assert.equal(assessment.actionable, true)
  assert.equal(assessment.quantity, 5)
})

test('stale quotes always degrade to no trade and quantity zero', () => {
  const item = { ...baseItem, quote_at: '2026-08-11T01:00:00Z' }
  const assessment = assessRecommendationContract(item, undefined, Date.parse('2026-08-11T02:00:00Z'))

  assert.equal(assessment.actionable, false)
  assert.equal(assessment.quoteFreshness, 'stale')
})

test('unix-second candle timestamps are evaluated as seconds, not milliseconds', () => {
  const now = Date.parse('2026-08-11T02:00:00Z')
  const quoteAt = Date.parse('2026-08-11T01:55:00Z') / 1000
  const assessment = assessRecommendationContract(baseItem, { price: 211, quoteAt }, now)

  assert.equal(assessment.actionable, true)
  assert.equal(assessment.quoteFreshness, 'fresh')
})
