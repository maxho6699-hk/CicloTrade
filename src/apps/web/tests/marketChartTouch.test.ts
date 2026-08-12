import assert from 'node:assert/strict'
import test from 'node:test'
import {
  CHART_TOUCH_LONG_PRESS_MS,
  CHART_TOUCH_MOVE_THRESHOLD_PX,
  chartTouchReleaseAction,
  createChartTouchSession,
  updateChartTouchSession,
} from '../src/components/marketChartTouch.ts'

test('coarse pointer tap observes one candle without becoming a drag', () => {
  const started = createChartTouchSession(7, { x: 100, y: 120 }, 1_000)
  const released = updateChartTouchSession(started, { x: 106, y: 126 })

  assert.equal(CHART_TOUCH_MOVE_THRESHOLD_PX, 10)
  assert.equal(released.moved, false)
  assert.equal(chartTouchReleaseAction(released), 'observe')
})

test('coarse pointer movement beyond the threshold stays native pan and does not switch candle', () => {
  const started = createChartTouchSession(8, { x: 80, y: 80 }, 2_000)
  const dragged = updateChartTouchSession(started, { x: 94, y: 80 })

  assert.equal(dragged.moved, true)
  assert.equal(chartTouchReleaseAction(dragged), 'ignore')
})

test('long press enters observation mode and release preserves the selected candle', () => {
  const started = createChartTouchSession(9, { x: 32, y: 48 }, 3_000)
  const observing = { ...started, observing: true }
  const moved = updateChartTouchSession(observing, { x: 77, y: 52 })

  assert.equal(CHART_TOUCH_LONG_PRESS_MS, 420)
  assert.equal(moved.observing, true)
  assert.equal(chartTouchReleaseAction(moved), 'release')
})

test('a cancelled or replaced pointer cannot be confused with the active session', () => {
  const started = createChartTouchSession(10, { x: 0, y: 0 }, 4_000)
  assert.equal(started.pointerId, 10)
  assert.notEqual(started.pointerId, 11)
})
