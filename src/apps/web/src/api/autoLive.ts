import { authenticatedJsonRequest } from './client.ts'

export type AutoLiveBrokerProvider = 'futu_moomoo' | 'tiger' | 'ibkr' | 'webull' | 'longbridge'
export type AutoLiveMandateState = 'draft' | 'pending_confirmation' | 'active' | 'paused' | 'blocked' | 'expired' | 'revoked'
export type AutoLivePauseScope = 'aggregate' | 'broker' | 'mandate'
const RUNTIME_STATES = ['stopped', 'starting', 'running', 'pausing', 'paused', 'blocked', 'unknown'] as const
const HEARTBEAT_STATES = ['fresh', 'stale', 'missing', 'unknown'] as const
const SUBMISSION_STATES = ['accepted', 'rejected', 'submission_unknown', 'cancelled'] as const
const TARGET_TYPES = ['aggregate', 'broker', 'mandate'] as const
const RECEIPT_STATUSES = ['pausing', 'paused', 'partial', 'failed', 'starting', 'blocked'] as const
const RECEIPT_STATES = ['active', 'blocked', 'paused', 'expired', 'revoked'] as const

export interface AutoLiveBrokerAccount {
  public_id: string
  provider: AutoLiveBrokerProvider
  status: string
}

export interface AutoLiveMandate {
  public_id: string
  broker_account_public_id: string | null
  strategy_version: string
  risk_version: string
  capital_limit_minor: number
  frequency_limit: number
  valid_from: string
  valid_until: string
  state: AutoLiveMandateState
  can_reduce_exposure: true
  snapshot_sha256: string
  confirmed_at: string | null
  created_at: string
  updated_at: string
}
export type AutoLiveActionMandate = Omit<AutoLiveMandate, 'broker_account_public_id'>

export interface AutoLiveGate { name: string; ok: boolean; reason: string }
export interface AutoLiveRuntimeProjection {
  mandate_public_id: string
  state: typeof RUNTIME_STATES[number]
  can_reduce_exposure: boolean | 0 | 1
  last_error_code: string | null
  observed_at: string
}
export interface AutoLiveHeartbeatProjection {
  mandate_public_id: string
  heartbeat_state: typeof HEARTBEAT_STATES[number]
  heartbeat_at: string | null
  observed_at: string
}
export interface AutoLiveOrderReceipt {
  public_id: string
  mandate_public_id: string
  client_order_id: string
  submission_state: typeof SUBMISSION_STATES[number]
  observed_at: string
  receipt_sha256: string
}

export interface AutoLiveSafeReceipt {
  public_id: string
  mandate_public_id?: string
  status?: typeof RECEIPT_STATUSES[number]
  state?: typeof RECEIPT_STATES[number]
  runtime_state?: typeof RUNTIME_STATES[number]
  fencing_epoch?: number
  created_at: string
  idempotency_key_sha256?: string
  request_fingerprint?: string
  scope?: AutoLivePauseScope
  confirmed?: number
  total?: number
  unconfirmed?: string[]
  target_details?: Array<{ target_type: typeof TARGET_TYPES[number]; target_public_id: string; confirmed: boolean; status: 'failed' | 'paused'; fencing_epoch: number; detail: string | null; created_at: string }>
  can_reduce_exposure?: boolean
  all_ok?: boolean
  gates?: AutoLiveGate[]
  actor?: 'owner'
}

export interface AutoLivePauseResult {
  public_id: string
  scope: AutoLivePauseScope
  status: 'pausing' | 'paused' | 'partial' | 'failed'
  confirmed: number
  total: number
  unconfirmed: string[]
  can_reduce_exposure: true
  receipt_sha256: string
  created_at: string
  updated_at: string
}

export interface AutoLiveSnapshot {
  snapshot_at: string
  broker_accounts: AutoLiveBrokerAccount[]
  mandates: AutoLiveMandate[]
  runtime_projections: AutoLiveRuntimeProjection[]
  heartbeat_projections: AutoLiveHeartbeatProjection[]
  pause_receipts: Array<{ public_id: string; status: string; receipt: AutoLiveSafeReceipt; receipt_sha256: string; created_at: string }>
  start_receipts: Array<AutoLiveSafeReceipt & { receipt_sha256: string }>
  order_receipts: AutoLiveOrderReceipt[]
}

