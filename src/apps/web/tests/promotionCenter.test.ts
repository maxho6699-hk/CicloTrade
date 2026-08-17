import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const root = path.resolve(import.meta.dirname, '..')
const page = fs.readFileSync(path.join(root, 'src/pages/PromotionCenterPage.tsx'), 'utf8')
const styles = fs.readFileSync(path.join(root, 'src/styles/promotion.css'), 'utf8')
const app = fs.readFileSync(path.join(root, 'src/App.tsx'), 'utf8')
const shell = fs.readFileSync(path.join(root, 'src/components/AppShell.tsx'), 'utf8')
const catalog = fs.readFileSync(path.join(root, 'src/domain/featureCatalog.ts'), 'utf8')

test('promotion dashboard exposes every authoritative balance and complete histories', () => {
  for (const field of ['earned_total_minor', 'withdrawable_minor', 'pending_minor', 'reserved_minor', 'paid_minor', 'debt_minor']) assert.match(page, new RegExp(field))
  for (const field of ['recharge_id', 'gross_amount_minor', 'rate_bps', 'earned_amount_minor', 'clawed_back_minor', 'settled_at']) assert.match(page, new RegExp(field))
  assert.match(page, /portal\.withdrawals\.map/)
  assert.match(page, /portal\.timeline\.length \? portal\.timeline\.map/)
  assert.match(page, /暂无提现记录/)
  assert.match(page, /暂无审计记录/)
})

test('promotion states, copy feedback, and audit tones remain truthful', () => {
  assert.match(page, /aria-label=\{localizeText\('复制邀请记录 ID'\)\}/)
  assert.match(page, /推广计划暂未开放/)
  assert.match(page, /当前账户无推广权限/)
  assert.match(page, /`referral:\$\{referral\.referral_id\}`/)
  assert.match(page, /withdrawal_rejected[\s\S]*?'danger'/)
  assert.doesNotMatch(page, /index === portal\.timeline\.length - 1/)
  assert.match(page, /sessionStorage\.setItem\(WITHDRAWAL_IDEMPOTENCY_STORAGE_KEY/)
  assert.match(page, /刷新后再次提交相同金额仍会安全复用/)
  assert.doesNotMatch(page, /changeAmount[\s\S]{0,400}sessionStorage\.removeItem/)
  assert.match(page, /promotion-state-guide/)
  assert.match(page, /邀请归因/)
  assert.match(page, /佣金冻结/)
  assert.match(page, /提现审核/)
})

test('promotion UI is routed, responsive, touch safe, and avoids desktop-table overflow', () => {
  assert.match(app, /path="\/promotion"/)
  assert.match(catalog, /MORE_NAV_ROUTES = \[[^\]]*'\/promotion'/)
  assert.match(shell, /to="\/more"/)
  assert.match(styles, /\.promotion-balance-grid \{[\s\S]*?repeat\(6/)
  assert.match(styles, /\.promotion-id-copy \{[\s\S]*?min-height: 44px/)
  assert.match(styles, /@media \(max-width: 600px\)[\s\S]*?\.promotion-record \{ grid-template-columns: 1fr/)
  assert.match(styles, /\.promotion-state-guide \{ display: grid; grid-template-columns: repeat\(3,minmax\(0,1fr\)\)/)
  assert.match(styles, /@media \(max-width: 820px\)[\s\S]*?\.promotion-state-guide \{ grid-template-columns: 1fr; \}/)
  assert.doesNotMatch(styles, /min-width:\s*640px/)
})
