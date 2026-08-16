import { authenticatedJsonRequest } from './client.ts'

export type AccountPolicyState = 'configured' | 'not_configured' | 'compatibility_only'

export interface AccountOverview {
  account: { public_id: string; display_name: string }
  membership: { state: 'available' | 'compatibility_only'; plan: string; subscription_expire: string | null }
  telegram: { state: string; policy_state: AccountPolicyState }
  brokers: { state: string; items: Array<{ provider: string; account_alias: string; mode: string; status: string; active: boolean }> }
  agent_levels: Record<'L0' | 'L1' | 'L2' | 'L3' | 'L4', { level: number | null; policy_state: AccountPolicyState }>
  runtime: { auto_live: 'not_ready' | 'ready' | 'paused' }
}

export interface AppearanceManifest {
  public_id: string
  skin_id: string
  asset_version: string
  manifest_sha256: string
  assets: Record<string, unknown>
  entitled: boolean
  rank: number
  created_at: string
}

export interface AppearancePayload {
  current: { public_id: string | null; skin_id: string | null; asset_version: string | null; manifest_sha256: string | null; source: 'selected' | 'fallback' | 'unavailable' }
  items: AppearanceManifest[]
}

export interface AccountContent {
  public_id: string
  content_key: string
  content_version: number
  content: Record<string, unknown>
  content_sha256: string
  expires_at: string | null
  created_at: string
}

export interface AccountMemory {
  public_id: string
  memory_key: string
  memory_json: string
  expires_at: string | null
  created_at: string
}

export interface DataAuthorization {
  data_kind: string
  authorized: boolean
  policy_state: AccountPolicyState
  receipt_public_id?: string
}

export interface AuthorizationReceipt {
  public_id: string
  data_kind: string
  policy_type: string
  policy_version: number
  action: 'granted' | 'revoked'
}

function record(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === 'object' && !Array.isArray(value) }
function exact(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return record(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key))
}
function timestamp(value: unknown): value is string { return typeof value === 'string' && Number.isFinite(Date.parse(value)) }
function nullableTimestamp(value: unknown): value is string | null { return value === null || timestamp(value) }
function policyState(value: unknown): value is AccountPolicyState { return value === 'configured' || value === 'not_configured' || value === 'compatibility_only' }
function validLevel(value: unknown): value is { level: number | null; policy_state: AccountPolicyState } {
  return exact(value, ['level', 'policy_state']) && (value.level === null || (typeof value.level === 'number' && Number.isInteger(value.level) && value.level >= 0 && value.level <= 4)) && policyState(value.policy_state)
}

export function validAccountOverview(value: unknown): value is AccountOverview {
  if (!exact(value, ['account', 'membership', 'telegram', 'brokers', 'agent_levels', 'runtime']) || !exact(value.account, ['public_id', 'display_name']) || !exact(value.membership, ['state', 'plan', 'subscription_expire']) || !exact(value.telegram, ['state', 'policy_state']) || !exact(value.brokers, ['state', 'items']) || !exact(value.runtime, ['auto_live'])) return false
  const levels = value.agent_levels
  return typeof value.account.public_id === 'string' && /^usr_[A-Za-z0-9_-]{24}$/.test(value.account.public_id)
    && typeof value.account.display_name === 'string' && value.account.display_name.length > 0
    && (value.membership.state === 'available' || value.membership.state === 'compatibility_only') && typeof value.membership.plan === 'string' && nullableTimestamp(value.membership.subscription_expire)
    && typeof value.telegram.state === 'string' && policyState(value.telegram.policy_state)
    && typeof value.brokers.state === 'string' && Array.isArray(value.brokers.items) && value.brokers.items.every((item) => exact(item, ['provider', 'account_alias', 'mode', 'status', 'active']) && typeof item.provider === 'string' && typeof item.account_alias === 'string' && typeof item.mode === 'string' && typeof item.status === 'string' && typeof item.active === 'boolean')
    && exact(levels, ['L0', 'L1', 'L2', 'L3', 'L4']) && validLevel(levels.L0) && validLevel(levels.L1) && validLevel(levels.L2) && validLevel(levels.L3) && validLevel(levels.L4)
    && (value.runtime.auto_live === 'not_ready' || value.runtime.auto_live === 'ready' || value.runtime.auto_live === 'paused')
}

export function validAppearanceCurrent(value: unknown): value is AppearancePayload['current'] {
  return exact(value, ['public_id', 'skin_id', 'asset_version', 'manifest_sha256', 'source']) && (value.public_id === null || typeof value.public_id === 'string') && (value.skin_id === null || typeof value.skin_id === 'string') && (value.asset_version === null || typeof value.asset_version === 'string') && (value.manifest_sha256 === null || /^[a-f0-9]{64}$/.test(String(value.manifest_sha256))) && ['selected', 'fallback', 'unavailable'].includes(String(value.source))
}

export function validAppearanceList(value: unknown): value is { items: AppearanceManifest[] } {
  return exact(value, ['items']) && Array.isArray(value.items) && value.items.every((item) => exact(item, ['public_id', 'skin_id', 'asset_version', 'manifest_sha256', 'assets', 'entitled', 'rank', 'created_at']) && typeof item.public_id === 'string' && typeof item.skin_id === 'string' && typeof item.asset_version === 'string' && typeof item.manifest_sha256 === 'string' && /^[a-f0-9]{64}$/.test(item.manifest_sha256) && record(item.assets) && typeof item.entitled === 'boolean' && typeof item.rank === 'number' && Number.isSafeInteger(item.rank) && timestamp(item.created_at))
}

