import { authenticatedJsonRequest } from './client.ts'
import { createIdempotencyKey } from './accountCenter.ts'

export type NotificationSeverity = 'info' | 'success' | 'warning' | 'error'
export type NotificationDeliveryStatus = 'queued' | 'sending' | 'sent' | 'delivered' | 'failed' | 'skipped'

export interface NotificationTarget { target_kind: string; public_id: string; version: number }
export interface NotificationDelivery { public_id: string; channel: string; status: NotificationDeliveryStatus; error_code: string | null }
export interface NotificationItem {
  public_id: string
  source_kind: string
  source_public_id: string
  source_version: number
  kind: string
  title: string
  body: string
  severity: NotificationSeverity
  target: NotificationTarget | null
  read: boolean
  delivery: NotificationDelivery[]
  created_at: string
}
export type NotificationTargetKind = 'account' | 'membership' | 'settings' | 'notifications' | 'today' | 'discover' | 'research' | 'paper' | 'portfolio' | 'reports' | 'trade' | 'orders' | 'payments' | 'payment' | 'content' | 'memory' | 'appearance'
export interface DeepLinkLocator { kind: NotificationTargetKind; public_id: string; version: number }
export interface DeepLinkResolution { route: string; locator: DeepLinkLocator | null; stale: boolean }
export type NotificationTransport = (path: string, init?: RequestInit) => Promise<unknown>
const TARGET_KINDS: readonly NotificationTargetKind[] = ['account', 'membership', 'settings', 'notifications', 'today', 'discover', 'research', 'paper', 'portfolio', 'reports', 'trade', 'orders', 'payments', 'payment', 'content', 'memory', 'appearance']
const ROUTES = new Set(['/account', '/membership', '/notifications', '/today', '/discover', '/research', '/paper', '/portfolio', '/reports', '/trade'])

function record(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === 'object' && !Array.isArray(value) }
function exact(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return record(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key))
}
function timestamp(value: unknown): value is string { return typeof value === 'string' && Number.isFinite(Date.parse(value)) }

export function validNotificationTarget(value: unknown): value is NotificationTarget {
  return exact(value, ['target_kind', 'public_id', 'version']) && typeof value.target_kind === 'string' && typeof value.public_id === 'string' && /^[a-z][a-z0-9]{1,15}_[A-Za-z0-9_-]{24}$/.test(value.public_id) && typeof value.version === 'number' && Number.isSafeInteger(value.version) && value.version > 0
}

export function validNotificationDelivery(value: unknown): value is NotificationDelivery {
  return exact(value, ['public_id', 'channel', 'status', 'error_code']) && typeof value.public_id === 'string' && typeof value.channel === 'string' && ['queued', 'sending', 'sent', 'delivered', 'failed', 'skipped'].includes(String(value.status)) && (value.error_code === null || typeof value.error_code === 'string')
}

export function validNotificationItem(value: unknown): value is NotificationItem {
  return exact(value, ['public_id', 'source_kind', 'source_public_id', 'source_version', 'kind', 'title', 'body', 'severity', 'target', 'read', 'delivery', 'created_at'])
    && typeof value.public_id === 'string' && /^ntf_[A-Za-z0-9_-]{24}$/.test(value.public_id)
    && typeof value.source_kind === 'string' && typeof value.source_public_id === 'string' && typeof value.source_version === 'number' && Number.isSafeInteger(value.source_version) && value.source_version > 0
    && typeof value.kind === 'string' && typeof value.title === 'string' && typeof value.body === 'string' && ['info', 'success', 'warning', 'error'].includes(String(value.severity))
    && (value.target === null || validNotificationTarget(value.target)) && typeof value.read === 'boolean' && Array.isArray(value.delivery) && value.delivery.every(validNotificationDelivery) && timestamp(value.created_at)
}

export function validNotificationList(value: unknown): value is { items: NotificationItem[] } {
  return exact(value, ['items']) && Array.isArray(value.items) && value.items.every(validNotificationItem)
}

export function validDeepLinkResolution(value: unknown): value is DeepLinkResolution {
  if (!exact(value, ['route', 'locator', 'stale']) || typeof value.route !== 'string' || !ROUTES.has(value.route) || typeof value.stale !== 'boolean') return false
  if (value.locator === null) return value.stale
  return !value.stale && exact(value.locator, ['kind', 'public_id', 'version']) && TARGET_KINDS.includes(value.locator.kind as NotificationTargetKind) && typeof value.locator.public_id === 'string' && /^[a-z][a-z0-9]{1,15}_[A-Za-z0-9_-]{24}$/.test(value.locator.public_id) && typeof value.locator.version === 'number' && Number.isSafeInteger(value.locator.version) && value.locator.version > 0
}

async function decode<T>(transport: NotificationTransport, path: string, validator: (value: unknown) => value is T, init?: RequestInit): Promise<T> {
  const value = await transport(path, init)
  if (!validator(value)) throw new Error('通知中心响应格式无效。')
  return value
}

export function createNotificationsApi(transport: NotificationTransport = authenticatedJsonRequest) {
  return {
    list: () => decode(transport, '/api/rewrite/v1/notifications', validNotificationList, { cache: 'no-store' }),
    markRead: (publicId: string) => decode(transport, `/api/rewrite/v1/notifications/${encodeURIComponent(publicId)}/read`, (value): value is { public_id: string; item_public_id: string } => exact(value, ['public_id', 'item_public_id']) && typeof value.public_id === 'string' && value.item_public_id === publicId, { method: 'POST', headers: { 'Idempotency-Key': createIdempotencyKey('notification-read') } }),
    resolve: (notificationPublicId: string) => decode(transport, '/api/rewrite/v1/notifications/resolve', validDeepLinkResolution, { method: 'POST', body: JSON.stringify({ notification_public_id: notificationPublicId }) }),
  }
}

export const notificationsApi = createNotificationsApi()
