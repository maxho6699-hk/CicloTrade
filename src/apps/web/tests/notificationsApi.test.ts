import assert from 'node:assert/strict'
import test from 'node:test'
import { createNotificationsApi, validDeepLinkResolution, validNotificationItem, validNotificationList } from '../src/api/notifications.ts'

const item = {
  public_id: 'ntf_123456789012345678901234', source_kind: 'test', source_public_id: 'source-1', source_version: 1,
  kind: 'system', title: '账户更新', body: '真实通知内容', severity: 'info', target: null, read: false,
  delivery: [{ public_id: 'dly_123456789012345678901234', channel: 'website', status: 'delivered', error_code: null }],
  created_at: '2026-08-16T00:00:00+00:00',
}

test('notification DTO validator accepts canonical inbox item but rejects fabricated shape', () => {
  assert.equal(validNotificationItem(item), true)
  assert.equal(validNotificationList({ items: [item] }), true)
  assert.equal(validNotificationList({ items: [{ ...item, delivery: [{ ...item.delivery[0], status: 'sent', extra: true }] }] }), false)
})

test('deep-link resolution preserves stale fallback as an explicit state', () => {
  assert.equal(validDeepLinkResolution({ route: '/notifications', locator: null, stale: true }), true)
  assert.equal(validDeepLinkResolution({ route: '/notifications', locator: null, stale: false }), false)
  assert.equal(validDeepLinkResolution({ route: '/account', locator: { kind: 'content', public_id: 'cnt_aaaaaaaaaaaaaaaaaaaaaaaa', version: 2 }, stale: false }), true)
  assert.equal(validDeepLinkResolution({ route: '/account', locator: { kind: 'content', public_id: 'bad', version: 2 }, stale: false }), false)
  assert.equal(validDeepLinkResolution({ route: '/evil', locator: { kind: 'content', public_id: 'cnt_aaaaaaaaaaaaaaaaaaaaaaaa', version: 2 }, stale: false }), false)
})

test('deep-link client sends only the notification id and cannot substitute its stored target', async () => {
  let request: { path: string; init?: RequestInit } | null = null
  const api = createNotificationsApi(async (path, init) => {
    request = { path, init }
    return { route: '/account', locator: { kind: 'content', public_id: 'cnt_aaaaaaaaaaaaaaaaaaaaaaaa', version: 2 }, stale: false }
  })
  await api.resolve('ntf_123456789012345678901234')
  assert.equal(request?.path, '/api/rewrite/v1/notifications/resolve')
  assert.deepEqual(JSON.parse(String(request?.init?.body)), {
    notification_public_id: 'ntf_123456789012345678901234',
  })
})
