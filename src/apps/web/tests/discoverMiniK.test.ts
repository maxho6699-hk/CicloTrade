import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  DISCOVER_MINI_PERIODS,
  discoverMiniCacheKey,
  normalizeDiscoverMiniPeriod,
  timeframeForDiscoverMiniPeriod,
} from '../src/data/discoverMiniK.ts'

const clientSource = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8')

test('normalizes URL period values and defaults to 1D', () => {
  assert.equal(normalizeDiscoverMiniPeriod('1D'), '1D')
  assert.equal(normalizeDiscoverMiniPeriod('1W'), '1W')
  assert.equal(normalizeDiscoverMiniPeriod('1M'), '1M')
  assert.equal(normalizeDiscoverMiniPeriod('bad'), '1D')
  assert.equal(normalizeDiscoverMiniPeriod(null), '1D')
})

test('maps visible periods to the real candles API timeframe', () => {
  assert.deepEqual(DISCOVER_MINI_PERIODS, [
    { key: '1D', timeframe: '日线' },
    { key: '1W', timeframe: '周线' },
    { key: '1M', timeframe: '月线' },
  ])
  assert.equal(timeframeForDiscoverMiniPeriod('1W'), '周线')
})

test('cache keys isolate symbol and timeframe', () => {
  assert.equal(discoverMiniCacheKey(' aapl ', '1D'), 'AAPL::日线')
  assert.equal(discoverMiniCacheKey('AAPL', '1W'), 'AAPL::周线')
  assert.notEqual(discoverMiniCacheKey('AAPL', '1D'), discoverMiniCacheKey('AAPL', '1M'))
})

test('market candle transport forwards an AbortSignal', () => {
  assert.match(clientSource, /fetchMarketCandles\(symbol: string, timeframe: string, signal\?: AbortSignal\)/)
  assert.match(clientSource, /timeframe=\$\{encodeURIComponent\(timeframe\)\}`, \{ signal \}\)/)
})
