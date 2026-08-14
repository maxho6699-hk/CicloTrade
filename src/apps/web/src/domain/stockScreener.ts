export type ScreenerCandidateState = 'official' | 'research'
export type ScreenerAction = 'buy' | 'short' | 'wait' | 'hold' | 'reduce' | 'exit'
export type ScreenerDataState = 'fresh' | 'delayed' | 'stale' | 'missing'
export type ScreenerHealth = 'healthy' | 'degraded' | 'unavailable'
export type ScreenerSortField = 'score' | 'symbol' | 'price' | 'change_pct' | 'updated_at'
export type ScreenerSort = { field: ScreenerSortField; direction: 'asc' | 'desc' }

export interface ScreenerFilters {
  min_score?: number; max_score?: number; min_price?: number; max_price?: number
  actions?: ScreenerAction[]; data_states?: ScreenerDataState[]; states?: ScreenerCandidateState[]; symbols?: string[]
}

export interface ScreenerPrefill { market: 'US'; symbol: string }
export interface ScreenerPaperPrefill { market: 'US'; symbol: string; side: 'BUY' }

export interface StockScreenerRow {
  symbol: string; name: string; state: ScreenerCandidateState; action: ScreenerAction; score: number | null; price: number; change_pct: number
  reasons: string[]; counter_evidence: string[]; risk: string; invalidation: string; data_state: ScreenerDataState; health: ScreenerHealth
  updated_at: string; hong_kong_time: string; research_url: string; alert_prefill: ScreenerPrefill; paper_prefill: ScreenerPaperPrefill | null
  blocked_reason: string | null; actionable: boolean
}

export interface StockScreenerPayload {
  schema_version: 1; preset: 'all' | 'momentum' | 'pullback' | 'risk_first'; filters: ScreenerFilters; sort: ScreenerSort
  page: number; page_size: number; total: number; items: StockScreenerRow[]
}

export interface StockScreenerPreset { schema_version: 1; version: number; name: string; filters: ScreenerFilters; sort: ScreenerSort }
export type ScreenerViewState = 'pending' | 'success' | 'empty' | 'stale' | 'offline' | 'unknown'

export const SCREENER_DRAFT_KEY = 'ciclotrade.stock-screener.draft.v1'
const actions = new Set<ScreenerAction>(['buy', 'short', 'wait', 'hold', 'reduce', 'exit'])
const dataStates = new Set<ScreenerDataState>(['fresh', 'delayed', 'stale', 'missing'])
const healthStates = new Set<ScreenerHealth>(['healthy', 'degraded', 'unavailable'])
const candidateStates = new Set<ScreenerCandidateState>(['official', 'research'])
const sortFields = new Set<ScreenerSortField>(['score', 'symbol', 'price', 'change_pct', 'updated_at'])
const presets = new Set<StockScreenerPayload['preset']>(['all', 'momentum', 'pullback', 'risk_first'])
const symbolPattern = /^[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,5})?$/

function object(value: unknown): Record<string, unknown> | null { return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null }
function exact(value: Record<string, unknown>, keys: readonly string[]) { return Object.keys(value).length === keys.length && keys.every((key) => key in value) }
function finite(value: unknown, minimum: number, maximum: number): value is number { return typeof value === 'number' && Number.isFinite(value) && value >= minimum && value <= maximum }
function text(value: unknown, maximum = 240): value is string { return typeof value === 'string' && value.trim().length > 0 && value.length <= maximum }
function listOfText(value: unknown, maximum = 8): value is string[] { return Array.isArray(value) && value.length <= maximum && value.every((item) => text(item)) && new Set(value).size === value.length }
function timestamp(value: unknown): value is string { return text(value, 40) && !Number.isNaN(Date.parse(value)) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) }
function hongKongTimestamp(value: unknown): value is string { return timestamp(value) && value.endsWith('+08:00') }

