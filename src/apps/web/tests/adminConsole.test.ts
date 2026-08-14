import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  validAdminComputeEvidenceHistory,
  validAdminComputeEvidenceLatest,
  validAdminComputeEvidenceStatus,
  validAdminReferralAnalytics,
  validAdminReferralCoupon,
  validAdminReferralPolicy,
  validAdminReferralWithdrawal,
  validAdminReferralWithdrawalReceipt,
} from '../src/api/client.ts'

const client = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const shell = readFileSync(new URL('../src/components/AppShell.tsx', import.meta.url), 'utf8')
const login = readFileSync(new URL('../src/pages/LoginPage.tsx', import.meta.url), 'utf8')
const page = readFileSync(new URL('../src/pages/AdminPage.tsx', import.meta.url), 'utf8')
const computeEvidenceStart = client.indexOf('export interface AdminComputeEvidenceStatus')
const computeEvidenceEnd = client.indexOf('export async function reviewAdminManualClaim')
const computeEvidenceClient = client.slice(computeEvidenceStart, computeEvidenceEnd)

test('super admin console uses the exact guarded API contract', () => {
  assert.ok(computeEvidenceStart >= 0 && computeEvidenceEnd > computeEvidenceStart)
  for (const path of ['/admin/overview', '/admin/users', '/admin/payments/manual-claims', '/admin/referrals/withdrawals', '/admin/brokers', '/admin/audit', '/admin/compute-evidence/status', '/admin/compute-evidence/latest', '/admin/compute-evidence/history']) assert.match(client, new RegExp(path.replaceAll('/', '\\/')))
  assert.match(client, /payments\/manual-claims\/\$\{id\}\/review/)
  assert.match(client, /method: 'POST'/)
  assert.match(client, /\/admin\/user-auto-trading/)
  assert.match(client, /method: 'PUT'/)
  assert.match(client, /admin_role: 'super_admin' \| null/)
  assert.match(app, /workspace\.user\?\.admin_role !== 'super_admin'/)
  assert.match(app, /<SuperAdminRoute><AdminPage \/><\/SuperAdminRoute>/)
  assert.match(shell, /const isSuperAdmin = workspace\.user\?\.admin_role === 'super_admin'/)
  assert.match(login, /'\/admin'/)
})

test('admin actions preserve human-review, masking, and simulation boundaries', () => {
  assert.match(page, /待人工审核/)
  assert.match(page, /不执行券商下单/)
  assert.match(page, /maskBrokerAccount/)
  assert.match(page, /暂停用户实盘服务/)
  assert.match(page, /恢复用户实盘服务/)
  assert.match(page, /type="password"/)
  assert.match(client, /decision: 'approve' \| 'reject'\r?\n  password: string/)
  assert.match(client, /actor_display\?: string/)
  assert.match(page, /item\.actor_display/)
  assert.doesNotMatch(page, /item\.user_email/)
  assert.match(page, /claim-review-password/)
  assert.match(page, /推广提现队列/)
  assert.match(page, /批准与付款确认必须由不同管理员完成/)
  assert.match(page, /withdrawal-admin-password/)
  assert.match(client, /headers: \{ 'Idempotency-Key': idempotencyKey \}/)
  for (const path of ['/admin/referrals/policy', '/admin/referrals/coupons', '/admin/referrals/analytics']) assert.match(client, new RegExp(path.replaceAll('/', '\\/')))
  assert.match(client, /function validAdminReferralPolicy/)
  assert.match(client, /function validAdminReferralCoupon/)
  assert.match(client, /function validAdminReferralAnalytics/)
  assert.match(page, /推广政策与奖金阶梯/)
  assert.match(page, /优惠码管理/)
  assert.match(page, /推广归因仪表盘/)
  for (const field of ['minimum_final_amount_minor', 'commission_cap_minor', 'withdrawal_paused', 'max_discount_minor', 'min_spend_minor', 'total_use_limit', 'per_user_limit']) assert.match(page, new RegExp(field))
  for (const field of ['coupon_code', 'campaign', 'status', 'promotion_type', 'started_at', 'ended_at']) assert.match(client, new RegExp(field))
  for (const copy of ['最高优惠（HKD 分，可留空）', '最低消费（HKD 分）', '总使用上限', '单用户上限', '创建后立即启用']) assert.match(page, new RegExp(copy.replace(/[（）]/g, '\\$&')))
  assert.match(page, /当前筛选没有符合条件的推广订单/)
  assert.match(page, /明细最多显示前 50 笔/)
  assert.match(page, /type="datetime-local"/)
  assert.match(page, /moment\.toISOString\(\)/)
  assert.match(page, /暂无优惠码；可在上方创建。/)
  assert.match(readFileSync(new URL('../src/styles/operations.css', import.meta.url), 'utf8'), /min-height: 44px/)
  assert.doesNotMatch(page, /生产环境激活|启用生产交易/)
  assert.match(page, /策略研究收据隔离区/)
  assert.match(page, /刷新收据/)
  assert.match(page, /不可执行、不可推送、不可对用户显示/)
  assert.doesNotMatch(page, /晋升候选|执行策略|推送策略/)
  for (const forbidden of ['payload_json', 'storage_path', 'lease_token', 'shared_secret', 'source_worker_id', 'job_id', 'receipt_key', 'package_id']) assert.doesNotMatch(computeEvidenceClient, new RegExp(forbidden))
})

