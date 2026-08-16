import { authenticatedJsonRequest, BrowserApiError } from './client.ts'
import type { RecommendationItem } from './client.ts'

const BASE = '/api/rewrite/v1/deliberations'
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/
const SHA256 = /^[0-9a-f]{64}$/
const STATUSES = ['queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled', 'blocked', 'timed_out'] as const
const SEATS = ['market_structure', 'fundamentals', 'news_macro', 'risk'] as const

export type DeliberationStatus = typeof STATUSES[number]
export type DeliberationSeatName = typeof SEATS[number]

export interface DeliberationBinding {
  market: string
  symbol: string
  timeframe: string
  question: string
  source_event_id: string
  source_event_version: number
  source_event_sha256: string
}

export interface DeliberationReadiness extends DeliberationBinding {
  ready: boolean
  status: DeliberationStatus
  missing: DeliberationSeatName[]
  reason?: string
  snapshot_sha256?: string
  evidence_version?: string | null
  research_version?: string | null
}

export interface DeliberationSeat {
  seat: DeliberationSeatName
  status: 'ready' | 'missing'
  support_strength: number | null
  counter_evidence_strength: number | null
  weight_bps: number
  contribution: { support: number | null; counter: number | null }
  coverage: number | null
  source: unknown | null
  citation: unknown | null
  missing: string[]
  invalidated_reason: string | null
}

export interface DeliberationResult extends DeliberationBinding {
  deliberation_public_id: string | null
  task_public_id: string | null
  status: DeliberationStatus
  method_version: string
  evidence_version: string | null
  research_version: string | null
  support_strength: number | null
  counter_evidence_strength: number | null
  coverage: number | null
  missing: DeliberationSeatName[]
  seats: Record<DeliberationSeatName, DeliberationSeat>
  observed_at: string | null
  available_at: string | null
  as_of: string | null
  calculated_at: string | null
  invalidated_reason: string | null
  evidence_snapshot_sha256: string | null
  result_sha256: string | null
}

export class DeliberationApiError extends Error {
  status: number
  constructor(message: string, status = 0) {
    super(message)
    this.name = 'DeliberationApiError'
    this.status = status
  }
}

export type DeliberationErrorKind = 'unauthorized' | 'forbidden' | 'missing' | 'conflict' | 'unavailable' | 'error'

export function classifyDeliberationError(status: number, operation: 'read' | 'write' = 'read'): DeliberationErrorKind {
  if (status === 401) return 'unauthorized'
  if (status === 403) return 'forbidden'
  if (status === 404) return operation === 'read' ? 'missing' : 'unavailable'
  if (status === 409) return 'conflict'
  if (status === 503) return 'unavailable'
  return 'error'
}

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function exact(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return record(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key))
}

function text(value: unknown, max = 512): value is string {
  return typeof value === 'string' && value.length <= max
}

function timestamp(value: unknown): value is string | null {
  return value === null || (typeof value === 'string' && value.length <= 64 && Number.isFinite(Date.parse(value)))
}

function number(value: unknown, min = 0, max = 100): value is number | null {
  return value === null || (typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max)
}

function jsonValue(value: unknown, depth = 0): boolean {
  if (depth > 6 || value === null || typeof value === 'string' || typeof value === 'boolean') return depth <= 6
  if (typeof value === 'number') return Number.isFinite(value)
  if (Array.isArray(value)) return value.length <= 100 && value.every((item) => jsonValue(item, depth + 1))
  return record(value) && Object.entries(value).every(([key, item]) => text(key, 128) && jsonValue(item, depth + 1))
}

function binding(value: unknown): value is DeliberationBinding {
  if (!record(value)) return false
  const version = value.source_event_version
  const digest = value.source_event_sha256
  return ['market', 'symbol', 'timeframe', 'question', 'source_event_id', 'source_event_version', 'source_event_sha256'].every((key) => Object.prototype.hasOwnProperty.call(value, key))
    && [value.market, value.symbol, value.timeframe, value.question, value.source_event_id].every((item) => text(item, 4000))
    && Number.isSafeInteger(version) && Number(version) >= 1
    && typeof digest === 'string' && SHA256.test(digest)
}

