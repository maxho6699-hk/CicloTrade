import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const root = new URL('../src/', import.meta.url)
const more = readFileSync(new URL('pages/MorePage.tsx', root), 'utf8')
const moreRoute = readFileSync(new URL('pages/MoreRoute.tsx', root), 'utf8')
const reports = readFileSync(new URL('pages/ReportsPage.tsx', root), 'utf8')
const earnings = readFileSync(new URL('pages/EarningsForecastPage.tsx', root), 'utf8')
const earningsOption = readFileSync(new URL('components/EarningsOptionStructure.tsx', root), 'utf8')
const lab = readFileSync(new URL('pages/ProfessionalLabPage.tsx', root), 'utf8')
const labApi = readFileSync(new URL('api/labStress.ts', root), 'utf8')
const labBacktests = readFileSync(new URL('components/lab/BacktestWorkspace.tsx', root), 'utf8')
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
  assert.match(more, /<Link className="feature-card-main"/)
  assert.match(more, /Promise\.resolve\(\)\s*\.then\(\(\) => onRecordRecent\(item\.key, catalogSnapshot\.preferences\.version\)\)/)
  assert.doesNotMatch(more, /preventDefault\(/)
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
  assert.match(earnings, /不会用虚构公司、历史财报或推测日期填充空白/)
  assert.match(earnings, /只读结果/)
})

test('professional lab reads the real queue without fabricating runnable inputs or results', () => {
  assert.match(lab, /BacktestWorkspace/)
  assert.match(lab, /真实队列已接线/)
  assert.match(labBacktests, /backtestApi\.listJobs/)
  assert.match(labBacktests, /backtestApi\.getJob/)
  assert.match(labBacktests, /backtestApi\.prepareJob/)
  assert.match(labBacktests, /backtestApi\.cancelJob/)
  assert.match(labBacktests, /backtestApi\.downloadArtifact/)
  assert.match(labBacktests, /准备真实回测任务/)
  assert.match(labBacktests, /相同参数会复用同一个幂等请求/)
  assert.doesNotMatch(`${lab}\n${labBacktests}`, /策略代码编辑器|手续费（%）|滑点（%）|自然语言生成策略/)
  assert.match(labBacktests, /没有服务端结果时不生成收益、回撤、OOS\/WF/)
  assert.match(lab, /尚未生成压力测试结论/)
  assert.doesNotMatch(labBacktests, /lease_token|heartbeat_at|fencing_epoch|storage_key/)
})

test('locked option research only links when the server provides an upgrade path', () => {
  assert.match(earningsOption, /option\.upgrade_path \? \(\s*<Link className="button primary" to=\{option\.upgrade_path\}/)
  assert.match(earningsOption, /: \(\s*<button className="button secondary" type="button" disabled>当前不开放新购<\/button>/)
})

test('professional lab pressure scenarios come only from the authenticated server catalog', () => {
  assert.match(labApi, /fetchLabStressCatalog/)
  assert.match(labApi, /catalog_sha256/)
  assert.match(labApi, /crypto\.subtle\.digest/)
  assert.match(lab, /fetchLabStressCatalog/)
  assert.match(lab, /压力场景目录正在读取|压力场景目录不可用/)
  assert.doesNotMatch(lab, /value="market_drawdown"|value="earnings_gap"|value="extreme_event"/)
  assert.doesNotMatch(lab, /-20% \/ 波动率 \+50%|-10% \/ 波动率 \+35%|-35% \/ 波动率 \+100%/)
  assert.match(labApi, /JSON\.stringify\(\{ scenario_key: scenarioKey \}\)/)
})

test('option workspace labels stocks and keeps research-only quote boundaries', () => {
  assert.match(options, /美股股票/)
  assert.match(options, /当前期权数据仅供研究/)
  assert.match(options, /缺失的 Greeks 会明确留空/)
  assert.doesNotMatch(options, /美股标的|美股標的/)
})