export function validAppearancePayload(value: unknown): value is AppearancePayload {
  return exact(value, ['current', 'items']) && validAppearanceCurrent(value.current) && validAppearanceList({ items: value.items })
}

export function validAccountContent(value: unknown): value is AccountContent {
  return exact(value, ['public_id', 'content_key', 'content_version', 'content', 'content_sha256', 'expires_at', 'created_at']) && typeof value.public_id === 'string' && typeof value.content_key === 'string' && typeof value.content_version === 'number' && Number.isSafeInteger(value.content_version) && value.content_version > 0 && record(value.content) && typeof value.content_sha256 === 'string' && /^[a-f0-9]{64}$/.test(value.content_sha256) && nullableTimestamp(value.expires_at) && timestamp(value.created_at)
}

export function validContentList(value: unknown): value is { items: AccountContent[] } {
  return exact(value, ['items']) && Array.isArray(value.items) && value.items.every(validAccountContent)
}

export function validAccountMemory(value: unknown): value is AccountMemory {
  return exact(value, ['public_id', 'memory_key', 'memory_json', 'expires_at', 'created_at']) && typeof value.public_id === 'string' && /^mem_[A-Za-z0-9_-]{24}$/.test(value.public_id) && typeof value.memory_key === 'string' && typeof value.memory_json === 'string' && nullableTimestamp(value.expires_at) && timestamp(value.created_at)
}

export function validMemoryList(value: unknown): value is { items: AccountMemory[] } {
  return exact(value, ['items']) && Array.isArray(value.items) && value.items.every(validAccountMemory)
}

export function validDataAuthorization(value: unknown): value is DataAuthorization {
  return (exact(value, ['data_kind', 'authorized', 'policy_state']) || exact(value, ['data_kind', 'authorized', 'policy_state', 'receipt_public_id'])) && typeof value.data_kind === 'string' && typeof value.authorized === 'boolean' && policyState(value.policy_state) && (value.receipt_public_id === undefined || typeof value.receipt_public_id === 'string')
}

export function validAuthorizationReceipt(value: unknown): value is AuthorizationReceipt {
  return exact(value, ['public_id', 'data_kind', 'policy_type', 'policy_version', 'action']) && typeof value.public_id === 'string' && typeof value.data_kind === 'string' && typeof value.policy_type === 'string' && typeof value.policy_version === 'number' && Number.isSafeInteger(value.policy_version) && value.policy_version > 0 && (value.action === 'granted' || value.action === 'revoked')
}

export function createIdempotencyKey(prefix: string): string { return `${prefix}-${crypto.randomUUID()}` }

async function decode<T>(path: string, validator: (value: unknown) => value is T, init?: RequestInit): Promise<T> {
  const value = await authenticatedJsonRequest<unknown>(path, init)
  if (!validator(value)) throw new Error('账户中心响应格式无效。')
  return value
}

export const accountCenterApi = {
  overview: () => decode('/api/rewrite/v1/account', validAccountOverview, { cache: 'no-store' }),
  appearance: async () => {
    const [current, list] = await Promise.all([
      decode('/api/rewrite/v1/account/appearance', validAppearanceCurrent, { cache: 'no-store' }),
      decode('/api/rewrite/v1/account/appearances', validAppearanceList, { cache: 'no-store' }),
    ])
    return { current, items: list.items }
  },
  content: () => decode('/api/rewrite/v1/account/content', validContentList, { cache: 'no-store' }),
  selectAppearance: (manifestPublicId: string) => decode('/api/rewrite/v1/account/appearance/select', (value): value is { public_id: string; manifest_public_id: string; skin_id: string; asset_version: string } => exact(value, ['public_id', 'manifest_public_id', 'skin_id', 'asset_version']) && Object.values(value).every((item) => typeof item === 'string'), { method: 'POST', headers: { 'Idempotency-Key': createIdempotencyKey('appearance-select') }, body: JSON.stringify({ manifest_public_id: manifestPublicId }) }),
  memories: () => decode('/api/rewrite/v1/account/memory', validMemoryList, { cache: 'no-store' }),
  saveMemory: (memoryKey: string, value: Record<string, unknown>, expiresAt: string | null) => decode('/api/rewrite/v1/account/memory', (payload): payload is { public_id: string; memory_key: string; expires_at: string | null } => exact(payload, ['public_id', 'memory_key', 'expires_at']) && typeof payload.public_id === 'string' && typeof payload.memory_key === 'string' && nullableTimestamp(payload.expires_at), { method: 'POST', headers: { 'Idempotency-Key': createIdempotencyKey('memory') }, body: JSON.stringify({ memory_key: memoryKey, value, expires_at: expiresAt }) }),
  deleteMemory: (publicId: string) => decode(`/api/rewrite/v1/account/memory/${encodeURIComponent(publicId)}/delete`, (payload): payload is { public_id: string; memory_public_id: string } => exact(payload, ['public_id', 'memory_public_id']) && typeof payload.public_id === 'string' && payload.memory_public_id === publicId, { method: 'POST', headers: { 'Idempotency-Key': createIdempotencyKey('memory-delete') }, body: JSON.stringify({ reason: '用户从账户中心删除' }) }),
  authorization: (dataKind: string) => decode(`/api/rewrite/v1/account/authorizations/${encodeURIComponent(dataKind)}`, validDataAuthorization, { cache: 'no-store' }),
  setAuthorization: (dataKind: string, action: 'granted' | 'revoked', scope: Record<string, unknown>) => decode('/api/rewrite/v1/account/authorizations', validAuthorizationReceipt, { method: 'POST', headers: { 'Idempotency-Key': createIdempotencyKey('authorization') }, body: JSON.stringify({ data_kind: dataKind, action, scope }) }),
}
