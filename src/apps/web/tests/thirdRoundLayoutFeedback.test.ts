import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const marketOverview = readFileSync(new URL('../src/components/MarketOverview.tsx', import.meta.url), 'utf8')
const marketStyles = readFileSync(new URL('../src/styles/market.css', import.meta.url), 'utf8')
const discover = readFileSync(new URL('../src/pages/DiscoverV2Page.tsx', import.meta.url), 'utf8')
const discoverStyles = readFileSync(new URL('../src/styles/today-discover-v2.css', import.meta.url), 'utf8')
const recommendations = readFileSync(new URL('../src/pages/RecommendationsPage.tsx', import.meta.url), 'utf8')
const recommendationStyles = readFileSync(new URL('../src/styles/recommendations.css', import.meta.url), 'utf8')
const account = readFileSync(new URL('../src/pages/AccountPage.tsx', import.meta.url), 'utf8')
const accountStyles = readFileSync(new URL('../src/styles/account-center.css', import.meta.url), 'utf8')

test('mobile market cards expose every real item instead of truncating the strip', () => {
  assert.doesNotMatch(marketOverview, /MOBILE_CARD_LIMIT/)
  assert.match(marketOverview, /const visibleCards = narrowCards\s*\? shown\s*:/)
  assert.match(marketOverview, /overview-mobile-card-status/)
  assert.match(marketStyles, /\.overview-mobile-card-status/)
})

test('Discover puts account snapshot under coverage and shows a compact three-row candidate viewport', () => {
  const leftStart = discover.indexOf('className="discover-left-rail')
  const coverage = discover.indexOf('<CoveragePanel', leftStart)
  const accountSnapshot = discover.indexOf('<AccountSnapshotPanel', leftStart)
  const center = discover.indexOf('className="discover-center-column', leftStart)
  const right = discover.indexOf('className={`v2-inspector discover-right-rail', center)
  assert.ok(leftStart >= 0 && coverage > leftStart && accountSnapshot > coverage && accountSnapshot < center)
  assert.equal(discover.indexOf('<AccountSnapshotPanel', right), -1)
  assert.match(discoverStyles, /\.discover-market-card \.v2-candidate-table\{[\s\S]*?max-height:\s*clamp\([^}]+\)[\s\S]*?overflow-y:\s*auto/)
  assert.match(discoverStyles, /\.discover-market-card \.v2-candidate-table tr\{[\s\S]*?grid-template-columns:\s*repeat\(3,/)
  assert.match(discoverStyles, /\.discover-sparkline\{[\s\S]*?height:\s*58px/)
  assert.doesNotMatch(discoverStyles, /\.discover-market-card \.v2-candidate-table\{height:auto!important;max-height:none!important/)
})

test('Discover stock-code search keeps icon and input on one horizontal 44px control', () => {
  assert.match(discoverStyles, /\.discover-filter-search \.v2-search-field\{[\s\S]*?display:\s*flex[\s\S]*?min-height:\s*44px[\s\S]*?align-items:\s*center/)
  assert.match(discoverStyles, /\.discover-filter-search \.v2-search-field svg\{[\s\S]*?flex:\s*0 0 auto/)
})

test('recommendations keep all results in an eight-card scroll viewport and use the left space for real peer comparison', () => {
  assert.match(recommendations, /recommendation-preview-scroll/)
  assert.match(recommendations, /RecommendationContextPanel/)
  assert.match(recommendations, /peers=\{items\.filter/)
  assert.doesNotMatch(recommendations, /items\.slice\(0,\s*8\)/)
  assert.match(recommendationStyles, /\.recommendation-preview-scroll\{[\s\S]*?overflow-y:\s*auto/)
  assert.match(recommendationStyles, /\.recommendation-context-panel\{[\s\S]*?position:\s*fixed/)
  assert.match(recommendationStyles, /@media\(max-width:1199px\)\{[\s\S]*?\.recommendation-context-panel\{display:none/)
})

test('account overview stops equal-height stretching and moves content shortcuts directly below the agent', () => {
  const overviewMain = account.indexOf('className="profile-overview-main"')
  const agent = account.indexOf('className="profile-agent-card', overviewMain)
  const shortcuts = account.indexOf('className="profile-overview-shortcuts"', agent)
  const myContent = account.indexOf('<h2>我的内容</h2>', shortcuts)
  const messages = account.indexOf('<h2>消息与设置</h2>', myContent)
  const side = account.indexOf('className="profile-side-stack"', messages)
  assert.ok(overviewMain >= 0 && agent > overviewMain && shortcuts > agent && myContent > shortcuts && messages > myContent && side > messages)
  const lowerStart = account.indexOf('className="profile-lower-grid"', side)
  const lowerEnd = account.indexOf('className="profile-settings-heading"', lowerStart)
  const lowerGrid = account.slice(lowerStart, lowerEnd)
  assert.doesNotMatch(lowerGrid, /<h2>我的内容<\/h2>|<h2>消息与设置<\/h2>/)
  assert.match(accountStyles, /\.profile-overview-layout\{[\s\S]*?align-items:start/)
  assert.match(accountStyles, /\.profile-overview-main\{[\s\S]*?align-content:start/)
  assert.match(accountStyles, /\.profile-overview-shortcuts\{[\s\S]*?grid-template-columns:repeat\(2,/)
  assert.match(accountStyles, /\.profile-agent-card\{[\s\S]*?align-self:start/)
})
