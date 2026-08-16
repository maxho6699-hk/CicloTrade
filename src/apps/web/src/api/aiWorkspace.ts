import { authenticatedJsonRequest, BrowserApiError } from './client.ts'

const BASE = '/api/rewrite/v1/ai/workspace'
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$/
const TASK_STATUSES = ['queued', 'running', 'partial', 'succeeded', 'failed', 'cancelled', 'blocked', 'timed_out'] as const

export type AiTaskStatus = typeof TASK_STATUSES[number]
export type AiSessionStatus = 'active' | 'archived'
export type AIWorkspaceTaskStatus = AiTaskStatus
export type AIWorkspaceSessionStatus = AiSessionStatus

export interface AiWorkspaceReadinessUnavailable {
  ready: false
  status: 'unavailable'
  missing: string[]
}

export interface AiWorkspaceReadinessReady {
  ready: true
  status: 'ready'
  missing: []
  provider_version: string
  contract_version: string
  model: string
}

export type AiWorkspaceReadiness = AiWorkspaceReadinessUnavailable | AiWorkspaceReadinessReady

export interface AiWorkspaceSessionSummary {
  public_id: string
  title: string
  status: AiSessionStatus
  context_snapshot_public_id: string | null
  created_at: string
}

export interface AiWorkspaceMessage {
  public_id: string
  role: 'user' | 'assistant' | 'system'
  content: Record<string, unknown>
  created_at: string
}

export interface AiWorkspaceSession extends AiWorkspaceSessionSummary {
  messages: AiWorkspaceMessage[]
}

export interface AiWorkspaceTask {
  public_id: string
  session_public_id: string
  status: AiTaskStatus
  blocked_reason: string | null
  error_code: string | null
  provider_version: string | null
  contract_version: string | null
  created_at: string
  updated_at: string
}

export interface AiWorkspaceAnswerSection {
  text: string | string[]
  citation_ids: string[]
}

export interface AiWorkspaceStructuredAnswer {
  conclusion: AiWorkspaceAnswerSection
  citations: string[]
  support: AiWorkspaceAnswerSection
  counter: AiWorkspaceAnswerSection
  risks: AiWorkspaceAnswerSection
  next_steps: AiWorkspaceAnswerSection
  tool_calls?: Array<{ name: string; arguments: Record<string, unknown> }>
}

export interface AiWorkspaceAssistant {
  public_id: string
  structured: AiWorkspaceStructuredAnswer
  created_at: string
}

export interface AiWorkspaceTaskResult {
  task: AiWorkspaceTask
  assistant: AiWorkspaceAssistant | null
  blocked: boolean
}

export interface AiWorkspaceTaskEvent {
  seq: number
  status: AiTaskStatus
  payload: Record<string, unknown>
  created_at: string
}

export interface AiWorkspaceSessionCreateInput {
  title?: string
  route?: string
  market?: string
  symbol?: string
  timeframe?: string
  question?: string
}

export class AiWorkspaceApiError extends Error {
  status: number
  receipt: AiWorkspaceTaskResult | null

  constructor(message: string, status = 0, receipt: AiWorkspaceTaskResult | null = null) {
    super(message)
    this.name = 'AiWorkspaceApiError'
    this.status = status
    this.receipt = receipt
  }
}

export type AiWorkspaceErrorKind = 'unauthorized' | 'forbidden' | 'missing' | 'conflict' | 'unavailable' | 'error'