function decodeFilters(value: unknown): ScreenerFilters | null {
  const filters = object(value)
  if (!filters || Object.keys(filters).some((key) => !['min_score', 'max_score', 'min_price', 'max_price', 'actions', 'data_states', 'states', 'symbols'].includes(key))) return null
  for (const key of ['min_score', 'max_score'] as const) if (key in filters && !finite(filters[key], 0, 100)) return null
  for (const key of ['min_price', 'max_price'] as const) if (key in filters && !finite(filters[key], Number.MIN_VALUE, 10_000_000)) return null
  if (typeof filters.min_score === 'number' && typeof filters.max_score === 'number' && filters.min_score > filters.max_score) return null
  if (typeof filters.min_price === 'number' && typeof filters.max_price === 'number' && filters.min_price > filters.max_price) return null
  if ('actions' in filters && (!Array.isArray(filters.actions) || filters.actions.length > 40 || !filters.actions.every((item) => actions.has(item as ScreenerAction)) || new Set(filters.actions).size !== filters.actions.length)) return null
  if ('data_states' in filters && (!Array.isArray(filters.data_states) || filters.data_states.length > 40 || !filters.data_states.every((item) => dataStates.has(item as ScreenerDataState)) || new Set(filters.data_states).size !== filters.data_states.length)) return null
  if ('states' in filters && (!Array.isArray(filters.states) || filters.states.length > 40 || !filters.states.every((item) => candidateStates.has(item as ScreenerCandidateState)) || new Set(filters.states).size !== filters.states.length)) return null
  if ('symbols' in filters && (!Array.isArray(filters.symbols) || filters.symbols.length > 40 || !filters.symbols.every((item) => typeof item === 'string' && symbolPattern.test(item)) || new Set(filters.symbols).size !== filters.symbols.length)) return null
  return filters as ScreenerFilters
}

function decodeSort(value: unknown): ScreenerSort | null {
  const sort = object(value)
  return sort && exact(sort, ['field', 'direction']) && sortFields.has(sort.field as ScreenerSortField) && (sort.direction === 'asc' || sort.direction === 'desc') ? sort as ScreenerSort : null
}

function decodePrefill(value: unknown, symbol: string, paper = false): ScreenerPrefill | ScreenerPaperPrefill | null {
  const prefill = object(value), keys = paper ? ['market', 'symbol', 'side'] : ['market', 'symbol']
  if (!prefill || !exact(prefill, keys) || prefill.market !== 'US' || prefill.symbol !== symbol || (paper && prefill.side !== 'BUY')) return null
  return prefill as unknown as ScreenerPrefill | ScreenerPaperPrefill
}

function safeResearchUrl(value: unknown, symbol: string): value is string {
  if (!text(value, 200) || !value.startsWith('/')) return false
  const url = new URL(value, 'https://ciclotrade.invalid')
  return url.origin === 'https://ciclotrade.invalid' && url.pathname === '/discover' && url.searchParams.get('tool') === 'screener' && url.searchParams.get('symbol') === symbol && [...url.searchParams.keys()].every((key) => key === 'tool' || key === 'symbol')
}

function decodeRow(value: unknown): StockScreenerRow | null {
  const row = object(value)
  const keys = ['symbol', 'name', 'state', 'action', 'score', 'price', 'change_pct', 'reasons', 'counter_evidence', 'risk', 'invalidation', 'data_state', 'health', 'updated_at', 'hong_kong_time', 'research_url', 'alert_prefill', 'paper_prefill', 'blocked_reason', 'actionable']
  if (!row || !exact(row, keys) || !text(row.symbol, 16) || !symbolPattern.test(row.symbol) || !text(row.name, 120) || !candidateStates.has(row.state as ScreenerCandidateState) || !actions.has(row.action as ScreenerAction) || (row.score !== null && !finite(row.score, 0, 100)) || !finite(row.price, Number.MIN_VALUE, 10_000_000) || !finite(row.change_pct, -1_000, 1_000) || !listOfText(row.reasons) || !listOfText(row.counter_evidence) || !text(row.risk) || !text(row.invalidation) || !dataStates.has(row.data_state as ScreenerDataState) || !healthStates.has(row.health as ScreenerHealth) || !timestamp(row.updated_at) || !hongKongTimestamp(row.hong_kong_time) || !safeResearchUrl(row.research_url, row.symbol)) return null
  const alert = decodePrefill(row.alert_prefill, row.symbol), paper = row.paper_prefill === null ? null : decodePrefill(row.paper_prefill, row.symbol, true)
  if (!alert || (row.paper_prefill !== null && !paper) || typeof row.actionable !== 'boolean' || (row.blocked_reason !== null && !text(row.blocked_reason, 120)) || row.actionable !== (paper !== null) || row.actionable !== (row.blocked_reason === null)) return null
  return { ...row, symbol: row.symbol as string, name: row.name as string, state: row.state as ScreenerCandidateState, action: row.action as ScreenerAction, score: row.score as number | null, price: row.price as number, change_pct: row.change_pct as number, reasons: row.reasons as string[], counter_evidence: row.counter_evidence as string[], risk: row.risk as string, invalidation: row.invalidation as string, data_state: row.data_state as ScreenerDataState, health: row.health as ScreenerHealth, updated_at: row.updated_at as string, hong_kong_time: row.hong_kong_time as string, research_url: row.research_url as string, alert_prefill: alert as ScreenerPrefill, paper_prefill: paper as ScreenerPaperPrefill | null, blocked_reason: row.blocked_reason as string | null, actionable: row.actionable }
}