export interface AutoLiveCreateMandateInput {
  broker_account_public_id: string
  strategy_version: string
  risk_version: string
  capital_limit_minor: number
  frequency_limit: number
  valid_from: string
  valid_until: string
}
export interface AutoLiveConfirmationInput {
  mandate_public_id: string
  snapshot_sha256: string
  confirmation_phrase: string
}
export interface AutoLiveStartInput { expected_fencing_epoch: number }
export type AutoLivePauseInput =
  | { scope: 'aggregate' }
  | { scope: 'broker'; broker_account_public_id: string }
  | { scope: 'mandate'; mandate_public_id: string }

function record(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === 'object' && !Array.isArray(value) }
export function exact(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return record(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key))
}
function timestamp(value: unknown): value is string { return typeof value === 'string' && Number.isFinite(Date.parse(value)) }
function id(value: unknown): value is string { return typeof value === 'string' && /^[A-Za-z0-9._:-]{8,128}$/.test(value) }
function hash(value: unknown): value is string { return typeof value === 'string' && /^[a-f0-9]{64}$/.test(value) }
function provider(value: unknown): value is AutoLiveBrokerProvider { return ['futu_moomoo', 'tiger', 'ibkr', 'webull', 'longbridge'].includes(String(value)) }
function gate(value: unknown): value is AutoLiveGate { return exact(value, ['name', 'ok', 'reason']) && typeof value.name === 'string' && typeof value.ok === 'boolean' && typeof value.reason === 'string' }

export function validAutoLiveMandate(value: unknown): value is AutoLiveMandate {
  if (!exact(value, ['public_id', 'broker_account_public_id', 'strategy_version', 'risk_version', 'capital_limit_minor', 'frequency_limit', 'valid_from', 'valid_until', 'state', 'can_reduce_exposure', 'snapshot_sha256', 'confirmed_at', 'created_at', 'updated_at'])) return false
  const item = value as Record<string, unknown>
  return id(item.public_id) && (item.broker_account_public_id === null || id(item.broker_account_public_id))
    && typeof item.strategy_version === 'string' && typeof item.risk_version === 'string'
    && typeof item.capital_limit_minor === 'number' && Number.isSafeInteger(item.capital_limit_minor) && item.capital_limit_minor > 0
    && typeof item.frequency_limit === 'number' && Number.isSafeInteger(item.frequency_limit) && item.frequency_limit > 0
    && timestamp(item.valid_from) && timestamp(item.valid_until) && item.valid_until > item.valid_from
    && ['draft', 'pending_confirmation', 'active', 'paused', 'blocked', 'expired', 'revoked'].includes(String(item.state))
    && item.can_reduce_exposure === true && hash(item.snapshot_sha256)
    && (item.confirmed_at === null || timestamp(item.confirmed_at)) && timestamp(item.created_at) && timestamp(item.updated_at)
}

export function validAutoLiveActionMandate(value: unknown): value is AutoLiveActionMandate {
  if (!exact(value, ['public_id', 'strategy_version', 'risk_version', 'capital_limit_minor', 'frequency_limit', 'valid_from', 'valid_until', 'state', 'can_reduce_exposure', 'snapshot_sha256', 'confirmed_at', 'created_at', 'updated_at'])) return false
  const item = value as Record<string, unknown>
  return id(item.public_id) && typeof item.strategy_version === 'string' && typeof item.risk_version === 'string'
    && typeof item.capital_limit_minor === 'number' && Number.isSafeInteger(item.capital_limit_minor) && item.capital_limit_minor > 0
    && typeof item.frequency_limit === 'number' && Number.isSafeInteger(item.frequency_limit) && item.frequency_limit > 0
    && timestamp(item.valid_from) && timestamp(item.valid_until) && item.valid_until > item.valid_from
    && ['draft', 'pending_confirmation', 'active', 'paused', 'blocked', 'expired', 'revoked'].includes(String(item.state))
    && item.can_reduce_exposure === true && hash(item.snapshot_sha256)
    && (item.confirmed_at === null || timestamp(item.confirmed_at)) && timestamp(item.created_at) && timestamp(item.updated_at)
}

