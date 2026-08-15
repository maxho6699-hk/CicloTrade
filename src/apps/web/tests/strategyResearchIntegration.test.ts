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

test('more catalog exposes a pinable expanded strategy research entry', () => {
  const catalog = readFileSync(new URL('../../../../core/feature_catalog.py', import.meta.url), 'utf8')
  const copy = readFileSync(new URL('../src/domain/featureCatalog.ts', import.meta.url), 'utf8')

  assert.match(catalog, /FeatureDefinition\("strategy-research"/)
  assert.match(catalog, /\/reports\?view=影子策略研究&research_scope=expanded/)
  assert.match(copy, /feature\.strategy_research\.title/)
  assert.match(copy, /97 标的扩容链/)
})
