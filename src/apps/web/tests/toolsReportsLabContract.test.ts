import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const root = new URL('../src/', import.meta.url)
const more = readFileSync(new URL('pages/MorePage.tsx', root), 'utf8')
const moreRoute = readFileSync(new URL('pages/MoreRoute.tsx', root), 'utf8')
const reports = readFileSync(new URL('pages/ReportsPage.tsx', root), 'utf8')
const earnings = readFileSync(new URL('pages/EarningsForecastPage.tsx', root), 'utf8')
const lab = readFileSync(new URL('pages/ProfessionalLabPage.tsx', root), 'utf8')
const overview = readFileSync(new URL('components/StrategyResearchOverviewCard.tsx', root), 'utf8')
const expanded = readFileSync(new URL('components/StrategyResearch97Panel.tsx', root), 'utf8')
const options = readFileSync(new URL('components/OptionResearchWorkspace.tsx', root), 'utf8')

test('more remains a server-catalog surface with real pin and recent actions', () => {
  assert.match(more, /onSavePins\?:/)
  assert.match(more, /onRecordRecent\?:/)
  assert.match(more, /featureOpenRoute\(item\)/)
  assert.match(moreRoute, /fetchFeatureCatalog\(\)/)
  assert.match(moreRoute, /saveFeatureCatalogPreferences/)
  assert.match(moreRoute, /recordRecentFeature/)
  assert.match(more, /feature-grid feature-grid--\$\{view\}/)
})

test('research reports keep stable and expanded stocks read-only and auditable', () => {
  assert.match(reports, /97 只股票扩容研究/)
  assert.match(reports, /97 隻股票擴容研究/)
  assert.match(expanded, /WAIT/)
  assert.match(expanded, /不可执行、不可下单、不可推送 Telegram/)
  assert.match(expanded, /invalidated/)
  assert.match(overview, /97 只股票扩容 research/)
  assert.match(overview, /97 隻股票擴容 research/)
  assert.doesNotMatch(`${reports}\n${expanded}\n${overview}`, /标的|標的/)
})

test('earnings preserves PIT snapshot, permission, and no-data boundaries', () => {
  assert.match(earnings, /D-7 至 D-1/)
  assert.match(earnings, /当前账户没有读取该研究的权限/)
  assert.match(earnings, /未来 7 天没有已确认事件/)
  assert.match(earnings, /不会用演示公司、历史财报或推测日期填充空白/)
  assert.match(earnings, /只读结果/)
})

test('professional lab never fabricates backtest or stress results', () => {
  assert.match(lab, /回测引擎尚未接入/)
  assert.match(lab, /尚未生成成绩/)
  assert.match(lab, /尚未生成压力测试结论/)
  assert.match(lab, /disabled=\{!maxBacktestYears/)
  assert.match(lab, /不会生成虚假成绩/)
  assert.match(lab, /不会生成成绩/)
})

test('option workspace labels stocks and keeps research-only quote boundaries', () => {
  assert.match(options, /美股股票/)
  assert.match(options, /当前期权数据仅供研究/)
  assert.match(options, /缺失的 Greeks 会明确留空/)
  assert.doesNotMatch(options, /美股标的|美股標的/)
})
