import type { RecommendationItem } from '../api/client'
import type { Decision } from '../types'

const MAX_ACTION_QUOTE_AGE_MS = 15 * 60 * 1000

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
    ? `行动合同不完整：${(item.missing_fields ?? ['止损、目标、数量或理由']).join('、')}`
    : !quoteAt
      ? '没有可验证的当前报价时间'
      : !quoteFresh
        ? '报价已过期或不是实时行情'
        : quantity <= 0
          ? '没有可执行数量'
          : ''
  return { actionable, coreComplete, price, quantity, quoteAt, quoteFreshness, blockReason }
}