function validBrokerAccount(value: unknown): value is AutoLiveBrokerAccount {
  return exact(value, ['public_id', 'provider', 'status']) && id(value.public_id) && provider(value.provider) && typeof value.status === 'string'
}
function validRuntime(value: unknown): value is AutoLiveRuntimeProjection {
  return exact(value, ['mandate_public_id', 'state', 'can_reduce_exposure', 'last_error_code', 'observed_at']) && id(value.mandate_public_id) && RUNTIME_STATES.includes(value.state as typeof RUNTIME_STATES[number]) && (typeof value.can_reduce_exposure === 'boolean' || value.can_reduce_exposure === 0 || value.can_reduce_exposure === 1) && (value.last_error_code === null || typeof value.last_error_code === 'string') && timestamp(value.observed_at)
}
function validHeartbeat(value: unknown): value is AutoLiveHeartbeatProjection {
  return exact(value, ['mandate_public_id', 'heartbeat_state', 'heartbeat_at', 'observed_at']) && id(value.mandate_public_id) && HEARTBEAT_STATES.includes(value.heartbeat_state as typeof HEARTBEAT_STATES[number]) && (value.heartbeat_at === null || timestamp(value.heartbeat_at)) && timestamp(value.observed_at)
}
function validTarget(value: unknown): boolean {
  if (!exact(value, ['target_type', 'target_public_id', 'confirmed', 'status', 'fencing_epoch', 'detail', 'created_at'])) return false
  const item = value as Record<string, unknown>
  return TARGET_TYPES.includes(item.target_type as typeof TARGET_TYPES[number]) && id(item.target_public_id) && typeof item.confirmed === 'boolean' && ['failed', 'paused'].includes(String(item.status)) && typeof item.fencing_epoch === 'number' && Number.isSafeInteger(item.fencing_epoch) && item.fencing_epoch >= 0 && (item.detail === null || typeof item.detail === 'string') && timestamp(item.created_at)
}
export const validAutoLiveTarget = validTarget
const receiptKeys = ['public_id', 'mandate_public_id', 'status', 'state', 'runtime_state', 'fencing_epoch', 'created_at', 'idempotency_key_sha256', 'request_fingerprint', 'scope', 'confirmed', 'total', 'unconfirmed', 'target_details', 'can_reduce_exposure', 'all_ok', 'gates', 'actor'] as const
function validReceipt(value: unknown): value is AutoLiveSafeReceipt {
  if (!record(value) || !Object.keys(value).every((key) => receiptKeys.includes(key as typeof receiptKeys[number]))) return false
  const item = value as Record<string, unknown>
  if (!id(item.public_id) || !timestamp(item.created_at)) return false
  if ('mandate_public_id' in item && !id(item.mandate_public_id)) return false
  if ('status' in item && !RECEIPT_STATUSES.includes(item.status as typeof RECEIPT_STATUSES[number])) return false
  if ('state' in item && !RECEIPT_STATES.includes(item.state as typeof RECEIPT_STATES[number])) return false
  if ('runtime_state' in item && !RUNTIME_STATES.includes(item.runtime_state as typeof RUNTIME_STATES[number])) return false
  if ('fencing_epoch' in item && (typeof item.fencing_epoch !== 'number' || !Number.isSafeInteger(item.fencing_epoch) || item.fencing_epoch < 0)) return false
  if ('idempotency_key_sha256' in item && !hash(item.idempotency_key_sha256)) return false
  if ('request_fingerprint' in item && !hash(item.request_fingerprint)) return false
  if ('scope' in item && !['aggregate', 'broker', 'mandate'].includes(String(item.scope))) return false
  if ('confirmed' in item && (typeof item.confirmed !== 'number' || !Number.isSafeInteger(item.confirmed) || item.confirmed < 0)) return false
  if ('total' in item && (typeof item.total !== 'number' || !Number.isSafeInteger(item.total) || item.total < 0)) return false
  if ('unconfirmed' in item && (!Array.isArray(item.unconfirmed) || !item.unconfirmed.every(id))) return false
  if ('target_details' in item && (!Array.isArray(item.target_details) || !item.target_details.every(validTarget))) return false
  if ('can_reduce_exposure' in item && typeof item.can_reduce_exposure !== 'boolean') return false
  if ('all_ok' in item && typeof item.all_ok !== 'boolean') return false
  if ('gates' in item && (!Array.isArray(item.gates) || !item.gates.every(gate))) return false
  if ('actor' in item && item.actor !== 'owner') return false
  return true
}
export const validAutoLiveSafeReceipt = validReceipt
function validSnapshotStartReceipt(value: unknown): boolean {
  if (!record(value) || !hash(value.receipt_sha256)) return false
  const { receipt_sha256: _receipt_sha256, ...receipt } = value
  return validReceipt(receipt) && Object.keys(value).every((key) => [...receiptKeys, 'receipt_sha256'].includes(key as typeof receiptKeys[number] | 'receipt_sha256'))
}

