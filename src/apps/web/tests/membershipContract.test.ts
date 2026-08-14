import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const pageSource = readFileSync(new URL('../src/pages/MembershipPage.tsx', import.meta.url), 'utf8')
const clientSource = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const opportunitiesSource = readFileSync(new URL('../src/pages/OpportunitiesPage.tsx', import.meta.url), 'utf8')

test('membership page keeps plans, prices, and checkout values API-sourced', () => {
  assert.match(pageSource, /workspace\.data\.membership\.plans\.filter\(isMembershipPlan\)/)
  assert.doesNotMatch(pageSource, /const plans\s*=\s*\[/)
  assert.doesNotMatch(pageSource, /planValue\s*:/)
  assert.doesNotMatch(pageSource, /prices\s*:\s*\{/)
  assert.doesNotMatch(pageSource, /HKD 30,000/)
  assert.match(pageSource, /plan:\s*selectedPlanDetails\.key,\s*cycle:\s*checkoutCycle/)
  assert.match(pageSource, /quoteMembershipOrder/)
  assert.match(pageSource, /coupon_code:/)
  assert.match(pageSource, /先优惠码、再对符合条件的推荐新客计算 95 折/)
  assert.match(pageSource, /每单仅可使用一张优惠码/)
  assert.match(pageSource, /membership\.legacy_plans/)
})

test('membership checkout requires a current server quote and revalidates it before creating an order', () => {
  assert.match(pageSource, /id="membership-coupon" name="membership-coupon" autoComplete="off" spellCheck=\{false\}/)
  assert.match(pageSource, /name="membership-terms-accepted"/)
  assert.match(pageSource, /function membershipQuoteFingerprint\(/)
  assert.match(pageSource, /const \[quotedInputFingerprint, setQuotedInputFingerprint\] = useState\(""\)/)
  assert.match(pageSource, /currentQuoteFingerprint\.current = quoteInputFingerprint/)
  assert.match(pageSource, /quotedInputFingerprint === quoteInputFingerprint/)
  assert.match(pageSource, /setQuote\(null\);\s*setQuotedInputFingerprint\(""\);\s*setQuoteBusy\(false\);/)
  assert.match(pageSource, /!hasCurrentQuote \|\|\s*quoteBusy/)
  assert.match(pageSource, /const verifiedQuote = await refreshQuote\(quoteRequest\)/)
  assert.match(pageSource, /currentQuoteFingerprint\.current !== requestFingerprint/)
  assert.match(pageSource, /quoteMatchesRequest\(verifiedQuote, quoteRequest\)/)
  assert.match(pageSource, /function quoteMatchesDisplayedQuote\(/)
  assert.match(pageSource, /displayed\.final_amount_minor === verified\.final_amount_minor/)
  assert.match(pageSource, /displayed\.referral_eligible === verified\.referral_eligible/)
  assert.match(pageSource, /最终报价已更新，请核对后再次建立订单。/)
  assert.match(pageSource, /plan: quoteRequest\.plan,\s*cycle: quoteRequest\.cycle/)
  assert.doesNotMatch(pageSource, /priceText\(selectedAmount\)/)
})

test('membership checkout reuses a stable idempotency key after an unknown response', () => {
  assert.match(pageSource, /function membershipOrderFingerprint\(/)
  assert.match(pageSource, /const membershipOrderIdempotency = useRef/)
  assert.match(pageSource, /function orderIdempotencyKey\(fingerprint: string\)/)
  assert.match(pageSource, /orderIdempotencyKey\(orderFingerprint\)/)
  assert.match(pageSource, /!safelyRejected\s*\? "订单结果暂时无法确认。/)
  assert.match(pageSource, /if \(safelyRejected\) clearOrderIdempotency\(orderFingerprint\)/)
  assert.match(pageSource, /clearOrderIdempotency\(orderFingerprint\);\s*setProofOrder/)
})

test('membership API types preserve canonical plan and billing values', () => {
  assert.match(clientSource, /export type MembershipPlanKey = '免费版' \| '标准版' \| '高级版' \| '专业版' \| '定制版'/)
  assert.match(clientSource, /export type MembershipBillingCycle = 'monthly' \| 'quarterly' \| 'yearly' \| 'project'/)
  assert.match(clientSource, /prices: Partial<Record<MembershipBillingCycle, number>>/)
  assert.match(clientSource, /can_purchase: boolean/)
  assert.match(clientSource, /purchase_action: MembershipPurchaseAction/)
  assert.match(clientSource, /can_submit_proof: boolean/)
  assert.match(clientSource, /export interface MembershipOrder \{[\s\S]*can_purchase: boolean/)
  assert.match(clientSource, /discount_order: \['coupon', 'referral'\]/)
  assert.match(clientSource, /server_reprices_on_order: true/)
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

test('opportunities describe the server-authoritative recommendation delay and upgrade path', () => {
  assert.match(clientSource, /delivery: \{ stock: number; option: number \}/)
  assert.match(opportunitiesSource, /const recommendationDelivery = workspace\.data\?\.recommendations\.delivery/)
  assert.match(opportunitiesSource, /网站推荐发布延迟/)
  assert.match(opportunitiesSource, /升级高级会员可即时查看正股建议；升级专业会员可即时查看正股与期权建议。/)
})
