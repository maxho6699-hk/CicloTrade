import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const client = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const shell = readFileSync(new URL('../src/components/AppShell.tsx', import.meta.url), 'utf8')
const login = readFileSync(new URL('../src/pages/LoginPage.tsx', import.meta.url), 'utf8')
const page = readFileSync(new URL('../src/pages/AdminPage.tsx', import.meta.url), 'utf8')

test('super admin console uses the exact guarded API contract', () => {
  for (const path of ['/admin/overview', '/admin/users', '/admin/payments/manual-claims', '/admin/brokers', '/admin/audit']) assert.match(client, new RegExp(path.replaceAll('/', '\\/')))
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
