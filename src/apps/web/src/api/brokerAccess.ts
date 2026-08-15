import { authenticatedJsonRequest, BrowserApiError } from './client.ts'

export type BrokerProvider = 'futu_moomoo' | 'tiger' | 'ibkr' | 'webull' | 'longbridge'
export type BrokerAccessStatus = 'submitted' | 'approved' | 'rejected' | 'withdrawn' | 'revoked' | 'expired'

export interface BrokerAccessApplication {
  id: string
  provider: BrokerProvider
  status: BrokerAccessStatus
  request_reason: string | null
  decision_reason: string | null
  created_at: string
  updated_at: string
  reviewed_at: string | null
  withdrawn_at: string | null
  eligibility_only: true
  broker_account_created: false
  execution_enabled: false
}

export interface AdminBrokerAccessApplication extends BrokerAccessApplication {
  user_id: number
  user_email: string
  user_display_name: string
  reviewed_by: number | null
  reviewer_email: string | null
}

export function isBrokerAccessRejection(error: unknown): error is BrowserApiError {
  return error instanceof BrowserApiError && error.status >= 400 && error.status < 500
}

function record(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === 'object' && !Array.isArray(value) }
function timestamp(value: unknown): value is string { return typeof value === 'string' && Number.isFinite(Date.parse(value)) }
const baseKeys = new Set(['id', 'provider', 'status', 'request_reason', 'decision_reason', 'created_at', 'updated_at', 'reviewed_at', 'withdrawn_at', 'eligibility_only', 'broker_account_created', 'execution_enabled'])
const adminKeys = new Set(['user_id', 'user_email', 'user_display_name', 'reviewed_by', 'reviewer_email'])
function baseApplication(value: unknown, allowAdminFields = false): value is BrokerAccessApplication {
  return record(value)
    && Object.keys(value).every((key) => baseKeys.has(key) || (allowAdminFields && adminKeys.has(key)))
    && Object.keys(value).length >= 12
    && typeof value.id === 'string' && /^bra_[A-Za-z0-9_-]{16,48}$/.test(value.id)
    && ['futu_moomoo', 'tiger', 'ibkr', 'webull', 'longbridge'].includes(String(value.provider))
    && ['submitted', 'approved', 'rejected', 'withdrawn', 'revoked', 'expired'].includes(String(value.status))
    && (value.request_reason === null || typeof value.request_reason === 'string')
    && (value.decision_reason === null || typeof value.decision_reason === 'string')
    && timestamp(value.created_at) && timestamp(value.updated_at)
    && (value.reviewed_at === null || timestamp(value.reviewed_at))
    && (value.withdrawn_at === null || timestamp(value.withdrawn_at))
    && value.eligibility_only === true && value.broker_account_created === false && value.execution_enabled === false
}

function application(value: unknown): value is BrokerAccessApplication { return baseApplication(value) }
function adminApplication(value: unknown): value is AdminBrokerAccessApplication {
  return baseApplication(value, true)
    && Object.keys(value).length === 17
    && typeof value.user_id === 'number'
    && typeof value.user_email === 'string'
    && typeof value.user_display_name === 'string'
    && (value.reviewed_by === null || typeof value.reviewed_by === 'number')
    && (value.reviewer_email === null || typeof value.reviewer_email === 'string')
}

function decodeApplications(value: unknown): BrokerAccessApplication[] {
  if (!record(value) || !Array.isArray(value.items) || !value.items.every(application)) throw new Error('券商资格申请响应格式无效。')
  return value.items
}

export interface BrokerAccessApi {
  list: (signal?: AbortSignal) => Promise<BrokerAccessApplication[]>
  create: (provider: BrokerProvider, requestReason: string | null, idempotencyKey: string, signal?: AbortSignal) => Promise<{ application: BrokerAccessApplication; replayed: boolean }>
  withdraw: (id: string, signal?: AbortSignal) => Promise<BrokerAccessApplication>
  adminList: (status?: BrokerAccessStatus, signal?: AbortSignal) => Promise<AdminBrokerAccessApplication[]>
  review: (id: string, decision: 'approved' | 'rejected', reason: string, signal?: AbortSignal) => Promise<AdminBrokerAccessApplication>
}

export function createBrokerAccessApi(transport: typeof authenticatedJsonRequest = authenticatedJsonRequest): BrokerAccessApi {
  return {
    async list(signal) { return decodeApplications(await transport('/api/rewrite/v1/broker-access-applications', { cache: 'no-store', signal })) },
    async create(provider, requestReason, idempotencyKey, signal) {
      if (!['futu_moomoo', 'tiger', 'ibkr', 'webull', 'longbridge'].includes(provider) || !/^[A-Za-z0-9._:-]{8,128}$/.test(idempotencyKey)) throw new Error('券商资格申请字段无效。')
      const value = await transport('/api/rewrite/v1/broker-access-applications', { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify({ provider, ...(requestReason ? { request_reason: requestReason } : {}) }), signal }) as unknown
      if (!record(value) || !application(value.application) || typeof value.replayed !== 'boolean') throw new Error('券商资格申请响应格式无效。')
      return value as { application: BrokerAccessApplication; replayed: boolean }
    },
    async withdraw(id, signal) {
      const value = await transport(`/api/rewrite/v1/broker-access-applications/${encodeURIComponent(id)}/withdraw`, { method: 'POST', signal }) as unknown
      if (!record(value) || !application(value.application)) throw new Error('券商资格撤回响应格式无效。')
      return value.application
    },
    async adminList(status = 'submitted', signal) {
      const value = await transport(`/api/rewrite/v1/admin/broker-access-applications?status=${encodeURIComponent(status)}`, { cache: 'no-store', signal }) as unknown
      if (!record(value) || !Array.isArray(value.items) || !value.items.every(adminApplication)) throw new Error('管理员券商资格响应格式无效。')
      return value.items as AdminBrokerAccessApplication[]
    },
    async review(id, decision, reason, signal) {
      if (!/^bra_[A-Za-z0-9_-]{16,48}$/.test(id) || !['approved', 'rejected'].includes(decision) || !reason.trim()) throw new Error('券商资格审核字段无效。')
      const value = await transport(`/api/rewrite/v1/admin/broker-access-applications/${encodeURIComponent(id)}/review`, { method: 'POST', body: JSON.stringify({ decision, reason }), signal }) as unknown
      if (!record(value) || !adminApplication(value.application)) throw new Error('管理员券商审核响应格式无效。')
      return value.application as AdminBrokerAccessApplication
    },
  }
}

export const brokerAccessApi = createBrokerAccessApi()
