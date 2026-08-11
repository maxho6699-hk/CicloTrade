import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const pageSource = readFileSync(new URL('../src/pages/MembershipPage.tsx', import.meta.url), 'utf8')
const clientSource = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8')

test('membership page keeps plans, prices, and checkout values API-sourced', () => {
  assert.match(pageSource, /workspace\.data\.membership\.plans\.filter\(isMembershipPlan\)/)
  assert.doesNotMatch(pageSource, /const plans\s*=\s*\[/)
  assert.doesNotMatch(pageSource, /planValue\s*:/)
  assert.doesNotMatch(pageSource, /prices\s*:\s*\{/)
  assert.doesNotMatch(pageSource, /HKD 30,000/)
  assert.match(pageSource, /plan:\s*selectedPlanDetails\.key,\s*cycle:\s*checkoutCycle/)
})

test('membership API types preserve canonical plan and billing values', () => {
  assert.match(clientSource, /export type MembershipPlanKey = '免费版' \| '标准版' \| '高级版' \| '专业版' \| '定制版'/)
  assert.match(clientSource, /export type MembershipBillingCycle = 'monthly' \| 'quarterly' \| 'yearly' \| 'project'/)
  assert.match(clientSource, /prices: Partial<Record<MembershipBillingCycle, number>>/)
  assert.match(clientSource, /can_purchase: boolean/)
  assert.match(clientSource, /purchase_action: MembershipPurchaseAction/)
  assert.match(clientSource, /can_submit_proof: boolean/)
  assert.match(clientSource, /export interface MembershipOrder \{[\s\S]*can_purchase: boolean/)
})

test('membership buttons consume authoritative purchase actions instead of array order', () => {
  assert.match(pageSource, /plan\.purchase_action === "covered"/)
  assert.match(pageSource, /plan\.purchase_action === "renew"/)
  assert.match(pageSource, /plan\.purchase_action === "upgrade"/)
  assert.match(pageSource, /plan\.can_purchase && !freePlan/)
  assert.match(pageSource, /order\.can_submit_proof/)
  assert.doesNotMatch(pageSource, /currentPlanIndex/)
  assert.doesNotMatch(pageSource, /planIndex < currentPlanIndex/)
})
