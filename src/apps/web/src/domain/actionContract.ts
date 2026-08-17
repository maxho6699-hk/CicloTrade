import type { RecommendationItem } from '../api/client'
import type { Decision } from '../types'

const MAX_ACTION_QUOTE_AGE_MS = 15 * 60 * 1000

export const RECOMMENDATION_FIELD_LABELS: Record<string, string> = {
  action: '建议方向',
  current_price: '当前价',
  reference_price: '参考价',
  quote_at: '报价时间',
  stop_price: '止损价',
  target_price: '目标价',
  max_loss: '最大风险',
  quantity_hint: '建议数量',
  quantity_delta: '调整数量',
  target_quantity: '目标数量',
  rationale: '推荐理由',
  option_expiry: '到期日',
  option_right: '期权方向',
  option_strike: '行权价',
  bid: '买价',
  ask: '卖价',
  implied_volatility: '隐含波动率',
  volume: '成交量',
  open_interest: '未平仓量',
}

export function recommendationFieldLabel(field: string) {
  return RECOMMENDATION_FIELD_LABELS[field] ?? '必要资料'
}

export function recommendationMissingLabels(fields?: string[]) {
  return (fields ?? []).map(recommendationFieldLabel)
}

export interface RecommendationQuoteOverride {
  price: number
  quoteAt?: string | number | null
}

export function assessRecommendationContract(
  item: RecommendationItem,
  quoteOverride?: RecommendationQuoteOverride,
  now = Date.now(),
) {
  const coreComplete = item.contract_status
    ? item.actionable === true
    : Number.isFinite(item.stop_price)
      && Number.isFinite(item.target_price)
      && Number.isFinite(item.max_loss)
      && Number.isFinite(item.quantity_hint)
      && Boolean(item.rationale)
  const price = Number(quoteOverride?.price ?? item.current_price ?? 0)
  const quoteAt = quoteOverride?.quoteAt ?? item.quote_at
  const quoteTime = quoteAt == null
    ? Number.NaN
    : typeof quoteAt === 'number'
      ? quoteAt < 1_000_000_000_000 ? quoteAt * 1000 : quoteAt
      : new Date(quoteAt).valueOf()
  const quoteAge = now - quoteTime
  const freshByAge = Number.isFinite(quoteTime) && quoteAge >= -60_000 && quoteAge <= MAX_ACTION_QUOTE_AGE_MS
  const quoteFresh = freshByAge && Number.isFinite(price) && price > 0
  const quantity = Math.abs(Number(item.quantity_hint ?? item.quantity_delta ?? 0))
  const actionable = coreComplete && quoteFresh && quantity > 0
  const quoteFreshness: Decision['quoteFreshness'] = quoteFresh ? 'fresh' : quoteAt ? 'stale' : 'missing'
  const blockReason = !coreComplete
    ? `行动合同不完整：${recommendationMissingLabels(item.missing_fields).join('、') || '止损价、目标价、建议数量或推荐理由'}`
    : !quoteAt
      ? '没有可验证的当前报价时间'
      : !quoteFresh
        ? '报价已过期或不是实时行情'
        : quantity <= 0
          ? '没有可执行数量'
          : ''
  return { actionable, coreComplete, price, quantity, quoteAt, quoteFreshness, blockReason }
}
