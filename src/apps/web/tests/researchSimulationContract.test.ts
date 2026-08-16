import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const research = readFileSync(new URL('../src/pages.tsx', import.meta.url), 'utf8')
const paper = readFileSync(new URL('../src/pages/PersonalPaperPage.tsx', import.meta.url), 'utf8')
const portfolio = readFileSync(new URL('../src/pages/PortfolioPage.tsx', import.meta.url), 'utf8')

test('research keeps the chart first without duplicating Discover or the global stock search', () => {
  assert.doesNotMatch(research, /import \{ MarketOverview \}/)
  assert.doesNotMatch(research, /<aside className="watchlist-panel"/)
  assert.doesNotMatch(research, /name="watchlist-search"/)
  assert.match(research, /className="page research-stock-empty"/)
  assert.match(research, /前往发现股票/)
  assert.match(research, /className="research-stock-switcher"/)
  assert.match(research, /const researchToolPanel = <div className="research-tool-panel-inner">/)
  assert.match(research, /toolPanel=\{researchToolPanel\}/)
  assert.match(research, /只提醒，不会自动买卖/)
  assert.doesNotMatch(research, /13 股票稳定研究链|97 股票扩展研究链|正式行动合同|research-ai-column/)
})

test('personal paper makes risk review primary and keeps final submit secondary and duplicate-safe', () => {
  assert.match(paper, /const \[reviewedWarnings, setReviewedWarnings\]/)
  assert.match(paper, /warningReviewComplete/)
  assert.match(paper, /submitInFlightRef/)
  assert.match(paper, /paper-risk-review-cta/)
  assert.match(paper, /查看风险与证据/)
  assert.match(paper, /重新检查风险与证据/)
  assert.match(paper, /当前浏览器审阅状态/)
  assert.match(paper, /className="button secondary paper-submit"/)
  assert.doesNotMatch(paper, /className="button primary paper-submit"/)
})

test('portfolio exposes official snapshot bounds and safe research/report handoffs without invented analytics', () => {
  for (const field of ['captured_at', 'returned_execution_limit', 'truncated', 'mark_source']) {
    assert.match(portfolio, new RegExp(field))
  }
  assert.match(portfolio, /\/research\?market=/)
  assert.match(portfolio, /打开验证报告/)
  assert.match(portfolio, /风险与计划偏差能力尚未接入/)
  assert.match(portfolio, /官方验证模拟/)
})
