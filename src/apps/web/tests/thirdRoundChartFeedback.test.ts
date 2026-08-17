import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import * as workspaceModel from '../src/components/chartWorkspaceModel.ts'
import * as drawingModel from '../src/data/chartDrawings.ts'

const chartWorkspace = readFileSync(new URL('../src/components/ChartWorkspace.tsx', import.meta.url), 'utf8')
const marketChart = readFileSync(new URL('../src/components/MarketChart.tsx', import.meta.url), 'utf8')
const drawingLayer = readFileSync(new URL('../src/components/ChartDrawingLayer.tsx', import.meta.url), 'utf8')
const marketStyles = readFileSync(new URL('../src/styles/market.css', import.meta.url), 'utf8')
const deliberation = readFileSync(new URL('../src/pages/DeliberationPage.tsx', import.meta.url), 'utf8')
const deliberationStyles = readFileSync(new URL('../src/styles/deliberation.css', import.meta.url), 'utf8')

test('same market symbol and timeframe synchronize viewport without forcing unrelated panes', () => {
  const shouldSync = (workspaceModel as Record<string, unknown>).shouldSyncChartViewport
  assert.equal(typeof shouldSync, 'function')
  const source = { id: 'chart-1', market: 'US', symbol: 'AAPL', timeframe: '日线' }
  assert.equal((shouldSync as Function)(source, { ...source, id: 'chart-2' }, false), true)
  assert.equal((shouldSync as Function)(source, { ...source, id: 'chart-2', timeframe: '周线' }, false), false)
  assert.equal((shouldSync as Function)(source, { ...source, id: 'chart-2', symbol: 'MSFT' }, false), false)
  assert.equal((shouldSync as Function)(source, { ...source, id: 'chart-2', symbol: 'MSFT' }, true), true)
  assert.match(marketChart, /setVisibleLogicalRange:\s*\(range\)/)
  assert.match(chartWorkspace, /chart\?\.setVisibleLogicalRange\(sourceViewport\)/)
  assert.match(chartWorkspace, /saveViewport\(slotId, \{ from: Number\(sourceViewport\.from\), to: Number\(sourceViewport\.to\) \}\)/)
})

test('every visible chart pane retains a stock picker including single-pane fullscreen', () => {
  assert.match(chartWorkspace, /<button className="chart-symbol-trigger"/)
  assert.doesNotMatch(chartWorkspace, /definition\.count > 1\s*\?\s*<button className="chart-symbol-trigger"/)
  assert.match(chartWorkspace, /onVisibleTimeRangeChange=\{\(range\) => syncVisibleTimeRange\(slot\.id, range\)\}/)
  assert.match(marketStyles, /\.is-workbench-open \.chart-slot-toolbar > :not\(\.timeframe-dropdown\):not\(\.chart-symbol-trigger\)/)
  assert.doesNotMatch(marketStyles, /\.is-workbench-open \.chart-slot-toolbar > :not\(\.timeframe-dropdown\) \{ display: none/)
})

test('cursor mode can translate a selected drawing and persists only valid converted points', () => {
  const translate = (drawingModel as Record<string, unknown>).translateDrawingScreenPoints
  assert.equal(typeof translate, 'function')
  const moved = (translate as Function)([
    { x: 10, y: 20 },
    { x: 30, y: 40 },
  ], 5, -3, (x: number, y: number) => ({ time: x, price: y }))
  assert.deepEqual(moved, [{ time: 15, price: 17 }, { time: 35, price: 37 }])
  assert.equal((translate as Function)([{ x: 10, y: 20 }], 500, 0, () => null), null)
  assert.match(drawingLayer, /selectedDrawingId/)
  assert.match(drawingLayer, /onPointerMove=\{moveSelectedDrawing\}/)
  assert.match(drawingLayer, /onPointerUp=\{finishMovingDrawing\}/)
})

test('drawing placement recovers from invalid plot-edge coordinates', () => {
  const resolvePoint = (drawingModel as Record<string, unknown>).resolveNearestDrawingPoint
  assert.equal(typeof resolvePoint, 'function')
  const point = (resolvePoint as Function)(99, 50, 100, 100, (x: number, y: number) => x <= 91 ? { time: x, price: y } : null)
  assert.deepEqual(point, { time: 91, price: 50 })
  assert.equal((resolvePoint as Function)(50, 50, 100, 100, () => null), null)
})

test('research tools fill the available chart height instead of occupying a single 42px row', () => {
  assert.match(marketStyles, /\.chart-tool-panel\s*\{[\s\S]*?grid-template-rows:\s*minmax\(0,\s*1fr\)/)
  assert.match(marketStyles, /\.chart-tool-panel-content\s*\{[\s\S]*?height:\s*100%/)
  assert.doesNotMatch(marketStyles, /\.chart-tool-panel\s*\{[\s\S]*?grid-template-rows:\s*42px minmax\(0,\s*1fr\)/)
})

test('deliberation actions stay horizontal and evidence controls form one readable toolbar', () => {
  assert.match(deliberation, /aria-label="刷新真实证据"[\s\S]*?<RefreshCw \/><span>刷新<\/span>/)
  assert.match(deliberationStyles, /\.app-shell \.deliberation-secondary-action[\s\S]*?white-space:\s*nowrap/)
  assert.match(deliberationStyles, /\.app-shell \.deliberation-section-title > \.deliberation-evidence-tools[\s\S]*?display:\s*flex/)
  assert.match(deliberationStyles, /\.deliberation-evidence > \.deliberation-section-title[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\)/)
  assert.match(deliberationStyles, /\.app-shell \.deliberation-evidence-refresh[\s\S]*?width:\s*auto/)
  assert.match(deliberationStyles, /\.deliberation-side-operation > span:first-child/)
  assert.doesNotMatch(deliberationStyles, /\.deliberation-side-operation > span \{ display: grid; width: 38px/)
  assert.match(deliberationStyles, /\.deliberation-side-operation > :is\(a, button, \.deliberation-secondary-action\)/)
})
