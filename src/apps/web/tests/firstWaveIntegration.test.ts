import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  validPersonalPaperAccount,
  validPersonalPaperOrderResult,
  validPersonalPaperQuoteProof,
  validPersonalPaperRiskProof,
  validPersonalPaperRiskProofRequest,
} from '../src/api/client.ts'

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const shell = readFileSync(new URL('../src/components/AppShell.tsx', import.meta.url), 'utf8')
const client = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const paper = readFileSync(new URL('../src/pages/PersonalPaperPage.tsx', import.meta.url), 'utf8')
const paperCore = readFileSync(new URL('../src/components/paper/CicloCore.tsx', import.meta.url), 'utf8')
const paperPrimitives = readFileSync(new URL('../src/components/paper/PaperPrimitives.tsx', import.meta.url), 'utf8')
const paperStyles = readFileSync(new URL('../src/styles/paper.css', import.meta.url), 'utf8')
const navigationStyles = readFileSync(new URL('../src/styles/navigation.css', import.meta.url), 'utf8')

const account = {
  season: { id: 'pps_1234567890', state: 'active', currency: 'USD', initial_cash: 10000, started_at: '2026-08-14T00:00:00+00:00', closed_at: null, version: 2 },
  cash: 9000, reserved_cash: 0, buying_power: 9000, market_value: 1000,
  realized_pnl: 0, unrealized_pnl: 0, total_equity: 10000,
  as_of: '2026-08-14T00:00:00+00:00', quote_state: 'fresh', account_version: 2,
  positions: [{ market: 'US', symbol: 'AAPL', quantity: 10 }],
}
const order = {
  id: 'ppo_1234567890', season_id: 'pps_1234567890', market: 'US', symbol: 'AAPL',
  side: 'BUY', order_type: 'MARKET', quantity: 10, status: 'FILLED',
  created_at: '2026-08-14T00:00:00+00:00', quote_id: 'ppq_1234567890',
}
const riskRequest = {
  season_id: 'pps_1234567890', market: 'US', symbol: 'AAPL', side: 'BUY', order_type: 'MARKET',
  quantity: 10, limit_price: null, stop_price: null, time_in_force: 'DAY', quote_id: 'ppq_1234567890',
  account_version: 2, source_context: { kind: 'manual', reference_id: null },
}
const riskChecks = [
  { code: 'buying_power', value: { required: 100, available: 900 }, limit: { required_max: 900 } },
  { code: 'max_loss', value: { usd: 100, pct: 1, unbounded: false }, limit: { usd: 1000, pct: 10 } },
  { code: 'position_concentration', value: { usd: 100, pct: 1 }, limit: { pct: 25 } },
  { code: 'sector_concentration', value: { industry: 'Technology', usd: 100, pct: 1 }, limit: { pct: 35 } },
  { code: 'drawdown', value: { pct: 2, peak_usd: 10000, current_usd: 9800 }, limit: { pct: 20 } },
  { code: 'event_gap', value: { scheduled_at: '2026-08-18T20:15:00Z', revision_id: 1, payload_sha256: 'c'.repeat(64) }, limit: { scheduled_at: 'must_be_known' } },
  { code: 'liquidity', value: { spread_pct: 0.5 }, limit: { spread_pct: 2 } },
].map(({ code, value, limit }) => ({
  code, status: 'pass', title: code, detail: `${code} passed`,
  value: JSON.stringify(value), limit: JSON.stringify(limit), data_state: 'fresh',
}))
const riskProof = {
  id: 'ppr_1234567890', schema_version: 'r1', season_id: 'pps_1234567890', quote_id: 'ppq_1234567890',
  account_version: 2, draft_sha256: 'a'.repeat(64), proof_sha256: 'b'.repeat(64), created_at: '2026-08-14T00:00:00+00:00',
  computed_at: '2026-08-14T00:00:01+00:00', marks_as_of: '2026-08-14T00:00:00+00:00',
  expires_at: '2026-08-14T00:05:00+00:00', decision: 'allow', risk_level: 'low', data_state: 'fresh',
  checks: riskChecks, blocking_reasons: [], warnings: [],
}

