import { discoverMiniCacheKey, timeframeForDiscoverMiniPeriod, type DiscoverMiniPeriod } from './discoverMiniK.ts'

type SparklineCacheEntry<T> = {
  controller: AbortController
  promise: Promise<T>
  subscribers: number
  settled: boolean
  expiresAt: number
}

type SparklineCacheOptions = {
  ttlMs?: number
  now?: () => number
}

export function createDiscoverSparklineCache<T>(
  fetcher: (symbol: string, timeframe: string, signal: AbortSignal) => Promise<T>,
  options: SparklineCacheOptions = {},
) {
  const ttlMs = options.ttlMs ?? 5 * 60 * 1000
  const now = options.now ?? Date.now
  const entries = new Map<string, SparklineCacheEntry<T>>()

  const subscribe = (symbol: string, period: DiscoverMiniPeriod) => {
    const normalizedSymbol = symbol.trim().toUpperCase()
    const key = discoverMiniCacheKey(normalizedSymbol, period)
    let entry = entries.get(key)
    if (entry?.settled && entry.expiresAt <= now()) {
      entries.delete(key)
      entry = undefined
    }
    if (!entry) {
      const controller = new AbortController()
      const next: SparklineCacheEntry<T> = {
        controller,
        promise: Promise.resolve(undefined as T),
        subscribers: 0,
        settled: false,
        expiresAt: Number.POSITIVE_INFINITY,
      }
      next.promise = fetcher(normalizedSymbol, timeframeForDiscoverMiniPeriod(period), controller.signal).then(
        (result) => {
          if (entries.get(key) === next) {
            next.settled = true
            next.expiresAt = now() + ttlMs
          }
          return result
        },
        (error) => {
          if (entries.get(key) === next) entries.delete(key)
          throw error
        },
      )
      entries.set(key, next)
      entry = next
    }
    entry.subscribers += 1
    let released = false
    return {
      promise: entry.promise,
      release() {
        if (released) return
        released = true
        entry.subscribers = Math.max(0, entry.subscribers - 1)
        if (entry.subscribers === 0 && !entry.settled) {
          if (entries.get(key) === entry) entries.delete(key)
          entry.controller.abort()
        }
      },
    }
  }

  return { subscribe, size: () => entries.size }
}