export function classifyAiWorkspaceError(status: number, operation: 'read' | 'write' | 'task' = 'read'): AiWorkspaceErrorKind {
  if (status === 401) return 'unauthorized'
  if (status === 403) return 'forbidden'
  if (status === 404) return operation === 'task' ? 'missing' : 'unavailable'
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

function text(value: unknown, max = 16_000): value is string {
  return typeof value === 'string' && value.length <= max
}

function id(value: unknown): value is string {
  return typeof value === 'string' && SAFE_ID.test(value)
}

function timestamp(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 64 && Number.isFinite(Date.parse(value))
}

function nullableText(value: unknown): value is string | null {
  return value === null || text(value, 512)
}

function decodeOrThrow<T>(value: unknown, valid: (value: unknown) => value is T, message: string): T {
  if (!valid(value)) throw new AiWorkspaceApiError(message, 502)
  return value
}

function validReadiness(value: unknown): value is AiWorkspaceReadiness {
  if (!record(value) || typeof value.ready !== 'boolean' || !['ready', 'unavailable'].includes(String(value.status)) || !Array.isArray(value.missing) || !value.missing.every((item) => text(item, 128))) return false
  if (!value.ready) return exact(value, ['ready', 'status', 'missing']) && value.status === 'unavailable'
  return exact(value, ['ready', 'status', 'missing', 'provider_version', 'contract_version', 'model'])
    && value.status === 'ready' && value.missing.length === 0 && [value.provider_version, value.contract_version, value.model].every((item) => text(item, 256))
}

function validSessionSummary(value: unknown): value is AiWorkspaceSessionSummary {
  return exact(value, ['public_id', 'title', 'status', 'context_snapshot_public_id', 'created_at'])
    && id(value.public_id) && text(value.title, 500) && ['active', 'archived'].includes(String(value.status))
    && (value.context_snapshot_public_id === null || id(value.context_snapshot_public_id)) && timestamp(value.created_at)
}

function validMessage(value: unknown): value is AiWorkspaceMessage {
  return exact(value, ['public_id', 'role', 'content', 'created_at']) && id(value.public_id)
    && ['user', 'assistant', 'system'].includes(String(value.role)) && record(value.content) && timestamp(value.created_at)
}

function validSession(value: unknown): value is AiWorkspaceSession {
  return exact(value, ['public_id', 'title', 'status', 'context_snapshot_public_id', 'created_at', 'messages'])
    && id(value.public_id) && text(value.title, 500) && ['active', 'archived'].includes(String(value.status))
    && (value.context_snapshot_public_id === null || id(value.context_snapshot_public_id)) && timestamp(value.created_at)
    && Array.isArray(value.messages) && value.messages.length <= 500 && value.messages.every(validMessage)
}

function validSection(value: unknown): value is AiWorkspaceAnswerSection {
  return exact(value, ['text', 'citation_ids'])
    && ((typeof value.text === 'string' && value.text.length <= 16_000) || (Array.isArray(value.text) && value.text.length <= 100 && value.text.every((item) => text(item, 16_000))))
    && Array.isArray(value.citation_ids) && value.citation_ids.length <= 100 && value.citation_ids.every(id)
}

function validStructured(value: unknown): value is AiWorkspaceStructuredAnswer {
  if (!record(value) || !exact(value, ['conclusion', 'citations', 'support', 'counter', 'risks', 'next_steps']) && !exact(value, ['conclusion', 'citations', 'support', 'counter', 'risks', 'next_steps', 'tool_calls'])) return false
  if (!validSection(value.conclusion) || !Array.isArray(value.citations) || value.citations.length < 1 || !value.citations.every(id) || !validSection(value.support) || !validSection(value.counter) || !validSection(value.risks) || !validSection(value.next_steps)) return false
  if (value.tool_calls !== undefined && (!Array.isArray(value.tool_calls) || value.tool_calls.length > 32 || !value.tool_calls.every((item) => record(item) && exact(item, ['name', 'arguments']) && text(item.name, 128) && record(item.arguments)))) return false
  return true
}

function validAssistant(value: unknown): value is AiWorkspaceAssistant {
  return exact(value, ['public_id', 'structured', 'created_at']) && id(value.public_id) && validStructured(value.structured) && timestamp(value.created_at)
}

function validTask(value: unknown): value is AiWorkspaceTask {
  return exact(value, ['public_id', 'session_public_id', 'status', 'blocked_reason', 'error_code', 'provider_version', 'contract_version', 'created_at', 'updated_at'])
    && id(value.public_id) && id(value.session_public_id) && TASK_STATUSES.includes(value.status as AiTaskStatus)
    && nullableText(value.blocked_reason) && nullableText(value.error_code) && nullableText(value.provider_version) && nullableText(value.contract_version)
    && timestamp(value.created_at) && timestamp(value.updated_at)
}

function validTaskResult(value: unknown): value is AiWorkspaceTaskResult {
  return exact(value, ['task', 'assistant', 'blocked']) && validTask(value.task) && (value.assistant === null || validAssistant(value.assistant)) && typeof value.blocked === 'boolean'
    && value.blocked === (value.task.status === 'blocked')
}

function validTaskEvent(value: unknown): value is AiWorkspaceTaskEvent {
  return exact(value, ['seq', 'status', 'payload', 'created_at']) && typeof value.seq === 'number' && Number.isSafeInteger(value.seq) && value.seq > 0
    && TASK_STATUSES.includes(value.status as AiTaskStatus) && record(value.payload) && timestamp(value.created_at)
}

function validatePathId(value: string, label: string) {
  if (!id(value)) throw new AiWorkspaceApiError(`${label} 无效。`, 400)
  return encodeURIComponent(value)
}

function validateIdempotency(value: string) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/.test(value)) throw new AiWorkspaceApiError('幂等键无效。', 400)
}

