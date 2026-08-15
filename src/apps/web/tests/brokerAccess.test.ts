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
  const api = createBrokerAccessApi(async (path) => path.includes('/admin/') ? { items: [{ ...base, user_id: 7, user_email: 'masked@example.com', user_display_name: '用户', reviewed_by: null, reviewer_email: null }] } : { items: [base] })
  assert.equal((await api.list()).length, 1)
  assert.equal((await api.adminList()).length, 1)
})

test('trade keeps pending broker intent across unknown responses and clears only known rejection', () => {
  const page = readFileSync(new URL('../src/pages/TradePage.tsx', import.meta.url), 'utf8')
  assert.match(page, /BROKER_ACCESS_PENDING_KEY/)
  assert.match(page, /sessionStorage\.setItem\(BROKER_ACCESS_PENDING_KEY, JSON\.stringify\(intent\)\)/)
  assert.match(page, /相同申请正文会复用原请求编号安全重试/)
  assert.match(page, /isBrokerAccessRejection\(error\)/)
  assert.match(page, /资格历史暂时无法读取，未将失败当作空历史/)
})
