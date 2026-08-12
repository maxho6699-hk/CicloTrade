import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  validAdminComputeEvidenceHistory,
  validAdminComputeEvidenceLatest,
  validAdminComputeEvidenceStatus,
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
  for (const path of ['/admin/overview', '/admin/users', '/admin/payments/manual-claims', '/admin/brokers', '/admin/audit', '/admin/compute-evidence/status', '/admin/compute-evidence/latest', '/admin/compute-evidence/history']) assert.match(client, new RegExp(path.replaceAll('/', '\\/')))
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
  assert.equal((page.match(/ref=\{modalRef\}/g) ?? []).length, 2)
  assert.match(page, /focusNoticeAfterLoadRef\.current = true/)
  assert.match(page, /noticeRef\.current\?\.focus\(\)/)
  assert.match(page, /ref=\{noticeRef\}/)
  assert.match(client, /settlement_reference_masked\?: string \| null/)
  assert.doesNotMatch(client, /\n  settlement_reference\?: string \| null/)
})