test('compute evidence client rejects authority drift, extra fields, and malformed hashes', () => {
  const authority = { publication_ceiling: 'shadow', research_only: true, actionable: false, user_visible: false } as const
  const item = {
    publication_state: 'quarantine', received_at: '2026-08-12T12:00:00Z', completed_at: '2026-08-12T11:59:00Z',
    candidate_id: 'candidate-1', candidate_version: 'v1', market: 'US', instrument_family: 'equity', symbols: ['AAPL'],
    candidate_status: 'shadow', manifest_sha256: 'a'.repeat(64), result_sha256: 'b'.repeat(64), package_sha256: 'c'.repeat(64),
    artifact_count: 2, research_only: true, actionable: false, user_visible: false,
  }
  const status = { ...authority, available: true, counts: { quarantine: 1, shadow: 0 }, last_received_at: '2026-08-12T12:00:00Z' }
  const latest = { ...authority, available: true, evidence: item }
  const history = { ...authority, available: true, limit: 20, items: [item] }
  assert.equal(validAdminComputeEvidenceStatus(status), true)
  assert.equal(validAdminComputeEvidenceLatest(latest), true)
  assert.equal(validAdminComputeEvidenceHistory(history), true)
  assert.equal(validAdminComputeEvidenceStatus({ ...status, actionable: true }), false)
  assert.equal(validAdminComputeEvidenceLatest({ ...latest, source_worker_id: 'private' }), false)
  assert.equal(validAdminComputeEvidenceHistory({ ...history, items: [{ ...item, package_sha256: 'bad' }] }), false)
})

test('high-risk admin dialogs trap focus and restore the trigger', () => {
  assert.match(page, /modalTriggerRef = useRef<HTMLButtonElement \| null>/)
  assert.match(page, /document\.addEventListener\('keydown', handleKeyDown, true\)/)
  assert.match(page, /document\.addEventListener\('focusin', handleFocusIn, true\)/)
  assert.match(page, /event\.key === 'Escape'/)
  assert.match(page, /event\.key !== 'Tab'/)
  assert.match(page, /trigger\?\.focus\(\)/)
  assert.equal((page.match(/ref=\{modalRef\}/g) ?? []).length, 4)
  assert.match(page, /focusNoticeAfterLoadRef\.current = true/)
  assert.match(page, /noticeRef\.current\?\.focus\(\)/)
  assert.match(page, /ref=\{noticeRef\}/)
  assert.match(client, /settlement_reference_masked\?: string \| null/)
  assert.doesNotMatch(client, /\n  settlement_reference\?: string \| null/)
})

test('referral withdrawal client rejects leaked or malformed admin fields', () => {
  const receipt = {
    withdrawal_id: `WDR${'A'.repeat(24)}`,
    amount_minor: 20_000,
    currency: 'HKD',
    status: 'submitted',
    submitted_at: '2026-08-14T10:00:00+08:00',
    reviewed_at: null,
    approved_at: null,
    paid_at: null,
    rejection_reason: null,
  } as const
  const row = { ...receipt, user_reference: `USR${'B'.repeat(24)}`, user_masked: 'u***@e***' }
  assert.equal(validAdminReferralWithdrawalReceipt(receipt), true)
  assert.equal(validAdminReferralWithdrawal(row), true)
  assert.equal(validAdminReferralWithdrawal({ ...row, user_id: 42 }), false)
  assert.equal(validAdminReferralWithdrawalReceipt({ ...receipt, payout_reference: 'secret' }), false)
  assert.equal(validAdminReferralWithdrawalReceipt({ ...receipt, amount_minor: -1 }), false)
})