export function validAutoLiveSnapshot(value: unknown): value is AutoLiveSnapshot {
  return exact(value, ['snapshot_at', 'broker_accounts', 'mandates', 'runtime_projections', 'heartbeat_projections', 'pause_receipts', 'start_receipts', 'order_receipts'])
    && timestamp(value.snapshot_at) && Array.isArray(value.broker_accounts) && value.broker_accounts.every(validBrokerAccount)
    && Array.isArray(value.mandates) && value.mandates.every(validAutoLiveMandate)
    && Array.isArray(value.runtime_projections) && value.runtime_projections.every(validRuntime)
    && Array.isArray(value.heartbeat_projections) && value.heartbeat_projections.every(validHeartbeat)
    && Array.isArray(value.pause_receipts) && value.pause_receipts.every((item) => exact(item, ['public_id', 'status', 'receipt', 'receipt_sha256', 'created_at']) && id(item.public_id) && ['paused', 'partial', 'failed'].includes(String(item.status)) && validReceipt(item.receipt) && hash(item.receipt_sha256) && timestamp(item.created_at))
    && Array.isArray(value.start_receipts) && value.start_receipts.every(validSnapshotStartReceipt)
    && Array.isArray(value.order_receipts) && value.order_receipts.every((item) => exact(item, ['public_id', 'mandate_public_id', 'client_order_id', 'submission_state', 'observed_at', 'receipt_sha256']) && id(item.public_id) && id(item.mandate_public_id) && typeof item.client_order_id === 'string' && SUBMISSION_STATES.includes(item.submission_state as typeof SUBMISSION_STATES[number]) && timestamp(item.observed_at) && hash(item.receipt_sha256))
}

export function validAutoLivePauseResult(value: unknown): value is AutoLivePauseResult {
  if (!exact(value, ['public_id', 'scope', 'status', 'confirmed', 'total', 'unconfirmed', 'can_reduce_exposure', 'receipt_sha256', 'created_at', 'updated_at'])) return false
  const item = value as Record<string, unknown>
  return id(item.public_id) && TARGET_TYPES.includes(item.scope as typeof TARGET_TYPES[number]) && ['pausing', 'paused', 'partial', 'failed'].includes(String(item.status)) && typeof item.confirmed === 'number' && Number.isSafeInteger(item.confirmed) && typeof item.total === 'number' && Number.isSafeInteger(item.total) && item.confirmed >= 0 && item.total >= item.confirmed && Array.isArray(item.unconfirmed) && item.unconfirmed.every(id) && item.can_reduce_exposure === true && hash(item.receipt_sha256) && timestamp(item.created_at) && timestamp(item.updated_at)
}

