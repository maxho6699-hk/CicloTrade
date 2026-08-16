import assert from 'node:assert/strict'
import test from 'node:test'
import { decodeLabStressCatalog, fetchLabStressCatalog, runLabStress } from '../src/api/labStress.ts'

const scenario = { key: 'market_drawdown', label: '市场回撤', price_shock_pct: -20, volatility_shock_pct: 50, gap_risk: 'normal' }
const content = { method_version: 'lab-stress.v1', fee_bps: 5, slippage_bps: 10, scenarios: [scenario] }
const realCatalog = {
  method_version: 'lab-stress.v1',
  fee_bps: 5,
  slippage_bps: 10,
  scenarios: [
    { key: 'earnings_gap', label: '财报跳空', price_shock_pct: -10, volatility_shock_pct: 35, gap_risk: 'earnings' },
    { key: 'extreme_event', label: '极端事件', price_shock_pct: -35, volatility_shock_pct: 100, gap_risk: 'extreme' },
    scenario,
  ],
  catalog_sha256: '8050e8a391032d886dd5c7c5e28deffe3f3d5840cc91c8b4484a59e5ade700bf',
}

test('lab stress catalog decoder rejects malformed and duplicate server entries', () => {
  assert.throws(() => decodeLabStressCatalog({ ...content, catalog_sha256: 'a'.repeat(64), scenarios: [{ ...scenario, extra: true }] }))
  assert.throws(() => decodeLabStressCatalog({ ...content, catalog_sha256: 'a'.repeat(64), scenarios: [scenario, scenario] }))
})

test('lab stress fetch verifies canonical catalog hash and POST sends only scenario_key', async () => {
  const valid = { ...content, catalog_sha256: 'a'.repeat(64) }
  await assert.rejects(() => fetchLabStressCatalog(async () => valid), /校验失败/)
  assert.equal((await fetchLabStressCatalog(async () => realCatalog)).catalog_sha256, realCatalog.catalog_sha256)
  let body = ''
  await runLabStress('market_drawdown', async (_path, init) => {
    body = String(init?.body)
    return {} as never
  })
  assert.deepEqual(JSON.parse(body), { scenario_key: 'market_drawdown' })
})
