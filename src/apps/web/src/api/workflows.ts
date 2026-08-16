import { authenticatedJsonRequest, BrowserApiError } from './client.ts'

const BASE = '/api/rewrite/v1/workflows'
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/
const SHA256 = /^[0-9a-f]{64}$/
const STATUSES = ['queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled', 'blocked', 'timed_out'] as const
export type WorkflowStatus = typeof STATUSES[number]
export type SafeJson = null | string | number | boolean | SafeJson[] | { [key: string]: SafeJson }

export interface WorkflowEvent {
  seq: number
  event_type: string
  status: WorkflowStatus
  payload: SafeJson
  created_at: string
}

export interface WorkflowTask {
  task_public_id: string
  source_kind: string
  source_public_id: string
  attempt: number
  status: WorkflowStatus
  context: SafeJson
  context_sha256: string
  provenance_sha256: string
  result: SafeJson
  result_sha256: string | null
  cancel_requested: boolean
  created_at: string
  updated_at: string
  completed_at: string | null
  events: WorkflowEvent[]
  deliberation: SafeJson | null
  source_sha256?: string | null
}

export class WorkflowApiError extends Error {
  status: number
  constructor(message: string, status = 0) {
    super(message)
    this.name = 'WorkflowApiError'
    this.status = status
  }
}

export type WorkflowErrorKind = 'unauthorized' | 'forbidden' | 'missing' | 'conflict' | 'unavailable' | 'error'
export function classifyWorkflowError(status: number, operation: 'read' | 'write' = 'read'): WorkflowErrorKind {
  if (status === 401) return 'unauthorized'
  if (status === 403) return 'forbidden'
  if (status === 404) return operation === 'read' ? 'missing' : 'unavailable'
  if (status === 409) return 'conflict'
  if (status === 503) return 'unavailable'
  return 'error'
}

function record(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === 'object' && !Array.isArray(value) }
function exact(value: unknown, keys: readonly string[]): value is Record<string, unknown> { return record(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key)) }
function timestamp(value: unknown): value is string | null { return value === null || (typeof value === 'string' && value.length <= 64 && Number.isFinite(Date.parse(value))) }
function text(value: unknown, max = 512): value is string { return typeof value === 'string' && value.length <= max }
const FORBIDDEN = /^(owner_id|provenance|provenance_json|chain_of_thought|thoughts|reasoning|artifact_url|download_url|url|uri|href)$/i
function safeJson(value: unknown, depth = 0): value is SafeJson {
  if (depth > 8) return false
  if (value === null || typeof value === 'boolean' || typeof value === 'number') return typeof value !== 'number' || Number.isFinite(value)
  if (typeof value === 'string') return !/^https?:\/\//i.test(value) && value.length <= 16_000
  if (Array.isArray(value)) return value.length <= 200 && value.every((item) => safeJson(item, depth + 1))
  return record(value) && Object.entries(value).every(([key, item]) => !FORBIDDEN.test(key) && text(key, 128) && safeJson(item, depth + 1))
}
function decode<T>(value: unknown, valid: (value: unknown) => value is T, message: string): T { if (!valid(value)) throw new WorkflowApiError(message, 502); return value }

function validEvent(value: unknown): value is WorkflowEvent {
  if (!record(value) || !exact(value, ['seq', 'event_type', 'status', 'payload', 'created_at'])) return false
  const seq = value.seq
  return Number.isSafeInteger(seq) && Number(seq) >= 1 && text(value.event_type, 64) && STATUSES.includes(value.status as WorkflowStatus) && safeJson(value.payload) && timestamp(value.created_at) && value.created_at !== null
}

