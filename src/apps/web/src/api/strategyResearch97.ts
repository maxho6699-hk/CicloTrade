import { authenticatedJsonRequest, BrowserApiError } from './client.ts'

/**
 * Browser projection contract for the expanded 97-symbol research chain.
 *
 * This is deliberately separate from the canonical 13-symbol system-cycle
 * contract. The server must publish only a read projection; no browser call
 * in this module can create an order, send Telegram, or control a strategy.
 */
export type StrategyResearch97State = 'waiting' | 'healthy' | 'stale' | 'degraded'
export type StrategyResearch97Signal = 'long' | 'flat' | 'wait'
export type StrategyResearch97DataState = 'fresh' | 'stale' | 'missing'

export interface StrategyResearch97Authority {
  publication_ceiling: 'shadow'
  research_only: true
  actionable: false
  outbound: false
  execution: false
  official: false
  live: false
}

export interface StrategyResearch97Universe {
  key: string
  version: string
  count: 97
  sha256: string
}

export interface StrategyResearch97Status {
  available: boolean
  state: StrategyResearch97State
  authority: StrategyResearch97Authority
  universe: StrategyResearch97Universe
  last_heartbeat_at: string | null
  last_result_at: string | null
  expires_at: string | null
  coverage_count: number
  no_data_count: number
  spool: { pending: number; claimed: number; retryable: number; delivered: number } | null
}

export interface StrategyResearch97Symbol {
  market: 'US'
  symbol: string
  tier: 'A' | 'C'
  data_state: StrategyResearch97DataState
  signal: StrategyResearch97Signal
  rationale: string | null
  updated_at: string | null
}

export interface StrategyResearch97Evidence {
  universe_sha256: string
  source_snapshot_sha256: string
  code_bundle_sha256: string
  result_sha256: string
}

export interface StrategyResearch97Cycle {
  cycle_id: string
  evaluation_date: string
  evaluated_at: string
  strategy_key: string
  strategy_name: string
  strategy_version: string
  summary: {
    long_count: number
    flat_count: number
    wait_count: number
    no_data_count: number
  }
  symbols: StrategyResearch97Symbol[]
  evidence: StrategyResearch97Evidence
}

export interface StrategyResearch97Latest {
  available: boolean
  authority: StrategyResearch97Authority
  validation_label: string
  cycle: StrategyResearch97Cycle | null
}

export interface StrategyResearch97HistoryItem {
  cycle_id: string
  evaluation_date: string
  evaluated_at: string
  received_at: string
  coverage_count: number
  no_data_count: number
  long_count: number
  flat_count: number
  wait_count: number
}

export interface StrategyResearch97History {
  available: boolean
  authority: StrategyResearch97Authority
  limit: 20
  items: StrategyResearch97HistoryItem[]
}

export interface StrategyResearch97Aggregate {
  status: StrategyResearch97Status
  latest: StrategyResearch97Latest
  history: StrategyResearch97History
}

export interface StrategyResearch97ResourceError {
  status: number
  message: string
}

export type StrategyResearch97Resource<T> =
  | { state: 'ready'; data: T }
  | { state: 'unavailable'; data: T }
  | { state: 'error'; error: StrategyResearch97ResourceError }

export interface StrategyResearch97AggregateLoad {
  phase: 'ready' | 'partial' | 'error'
  status: StrategyResearch97Resource<StrategyResearch97Status>
  latest: StrategyResearch97Resource<StrategyResearch97Latest>
  history: StrategyResearch97Resource<StrategyResearch97History>
  data?: StrategyResearch97Aggregate
  reason?: 'resource_unavailable' | 'resource_error' | 'cross_source_mismatch'
  forbidden: boolean
}

const BASE_PATH = '/api/rewrite/v1/strategy-research/expanded'

export async function fetchStrategyResearch97Status(): Promise<StrategyResearch97Status> {
  const payload = await authenticatedJsonRequest<unknown>(`${BASE_PATH}/status`)
  if (!validStrategyResearch97Status(payload)) throw new BrowserApiError('扩容策略研究状态响应格式无效。', 502)
  return payload
}

export async function fetchStrategyResearch97Latest(): Promise<StrategyResearch97Latest> {
  const payload = await authenticatedJsonRequest<unknown>(`${BASE_PATH}/latest`)
  if (!validStrategyResearch97Latest(payload)) throw new BrowserApiError('扩容策略研究最新周期响应格式无效。', 502)
  return payload
}

export async function fetchStrategyResearch97History(): Promise<StrategyResearch97History> {
  const payload = await authenticatedJsonRequest<unknown>(`${BASE_PATH}/history?limit=20`)
  if (!validStrategyResearch97History(payload)) throw new BrowserApiError('扩容策略研究历史响应格式无效。', 502)
  return payload
}

