import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const root = path.resolve(import.meta.dirname, '..')
const page = fs.readFileSync(path.join(root, 'src/pages/PromotionCenterPage.tsx'), 'utf8')
const styles = fs.readFileSync(path.join(root, 'src/styles/promotion.css'), 'utf8')
const app = fs.readFileSync(path.join(root, 'src/App.tsx'), 'utf8')
const shell = fs.readFileSync(path.join(root, 'src/components/AppShell.tsx'), 'utf8')

test('promotion dashboard exposes every authoritative balance and complete histories', () => {
  for (const field of ['earned_total_minor', 'withdrawable_minor', 'pending_minor', 'reserved_minor', 'paid_minor', 'debt_minor']) assert.match(page, new RegExp(field))
  for (const field of ['recharge_id', 'gross_amount_minor', 'rate_bps', 'earned_amount_minor', 'clawed_back_minor', 'settled_at']) assert.match(page, new RegExp(field))
  assert.match(page, /portal\.withdrawals\.map/)
  assert.match(page, /portal\.timeline\.length \? portal\.timeline\.map/)
  assert.match(page, /暂无提现记录/)
  assert.match(page, /暂无审计记录/)
})

test('promotion states, copy feedback, and audit tones remain truthful', () => {
  assert.match(page, /推广计划暂未开放/)
  assert.match(page, /当前账户无推广权限/)
  assert.match(page, /`referral:\$\{referral\.referral_id\}`/)
  assert.match(page, /withdrawal_rejected[\s\S]*?'danger'/)
  assert.doesNotMatch(page, /index === portal\.timeline\.length - 1/)
})

test('promotion UI is routed, responsive, touch safe, and avoids desktop-table overflow', () => {
  assert.match(app, /path="\/promotion"/)
  assert.match(shell, /to: '\/promotion'/)
  assert.match(styles, /\.promotion-balance-grid \{[\s\S]*?repeat\(6/)
  assert.match(styles, /\.promotion-id-copy \{[\s\S]*?min-height: 44px/)
  assert.match(styles, /@media \(max-width: 600px\)[\s\S]*?\.promotion-record \{ grid-template-columns: 1fr/)
  assert.doesNotMatch(styles, /min-width:\s*640px/)
})