test('personal paper decoders fail closed on mixed account domains and unknown DTO fields', () => {
  assert.equal(validPersonalPaperAccount(account), true)
  assert.equal(validPersonalPaperAccount({ ...account, account_mode: 'official' }), false)
  assert.equal(validPersonalPaperAccount({ ...account, season: { ...account.season, initial_cash: 100000 } }), false)
  assert.equal(validPersonalPaperAccount({ ...account, positions: [{ market: 'HK', symbol: '0700', quantity: 1 }] }), false)
  assert.equal(validPersonalPaperAccount({ ...account, positions: [{ market: 'US', symbol: 'AAPL', quantity: 0.5 }] }), false)
  assert.equal(validPersonalPaperQuoteProof({ quote_id: 'ppq_1234567890', market: 'US', symbol: 'AAPL' }), true)
  assert.equal(validPersonalPaperQuoteProof({ quote_id: 'ppq_1234567890', market: 'US', symbol: 'AAPL', last: 200 }), false)
  assert.equal(validPersonalPaperOrderResult({ order, account, replayed: false }), true)
  assert.equal(validPersonalPaperOrderResult({ order: { ...order, season_id: 'pps_other' }, account, replayed: false }), false)
})

test('personal paper risk proof freezes the request matrix and seven-check decision contract', () => {
  assert.equal(validPersonalPaperRiskProofRequest(riskRequest), true)
  assert.equal(validPersonalPaperRiskProofRequest({ ...riskRequest, unexpected: true }), false)
  assert.equal(validPersonalPaperRiskProofRequest({ ...riskRequest, limit_price: 180 }), false)
  assert.equal(validPersonalPaperRiskProofRequest({ ...riskRequest, order_type: 'LIMIT', limit_price: 180 }), true)
  assert.equal(validPersonalPaperRiskProofRequest({ ...riskRequest, order_type: 'LIMIT', limit_price: 180, stop_price: 175 }), false)
  assert.equal(validPersonalPaperRiskProofRequest({ ...riskRequest, order_type: 'STOP', stop_price: 175 }), true)
  assert.equal(validPersonalPaperRiskProofRequest({ ...riskRequest, order_type: 'STOP', limit_price: 180, stop_price: 175 }), false)
  assert.equal(validPersonalPaperRiskProofRequest({ ...riskRequest, order_type: 'STOP_LIMIT', limit_price: 180, stop_price: 175 }), true)

  assert.equal(validPersonalPaperRiskProof(riskProof), true)
  assert.equal(validPersonalPaperRiskProof({ ...riskProof, schema_version: 'r2' }), false)
  assert.equal(validPersonalPaperRiskProof({ ...riskProof, proof_sha256: 'B'.repeat(64) }), false)
  assert.equal(validPersonalPaperRiskProof({ ...riskProof, computed_at: 'not-a-time' }), false)
  assert.equal(validPersonalPaperRiskProof({ ...riskProof, marks_as_of: 'not-a-time' }), false)
  assert.equal(validPersonalPaperRiskProof({ ...riskProof, unexpected: true }), false)
  assert.equal(validPersonalPaperRiskProof({ ...riskProof, checks: [...riskChecks.slice(0, 6), riskChecks[0]] }), false)
  const failedChecks = riskChecks.map((check, index) => index === 0 ? { ...check, status: 'fail' } : check)
  assert.equal(validPersonalPaperRiskProof({ ...riskProof, checks: failedChecks }), false)
  assert.equal(validPersonalPaperRiskProof({ ...riskProof, decision: 'reject', risk_level: 'blocked', checks: failedChecks, blocking_reasons: [failedChecks[0].detail] }), true)
  assert.equal(validPersonalPaperRiskProof({ ...riskProof, decision: 'reject', risk_level: 'blocked' }), false)
})

