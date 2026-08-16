import assert from 'node:assert/strict'
import test from 'node:test'
import { createReferralApi, decodeReferralPortal, type ReferralPortal } from '../src/api/promotion.ts'
import { withdrawalIdempotencyKey } from '../src/domain/referralWithdrawal.ts'
import { localizeText, setRuntimeLocale } from '../src/i18n/runtime.ts'

const portal: ReferralPortal = {
  program: { enabled: true, cutover_at: '2026-08-12T12:00:00+08:00', currency: 'HKD', policy_version: 'membership-promotions-v2:1', hold_days: 14, minimum_withdrawal_minor: 10000, maximum_withdrawal_minor: 500000, withdrawal_paused: false, referral_discount_bps: 500, referrer_commission_bps: 1000, subsequent_order_commission_bps: 0, bonus_enabled: true, bonus_tiers: [{ qualified_count: 3, cumulative_amount_minor: 5000 }], bonus_progress: { period_key: '2026-08', qualified_count: 1, current_target_minor: 0, earned_amount_minor: 0, clawed_back_minor: 0, net_amount_minor: 0, status: 'not_qualified' } },
  invite: { invite_code: 'CT8K2M9Q', invite_link: 'https://app.example/login?ref=CT8K2M9Q', qr_payload: 'https://app.example/login?ref=CT8K2M9Q' },
  balances: { earned_total_minor: 12000, pending_minor: 1000, withdrawable_minor: 10000, reserved_minor: 0, paid_minor: 0, clawed_back_total_minor: 0, debt_minor: 0 },
  trends: { windows: [7, 30, 90].map((days) => ({ days: days as 7 | 30 | 90, visits: 1, registrations: 1, settled_orders: 1, gross_amount_minor: 100, earned_amount_minor: 20, clawed_back_minor: 0 })) },
  funnel: { visits_30d: 1, registrations_30d: 1, settled_referrals_30d: 1, registration_rate_bps: 10000, settlement_rate_bps: 10000 },
  referrals: [{ referral_id: 'RFR8A1F3', user_masked: 'm***@e***.com', joined_at: '2026-08-12T12:00:00+08:00', settled_orders: 1, last_settled_at: null }],
  commissions: [{ commission_id: 'COM3C9E2', recharge_id: 'RCH7V4P8', commission_type: 'initial_purchase', gross_amount_minor: 100, rate_bps: 1000, earned_amount_minor: 10, clawed_back_minor: 0, net_amount_minor: 10, status: 'pending', settled_at: '2026-08-12T12:00:00+08:00', available_at: '2026-08-26T12:00:00+08:00' }],
  withdrawals: [], timeline: [{ event_id: 'AUD4F9C2', event_type: 'registration', public_reference: 'RFR8A1F3', amount_minor: null, occurred_at: '2026-08-12T12:00:00+08:00' }],
  withdrawal_eligibility: { status: 'eligible', reason_code: 'eligible', reason: '当前提现条件已满足。', min_minor: 10000, max_minor: 500000, available_minor: 10000, next_eligible_at: null, evaluated_at: '2026-08-12T12:00:00+08:00' },
}

test('referral portal decoder accepts the exact canonical HKT/minor contract', () => {
  assert.deepEqual(decodeReferralPortal(portal), portal)
  assert.throws(() => decodeReferralPortal({ ...portal, provider: 'private' }), /响应格式无效/)
  assert.throws(() => decodeReferralPortal({ ...portal, balances: { ...portal.balances, withdrawable_minor: 1.2 } }), /结算字段无效/)
  assert.throws(() => decodeReferralPortal({ ...portal, referrals: [{ ...portal.referrals[0], joined_at: '2026-08-12T12:00:00Z' }] }), /邀请字段无效/)
  assert.throws(() => decodeReferralPortal({ ...portal, program: { ...portal.program, bonus_progress: { ...portal.program.bonus_progress, status: '' } } }), /推广计划字段无效/)
})

test('withdrawal client sends only HKD integer minor units and idempotency key', async () => {
  let captured: RequestInit | undefined
  const api = createReferralApi(async (_path, init) => {
    captured = init
    return { withdrawal: { withdrawal_id: 'WDR4F9C2', amount_minor: 10000, currency: 'HKD', status: 'submitted', submitted_at: '2026-08-12T12:00:00+08:00', reviewed_at: null, approved_at: null, paid_at: null, rejection_reason: null }, balances: { withdrawable_minor: 0, reserved_minor: 10000, debt_minor: 0 } }
  })
  await api.requestWithdrawal({ amount_minor: 10000, currency: 'HKD' }, 'idem-key-123')
  const headers = captured?.headers
  assert.ok(headers)
  assert.equal(headers instanceof Headers ? headers.get('Idempotency-Key') : (headers as Record<string, string>)['Idempotency-Key'], 'idem-key-123')
  assert.equal(captured?.body, '{"amount_minor":10000,"currency":"HKD"}')
  await assert.rejects(() => api.requestWithdrawal({ amount_minor: 1.5, currency: 'HKD' }, 'idem-key-123'), /申请字段无效/)
})

test('a response-lost withdrawal retry reuses its idempotency key until amount changes', () => {
  const first = withdrawalIdempotencyKey(10000, null)
  for (const partialInput of [100, 1000]) assert.equal(withdrawalIdempotencyKey(partialInput, first).amountMinor, partialInput)
  const retry = withdrawalIdempotencyKey(10000, first)
  const changed = withdrawalIdempotencyKey(20000, first)
  assert.equal(retry.key, first.key)
  assert.notEqual(changed.key, first.key)
})

test('referral money states have complete Traditional Chinese runtime copy', () => {
  setRuntimeLocale('zh-Hant')
  assert.deepEqual(
    ['已付款', '已批准', '已回退', '部分回退', '佣金回退', '已人工付款', '正在提交…', '冻结佣金'].map(localizeText),
    ['已撥款', '已核准', '已追回', '部分追回', '傭金追回', '已人工撥款', '正在送出…', '凍結傭金'],
  )
  assert.deepEqual(
    ['佣金', '累计佣金', '首单佣金流水', '佣金冻结、退款追回和提现状态均以平台资金账本为准。', '后续佣金将优先抵扣', '已得佣金', '佣金比例', '原始佣金', '净佣金'].map(localizeText),
    ['傭金', '累計傭金', '首單傭金流水', '傭金凍結、退款追回和提現狀態均以平台資金帳本為準。', '後續傭金將優先抵扣', '已得傭金', '傭金比例', '原始傭金', '淨傭金'],
  )
  setRuntimeLocale('zh-Hans')
})