async function browserTransport(path: string, init?: RequestInit) {
  try {
    return await authenticatedJsonRequest<unknown>(path, init)
  } catch (error) {
    if (error instanceof BrowserApiError) throw new AiWorkspaceApiError(error.message.slice(0, 300), error.status)
    throw error
  }
}

export type AiWorkspaceTransport = (path: string, init?: RequestInit) => Promise<unknown>

export interface AiWorkspaceApi {
  readiness: (signal?: AbortSignal) => Promise<AiWorkspaceReadiness>
  listSessions: (signal?: AbortSignal) => Promise<AiWorkspaceSessionSummary[]>
  createSession: (input: AiWorkspaceSessionCreateInput, idempotencyKey: string, signal?: AbortSignal) => Promise<AiWorkspaceSession>
  getSession: (sessionId: string, signal?: AbortSignal) => Promise<AiWorkspaceSession>
  archiveSession: (sessionId: string, idempotencyKey: string, signal?: AbortSignal) => Promise<AiWorkspaceSession>
  sendMessage: (sessionId: string, content: string, idempotencyKey: string, signal?: AbortSignal) => Promise<AiWorkspaceTaskResult>
  getTask: (taskId: string, signal?: AbortSignal) => Promise<AiWorkspaceTask>
  listTaskEvents: (taskId: string, signal?: AbortSignal) => Promise<AiWorkspaceTaskEvent[]>
  cancelTask: (taskId: string, idempotencyKey: string, signal?: AbortSignal) => Promise<AiWorkspaceTask>
}

function safeContent(content: string) {
  if (!content.trim() || content.length > 16_000) throw new AiWorkspaceApiError('问题不能为空，且不能超过 16,000 字。', 400)
}

