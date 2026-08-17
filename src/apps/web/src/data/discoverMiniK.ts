export type DiscoverMiniPeriod = '1D' | '1W' | '1M'

export const DISCOVER_MINI_PERIODS = [
  { key: '1D', timeframe: '日线' },
  { key: '1W', timeframe: '周线' },
  { key: '1M', timeframe: '月线' },
] as const satisfies ReadonlyArray<{ key: DiscoverMiniPeriod; timeframe: string }>

export function normalizeDiscoverMiniPeriod(value: string | null | undefined): DiscoverMiniPeriod {
  return value === '1W' || value === '1M' ? value : '1D'
}

export function timeframeForDiscoverMiniPeriod(period: DiscoverMiniPeriod) {
  return DISCOVER_MINI_PERIODS.find((item) => item.key === period)?.timeframe ?? '日线'
}

export function discoverMiniCacheKey(symbol: string, period: DiscoverMiniPeriod) {
  return `${symbol.trim().toUpperCase()}::${timeframeForDiscoverMiniPeriod(period)}`
}
