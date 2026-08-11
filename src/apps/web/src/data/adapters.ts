import type { RecommendationItem } from '../api/client'
import type { ActionKind, Decision, Market, OrderRecord, Position } from '../types'
import { getFormatLocale, localizeText } from '../i18n/runtime'
import { assessRecommendationContract, type RecommendationQuoteOverride } from '../domain/actionContract'
import { positionReturnPct } from '../domain/portfolioMath'

const actionMap: Record<string, ActionKind> = { BUY: 'buy', REDUCE: 'reduce', EXIT: 'exit', SHORT: 'short', COVER: 'cover' }
export function recommendationToDecision(
  item: RecommendationItem,
  index = 0,
  formatLocale = getFormatLocale(),
  quoteOverride?: RecommendationQuoteOverride,
): Decision | null {
  if (!item.symbol || item.state === 'locked') return null
  const action = item.position_action === 'open_short' || item.position_action === 'add_short' || item.position_action === 'reverse_to_short'
    ? 'short'
    : item.position_action === 'close_short' || item.position_action === 'reduce_short'
      ? 'cover'
      : actionMap[item.action ?? ''] ?? 'wait'
  const contract = assessRecommendationContract(item, quoteOverride)
  const reference = item.reference_price == null ? '未记录' : Number(item.reference_price).toFixed(2)
  const currency = item.currency === 'CNY' ? 'CNY' : 'USD'
  const quantityUnit = item.instrument_type === 'option' ? '张' : '股'
  const currentInstruction = contract.actionable
    ? currentActionInstruction(action)
    : '现在不买、不卖；数量 0'
  return {
    state: 'official',
    action,
    instrument: {
      symbol: item.symbol,
      name: item.instrument_type === 'option' ? '期权量化事件' : '量化日志标的',
      market: normalizeMarket(item.market),
      price: Number(item.reference_price ?? 0),
      changePct: 0,
      currency: item.currency === 'CNY' ? 'CNY' : 'USD',
    },
    title: `${item.strategy_name} · ${contract.actionable ? actionLabel(action) : `原始记录 ${actionLabel(action)}`}`,
    summary: contract.actionable
      ? '行动合同完整，且已取得可验证的当前报价；下单前仍需核对券商权限、流动性与持仓。'
      : `${contract.blockReason}。这条记录当前只供核对，不能作为立即交易指令。`,
    entry: `事件参考 ${currency} ${reference}`,
    stop: item.stop_price == null ? '未记录' : Number(item.stop_price).toFixed(2),
    target: item.target_price == null ? '未记录' : Number(item.target_price).toFixed(2),
    maxLoss: item.max_loss == null ? '未记录' : `${item.currency === 'CNY' ? 'CNY' : 'USD'} ${Number(item.max_loss).toFixed(2)}`,
    horizon: '历史事件',
    confidence: '正式记录',
    evidence: contract.coreComplete ? ['行动、参考价与发生时间已写入不可变量化账本', '策略名称和版本随事件保存', item.rationale ?? ''] : ['行动、参考价与发生时间已写入不可变量化账本', '这条记录尚未形成完整行动合同'],
    counterEvidence: contract.actionable
      ? ['当前报价仍可能在下单前变化', '仍需核对券商权限、流动性和当前持仓']
      : [contract.blockReason, '未满足全部条件前，数量固定为 0'],
    eventId: `QE-${item.event_id || index}`,
    officialEventId: item.event_id,
    modelVersion: item.strategy_version,
    updatedAt: formatTime(item.occurred_at, formatLocale),
    actionable: contract.actionable,
    currentInstruction,
    currentPrice: contract.quoteFreshness === 'fresh' ? `${currency} ${contract.price.toFixed(2)}` : '当前报价未核对',
    quantityHint: contract.actionable
      ? `${Number.isInteger(contract.quantity) ? contract.quantity.toFixed(0) : contract.quantity.toFixed(2)} ${quantityUnit}`
      : `0 ${quantityUnit}（现在不买、不卖）`,
    quoteUpdatedAt: contract.quoteFreshness === 'fresh' ? formatTime(contract.quoteAt, formatLocale) : contract.blockReason,
    quoteFreshness: contract.quoteFreshness,
    actionBlockReason: contract.blockReason || undefined,
    plainLanguage: {
      reason: item.rationale || '正式记录没有提供可供零基础用户理解的白话理由。',
      setup: `事件发生于 ${formatTime(item.occurred_at, formatLocale)}`,
      takeProfit: item.target_price == null ? '目标未记录，不能猜测。' : `${currency} ${Number(item.target_price).toFixed(2)}`,
      quantityHint: contract.actionable
        ? `${Number.isInteger(contract.quantity) ? contract.quantity.toFixed(0) : contract.quantity.toFixed(2)} ${quantityUnit}`
        : `现在不买、不卖；数量 0 ${quantityUnit}`,
    },
  }
}

