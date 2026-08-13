import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  validPersonalPaperAccount,
  validPersonalPaperOrderResult,
  validPersonalPaperQuoteProof,
} from '../src/api/client.ts'

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const shell = readFileSync(new URL('../src/components/AppShell.tsx', import.meta.url), 'utf8')
const client = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const paper = readFileSync(new URL('../src/pages/PersonalPaperPage.tsx', import.meta.url), 'utf8')
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
  assert.match(client, /request<unknown>\('\/api\/rewrite\/v1\/features\/catalog'\)/)
  assert.match(client, /request<unknown>\('\/api\/rewrite\/v1\/features\/preferences', \{\s*method: 'PUT'/)
  assert.match(client, /request<unknown>\('\/api\/rewrite\/v1\/features\/recent', \{\s*method: 'PUT'/)
  assert.match(client, /request<unknown>\(`\/api\/rewrite\/v1\/personal-paper\/seasons\/\$\{encodeURIComponent\(seasonId\)\}`\)/)
  assert.doesNotMatch(client, /\/api\/rewrite\/v1\/feature-catalog/)
  assert.doesNotMatch(client, /personal-paper\/seasons\/\$\{encodeURIComponent\(seasonId\)\}\/account/)
})

test('personal paper ticket preserves explicit confirmation and safe retry identity', () => {
  for (const value of ['MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT', 'BUY', 'SELL', 'SHORT', 'COVER']) assert.match(paper, new RegExp(`'${value}'`))
  assert.match(paper, /Keep the same idempotency key and exact payload/)
  assert.match(paper, /disabled=\{!quote \|\| !ticketValid \|\| Boolean\(busy\)\}/)
  assert.match(paper, /个人模拟/)
  assert.match(paper, /官方模拟/)
  assert.match(paper, /券商实盘/)
  assert.doesNotMatch(paper, /official_paper|account_mode|\/trade\/order/)
  assert.match(paper, /name="personal-paper-symbol" autoComplete="off"/)
  assert.match(paperStyles, /@media \(max-width: 430px\)/)
  assert.match(paperStyles, /@media \(max-width: 980px\) and \(max-height: 560px\) and \(orientation: landscape\)/)
})