/** Reject unsafe, stale-shape, duplicate, or internally inconsistent server data. */
export function decodeStockScreenerPayload(value: unknown): StockScreenerPayload | null {
  const payload = object(value)
  if (!payload || !exact(payload, ['schema_version', 'preset', 'filters', 'sort', 'page', 'page_size', 'total', 'items']) || payload.schema_version !== 1 || !presets.has(payload.preset as StockScreenerPayload['preset']) || !Array.isArray(payload.items) || !finite(payload.page, 1, 1_000) || !Number.isInteger(payload.page) || !finite(payload.page_size, 1, 100) || !Number.isInteger(payload.page_size) || !finite(payload.total, 0, 500) || !Number.isInteger(payload.total) || payload.items.length > payload.page_size) return null
  const filters = decodeFilters(payload.filters), sort = decodeSort(payload.sort), items = payload.items.map(decodeRow)
  if (!filters || !sort || items.some((item) => item === null) || new Set(items.map((item) => item!.symbol)).size !== items.length || (payload.total === 0 && items.length !== 0) || (payload.total > 0 && payload.page > Math.ceil(payload.total / payload.page_size))) return null
  return { schema_version: 1, preset: payload.preset as StockScreenerPayload['preset'], filters, sort, page: payload.page, page_size: payload.page_size, total: payload.total, items: items as StockScreenerRow[] }
}

export function decodeStockScreenerPreset(value: unknown): StockScreenerPreset | null {
  const preset = object(value)
  if (!preset || !exact(preset, ['schema_version', 'version', 'name', 'filters', 'sort']) || preset.schema_version !== 1 || !finite(preset.version, 0, Number.MAX_SAFE_INTEGER) || !Number.isInteger(preset.version) || !text(preset.name, 80)) return null
  const filters = decodeFilters(preset.filters), sort = decodeSort(preset.sort)
  return filters && sort ? { schema_version: 1, version: preset.version, name: preset.name, filters, sort } : null
}

export function decodeStockScreenerDraft(value: string | null): StockScreenerPreset | null { try { return value ? decodeStockScreenerPreset(JSON.parse(value)) : null } catch { return null } }

export function screenerViewState(payload: StockScreenerPayload | null, loading = false): ScreenerViewState {
  if (loading) return 'pending'
  if (!payload) return 'unknown'
  if (payload.total === 0) return 'empty'
  if (payload.items.some((item) => item.health === 'unavailable')) return 'offline'
  return payload.items.some((item) => item.data_state !== 'fresh' || item.health !== 'healthy') ? 'stale' : 'success'
}

export function alertPrefillUrl(prefill: ScreenerPrefill) { return `/notifications?${new URLSearchParams({ market: prefill.market, symbol: prefill.symbol, draft: 'alert' })}` }
export function paperPrefillUrl(prefill: ScreenerPaperPrefill) { return `/paper?${new URLSearchParams({ market: prefill.market, symbol: prefill.symbol, side: prefill.side })}` }
