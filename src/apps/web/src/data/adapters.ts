import type { RecommendationItem } from '../api/client'
import type { ActionKind, Decision, Market, OrderRecord, Position } from '../types'
import { getFormatLocale, localizeText } from '../i18n/runtime'

const actionMap: Record<string, ActionKind> = { BUY: 'buy', REDUCE: 'reduce', EXIT: 'exit' }

export function recommendationToDecision(
  item: RecommendationItem,
  index = 0,
  formatLocale = getFormatLocale(),
): Decision | null {
  if (!item.symbol || item.state === 'locked') return null
  const action = actionMap[item.action ?? ''] ?? 'wait'
  const reference = item.reference_price == null ? '未记录' : Number(item.reference_price).toFixed(2)
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
    title: `${item.strategy_name} · ${actionLabel(action)}`,
    summary: '来自不可变量化日志的正式行动记录。参考价是事件发生时的记录值，不等于当前可成交价格。',
    entry: reference,
    stop: '该事件未记录',
    target: '该事件未记录',
    maxLoss: '该事件未记录',
    horizon: '历史事件',
    confidence: '正式记录',
    evidence: ['行动、参考价与发生时间已写入不可变量化账本', '策略名称和版本随事件保存'],
    counterEvidence: ['该事件没有绑定当前行情快照', '该事件未记录结构化止损、目标和最大建模亏损'],
    eventId: `QE-${item.event_id || index}`,
    modelVersion: item.strategy_version,
    updatedAt: formatTime(item.occurred_at, formatLocale),
  }
}

export function normalizeMarket(value?: string): Market {
  return value === 'CN' || value === 'A股' ? 'CN' : 'US'
}

export function actionLabel(action: ActionKind) {
  return localizeText({ buy: '买入 / 增持', hold: '继续持有', reduce: '减仓 / 回避', exit: '全部退出', wait: '等待确认' }[action])
}

export function apiPositionToPosition(item: {
  symbol: string
  quantity: number
  average_price: number
  last_trade_price: number
  market_value: number
  unrealized_pnl: number
}): Position {
  const basis = Math.abs(item.quantity * item.average_price)
  return {
    symbol: item.symbol,
    name: '模拟持仓',
    market: /^\d{6}$/.test(item.symbol) ? 'CN' : 'US',
    quantity: item.quantity,
    averagePrice: item.average_price,
    lastPrice: item.last_trade_price,
    marketValue: item.market_value,
    unrealizedPnl: item.unrealized_pnl,
    unrealizedPnlPct: basis ? item.unrealized_pnl / basis * 100 : 0,
    action: item.unrealized_pnl < 0 ? 'wait' : 'hold',
  }
}

export function apiOrderToOrder(item: {
  order_id: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  price: number
  status: string
  created_at: string
}, formatLocale = getFormatLocale()): OrderRecord {
  const status = item.status === 'FILLED' ? 'FILLED' : item.status === 'REJECTED' ? 'REJECTED' : 'PENDING'
  return { id: item.order_id, symbol: item.symbol, side: item.side, quantity: item.quantity, price: item.price, status, mode: 'paper', createdAt: formatTime(item.created_at, formatLocale) }
}

export function formatTime(value?: string | null, formatLocale = getFormatLocale()) {
  if (!value) return '时间未记录'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString(formatLocale, { hour12: false })
}