export function createAiWorkspaceApi(transport: AiWorkspaceTransport = browserTransport): AiWorkspaceApi {
  return {
    async readiness(signal) {
      return decodeOrThrow(await transport(`${BASE}/readiness`, { method: 'GET', cache: 'no-store', signal }), validReadiness, 'AI readiness 响应格式无效。')
    },
    async listSessions(signal) {
      const value = await transport(`${BASE}/sessions`, { method: 'GET', cache: 'no-store', signal })
      if (!exact(value, ['items']) || !Array.isArray(value.items) || value.items.length > 200 || !value.items.every(validSessionSummary)) throw new AiWorkspaceApiError('AI 会话列表响应格式无效。', 502)
      return value.items
    },
    async createSession(input, idempotencyKey, signal) {
      validateIdempotency(idempotencyKey)
      if (!record(input)) throw new AiWorkspaceApiError('AI 会话字段无效。', 400)
      const allowed = ['title', 'route', 'market', 'symbol', 'timeframe', 'question']
      if (Object.keys(input).some((key) => !allowed.includes(key)) || Object.values(input).some((value) => value !== undefined && !text(value, 512))) throw new AiWorkspaceApiError('AI 会话字段无效。', 400)
      const value = await transport(`${BASE}/sessions`, { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify(input), signal })
      return decodeOrThrow(value, validSession, 'AI 会话响应格式无效。')
    },
    async getSession(sessionId, signal) {
      const value = await transport(`${BASE}/sessions/${validatePathId(sessionId, '会话')}`, { method: 'GET', cache: 'no-store', signal })
      return decodeOrThrow(value, validSession, 'AI 会话详情响应格式无效。')
    },
    async archiveSession(sessionId, idempotencyKey, signal) {
      validateIdempotency(idempotencyKey)
      const value = await transport(`${BASE}/sessions/${validatePathId(sessionId, '会话')}/archive`, { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: '{}', signal })
      return decodeOrThrow(value, validSession, 'AI 会话归档响应格式无效。')
    },
    async sendMessage(sessionId, content, idempotencyKey, signal) {
      validatePathId(sessionId, '会话')
      validateIdempotency(idempotencyKey)
      safeContent(content)
      const value = await transport(`${BASE}/sessions/${validatePathId(sessionId, '会话')}/messages`, { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify({ content }), signal })
      return decodeOrThrow(value, validTaskResult, 'AI 任务回执格式无效。')
    },
    async getTask(taskId, signal) {
      const value = await transport(`${BASE}/tasks/${validatePathId(taskId, '任务')}`, { method: 'GET', cache: 'no-store', signal })
      return decodeOrThrow(value, validTask, 'AI 任务响应格式无效。')
    },
    async listTaskEvents(taskId, signal) {
      const value = await transport(`${BASE}/tasks/${validatePathId(taskId, '任务')}/events`, { method: 'GET', cache: 'no-store', signal })
      if (!exact(value, ['items']) || !Array.isArray(value.items) || value.items.length > 200 || !value.items.every(validTaskEvent)) throw new AiWorkspaceApiError('AI 任务事件响应格式无效。', 502)
      return value.items
    },
    async cancelTask(taskId, idempotencyKey, signal) {
      validateIdempotency(idempotencyKey)
      const value = await transport(`${BASE}/tasks/${validatePathId(taskId, '任务')}/cancel`, { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: '{}', signal })
      return decodeOrThrow(value, validTask, 'AI 任务取消响应格式无效。')
    },
  }
}

export const aiWorkspaceApi = createAiWorkspaceApi()

export const AI_TASK_STATUSES = TASK_STATUSES
export const createAIWorkspaceApi = createAiWorkspaceApi
export const AIWorkspaceApiError = AiWorkspaceApiError
export const aiWorkspaceAPI = aiWorkspaceApi

export const decodeAiWorkspaceReadiness = (value: unknown) => decodeOrThrow(value, validReadiness, 'AI readiness 响应格式无效。')
export const decodeAiWorkspaceSession = (value: unknown) => decodeOrThrow(value, validSession, 'AI 会话响应格式无效。')
export const decodeAiWorkspaceTask = (value: unknown) => decodeOrThrow(value, validTask, 'AI 任务响应格式无效。')
export const decodeAiWorkspaceTaskResult = (value: unknown) => decodeOrThrow(value, validTaskResult, 'AI 任务回执格式无效。')

/** Only the public structured answer block is exposed; provider metadata stays server-side. */
export function readAiWorkspaceStructuredMessage(value: unknown): AiWorkspaceStructuredAnswer | null {
  if (!record(value) || !validStructured(value.structured)) return null
  return value.structured
}