export function validStrategyResearch97Aggregate(value: unknown): value is StrategyResearch97Aggregate {
  if (!exactKeys(value, ['status', 'latest', 'history'])
    || !validStrategyResearch97Status(value.status)
    || !validStrategyResearch97Latest(value.latest)
    || !validStrategyResearch97History(value.history)
    || !value.status.available || !value.latest.available || !value.history.available
    || value.latest.cycle === null) return false
  const cycle = value.latest.cycle
  const missing = cycle.symbols.filter((item) => item.data_state === 'missing').length
  const covered = cycle.symbols.length - missing
  const signals = cycle.symbols.filter((item) => item.data_state !== 'missing').reduce((counts, item) => {
    counts[item.signal] += 1
    return counts
  }, { long: 0, flat: 0, wait: 0 })
  const tierCounts = cycle.symbols.reduce((counts, item) => {
    counts[item.tier] += 1
    return counts
  }, { A: 0, C: 0 })
  const historyLatest = value.history.items[0]
  if (!historyLatest
    || historyLatest.cycle_id !== cycle.cycle_id
    || historyLatest.evaluation_date !== cycle.evaluation_date
    || historyLatest.evaluated_at !== cycle.evaluated_at
    || historyLatest.coverage_count !== covered
    || historyLatest.no_data_count !== missing
    || historyLatest.long_count !== signals.long
    || historyLatest.flat_count !== signals.flat
    || historyLatest.wait_count !== signals.wait
    || tierCounts.A !== 13
    || tierCounts.C !== 84
    || value.status.last_result_at !== cycle.evaluated_at
    || (value.status.state === 'waiting' && value.status.last_result_at !== null)) return false
  return value.status.universe.sha256 === cycle.evidence.universe_sha256
    && value.status.coverage_count === covered
    && value.status.no_data_count === missing
    && cycle.summary.no_data_count === missing
    && cycle.summary.long_count === signals.long
    && cycle.summary.flat_count === signals.flat
    && cycle.summary.wait_count === signals.wait
}

async function settleResource<T>(load: () => Promise<T>, available: (value: T) => boolean): Promise<StrategyResearch97Resource<T>> {
  try {
    const data = await load()
    return available(data) ? { state: 'ready', data } : { state: 'unavailable', data }
  } catch (error: unknown) {
    if (error instanceof BrowserApiError) return { state: 'error', error: { status: error.status, message: error.message } }
    return { state: 'error', error: { status: 500, message: '研究资源读取失败。' } }
  }
}

export async function fetchStrategyResearch97Aggregate(): Promise<StrategyResearch97AggregateLoad> {
  const [status, latest, history] = await Promise.all([
    settleResource(fetchStrategyResearch97Status, (value) => value.available),
    settleResource(fetchStrategyResearch97Latest, (value) => value.available),
    settleResource(fetchStrategyResearch97History, (value) => value.available),
  ])
  const resources = [status, latest, history]
  const forbidden = resources.every((resource) => resource.state === 'error' && resource.error.status === 403)
  const historyOnlyError = history.state === 'error' && status.state === 'ready' && latest.state === 'ready'
  if (historyOnlyError) return { phase: 'partial', status, latest, history, reason: 'resource_error', forbidden: false }
  if (resources.some((resource) => resource.state === 'error')) return { phase: 'error', status, latest, history, reason: 'resource_error', forbidden }
  if (resources.some((resource) => resource.state === 'unavailable')) return { phase: 'partial', status, latest, history, reason: 'resource_unavailable', forbidden: false }
  if (status.state !== 'ready' || latest.state !== 'ready' || history.state !== 'ready') return { phase: 'error', status, latest, history, reason: 'resource_error', forbidden: false }
  const data = { status: status.data, latest: latest.data, history: history.data }
  if (!validStrategyResearch97Aggregate(data)) return { phase: 'error', status, latest, history, reason: 'cross_source_mismatch', forbidden: false }
  return { phase: 'ready', status, latest, history, data, forbidden: false }
}

function plainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function exactKeys(value: unknown, expected: readonly string[]): value is Record<string, unknown> {
  return plainObject(value) && Object.keys(value).length === expected.length && Object.keys(value).every((key) => expected.includes(key))
}

function text(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function sha256(value: unknown): value is string {
  return typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value)
}

function isoTimestamp(value: unknown): value is string {
  return typeof value === 'string' && !Number.isNaN(Date.parse(value))
}

