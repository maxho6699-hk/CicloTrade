import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = (relative: string) => readFileSync(new URL(`../${relative}`, import.meta.url), 'utf8')
const discoverStyles = read('src/styles/today-discover-v2.css')
const marketStyles = read('src/styles/market.css')
const responsiveStyles = read('src/styles/responsive.css')
const recommendationStyles = read('src/styles/recommendations.css')
const intelligenceStyles = read('src/styles/intelligence.css')
const aiStyles = read('src/styles/ai-workspace.css')
const deliberationStyles = read('src/styles/deliberation.css')
const componentStyles = read('src/styles/components.css')
const chart = read('src/components/ChartWorkspace.tsx')
const recommendations = read('src/pages/RecommendationsPage.tsx')
const aiPage = read('src/pages/AIWorkspacePage.tsx')
const deliberation = read('src/pages/DeliberationPage.tsx')
const discover = read('src/pages/DiscoverV2Page.tsx')

test('Discover keeps the candidate matrix dominant instead of giving the left rail twenty times its share', () => {
  assert.match(discoverStyles, /@media \(min-width:1481px\)\{[\s\S]*?\.discover-dashboard\{grid-template-columns:minmax\(220px,280px\) minmax\(0,1fr\) minmax\(292px,336px\)\}/)
  assert.doesNotMatch(discoverStyles, /minmax\(220px,20fr\) minmax\(0,1fr\)/)
})

test('Research uses its only page column and places research tools before drawing tools next to the chart', () => {
  assert.match(responsiveStyles, /@media \(max-width: 1180px\) \{[\s\S]*?\.research-workbench-grid \{ grid-template-columns: minmax\(0, 1fr\); \}/)
  assert.doesNotMatch(responsiveStyles, /\.research-workbench-grid \{ grid-template-columns: 190px minmax\(0, 1fr\); \}/)
  assert.ok(chart.indexOf('className="chart-tool-panel"') < chart.indexOf('<SharedDrawingToolbar'), 'research tools must precede drawing tools')
  assert.ok(chart.indexOf('<SharedDrawingToolbar') < chart.indexOf('className={`multi-chart-grid'), 'drawing tools must touch the chart')
  assert.match(marketStyles, /\.chart-workbench-body \{[\s\S]*?grid-template-columns: auto 43px minmax\(0, 1fr\) auto/)
  assert.match(marketStyles, /\.chart-tool-panel \{[\s\S]*?grid-column: 1/)
  assert.match(marketStyles, /\.drawing-tools-root \{[\s\S]*?grid-column: 2/)
})

test('Layout selection sits beside watchlist and chart popovers can escape a split pane', () => {
  assert.match(chart, /<div className="workbench-title">[\s\S]*?<WatchlistToggle[\s\S]*?<div className="multi-chart-layout-picker"/)
  assert.match(marketStyles, /\.chart-slot:has\(\.chart-symbol-popover\)[^{]*\{[^}]*overflow: visible/)
  assert.match(marketStyles, /\.multi-chart-grid:has\(\.chart-symbol-popover\)[^{]*\{[^}]*overflow: visible/)
})

test('K line fullscreen uses the browser Fullscreen API and synchronizes browser escape', () => {
  assert.match(chart, /requestFullscreen/)
  assert.match(chart, /exitFullscreen/)
  assert.match(chart, /fullscreenchange/)
  assert.match(chart, /document\.fullscreenElement/)
})

test('Recommendation detail opens in a page-level drawer instead of stretching one grid card', () => {
  assert.match(recommendations, /recommendation-detail-drawer/)
  assert.match(recommendations, /recommendation-detail-backdrop/)
  assert.match(recommendations, /selectedDetail/)
  assert.doesNotMatch(recommendations, /const \[expanded, setExpanded\] = useState/)
  assert.match(recommendationStyles, /\.recommendation-detail-drawer\{position:fixed/)
  assert.match(recommendationStyles, /@media\(max-width:760px\)[\s\S]*?\.recommendation-detail-drawer\{[^}]*inset:auto 0 0/)
})

test('AI header uses readable Chinese readiness state and a compact balanced card', () => {
  assert.doesNotMatch(aiPage, />\{readiness\?\.ready \? 'ready' : 'unavailable'\}</)
  assert.match(aiPage, /AI 服务可用|AI 服务暂不可用/)
  assert.match(aiStyles, /\.ai-workspace-header\{[^}]*min-height:/)
  assert.match(aiStyles, /\.ai-workspace-header h1\{[^}]*color:/)
  assert.match(intelligenceStyles, /\.intelligence-page-header/)
})

test('Deliberation empty and disabled states remain explicit and readable', () => {
  assert.doesNotMatch(deliberation, /<span title="筛选视图"><Filter \/><\/span>/)
  assert.doesNotMatch(deliberation, /<span title="网格布局"><LayoutGrid \/><\/span>/)
  assert.match(deliberation, /deliberation-secondary-action is-disabled/)
  assert.match(deliberationStyles, /\.deliberation-secondary-action\.is-disabled/)
  assert.match(deliberationStyles, /\.deliberation-state p \{[^}]*overflow-wrap: anywhere/)
})

test('Saved watchlist stars are yellow in dark theme and red in light theme, never black', () => {
  assert.match(componentStyles, /:root:not\(\[data-theme='light'\]\) \.watchlist-toggle\.is-saved \{[^}]*color: #facc15/i)
  assert.match(componentStyles, /:root\[data-theme='light'\] \.watchlist-toggle\.is-saved \{[^}]*color: #dc2626/i)
  assert.doesNotMatch(componentStyles, /\.watchlist-toggle\.is-saved \{[^}]*color: (?:#000|black)/i)
})

test('Discover mini K-line periods never render inert or unexplained disabled buttons', () => {
  assert.doesNotMatch(discover, /className="discover-periods"[^\n]*<button/)
  assert.match(discover, /1W · 待接入/)
  assert.match(discover, /1M · 待接入/)
})