test('first wave exposes six desktop modules and exactly five fixed mobile modules', () => {
  const desktopBlock = shell.match(/const navItems = \[([\s\S]*?)\] as const/)?.[1] ?? ''
  const mobileBlock = shell.match(/const mobileNavItems = \[([\s\S]*?)\] as const/)?.[1] ?? ''
  for (const route of ['/today', '/discover', '/research', '/paper', '/portfolio', '/more']) assert.match(desktopBlock, new RegExp(`to: '${route}'`))
  assert.equal((desktopBlock.match(/to: '/g) ?? []).length, 6)
  for (const route of ['/today', '/discover', '/research', '/paper', '/more']) assert.match(mobileBlock, new RegExp(`to: '${route}'`))
  assert.equal((mobileBlock.match(/to: '/g) ?? []).length, 5)
  assert.doesNotMatch(mobileBlock, /pinned|secondaryTools/)
  assert.match(navigationStyles, /@media \(max-width: 1024px\) \{ \.app-shell \.secondary-tools \{ display: none;/)
})

test('paper and more routes are local authenticated pages while legacy URLs retain query and hash', () => {
  assert.match(app, /path="\/paper" element=\{<PersonalPaperPage \/>\}/)
  assert.match(app, /path="\/more" element=\{<MoreRoute \/>\}/)
  assert.match(app, /`\$\{to\}\$\{location\.search\}\$\{location\.hash\}`/)
  assert.doesNotMatch(client, /\/api\/rewrite\/v1\/paper\/orders/)
  assert.match(client, /\/api\/rewrite\/v1\/personal-paper\/orders/)
  assert.match(client, /\/api\/rewrite\/v1\/personal-paper\/risk-proofs/)
  assert.match(client, /risk_proof_id/)
  assert.match(client, /computed_at/)
  assert.match(client, /marks_as_of/)
  assert.match(client, /proof_sha256/)
  assert.match(client, /request<unknown>\('\/api\/rewrite\/v1\/features\/catalog'\)/)
  assert.match(client, /request<unknown>\('\/api\/rewrite\/v1\/features\/preferences', \{\s*method: 'PUT'/)
  assert.match(client, /request<unknown>\('\/api\/rewrite\/v1\/features\/recent', \{\s*method: 'PUT'/)
  assert.match(client, /request<unknown>\(`\/api\/rewrite\/v1\/personal-paper\/seasons\/\$\{encodeURIComponent\(seasonId\)\}`\)/)
  assert.doesNotMatch(client, /\/api\/rewrite\/v1\/feature-catalog/)
  assert.doesNotMatch(client, /personal-paper\/seasons\/\$\{encodeURIComponent\(seasonId\)\}\/account/)
})

test('personal paper ticket preserves explicit confirmation and safe retry identity', () => {
  for (const value of ['MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT', 'BUY', 'SELL', 'SHORT', 'COVER']) assert.match(paper, new RegExp(`'${value}'`))
  assert.match(paper, /Keep the same idempotency key, quote, risk proof, account version, source and original request/)
  assert.match(paper, /disabled=\{!proofPermitsSubmit \|\| !draftValid \|\| workflowLocked\}/)
  assert.match(paper, /提交结果暂时未知/)
  assert.match(paper, /核对提交结果/)
  assert.match(paper, /PaperRefreshButton label=\{copy\.refresh\} busy=\{busy === 'refresh'\} disabled=\{workflowLocked\}/)
  assert.match(paper, /Date\.parse\(riskProof\.expires_at\) - Date\.now\(\) \+ 25/)
  assert.match(paper, /window\.setTimeout/)
  assert.doesNotMatch(paper, /window\.setInterval/)
  assert.match(paper, /setRiskProof\(null\); setKey\(''\); setPendingRequest\(null\); setSubmitState\('idle'\)/)
  assert.match(paper, /validSourceReference\(referenceParam\)/)
  assert.match(paper, /个人模拟/)
  assert.match(paper, /官方模拟/)
  assert.match(paper, /券商实盘/)
  assert.doesNotMatch(paper, /official_paper|account_mode|\/trade\/order/)
  assert.doesNotMatch(`${paper}\n${paperCore}\n${paperPrimitives}`, /标的|標的/)
  assert.match(paperPrimitives, /name="personal-paper-symbol" autoComplete="off"/)
  assert.match(paperPrimitives, /paper-evidence-mark/)
  assert.doesNotMatch(paperPrimitives, /Sparkles/)
  assert.match(paperPrimitives, /aria-invalid=\{!symbolValid\}/)
  assert.match(paperPrimitives, /aria-describedby=\{!quantityValid \? validationId : undefined\}/)
  assert.match(paper, /CICLO RISK CORE/)
  assert.doesNotMatch(paper, /<main className="paper-flow"/)
  assert.match(paper, /<section className="paper-flow" aria-labelledby="paper-draft-title">/)
  assert.match(paperStyles, /--paper-ai-text: #7083ff/)
  assert.match(paperStyles, /--paper-control-border: #586980/)
  assert.match(paperStyles, /paper-refresh-button \{ width: 44px; height: 44px; min-height: 44px/)
  assert.match(paperStyles, /paper-alert button, \.app-shell \.paper-source-context button \{ min-height: 44px/)
  assert.doesNotMatch(paperStyles, /paper-core-panel \{ display: none;/)
  assert.match(paperStyles, /paper-core-panel \{ min-height: 240px; grid-template-columns: 1fr;/)
  assert.match(paperStyles, /paper-receipt > svg:last-child \{ grid-column: 1 \/ -1; width: 24px; height: 24px;/)
  assert.match(paperStyles, /@media \(max-width: 430px\)/)
  assert.match(paperStyles, /@media \(min-width: 760px\) and \(max-width: 980px\) and \(max-height: 560px\) and \(orientation: landscape\)/)
})