function validMandateWithConfirmation(value: unknown): boolean {
  if (!exact(value, ['public_id', 'strategy_version', 'risk_version', 'capital_limit_minor', 'frequency_limit', 'valid_from', 'valid_until', 'state', 'can_reduce_exposure', 'snapshot_sha256', 'confirmed_at', 'created_at', 'updated_at', 'confirmation_phrase', 'confirmation_snapshot_sha256'])) return false
  const { confirmation_phrase, confirmation_snapshot_sha256, ...mandate } = value
  return typeof confirmation_phrase === 'string' && confirmation_phrase.length > 0 && hash(confirmation_snapshot_sha256) && validAutoLiveActionMandate(mandate)
}

function validMandateGateResult(value: unknown): boolean {
  if (!record(value)) return false
  const item = value as Record<string, unknown>
  const keys = Object.keys(item)
  const mandate = Object.fromEntries(keys.filter((key) => !['all_ok', 'gates'].includes(key)).map((key) => [key, item[key]]))
  return validAutoLiveActionMandate(value) || (keys.includes('all_ok') && keys.includes('gates') && exact(item, [...keys.filter((key) => key !== 'all_ok' && key !== 'gates'), 'all_ok', 'gates']) && validAutoLiveActionMandate(mandate) && typeof item.all_ok === 'boolean' && Array.isArray(item.gates) && item.gates.every(gate))
}

function validConfirmationResponse(value: unknown): boolean {
  if (!record(value)) return false
  const keys = Object.keys(value)
  if (keys.includes('confirmation_phrase')) {
    if (validMandateWithConfirmation(value)) return true
    if (!keys.includes('all_ok') || !keys.includes('gates')) return false
    const item = value as Record<string, unknown>
    const { confirmation_phrase, confirmation_snapshot_sha256, all_ok, gates, ...mandate } = item
    return typeof confirmation_phrase === 'string' && confirmation_phrase.length > 0 && hash(confirmation_snapshot_sha256) && typeof all_ok === 'boolean' && Array.isArray(gates) && gates.every(gate) && validAutoLiveActionMandate(mandate)
  }
  return validMandateGateResult(value)
}

function validStartReceipt(value: unknown): value is AutoLiveSafeReceipt { return validReceipt(value) }
function validString(value: string, label: string): string { if (!value.trim() || value.length > 128) throw new Error(`${label}无效。`); return value }
function validIdempotencyKey(value: string): string { if (!/^[A-Za-z0-9._:-]{8,128}$/.test(value)) throw new Error('Idempotency-Key 格式无效。'); return value }

export interface AutoLiveApi {
  snapshot: (signal?: AbortSignal) => Promise<AutoLiveSnapshot>
  createMandate: (input: AutoLiveCreateMandateInput, idempotencyKey: string, signal?: AbortSignal) => Promise<AutoLiveMandate>
  requestConfirmation: (mandatePublicId: string, signal?: AbortSignal) => Promise<Record<string, unknown>>
  confirm: (mandatePublicId: string, input: AutoLiveConfirmationInput, signal?: AbortSignal) => Promise<Record<string, unknown>>
  resume: (mandatePublicId: string, signal?: AbortSignal) => Promise<Record<string, unknown>>
  revoke: (mandatePublicId: string, reason: string, signal?: AbortSignal) => Promise<AutoLiveActionMandate>
  start: (mandatePublicId: string, input: AutoLiveStartInput, idempotencyKey: string, signal?: AbortSignal) => Promise<AutoLiveSafeReceipt>
  pause: (input: AutoLivePauseInput, idempotencyKey: string, signal?: AbortSignal) => Promise<AutoLivePauseResult>
}

