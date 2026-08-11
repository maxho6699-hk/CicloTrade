import assert from 'node:assert/strict'
import test from 'node:test'
import type { OptionContract } from '../src/api/client.ts'
import {
  buildOptionTemplate,
  describeOptionDataStatus,
  inferOptionStrategyLabel,
  summarizeOptionCombination,
  type OptionStrategyLeg,
} from '../src/domain/optionResearch.ts'
import { displayDataSource } from '../src/domain/dataSourcePresentation.ts'

function contract(optionType: 'CALL' | 'PUT', strike: number, bid: number | null, ask: number | null): OptionContract {
  return {
    expiry: '2026-09-18',
    option_type: optionType,
    contract_code: `US.AAPL260918${optionType === 'CALL' ? 'C' : 'P'}${String(strike * 1000).padStart(6, '0')}`,
    strike,
    last: bid,
    bid,
    ask,
    spread: bid !== null && ask !== null ? ask - bid : null,
    volume: 100,
    open_interest: 500,
    implied_volatility: 0.3,
    greeks: { delta: optionType === 'CALL' ? 0.5 : -0.5, gamma: 0.02, theta: -0.1, vega: 0.2, rho: 0.05 },
    quote_at: '2026-08-11T10:00:00Z',
  }
}

test('summarizes a long straddle when research quotes are complete', () => {
  const legs: OptionStrategyLeg[] = [
    { id: 'call', contract: contract('CALL', 210, 5.1, 5.3), side: 'BUY', quantity: 1 },
    { id: 'put', contract: contract('PUT', 210, 4.8, 5), side: 'BUY', quantity: 1 },
  ]
  const summary = summarizeOptionCombination(legs)

  assert.equal(inferOptionStrategyLabel(legs), '买入跨式组合')
  assert.equal(summary.quoteComplete, true)
  assert.equal(summary.quoteLabel, '净付权利金')
  assert.equal(summary.netCash, -1030)
  assert.equal(summary.delta, 0)
})

test('does not invent a combination price when a tradable quote is missing', () => {
  const legs: OptionStrategyLeg[] = [
    { id: 'call', contract: contract('CALL', 210, 5.1, null), side: 'BUY', quantity: 1 },
  ]
  const summary = summarizeOptionCombination(legs)

  assert.equal(summary.quoteComplete, false)
  assert.equal(summary.netCash, null)
  assert.equal(summary.quoteLabel, '报价不完整')
})

test('anonymizes delayed option providers while retaining the research boundary', () => {
  assert.equal(describeOptionDataStatus({
    source: 'Yahoo Finance',
    freshness: '免费延迟期权研究数据，不用于立即交易',
    is_realtime: false,
    actionable_quote: false,
    delivery_delay_minutes: 15,
  }), '真实数据来源 · 延迟 15 分钟 · 仅供研究')
})

test('account delivery delay overrides provider realtime flags', () => {
  assert.equal(describeOptionDataStatus({
    source: 'OpenD',
    freshness: '美股期权 LV3 实时权限已验证',
    is_realtime: true,
    actionable_quote: true,
    delivery_delay_minutes: 15,
  }), '真实数据来源 · 延迟 15 分钟 · 仅供研究')
})

test('only describes an option source as verified when both realtime flags are true', () => {
  assert.match(describeOptionDataStatus({
    source: 'OpenD',
    freshness: '美股期权 LV2 实时权限已验证',
    is_realtime: true,
    actionable_quote: true,
  }), /^真实数据来源 · 实时权限已验证$/)
  assert.match(describeOptionDataStatus({
    source: 'OpenD',
    freshness: '实时开关未启用',
    is_realtime: false,
    actionable_quote: true,
  }), /仅供研究$/)
})

test('only exact non-real source states remain visible', () => {
  assert.equal(displayDataSource('界面演示数据'), '界面演示数据')
  assert.equal(displayDataSource('offline'), '离线')
  assert.equal(displayDataSource('unavailable'), '不可用')
  assert.equal(displayDataSource('demo · private-adapter'), '真实数据来源')
  assert.equal(displayDataSource('private-adapter unavailable'), '真实数据来源')
})

test('builds a bull call spread from the anchor and next higher strike', () => {
  const chain = [
    contract('CALL', 210, 5.1, 5.3),
    contract('CALL', 215, 3.1, 3.3),
    contract('PUT', 210, 4.8, 5),
  ]
  const legs = buildOptionTemplate('bull-call-spread', chain[0], chain)

  assert.equal(legs.length, 2)
  assert.deepEqual(legs.map((leg) => [leg.contract.strike, leg.side]), [[210, 'BUY'], [215, 'SELL']])
  assert.equal(inferOptionStrategyLabel(legs), '牛市看涨价差')
})
