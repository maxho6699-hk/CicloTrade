import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import test from 'node:test'

const { readFileSync } = await import('node:fs')
const stockLogoSource = readFileSync(new URL('../src/components/StockLogo.tsx', import.meta.url), 'utf8')
const v2 = readFileSync(new URL('../src/components/v2/V2Primitives.tsx', import.meta.url), 'utf8')
const discover = readFileSync(new URL('../src/pages/DiscoverV2Page.tsx', import.meta.url), 'utf8')
const marketOverview = readFileSync(new URL('../src/components/MarketOverview.tsx', import.meta.url), 'utf8')
const workspaceContext = readFileSync(new URL('../src/api/WorkspaceContext.tsx', import.meta.url), 'utf8')

const covered: Array<[string, string]> = [
  ['US', 'AAPL'], ['US', 'NVDA'], ['US', 'MSFT'], ['US', 'AMZN'], ['US', 'GOOGL'], ['US', 'META'],
  ['US', 'AVGO'], ['US', 'JPM'], ['US', 'TSLA'], ['US', 'ADBE'], ['US', 'NFLX'], ['US', 'COST'],
  ['US', 'PLTR'], ['US', 'SPY'], ['US', 'BABA'],
  ['CN', '600519'], ['CN', '300750'], ['CN', '601318'], ['CN', '000001'], ['CN', '600036'], ['CN', '000858'],
]

test('all actual stock identities resolve to verified local logo assets', async () => {
  const registryUrl = new URL('../src/data/stockLogoRegistry.ts', import.meta.url)
  assert.equal(existsSync(registryUrl), true, 'stock logo registry is missing')
  const logoModule = await import('../src/data/stockLogoRegistry.ts')
  const resolve = (logoModule as Record<string, unknown>).resolveStockLogo
  assert.equal(typeof resolve, 'function')
  for (const [market, symbol] of covered) {
    const path = (resolve as (market: string, symbol: string) => string | null)(market, symbol)
    assert.match(path || '', /^\/stock-logos\/[a-z0-9.-]+\.png$/)
    assert.equal(existsSync(new URL(`../public${path}`, import.meta.url)), true, `${market}:${symbol} missing ${path}`)
  }
  assert.equal((resolve as (market: string, symbol: string) => string | null)('CN', 'BABA'), '/stock-logos/baba.us.png')
})

test('shared stock identity components render official images without letter completion states', () => {
  assert.match(v2, /<StockLogo symbol=\{symbol\} market=\{market\}/)
  assert.match(marketOverview, /<StockLogo symbol=\{item\.symbol\} market=\{market\}/)
  assert.doesNotMatch(discover, /function CandidateLogo|discover-company-logo is-fallback/)
  assert.match(discover, /<StockTaskBadge symbol=\{item\.symbol\}[^>]*market=\{marketName\(item\.market\)\}/)
  assert.doesNotMatch(stockLogoSource, /<b[^>]*>\{normalized\.slice/)
})

test('duplicate logo hashes are allowed only for an explicitly audited shared corporate mark', () => {
  const manifest = JSON.parse(readFileSync(new URL('../public/stock-logos/manifest.json', import.meta.url), 'utf8')) as Record<string, { entity?: string; sha256: string; shared_brand_group?: string }>
  assert.equal(manifest['CN:601318'].entity, 'Ping An Insurance')
  assert.equal(manifest['CN:000001'].entity, 'Ping An Bank')
  assert.equal(manifest['CN:601318'].shared_brand_group, 'PING_AN')
  assert.equal(manifest['CN:000001'].shared_brand_group, 'PING_AN')
  const byHash = new Map<string, Array<{ key: string; group?: string }>>()
  for (const [key, entry] of Object.entries(manifest)) byHash.set(entry.sha256, [...(byHash.get(entry.sha256) ?? []), { key, group: entry.shared_brand_group }])
  for (const entries of byHash.values()) {
    if (entries.length < 2) continue
    assert.ok(entries[0].group && entries.every((entry) => entry.group === entries[0].group), `unaudited duplicate logo: ${entries.map((entry) => entry.key).join(', ')}`)
  }
})

test('watchlist write failures refresh authoritative account state before surfacing the error', () => {
  assert.match(workspaceContext, /const changeWatchlist = useCallback\(async[\s\S]*?try \{[\s\S]*?apiUpdateWatchlist[\s\S]*?\} catch \(caught\) \{[\s\S]*?await loadBootstrap\(\)\.catch[\s\S]*?throw caught/)
  assert.match(workspaceContext, /apiUpdateWatchlist\(market, symbol, remove\)/)
})
