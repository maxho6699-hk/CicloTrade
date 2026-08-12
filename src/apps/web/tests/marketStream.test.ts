import assert from 'node:assert/strict'
import test from 'node:test'
import {
  applyFormingBarOverlay,
  decodeMarketStreamEvent,
  shouldShowRealtimeLabel,
  type FormingMarketBar,
} from '../src/api/marketStream.ts'
import type { Candle } from '../src/types.ts'

const canonical: Candle[] = [{ time: '2026-08-12T01:00:00Z', open: 100, high: 102, low: 99, close: 101, volume: 50 }]
const forming: FormingMarketBar = {
  sequence: 7, symbol: 'AAPL', timeframe: '5分', bar_start: '2026-08-12T01:00:00Z',
  open: 100, high: 104, low: 99, close: 103, volume: 78, state: 'forming', forming: true,
  observed_at: '2026-08-12T01:03:00Z', visible_as_of: '2026-08-12T01:03:00Z',
  realtime: true, authorized: true, stale: false,
}

test('forming-bar decoder accepts only the public incremental contract', () => {
  const decoded = decodeMarketStreamEvent('forming_bar', JSON.stringify(forming))
  assert.deepEqual(decoded, { type: 'forming_bar', bar: forming })
  assert.equal(decodeMarketStreamEvent('forming_bar', JSON.stringify({ ...forming, provider: 'secret' })), null)
  assert.equal(decodeMarketStreamEvent('forming_bar', JSON.stringify({ ...forming, stale: 'no' })), null)
  assert.deepEqual(decodeMarketStreamEvent('status', '{"state":"catching_up"}'), { type: 'status', state: 'catching_up' })
  assert.equal(decodeMarketStreamEvent('status', '{"state":"unknown"}'), null)
})

test('forming overlay replaces or appends a display copy without changing canonical history', () => {
  const replaced = applyFormingBarOverlay(canonical, forming)
  assert.notEqual(replaced, canonical)
  assert.equal(replaced.length, 1)
  assert.equal(replaced[0].close, 103)
  assert.equal(canonical[0].close, 101)

  const appended = applyFormingBarOverlay(canonical, { ...forming, bar_start: '2026-08-12T01:05:00Z' })
  assert.equal(appended.length, 2)
  assert.equal(canonical.length, 1)
  assert.deepEqual(applyFormingBarOverlay([], forming), [{ time: forming.bar_start, open: 100, high: 104, low: 99, close: 103, volume: 78 }])
  assert.equal(applyFormingBarOverlay(canonical, { ...forming, bar_start: '2026-08-12T00:55:00Z' }), canonical)
})

test('realtime label fails closed for stale, unauthorized, or non-connected stream states', () => {
  assert.equal(shouldShowRealtimeLabel(forming, 'connected'), true)
  assert.equal(shouldShowRealtimeLabel({ ...forming, stale: true }, 'connected'), false)
  assert.equal(shouldShowRealtimeLabel({ ...forming, authorized: false }, 'connected'), false)
  assert.equal(shouldShowRealtimeLabel(forming, 'catching_up'), false)
  assert.equal(shouldShowRealtimeLabel(forming, 'disconnected'), false)
})