export function normalizeMarket(value?: string): Market {
  return value === 'CN' || value === 'A股' ? 'CN' : 'US'
}

export function actionLabel(action: ActionKind) {
  return localizeText({ buy: '买入 / 增持', hold: '继续持有', reduce: '减仓 / 回避', exit: '全部退出', short: '卖出做空', cover: '买入平空', wait: '等待确认' }[action])
}

function currentActionInstruction(action: ActionKind) {
  return localizeText({
    buy: '现在可以按数量分批买入',
    hold: '继续持有，不新增数量',
    reduce: '只处理已有多头：按数量减仓',
    exit: '只处理已有持仓：全部退出',
    short: '现在可以按数量卖出做空',
    cover: '只处理已有空头：按数量买入平空',
    wait: '现在不买、不卖；数量 0',
  }[action])
}

export function apiPositionToPosition(item: {
  symbol: string
  market: 'US' | 'CN'
  instrument_type: 'stock' | 'option'
  option_expiry?: string | null
  option_right?: 'CALL' | 'PUT' | null
  option_strike?: number | null
  multiplier: number
  quantity: number
  average_price: number
  last_trade_price: number
  market_value: number
  unrealized_pnl: number
}): Position {
  return {
    symbol: item.symbol,
    name: item.instrument_type === 'option'
      ? `${item.option_expiry ?? '到期日未记录'} ${item.option_right ?? ''} ${item.option_strike ?? ''}`.trim()
      : '官方验证持仓',
    market: item.market,
    instrumentType: item.instrument_type,
    quantity: item.quantity,
    averagePrice: item.average_price,
    lastPrice: item.last_trade_price,
    marketValue: item.market_value,
    unrealizedPnl: item.unrealized_pnl,
    unrealizedPnlPct: positionReturnPct(item.quantity, item.average_price, item.multiplier, item.unrealized_pnl),
    action: item.quantity < 0 ? 'short' : item.unrealized_pnl < 0 ? 'wait' : 'hold',
  }
}

export function apiOrderToOrder(item: {
  order_id: string
  symbol: string
  market: 'US' | 'CN'
  instrument_type: 'stock' | 'option'
  side: 'BUY' | 'SELL'
  quantity: number
  price: number
  status: string
  created_at: string
}, formatLocale = getFormatLocale()): OrderRecord {
  const status = item.status === 'FILLED' || item.status === 'VERIFIED' ? 'FILLED' : item.status === 'REJECTED' ? 'REJECTED' : 'PENDING'
  return { id: item.order_id, symbol: item.symbol, market: item.market, instrumentType: item.instrument_type, side: item.side, quantity: item.quantity, price: item.price, status, mode: 'official', createdAt: formatTime(item.created_at, formatLocale) }
}

export function formatTime(value?: string | number | null, formatLocale = getFormatLocale()): string {
  if (!value) return '时间未记录'
  const normalized = typeof value === 'number' && value < 1_000_000_000_000 ? value * 1000 : value
  const date = new Date(normalized)
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString(formatLocale, { hour12: false })
}