test('promotion administration decoders reject malformed tiers, coupons, and financial dashboards', () => {
  const policy = {
    version: 4,
    policy: {
      commission_rate_bps: 1000, referral_discount_bps: 500, minimum_final_amount_minor: 1,
      commission_cap_minor: 50_000, hold_days: 30, withdrawal_min_minor: 20_000,
      withdrawal_max_minor: 500_000, withdrawal_daily_limit: 3, withdrawal_monthly_limit: 2,
      withdrawal_open_limit: 1, withdrawal_cooldown_days: 0,
      automatic_payout_review_threshold_minor: 1_000_000, withdrawal_paused: false,
      bonus_enabled: true,
      bonus_tiers: [{ qualified_count: 5, cumulative_amount_minor: 10_000 }],
    },
  } as const
  const coupon = {
    coupon_id: `CPN${'A'.repeat(24)}`, code: 'WELCOME10', campaign_name: 'Launch',
    discount_type: 'percent', discount_value: 1000, max_discount_minor: null,
    min_spend_minor: 0, total_use_limit: 100, per_user_limit: 1,
    applicable_plans: ['标准版'], applicable_cycles: ['monthly'],
    starts_at: '2026-08-14T10:00:00+08:00', expires_at: '2026-08-21T10:00:00+08:00',
    enabled: true, version: 1, created_at: '2026-08-14T10:00:00+08:00', updated_at: '2026-08-14T10:00:00+08:00',
  } as const
  const item = {
    coupon_code: 'WELCOME10', campaign: 'Launch', customer: `USR${'B'.repeat(24)}`,
    order_id: 'ORD-001', status: 'paid', list_price_minor: 29_800,
    coupon_discount_minor: 2_980, referral_discount_minor: 1_341,
    paid_revenue_minor: 25_479, refund_or_chargeback_minor: 0, net_revenue_minor: 25_479,
    discount_cost_minor: 4_321, created_at: '2026-08-14T10:00:00+08:00',
    paid_at: '2026-08-14T10:00:00+08:00', refunded_at: null,
    promotion_type: 'stacked', commission_cost_minor: 2_547, bonus_cost_minor: 1_000,
    promotion_cost_minor: 7_868,
  } as const
  const analytics = {
    items: [item],
    summary: {
      orders: 1, list_price_minor: 29_800, coupon_cost_minor: 2_980,
      referral_cost_minor: 1_341, paid_revenue_minor: 25_479,
      refund_or_chargeback_minor: 0, net_revenue_minor: 25_479, customers: 1,
      coupon_only_orders: 0, referral_only_orders: 0, stacked_orders: 1,
      unattributed_orders: 0, commission_cost_minor: 2_547, bonus_cost_minor: 1_000,
      promotion_cost_minor: 7_868,
    },
  } as const
  assert.equal(validAdminReferralPolicy(policy), true)
  assert.equal(validAdminReferralCoupon(coupon), true)
  assert.equal(validAdminReferralAnalytics(analytics), true)
  assert.equal(validAdminReferralPolicy({ ...policy, policy: { ...policy.policy, bonus_tiers: [{ qualified_count: 5, cumulative_amount_minor: -1 }] } }), false)
  assert.equal(validAdminReferralCoupon({ ...coupon, applicable_cycles: ['weekly'] }), false)
  assert.equal(validAdminReferralAnalytics({ ...analytics, summary: { ...analytics.summary, private_revenue: 1 } }), false)
})

test('coupon pauses require modal confirmation and retain their idempotency key for a safe retry', () => {
  assert.match(page, /couponPauseTarget/)
  assert.match(page, /确认暂停优惠码/)
  assert.match(page, /onSubmit=\{confirmCouponPause\}/)
  assert.match(page, /setCouponPauseKey\(crypto\.randomUUID\(\)\)/)
  assert.match(page, /pauseAdminReferralCoupon\(couponPauseTarget\.coupon_id, couponPauseTarget\.version, couponPausePassword, couponPauseKey\)/)
  assert.match(page, /响应不确定时可使用同一请求安全重试/)
  assert.match(page, /function closeCouponPause\(\)[\s\S]*setCouponPauseKey\(''\)/)
})