function validTask(value: unknown, detail: boolean): value is WorkflowTask {
  const base = ['task_public_id', 'source_kind', 'source_public_id', 'attempt', 'status', 'context', 'context_sha256', 'provenance_sha256', 'result', 'result_sha256', 'cancel_requested', 'created_at', 'updated_at', 'completed_at']
  if (!record(value)) return false
  const optionalHash = Object.prototype.hasOwnProperty.call(value, 'source_sha256') ? ['source_sha256'] : Object.prototype.hasOwnProperty.call(value, 'source_hash') ? ['source_hash'] : []
  const keys = detail ? [...base, ...optionalHash, 'events', 'deliberation'] : [...base, ...optionalHash]
  if (!exact(value, keys)) return false
  const attempt = value.attempt
  const sourceId = value.source_public_id
  const sourceSha = value.source_sha256 ?? value.source_hash
  if ((sourceSha !== undefined && sourceSha !== null && (typeof sourceSha !== 'string' || !SHA256.test(sourceSha))) || typeof value.task_public_id !== 'string' || !SAFE_ID.test(value.task_public_id) || !text(value.source_kind, 64) || typeof sourceId !== 'string' || !SAFE_ID.test(sourceId) || !Number.isSafeInteger(attempt) || Number(attempt) < 1 || !STATUSES.includes(value.status as WorkflowStatus) || !safeJson(value.context) || typeof value.context_sha256 !== 'string' || !SHA256.test(value.context_sha256) || typeof value.provenance_sha256 !== 'string' || !SHA256.test(value.provenance_sha256) || !safeJson(value.result) || (value.result_sha256 !== null && (typeof value.result_sha256 !== 'string' || !SHA256.test(value.result_sha256))) || typeof value.cancel_requested !== 'boolean' || !timestamp(value.created_at) || value.created_at === null || !timestamp(value.updated_at) || value.updated_at === null || !timestamp(value.completed_at)) return false
  if (!detail) return true
  return Array.isArray(value.events) && value.events.length <= 500 && value.events.every(validEvent) && (value.deliberation === null || safeJson(value.deliberation))
}

async function browserTransport(path: string, init?: RequestInit) {
  try { return await authenticatedJsonRequest<unknown>(path, init) } catch (error) { if (error instanceof BrowserApiError) throw new WorkflowApiError(error.message.slice(0, 300), error.status); throw error }
}
export type WorkflowTransport = (path: string, init?: RequestInit) => Promise<unknown>
export interface WorkflowApi {
  list: (signal?: AbortSignal) => Promise<WorkflowTask[]>
  get: (id: string, signal?: AbortSignal) => Promise<WorkflowTask>
  cancel: (id: string, signal?: AbortSignal) => Promise<WorkflowTask>
  retry: (id: string, signal?: AbortSignal) => Promise<WorkflowTask>
}
function safeId(value: string) { if (!SAFE_ID.test(value)) throw new WorkflowApiError('Workflow 任务 ID 无效。', 400); return encodeURIComponent(value) }
export function createWorkflowApi(transport: WorkflowTransport = browserTransport): WorkflowApi {
  return {
    async list(signal) { const value = await transport(`${BASE}?limit=100`, { method: 'GET', cache: 'no-store', signal }); if (!record(value) || !exact(value, ['items']) || !Array.isArray(value.items) || value.items.length > 100) throw new WorkflowApiError('Workflow 列表响应格式无效。', 502); return value.items.map((item) => decode(item, (entry): entry is WorkflowTask => validTask(entry, false), 'Workflow 任务响应格式无效。')) },
    async get(id, signal) { return decode(await transport(`${BASE}/${safeId(id)}`, { method: 'GET', cache: 'no-store', signal }), (value): value is WorkflowTask => validTask(value, true), 'Workflow 任务详情响应格式无效。') },
    async cancel(id, signal) { return decode(await transport(`${BASE}/${safeId(id)}/cancel`, { method: 'POST', body: '{}', signal }), (value): value is WorkflowTask => validTask(value, true), 'Workflow 取消响应格式无效。') },
    async retry(id, signal) { return decode(await transport(`${BASE}/${safeId(id)}/retry`, { method: 'POST', body: '{}', signal }), (value): value is WorkflowTask => validTask(value, false), 'Workflow 重试响应格式无效。') },
  }
}
export const workflowApi = createWorkflowApi()
export const decodeWorkflowTask = (value: unknown) => decode(value, (entry): entry is WorkflowTask => validTask(entry, true), 'Workflow 任务详情响应格式无效。')
