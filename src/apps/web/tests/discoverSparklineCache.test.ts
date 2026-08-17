import assert from 'node:assert/strict'
import test from 'node:test'

import { createDiscoverSparklineCache } from '../src/data/discoverSparklineCache.ts'

test('deduplicates in-flight requests and aborts only after the final subscriber leaves', async () => {
  let calls = 0
  let signal: AbortSignal | null = null
  const cache = createDiscoverSparklineCache<string[]>(async (_symbol, _timeframe, nextSignal) => {
    calls += 1
    signal = nextSignal
    await new Promise((_resolve, reject) => nextSignal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true }))
    return []
  })
  const first = cache.subscribe('AAPL', '1W')
  const second = cache.subscribe('aapl', '1W')
  first.promise.catch(() => undefined)
  second.promise.catch(() => undefined)
  assert.equal(calls, 1)
  first.release()
  assert.equal(signal?.aborted, false)
  second.release()
  assert.equal(signal?.aborted, true)
  assert.equal(cache.size(), 0)
})

test('reuses successful data only within the TTL', async () => {
  let now = 1_000
  let calls = 0
  const cache = createDiscoverSparklineCache(async () => {
    calls += 1
    return [`call-${calls}`]
  }, { ttlMs: 100, now: () => now })
  const first = cache.subscribe('AAPL', '1D')
  assert.deepEqual(await first.promise, ['call-1'])
  first.release()
  const cached = cache.subscribe('AAPL', '1D')
  assert.deepEqual(await cached.promise, ['call-1'])
  cached.release()
  assert.equal(calls, 1)
  now += 101
  const refreshed = cache.subscribe('AAPL', '1D')
  assert.deepEqual(await refreshed.promise, ['call-2'])
  refreshed.release()
  assert.equal(calls, 2)
})

test('removes failed requests so a retry can start immediately', async () => {
  let calls = 0
  const cache = createDiscoverSparklineCache(async () => {
    calls += 1
    if (calls === 1) throw new Error('network')
    return ['ok']
  })
  const failed = cache.subscribe('AAPL', '1M')
  await assert.rejects(failed.promise, /network/)
  failed.release()
  const retried = cache.subscribe('AAPL', '1M')
  assert.deepEqual(await retried.promise, ['ok'])
  retried.release()
  assert.equal(calls, 2)
})
