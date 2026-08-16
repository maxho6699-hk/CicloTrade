import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('reports page exposes stable and expanded research as explicit URL-bound scopes', () => {
  const source = readFileSync(new URL('../src/pages/ReportsPage.tsx', import.meta.url), 'utf8')

  assert.match(source, /StrategyResearch97Panel/)
  assert.match(source, /research_scope/)
  assert.match(source, /13 股稳定研究/)
  assert.match(source, /97 只股票扩容研究/)
  assert.match(source, /researchScope === 'expanded' \? <StrategyResearch97Panel \/> : <SystemCycleResearchPanel \/>/)
})

test('reports page never renders local demo reports or fabricated model performance', () => {
  const source = readFileSync(new URL('../src/pages/ReportsPage.tsx', import.meta.url), 'utf8')

  assert.doesNotMatch(source, /modelReports|reportReturns|12\.84|preview\.csv|界面演示数据|稳定性|胜率/)
  assert.match(source, /真实模型注册表尚未开放到报告接口/)
  assert.match(source, /只有服务端返回真实权益快照后才显示/)
})

test('more catalog exposes a pinable expanded strategy research entry', () => {
  const catalog = readFileSync(new URL('../../../../core/feature_catalog.py', import.meta.url), 'utf8')
  const copy = readFileSync(new URL('../src/domain/featureCatalog.ts', import.meta.url), 'utf8')

  assert.match(catalog, /FeatureDefinition\("strategy-research"/)
  assert.match(catalog, /\/reports\?view=影子策略研究&research_scope=expanded/)
  assert.match(copy, /feature\.strategy_research\.title/)
  assert.match(copy, /97 只股票扩容链/)
})
