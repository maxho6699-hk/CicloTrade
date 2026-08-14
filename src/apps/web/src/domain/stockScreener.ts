export type ScreenerMarket = 'US' | 'CN'
export type ScreenerTrend = 'rising' | 'neutral' | 'falling'
export type ScreenerRisk = 'low' | 'medium' | 'high'
export type ScreenerCap = 'large' | 'mid' | 'small'
export type ScreenerServiceState = 'loading' | 'ready' | 'empty' | 'error' | 'delayed' | 'locked' | 'degraded'
export type ScreenerSort = 'score-desc' | 'symbol-asc' | 'risk-asc'

export interface StockScreenerRow {
  symbol: string
  name: string
  market: ScreenerMarket
  trend: ScreenerTrend
  risk: ScreenerRisk
  marketCap: ScreenerCap
  score: number
}

export interface StockScreenerPayload {
  state: ScreenerServiceState
  source: 'connected'
  dataAsOf: string | null
  rows: StockScreenerRow[]
}

export interface StockScreenerFilters {
  market: ScreenerMarket | 'all'
  trend: ScreenerTrend | 'all'
  risk: ScreenerRisk | 'all'
  marketCap: ScreenerCap | 'all'
}

export const DEFAULT_SCREENER_FILTERS: StockScreenerFilters = {
  market: 'all', trend: 'rising', risk: 'all', marketCap: 'all',
}

export const SCREENER_PRESETS: Array<{ id: 'trend' | 'stable' | 'value'; filters: StockScreenerFilters }> = [
  { id: 'trend', filters: DEFAULT_SCREENER_FILTERS },
  { id: 'stable', filters: { market: 'all', trend: 'rising', risk: 'low', marketCap: 'large' } },
  { id: 'value', filters: { market: 'CN', trend: 'neutral', risk: 'medium', marketCap: 'mid' } },
]

const SERVICE_STATES = new Set<ScreenerServiceState>(['loading', 'ready', 'empty', 'error', 'delayed', 'locked', 'degraded'])
const MARKETS = new Set<ScreenerMarket>(['US', 'CN'])
const TRENDS = new Set<ScreenerTrend>(['rising', 'neutral', 'falling'])
const RISKS = new Set<ScreenerRisk>(['low', 'medium', 'high'])
const CAPS = new Set<ScreenerCap>(['large', 'mid', 'small'])

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]) {
  return Object.keys(value).length === keys.length && keys.every((key) => key in value)
}

function decodeRow(value: unknown): StockScreenerRow | null {
  const row = object(value)
  if (!row || !exactKeys(row, ['symbol', 'name', 'market', 'trend', 'risk', 'marketCap', 'score'])) return null
  if (typeof row.symbol !== 'string' || !/^(?:[A-Z][A-Z0-9.-]{0,15}|\d{6})$/.test(row.symbol)) return null
  if (typeof row.name !== 'string' || !row.name.trim() || row.name.length > 120) return null
  if (!MARKETS.has(row.market as ScreenerMarket) || !TRENDS.has(row.trend as ScreenerTrend) || !RISKS.has(row.risk as ScreenerRisk) || !CAPS.has(row.marketCap as ScreenerCap)) return null
  if (typeof row.score !== 'number' || !Number.isFinite(row.score) || row.score < 0 || row.score > 100) return null
  return { symbol: row.symbol, name: row.name, market: row.market as ScreenerMarket, trend: row.trend as ScreenerTrend, risk: row.risk as ScreenerRisk, marketCap: row.marketCap as ScreenerCap, score: row.score }
}

/** Rejects unrecognised payloads so an unverified feed cannot be presented as research data. */
export function decodeStockScreenerPayload(input: unknown): StockScreenerPayload | null {
  const payload = object(input)
  if (!payload || !exactKeys(payload, ['state', 'source', 'dataAsOf', 'rows'])) return null
  if (!SERVICE_STATES.has(payload.state as ScreenerServiceState) || payload.source !== 'connected' || !Array.isArray(payload.rows)) return null
  if (payload.dataAsOf !== null && (typeof payload.dataAsOf !== 'string' || Number.isNaN(Date.parse(payload.dataAsOf)))) return null
  const rows = payload.rows.map(decodeRow)
  if (rows.some((row) => row === null)) return null
  if (payload.state === 'ready' && (payload.dataAsOf === null || rows.length === 0)) return null
  return { state: payload.state as ScreenerServiceState, source: 'connected', dataAsOf: payload.dataAsOf as string | null, rows: rows as StockScreenerRow[] }
}

export function filterAndSortScreenerRows(rows: StockScreenerRow[], filters: StockScreenerFilters, sort: ScreenerSort) {
  const visible = rows.filter((row) => (
    (filters.market === 'all' || row.market === filters.market)
    && (filters.trend === 'all' || row.trend === filters.trend)
    && (filters.risk === 'all' || row.risk === filters.risk)
    && (filters.marketCap === 'all' || row.marketCap === filters.marketCap)
  ))
  return visible.sort((left, right) => sort === 'symbol-asc'
    ? left.symbol.localeCompare(right.symbol)
    : sort === 'risk-asc'
      ? RISKS_ORDER[left.risk] - RISKS_ORDER[right.risk] || right.score - left.score
      : right.score - left.score || left.symbol.localeCompare(right.symbol))
}

const RISKS_ORDER: Record<ScreenerRisk, number> = { low: 0, medium: 1, high: 2 }

export function pagedScreenerRows(rows: StockScreenerRow[], page: number, pageSize = 8) {
  const safePage = Math.max(0, Math.min(page, Math.max(0, Math.ceil(rows.length / pageSize) - 1)))
  return { page: safePage, pageCount: Math.max(1, Math.ceil(rows.length / pageSize)), rows: rows.slice(safePage * pageSize, (safePage + 1) * pageSize) }
}

function params(row: StockScreenerRow, extras: Record<string, string> = {}) {
  const search = new URLSearchParams({ market: row.market, symbol: row.symbol, ...extras })
  return search.toString()
}

export function researchUrl(row: StockScreenerRow) { return `/research?${params(row)}` }
export function alertDraftUrl(row: StockScreenerRow) { return `/notifications?${params(row, { draft: 'alert' })}` }
export function personalPaperPrefillUrl(row: StockScreenerRow) { return `/paper?${params(row, { side: 'BUY' })}` }