export function createAutoLiveApi(transport: typeof authenticatedJsonRequest = authenticatedJsonRequest): AutoLiveApi {
  async function decode<T>(path: string, validator: (value: unknown) => value is T, init?: RequestInit): Promise<T> {
    const value = await transport<unknown>(path, init)
    if (!validator(value)) throw new Error('自动实盘响应格式无效。')
    return value
  }
  return {
    snapshot: (signal) => decode('/api/rewrite/v1/auto-live', validAutoLiveSnapshot, { cache: 'no-store', signal }),
    createMandate: (input, idempotencyKey, signal) => {
      if (!exact(input, ['broker_account_public_id', 'strategy_version', 'risk_version', 'capital_limit_minor', 'frequency_limit', 'valid_from', 'valid_until']) || !id(input.broker_account_public_id) || !input.strategy_version.trim() || !input.risk_version.trim() || !Number.isSafeInteger(input.capital_limit_minor) || input.capital_limit_minor <= 0 || !Number.isSafeInteger(input.frequency_limit) || input.frequency_limit <= 0 || !timestamp(input.valid_from) || !timestamp(input.valid_until) || input.valid_until <= input.valid_from) throw new Error('mandate 字段无效。')
      return decode('/api/rewrite/v1/auto-live/mandates', (value): value is AutoLiveMandate => validAutoLiveMandate(value), { method: 'POST', headers: { 'Idempotency-Key': validIdempotencyKey(idempotencyKey) }, body: JSON.stringify(input), signal })
    },
    requestConfirmation: (mandatePublicId, signal) => decode(`/api/rewrite/v1/auto-live/mandates/${encodeURIComponent(validString(mandatePublicId, 'mandate public id'))}/confirmation`, (value): value is Record<string, unknown> => validConfirmationResponse(value), { method: 'POST', signal }),
    confirm: (mandatePublicId, input, signal) => {
      if (!exact(input, ['mandate_public_id', 'snapshot_sha256', 'confirmation_phrase']) || input.mandate_public_id !== mandatePublicId || !hash(input.snapshot_sha256) || !input.confirmation_phrase.trim()) throw new Error('确认字段无效。')
      return decode(`/api/rewrite/v1/auto-live/mandates/${encodeURIComponent(validString(mandatePublicId, 'mandate public id'))}/confirm`, (value): value is Record<string, unknown> => validConfirmationResponse(value), { method: 'POST', body: JSON.stringify(input), signal })
    },
    resume: (mandatePublicId, signal) => decode(`/api/rewrite/v1/auto-live/mandates/${encodeURIComponent(validString(mandatePublicId, 'mandate public id'))}/resume`, (value): value is Record<string, unknown> => validConfirmationResponse(value), { method: 'POST', signal }),
    revoke: (mandatePublicId, reason, signal) => {
      if (!reason.trim() || reason.length > 500) throw new Error('撤销原因无效。')
      return decode(`/api/rewrite/v1/auto-live/mandates/${encodeURIComponent(validString(mandatePublicId, 'mandate public id'))}/revoke`, validAutoLiveActionMandate, { method: 'POST', body: JSON.stringify({ reason }), signal })
    },
    start: (mandatePublicId, input, idempotencyKey, signal) => {
      if (!Number.isSafeInteger(input.expected_fencing_epoch) || input.expected_fencing_epoch < 0) throw new Error('启动 fencing epoch 无效。')
      return decode(`/api/rewrite/v1/auto-live/mandates/${encodeURIComponent(validString(mandatePublicId, 'mandate public id'))}/start`, validStartReceipt, { method: 'POST', headers: { 'Idempotency-Key': validIdempotencyKey(idempotencyKey) }, body: JSON.stringify(input), signal })
    },
    pause: (input, idempotencyKey, signal) => {
      const valid = (input.scope === 'aggregate' && exact(input, ['scope'])) || (input.scope === 'broker' && exact(input, ['scope', 'broker_account_public_id']) && id(input.broker_account_public_id)) || (input.scope === 'mandate' && exact(input, ['scope', 'mandate_public_id']) && id(input.mandate_public_id))
      if (!valid) throw new Error('暂停范围字段无效。')
      return decode('/api/rewrite/v1/auto-live/pause', validAutoLivePauseResult, { method: 'POST', headers: { 'Idempotency-Key': validIdempotencyKey(idempotencyKey) }, body: JSON.stringify(input), signal })
    },
  }
}

export const autoLiveApi = createAutoLiveApi()