function isoDate(value: unknown): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`))
}

function count(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0
}

function validAuthority(value: unknown): value is StrategyResearch97Authority {
  return exactKeys(value, ['publication_ceiling', 'research_only', 'actionable', 'outbound', 'execution', 'official', 'live'])
    && value.publication_ceiling === 'shadow'
    && value.research_only === true
    && value.actionable === false
    && value.outbound === false
    && value.execution === false
    && value.official === false
    && value.live === false
}

function validUniverse(value: unknown): value is StrategyResearch97Universe {
  return exactKeys(value, ['key', 'version', 'count', 'sha256']) && text(value.key) && text(value.version) && value.count === 97 && sha256(value.sha256)
}

function validSpool(value: unknown): boolean {
  return value === null || (exactKeys(value, ['pending', 'claimed', 'retryable', 'delivered']) && Object.values(value).every(count))
}

function validStatusCounts(coverage: unknown, noData: unknown): boolean {
  return count(coverage) && count(noData) && coverage + noData === 97
}

export function validStrategyResearch97Status(value: unknown): value is StrategyResearch97Status {
  if (!exactKeys(value, ['available', 'state', 'authority', 'universe', 'last_heartbeat_at', 'last_result_at', 'expires_at', 'coverage_count', 'no_data_count', 'spool'])) return false
  return typeof value.available === 'boolean'
    && ['waiting', 'healthy', 'stale', 'degraded'].includes(value.state as string)
    && validAuthority(value.authority)
    && validUniverse(value.universe)
    && (value.last_heartbeat_at === null || isoTimestamp(value.last_heartbeat_at))
    && (value.last_result_at === null || isoTimestamp(value.last_result_at))
    && (value.expires_at === null || isoTimestamp(value.expires_at))
    && validStatusCounts(value.coverage_count, value.no_data_count)
    && validSpool(value.spool)
}

function validSymbol(value: unknown): value is StrategyResearch97Symbol {
  return exactKeys(value, ['market', 'symbol', 'tier', 'data_state', 'signal', 'rationale', 'updated_at'])
    && value.market === 'US'
    && text(value.symbol)
    && (value.tier === 'A' || value.tier === 'C')
    && ['fresh', 'stale', 'missing'].includes(value.data_state as string)
    && ['long', 'flat', 'wait'].includes(value.signal as string)
    && (value.rationale === null || text(value.rationale))
    && (value.updated_at === null || isoTimestamp(value.updated_at))
    && (value.data_state === 'missing' ? value.signal === 'wait' : true)
}

function validEvidence(value: unknown): value is StrategyResearch97Evidence {
  return exactKeys(value, ['universe_sha256', 'source_snapshot_sha256', 'code_bundle_sha256', 'result_sha256']) && Object.values(value).every(sha256)
}

function validCycle(value: unknown): value is StrategyResearch97Cycle {
  if (!exactKeys(value, ['cycle_id', 'evaluation_date', 'evaluated_at', 'strategy_key', 'strategy_name', 'strategy_version', 'summary', 'symbols', 'evidence'])) return false
  const summary = value.summary
  const symbols = value.symbols
  if (!text(value.cycle_id) || !isoDate(value.evaluation_date) || !isoTimestamp(value.evaluated_at) || !text(value.strategy_key) || !text(value.strategy_name) || !text(value.strategy_version)) return false
  if (!exactKeys(summary, ['long_count', 'flat_count', 'wait_count', 'no_data_count']) || !count(summary.long_count) || !count(summary.flat_count) || !count(summary.wait_count) || !count(summary.no_data_count)) return false
  const summaryTotal = summary.long_count + summary.flat_count + summary.wait_count + summary.no_data_count
  if (summaryTotal !== 97) return false
  if (!Array.isArray(symbols) || symbols.length !== 97 || !symbols.every(validSymbol) || new Set(symbols.map((item) => `${item.market}:${item.symbol}`)).size !== 97) return false
  return validEvidence(value.evidence)
}

export function validStrategyResearch97Latest(value: unknown): value is StrategyResearch97Latest {
  return exactKeys(value, ['available', 'authority', 'validation_label', 'cycle'])
    && typeof value.available === 'boolean'
    && validAuthority(value.authority)
    && text(value.validation_label)
    && (value.cycle === null || validCycle(value.cycle))
}

function validHistoryItem(value: unknown): value is StrategyResearch97HistoryItem {
  if (!exactKeys(value, ['cycle_id', 'evaluation_date', 'evaluated_at', 'received_at', 'coverage_count', 'no_data_count', 'long_count', 'flat_count', 'wait_count'])
    || !text(value.cycle_id) || !isoDate(value.evaluation_date) || !isoTimestamp(value.evaluated_at) || !isoTimestamp(value.received_at)
    || !count(value.coverage_count) || !count(value.no_data_count) || !count(value.long_count) || !count(value.flat_count) || !count(value.wait_count)
    || value.coverage_count + value.no_data_count !== 97) return false
  return value.long_count + value.flat_count + value.wait_count <= value.coverage_count
}

export function validStrategyResearch97History(value: unknown): value is StrategyResearch97History {
  return exactKeys(value, ['available', 'authority', 'limit', 'items'])
    && typeof value.available === 'boolean'
    && validAuthority(value.authority)
    && value.limit === 20
    && Array.isArray(value.items)
    && value.items.length <= 20
    && value.items.every(validHistoryItem)
    && new Set(value.items.map((item) => item.cycle_id)).size === value.items.length
}
