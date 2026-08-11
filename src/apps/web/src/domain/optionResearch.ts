import type { OptionContract } from '../api/client'
import { deliveryAllowsImmediateAction, displayDataSource, displayDeliveryDelay, displayFreshness } from './dataSourcePresentation.ts'
export { displayDataSource } from './dataSourcePresentation.ts'

export type OptionLegSide = 'BUY' | 'SELL'

export interface OptionStrategyLeg {
  id: string
  contract: OptionContract
  side: OptionLegSide
  quantity: number
}

export type OptionTemplateId =
  | 'long-straddle'
  | 'long-strangle'
  | 'bull-call-spread'
  | 'bear-put-spread'

export interface OptionCombinationSummary {
  label: string
  quoteComplete: boolean
  netCash: number | null
  quoteLabel: '净收权利金' | '净付权利金' | '权利金持平' | '报价不完整'
  delta: number | null
  gamma: number | null
  theta: number | null
  vega: number | null
}

export interface OptionDataStatusMetadata {
  source: string
  freshness: string
  is_realtime: boolean
  actionable_quote: boolean
  delivery_delay_minutes?: number
}

export function describeOptionDataStatus(metadata: OptionDataStatusMetadata) {
  const boundary = deliveryAllowsImmediateAction(metadata)
    ? '实时权限已验证'
    : '仅供研究'
  const freshness = displayDeliveryDelay(metadata.delivery_delay_minutes) || displayFreshness(metadata.freshness)
  return [displayDataSource(metadata.source), freshness, freshness === boundary ? '' : boundary].filter(Boolean).join(' · ')
}

function finitePositive(value: number | null) {
  return value !== null && Number.isFinite(value) && value > 0 ? value : null
}

function legQuote(leg: OptionStrategyLeg) {
  return leg.side === 'BUY'
    ? finitePositive(leg.contract.ask)
    : finitePositive(leg.contract.bid)
}

function sameExpiry(legs: OptionStrategyLeg[]) {
  return new Set(legs.map((leg) => leg.contract.expiry)).size === 1
}

export function inferOptionStrategyLabel(legs: OptionStrategyLeg[]) {
  if (!legs.length) return '尚未加入组合腿'
  if (legs.length === 1) return '单腿期权研究'
  if (legs.length !== 2 || !sameExpiry(legs)) return '自定义多腿组合'

  const [left, right] = [...legs].sort((a, b) => a.contract.strike - b.contract.strike)
  const bothBuy = left.side === 'BUY' && right.side === 'BUY'
  const bothSell = left.side === 'SELL' && right.side === 'SELL'
  const sameStrike = left.contract.strike === right.contract.strike
  const rights = new Set(legs.map((leg) => leg.contract.option_type))

  if (rights.size === 2 && sameStrike && bothBuy) return '买入跨式组合'
  if (rights.size === 2 && sameStrike && bothSell) return '卖出跨式组合'
  if (rights.size === 2 && bothBuy) return '买入宽跨式组合'
  if (rights.size === 2 && bothSell) return '卖出宽跨式组合'

  if (left.contract.option_type === 'CALL' && right.contract.option_type === 'CALL') {
    if (left.side === 'BUY' && right.side === 'SELL') return '牛市看涨价差'
    if (left.side === 'SELL' && right.side === 'BUY') return '熊市看涨价差'
  }
  if (left.contract.option_type === 'PUT' && right.contract.option_type === 'PUT') {
    if (left.side === 'SELL' && right.side === 'BUY') return '熊市看跌价差'
    if (left.side === 'BUY' && right.side === 'SELL') return '牛市看跌价差'
  }
  return '自定义双腿组合'
}

function aggregateGreek(legs: OptionStrategyLeg[], greek: keyof OptionContract['greeks']) {
  if (!legs.length || legs.some((leg) => leg.contract.greeks[greek] === null)) return null
  return legs.reduce((total, leg) => {
    const direction = leg.side === 'BUY' ? 1 : -1
    return total + (leg.contract.greeks[greek] ?? 0) * direction * leg.quantity * 100
  }, 0)
}

