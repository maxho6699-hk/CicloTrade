import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const root = new URL('../src/', import.meta.url)
const read = (path: string) => readFileSync(new URL(path, root), 'utf8')
const research = read('pages.tsx')
const today = read('pages/TodayV2Page.tsx')
const discover = read('pages/DiscoverV2Page.tsx')
const paper = read('pages/PersonalPaperPage.tsx')
const portfolio = read('pages/PortfolioPage.tsx')

test('stock deep links carry explicit market context', () => {
  assert.match(today, /research\?market=.*symbol=.*event_id=/)
  assert.match(discover, /research\?market=.*symbol=.*event_id=/)
  assert.match(discover, /paper\?symbol=.*market=.*source=screener/)
  assert.match(discover, /research\?market=.*symbol=.*panel=预警&draft=1/)
  assert.match(today, /paper\?market=US/)
})

test('research event resolution fails closed on missing or mismatched market context', () => {
  assert.match(research, /const hasExplicitMarket =/) 
  assert.match(research, /if \(!hasExplicitMarket \|\| !candidateSymbol \|\| !candidateMarket \|\| !selected\.symbol \|\| !selected\.market\)/)
  assert.match(research, /candidateSymbol !== selected\.symbol\.toUpperCase\(\) \|\| candidateMarket !== selected\.market/)
  assert.match(research, /!hasEvent \|\| candidate\.event_id === eventId/)
})

test('offline and demo research paths never supply fixture candles or AAPL', () => {
  assert.doesNotMatch(research, /import \{[^}]*candles/)
  assert.doesNotMatch(research, /demoMode|演示K线|界面演示数据/)
  assert.doesNotMatch(research, /'AAPL'/)
  assert.doesNotMatch(paper, /'AAPL'/)
  assert.match(research, /candles=\{chartData\}/)
  assert.match(research, /暂无真实行情/)
})

test('personal paper is explicitly US-only and does not invent a stock', () => {
  assert.match(paper, /requestedMarket =/) 
  assert.match(paper, /marketSupported = requestedMarket === 'US'/)
  assert.match(paper, /symbol: .*initialSymbol.*: ''/)
  assert.match(paper, /个人模拟需要明确的 market 参数/)
  assert.match(paper, /workflowLocked = !marketSupported/)
})

test('portfolio preserves nullable settlement fields and validates deep-link context', () => {
  assert.match(portfolio, /exit: number \| null/)
  assert.match(portfolio, /pnl: number \| null/)
  assert.doesNotMatch(portfolio, /average_exit_price \?\? 0|realized_pnl \?\? 0/)
  assert.match(portfolio, /未记录\/尚未结算/)
  assert.match(portfolio, /requestedMarket =/) 
  assert.match(portfolio, /requestedSymbol =/) 
  assert.match(portfolio, /requestedEvent =/) 
  assert.match(portfolio, /contextValid =/) 
})

test('up and down chart colors remain semantic tokens rather than arbitrary controls', () => {
  assert.doesNotMatch(research, /上涨颜色<input type="color"/)
  assert.doesNotMatch(research, /下跌颜色<input type="color"/)
  assert.match(research, /上涨颜色 · 系统语义色/)
  assert.match(research, /下跌颜色 · 系统语义色/)
})
