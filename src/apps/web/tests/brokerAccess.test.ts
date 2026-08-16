import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { createBrokerAccessApi } from '../src/api/brokerAccess.ts'

const base = {
  id: 'bra_123456789012345678', provider: 'ibkr', status: 'submitted', request_reason: null, decision_reason: null,
  created_at: '2026-08-15T10:00:00+00:00', updated_at: '2026-08-15T10:00:00+00:00', reviewed_at: null, withdrawn_at: null,
  eligibility_only: true, broker_account_created: false, execution_enabled: false,
} as const

test('admin broker access decoder accepts server-only review fields without widening user DTO', async () => {
  const api = createBrokerAccessApi(async (path) => path.endsWith('/readiness') ? { can_apply: true, membership_eligible: true, telegram_ready: true, requires_telegram: true, providers: ['futu_moomoo', 'tiger', 'ibkr', 'webull', 'longbridge'], reason: null, eligibility_only: true, broker_account_created: false, execution_enabled: false } : path.includes('/admin/') ? { items: [{ ...base, user_id: 7, user_email: 'masked@example.com', user_display_name: '用户', reviewed_by: null, reviewer_email: null }] } : { items: [base] })
  assert.equal((await api.readiness()).can_apply, true)
  assert.equal((await api.list()).length, 1)
  assert.equal((await api.adminList()).length, 1)
})

test('broker readiness is server authoritative and rejects inconsistent or widened payloads', async () => {
  const valid = { can_apply: false, membership_eligible: true, telegram_ready: false, requires_telegram: true, providers: ['futu_moomoo', 'tiger', 'ibkr', 'webull', 'longbridge'], reason: '需要 Telegram', eligibility_only: true, broker_account_created: false, execution_enabled: false }
  assert.equal((await createBrokerAccessApi(async () => valid).readiness()).reason, '需要 Telegram')
  await assert.rejects(() => createBrokerAccessApi(async () => ({ ...valid, can_apply: true })).readiness(), /响应格式无效/)
  await assert.rejects(() => createBrokerAccessApi(async () => ({ ...valid, secret: 'leak' })).readiness(), /响应格式无效/)
})

test('broker access decoders reject missing, extra, and cross-scope fields', async () => {
  const admin = { ...base, user_id: 7, user_email: 'masked@example.com', user_display_name: '用户', reviewed_by: null, reviewer_email: null }
  const api = createBrokerAccessApi(async (path) => {
    if (path.includes('/admin/')) return { items: [{ ...admin, leaked: true }] }
    return { items: [{ ...base, user_id: 7 }] }
  })
  await assert.rejects(() => api.list(), /响应格式无效/)
  await assert.rejects(() => api.adminList(), /管理员券商资格响应格式无效/)
})

test('trade keeps pending broker intent across unknown responses and clears only known rejection', () => {
  const page = readFileSync(new URL('../src/pages/TradePage.tsx', import.meta.url), 'utf8')
  assert.match(page, /BROKER_ACCESS_PENDING_KEY/)
  assert.match(page, /sessionStorage\.setItem\(BROKER_ACCESS_PENDING_KEY, JSON\.stringify\(intent\)\)/)
  assert.match(page, /相同申请正文会复用原请求编号安全重试/)
  assert.match(page, /isBrokerAccessRejection\(error\)/)
  assert.match(page, /资格历史暂时无法读取，未将失败当作空历史/)
  assert.match(page, /brokerAccessApi\.readiness\(\)/)
  assert.doesNotMatch(page, /brokerCatalog\.filter\(\(broker\) => broker\.connection_available\)/)
})