export function summarizeOptionCombination(legs: OptionStrategyLeg[]): OptionCombinationSummary {
  const quotes = legs.map(legQuote)
  const quoteComplete = legs.length > 0 && quotes.every((quote) => quote !== null)
  const netCash = quoteComplete
    ? legs.reduce((total, leg, index) => {
      const direction = leg.side === 'SELL' ? 1 : -1
      return total + direction * (quotes[index] ?? 0) * leg.quantity * 100
    }, 0)
    : null
  const quoteLabel = netCash === null
    ? '报价不完整'
    : netCash > 0.005 ? '净收权利金' : netCash < -0.005 ? '净付权利金' : '权利金持平'

  return {
    label: inferOptionStrategyLabel(legs),
    quoteComplete,
    netCash,
    quoteLabel,
    delta: aggregateGreek(legs, 'delta'),
    gamma: aggregateGreek(legs, 'gamma'),
    theta: aggregateGreek(legs, 'theta'),
    vega: aggregateGreek(legs, 'vega'),
  }
}

function findExact(contracts: OptionContract[], right: OptionContract['option_type'], strike: number) {
  return contracts.find((contract) => contract.option_type === right && contract.strike === strike)
}

function nearestDirectional(
  contracts: OptionContract[],
  right: OptionContract['option_type'],
  strike: number,
  direction: 'lower' | 'higher',
) {
  const candidates = contracts
    .filter((contract) => contract.option_type === right)
    .filter((contract) => direction === 'lower' ? contract.strike < strike : contract.strike > strike)
    .sort((a, b) => Math.abs(a.strike - strike) - Math.abs(b.strike - strike))
  return candidates[0]
}

function strategyLeg(contract: OptionContract, side: OptionLegSide, index: number): OptionStrategyLeg {
  return { id: `${contract.contract_code}-${side}-${index}`, contract, side, quantity: 1 }
}

export function buildOptionTemplate(
  template: OptionTemplateId,
  anchor: OptionContract,
  contracts: OptionContract[],
): OptionStrategyLeg[] {
  const sameExpiryContracts = contracts.filter((contract) => contract.expiry === anchor.expiry)
  if (template === 'long-straddle') {
    const call = findExact(sameExpiryContracts, 'CALL', anchor.strike)
    const put = findExact(sameExpiryContracts, 'PUT', anchor.strike)
    return call && put ? [strategyLeg(call, 'BUY', 0), strategyLeg(put, 'BUY', 1)] : []
  }
  if (template === 'long-strangle') {
    const put = nearestDirectional(sameExpiryContracts, 'PUT', anchor.strike, 'lower')
    const call = nearestDirectional(sameExpiryContracts, 'CALL', anchor.strike, 'higher')
    return call && put ? [strategyLeg(put, 'BUY', 0), strategyLeg(call, 'BUY', 1)] : []
  }
  if (template === 'bull-call-spread') {
    const longCall = anchor.option_type === 'CALL'
      ? anchor
      : findExact(sameExpiryContracts, 'CALL', anchor.strike)
    const shortCall = longCall
      ? nearestDirectional(sameExpiryContracts, 'CALL', longCall.strike, 'higher')
      : undefined
    return longCall && shortCall
      ? [strategyLeg(longCall, 'BUY', 0), strategyLeg(shortCall, 'SELL', 1)]
      : []
  }
  const longPut = anchor.option_type === 'PUT'
    ? anchor
    : findExact(sameExpiryContracts, 'PUT', anchor.strike)
  const shortPut = longPut
    ? nearestDirectional(sameExpiryContracts, 'PUT', longPut.strike, 'lower')
    : undefined
  return longPut && shortPut
    ? [strategyLeg(longPut, 'BUY', 0), strategyLeg(shortPut, 'SELL', 1)]
    : []
}