function validSeat(value: unknown): value is DeliberationSeat {
  if (!record(value) || !exact(value, ['seat', 'status', 'support_strength', 'counter_evidence_strength', 'weight_bps', 'contribution', 'coverage', 'source', 'citation', 'missing', 'invalidated_reason'])) return false
  const weight = value.weight_bps
  const contribution = value.contribution
  return SEATS.includes(value.seat as DeliberationSeatName) && (value.status === 'ready' || value.status === 'missing')
    && number(value.support_strength) && number(value.counter_evidence_strength) && Number.isSafeInteger(weight) && Number(weight) >= 0
    && record(contribution) && exact(contribution, ['support', 'counter']) && number(contribution.support, 0, 100) && number(contribution.counter, 0, 100)
    && number(value.coverage, 0, 1) && (value.source === null || jsonValue(value.source)) && (value.citation === null || jsonValue(value.citation))
    && Array.isArray(value.missing) && value.missing.length <= 16 && value.missing.every((item) => text(item, 128))
    && (value.invalidated_reason === null || text(value.invalidated_reason, 512))
}

function validReadiness(value: unknown): value is DeliberationReadiness {
  if (!record(value) || !binding(value) || !exact(value, ['market', 'symbol', 'timeframe', 'question', 'source_event_id', 'source_event_version', 'source_event_sha256', 'ready', 'status', 'missing', ...(value.reason !== undefined ? ['reason'] : []), ...(value.snapshot_sha256 !== undefined ? ['snapshot_sha256'] : []), ...(value.evidence_version !== undefined ? ['evidence_version'] : []), ...(value.research_version !== undefined ? ['research_version'] : [])])) return false
  return typeof value.ready === 'boolean' && STATUSES.includes(value.status as DeliberationStatus)
    && Array.isArray(value.missing) && value.missing.every((item) => SEATS.includes(item as DeliberationSeatName))
    && (value.reason === undefined || text(value.reason, 256))
    && (value.snapshot_sha256 === undefined || (typeof value.snapshot_sha256 === 'string' && SHA256.test(value.snapshot_sha256)))
    && (value.evidence_version === undefined || value.evidence_version === null || text(value.evidence_version, 256))
    && (value.research_version === undefined || value.research_version === null || text(value.research_version, 256))
}

function validResult(value: unknown): value is DeliberationResult {
  if (!record(value) || !binding(value)) return false
  const keys = ['market', 'symbol', 'timeframe', 'question', 'source_event_id', 'source_event_version', 'source_event_sha256', 'deliberation_public_id', 'task_public_id', 'status', 'method_version', 'evidence_version', 'research_version', 'support_strength', 'counter_evidence_strength', 'coverage', 'missing', 'seats', 'observed_at', 'available_at', 'as_of', 'calculated_at', 'invalidated_reason', 'evidence_snapshot_sha256', 'result_sha256']
  const seats = value.seats
  const deliberationId = value.deliberation_public_id
  const taskId = value.task_public_id
  if (!exact(value, keys) || (deliberationId !== null && (typeof deliberationId !== 'string' || !SAFE_ID.test(deliberationId))) || (taskId !== null && (typeof taskId !== 'string' || !SAFE_ID.test(taskId))) || !STATUSES.includes(value.status as DeliberationStatus) || !text(value.method_version, 256) || ![value.evidence_version, value.research_version].every((item) => item === null || text(item, 256)) || !number(value.support_strength) || !number(value.counter_evidence_strength) || !number(value.coverage, 0, 1) || !Array.isArray(value.missing) || !value.missing.every((item) => SEATS.includes(item as DeliberationSeatName)) || !record(seats) || Object.keys(seats).length !== SEATS.length || SEATS.some((seat) => !validSeat(seats[seat])) || ![value.observed_at, value.available_at, value.as_of, value.calculated_at].every(timestamp) || (value.invalidated_reason !== null && !text(value.invalidated_reason, 512)) || (value.evidence_snapshot_sha256 !== null && !SHA256.test(String(value.evidence_snapshot_sha256))) || (value.result_sha256 !== null && !SHA256.test(String(value.result_sha256)))) return false
  return true
}

async function browserTransport(path: string, init?: RequestInit) {
  try {
    return await authenticatedJsonRequest<unknown>(path, init)
  } catch (error) {
    if (error instanceof BrowserApiError) throw new DeliberationApiError(error.message.slice(0, 300), error.status)
    throw error
  }
}

