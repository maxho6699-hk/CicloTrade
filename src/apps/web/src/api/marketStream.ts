import { authenticatedStreamRequest } from './client.ts'
import type { Candle } from '../types.ts'

export type MarketStreamConnectionState = 'connecting' | 'connected' | 'catching_up' | 'disconnected'

export interface FormingMarketBar {
  sequence: number
  symbol: string
  timeframe: string
  bar_start: string | number
  open: number
  high: number
  low: number
  close: number
  volume: number
  state: 'forming'
  forming: true
  observed_at: string
  visible_as_of: string
  realtime: boolean
  authorized: boolean
  stale: boolean
}

export type MarketStreamEvent =
  | { type: 'status'; state: Exclude<MarketStreamConnectionState, 'connecting'> }
  | { type: 'forming_bar'; bar: FormingMarketBar }

export type MarketStreamListener = (event: MarketStreamEvent) => void
export type MarketStreamSubscription = (symbol: string, timeframe: string, listener: MarketStreamListener) => () => void
export type MarketStreamResponseTransport = (path: string, init?: RequestInit) => Promise<Response>

const FORMING_BAR_KEYS = [
  'sequence', 'symbol', 'timeframe', 'bar_start', 'open', 'high', 'low', 'close', 'volume',
  'state', 'forming', 'observed_at', 'visible_as_of', 'realtime', 'authorized', 'stale',
]

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]) {
  const keys = Object.keys(value)
  return keys.length === expected.length && keys.every((key) => expected.includes(key))
}

function validTimestamp(value: unknown): value is string {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
}

function validBarStart(value: unknown): value is string | number {
  return (typeof value === 'string' && Number.isFinite(Date.parse(value)))
    || (typeof value === 'number' && Number.isFinite(value) && value > 0)
}

function validBar(value: unknown): value is FormingMarketBar {
  if (!isRecord(value) || !exactKeys(value, FORMING_BAR_KEYS)) return false
  const numbers = [value.open, value.high, value.low, value.close, value.volume]
  return Number.isSafeInteger(value.sequence) && Number(value.sequence) >= 0
    && typeof value.symbol === 'string' && value.symbol.length > 0 && value.symbol.length <= 32
    && typeof value.timeframe === 'string' && value.timeframe.length > 0 && value.timeframe.length <= 32
    && validBarStart(value.bar_start)
    && numbers.every((item) => typeof item === 'number' && Number.isFinite(item))
    && Number(value.open) > 0 && Number(value.high) > 0 && Number(value.low) > 0 && Number(value.close) > 0 && Number(value.volume) >= 0
    && Number(value.low) <= Number(value.open) && Number(value.low) <= Number(value.close)
    && Number(value.high) >= Number(value.open) && Number(value.high) >= Number(value.close)
    && value.state === 'forming' && value.forming === true
    && validTimestamp(value.observed_at) && validTimestamp(value.visible_as_of)
    && typeof value.realtime === 'boolean' && typeof value.authorized === 'boolean' && typeof value.stale === 'boolean'
}

export function decodeMarketStreamEvent(eventName: string, data: string): MarketStreamEvent | null {
  let payload: unknown
  try {
    payload = JSON.parse(data)
  } catch {
    return null
  }
  if (eventName === 'forming_bar') return validBar(payload) ? { type: 'forming_bar', bar: payload } : null
  if (eventName === 'status' && isRecord(payload) && exactKeys(payload, ['state'])
    && (payload.state === 'connected' || payload.state === 'catching_up' || payload.state === 'disconnected')) {
    return { type: 'status', state: payload.state }
  }
  return null
}

function barEpoch(value: string | number) {
  if (typeof value === 'number') return value > 10_000_000_000 ? value : value * 1000
  return Date.parse(value)
}

/** Display-only overlay; canonical historical candles remain immutable. */
export function applyFormingBarOverlay(candles: Candle[], bar: FormingMarketBar | null | undefined): Candle[] {
  if (!bar || !isRealtimeFormingDisplay(bar)) return candles
  const overlay: Candle = {
    time: bar.bar_start, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume,
  }
  if (candles.length === 0) return [overlay]
  const latestEpoch = barEpoch(candles.at(-1)!.time)
  const overlayEpoch = barEpoch(bar.bar_start)
  if (!Number.isFinite(latestEpoch) || !Number.isFinite(overlayEpoch) || overlayEpoch < latestEpoch) return candles
  if (overlayEpoch === latestEpoch) return [...candles.slice(0, -1), overlay]
  return [...candles, overlay]
}

export function isRealtimeFormingDisplay(bar: FormingMarketBar | null | undefined) {
  return Boolean(bar && bar.state === 'forming' && bar.forming && bar.realtime && bar.authorized && !bar.stale)
}

export function shouldShowRealtimeLabel(
  bar: FormingMarketBar | null | undefined,
  connectionState: MarketStreamConnectionState | undefined,
) {
  return connectionState === 'connected' && isRealtimeFormingDisplay(bar)
}

function consumeSseFrame(frame: string, listener: MarketStreamListener) {
  let eventName = 'message'
  const data: string[] = []
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim()
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (!data.length) return
  const event = decodeMarketStreamEvent(eventName, data.join('\n'))
  if (event) listener(event)
}

export function createMarketStreamSubscription(
  transport: MarketStreamResponseTransport = authenticatedStreamRequest,
): MarketStreamSubscription {
  return (symbol, timeframe, listener) => {
    const controller = new AbortController()
    const path = `/api/rewrite/v1/market/stream?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`
    void (async () => {
      try {
        const response = await transport(path, { method: 'GET', signal: controller.signal, cache: 'no-store' })
        if (!response.body) throw new Error('行情流没有响应内容。')
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        while (!controller.signal.aborted) {
          const next = await reader.read()
          if (next.done) break
          buffer += decoder.decode(next.value, { stream: true })
          const frames = buffer.split(/\r?\n\r?\n/)
          buffer = frames.pop() ?? ''
          frames.forEach((frame) => consumeSseFrame(frame, listener))
        }
        if (!controller.signal.aborted) listener({ type: 'status', state: 'disconnected' })
      } catch {
        if (!controller.signal.aborted) listener({ type: 'status', state: 'disconnected' })
      }
    })()
    return () => controller.abort()
  }
}

export const subscribeMarketStream = createMarketStreamSubscription()