export type DeliberationTransport = (path: string, init?: RequestInit) => Promise<unknown>
export interface DeliberationApi {
  readiness: (input: DeliberationBinding, signal?: AbortSignal) => Promise<DeliberationReadiness>
  list: (signal?: AbortSignal) => Promise<DeliberationResult[]>
  get: (id: string, signal?: AbortSignal) => Promise<DeliberationResult>
  create: (input: DeliberationBinding, signal?: AbortSignal) => Promise<DeliberationResult>
  cancel: (id: string, signal?: AbortSignal) => Promise<DeliberationResult>
  retry: (id: string, signal?: AbortSignal) => Promise<DeliberationResult>
}

const SOURCE_MATERIAL_KEYS = [
  'event_id', 'action', 'position_action', 'market', 'symbol',
  'reference_price', 'current_price', 'quote_at', 'stop_price',
  'target_price', 'max_loss', 'rationale', 'strategy_name',
  'strategy_version', 'occurred_at', 'recorded_at', 'available_at',
  'contract_status', 'missing_fields',
] as const

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value)
  if (typeof value === 'number') return Number.isFinite(value) ? JSON.stringify(value) : 'null'
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(',')}]`
  if (record(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`
  return 'null'
}

async function sha256(value: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

/** Build the exact source binding expected by the server-side recommendation ledger. */
export async function deliberationBindingFromRecommendation(item: RecommendationItem, timeframe = '1d', question = '资料审阅'): Promise<DeliberationBinding> {
  if (!Number.isSafeInteger(item.event_id) || item.event_id < 1 || !item.symbol || !item.market) throw new DeliberationApiError('当前股票缺少可核验的量化事件。', 409)
  const material = Object.fromEntries(SOURCE_MATERIAL_KEYS.map((key) => [key, item[key] ?? null]))
  return {
    market: item.market === 'A股' ? 'CN' : item.market === '美股' ? 'US' : item.market,
    symbol: item.symbol,
    timeframe,
    question,
    source_event_id: `qevt_${item.event_id}`,
    source_event_version: 1,
    source_event_sha256: await sha256(canonicalJson(material)),
  }
}

function safeId(value: string, label: string) {
  if (!SAFE_ID.test(value)) throw new DeliberationApiError(`${label} 无效。`, 400)
  return encodeURIComponent(value)
}

function safeBinding(input: DeliberationBinding) {
  if (!binding(input)) throw new DeliberationApiError('审议绑定字段无效。', 400)
}

function decode<T>(value: unknown, valid: (value: unknown) => value is T, message: string): T {
  if (!valid(value)) throw new DeliberationApiError(message, 502)
  return value
}

export function createDeliberationApi(transport: DeliberationTransport = browserTransport): DeliberationApi {
  return {
    async readiness(input, signal) {
      safeBinding(input)
      const query = new URLSearchParams(input as unknown as Record<string, string>)
      return decode(await transport(`${BASE}/readiness?${query}`, { method: 'GET', cache: 'no-store', signal }), validReadiness, '审议 readiness 响应格式无效。')
    },
    async list(signal) {
      const value = await transport(`${BASE}?limit=100`, { method: 'GET', cache: 'no-store', signal })
      if (!record(value) || !exact(value, ['items']) || !Array.isArray(value.items) || value.items.length > 100) throw new DeliberationApiError('审议列表响应格式无效。', 502)
      return value.items.map((item) => decode(item, validResult, '审议结果响应格式无效。'))
    },
    async get(id, signal) {
      return decode(await transport(`${BASE}/${safeId(id, '审议')}`, { method: 'GET', cache: 'no-store', signal }), validResult, '审议结果响应格式无效。')
    },
    async create(input, signal) {
      safeBinding(input)
      return decode(await transport(BASE, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input), signal }), validResult, '审议创建响应格式无效。')
    },
    async cancel(id, signal) {
      return decode(await transport(`${BASE}/${safeId(id, '审议')}/cancel`, { method: 'POST', body: '{}', signal }), validResult, '审议取消响应格式无效。')
    },
    async retry(id, signal) {
      return decode(await transport(`${BASE}/${safeId(id, '审议')}/retry`, { method: 'POST', body: '{}', signal }), validResult, '审议重试响应格式无效。')
    },
  }
}

export const deliberationApi = createDeliberationApi()
export const decodeDeliberationReadiness = (value: unknown) => decode(value, validReadiness, '审议 readiness 响应格式无效。')
export const decodeDeliberationResult = (value: unknown) => decode(value, validResult, '审议结果响应格式无效。')
