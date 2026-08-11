import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createInitialWorkspace,
  ensureLayoutSlots,
  normalizeWorkspace,
  updateChartSlot,
  type ChartSlotState,
} from '../src/components/chartWorkspaceModel.ts'

const initial: ChartSlotState = {
  id: 'chart-1',
  symbol: 'AAPL',
  market: 'US',
  timeframe: '日线',
}

test('creates eight stable panes without discarding them when returning to one pane', () => {
  const expanded = ensureLayoutSlots(createInitialWorkspace(initial), 'eight-grid')
  assert.equal(expanded.slots.length, 8)
  assert.deepEqual(expanded.slots.map((slot) => slot.id), [
    'chart-1', 'chart-2', 'chart-3', 'chart-4', 'chart-5', 'chart-6', 'chart-7', 'chart-8',
  ])
  const restored = ensureLayoutSlots({ ...expanded, activeSlotId: 'chart-8' }, 'one')
  assert.equal(restored.layout, 'one')
  assert.equal(restored.slots.length, 8)
  assert.equal(restored.activeSlotId, 'chart-1')
})

test('updates only the active pane until an explicit sync option is enabled', () => {
  const base = ensureLayoutSlots(createInitialWorkspace(initial), 'two-columns')
  const isolated = updateChartSlot(base, 'chart-2', { symbol: 'MSFT', timeframe: '3小时' })
  assert.equal(isolated.slots[0].symbol, 'AAPL')
  assert.equal(isolated.slots[0].timeframe, '日线')
  assert.equal(isolated.slots[1].symbol, 'MSFT')
  assert.equal(isolated.slots[1].timeframe, '3小时')

  const synced = updateChartSlot({
    ...isolated,
    sync: { ...isolated.sync, symbol: true, timeframe: true },
  }, 'chart-2', { symbol: 'TSLA', market: 'US', timeframe: '15分' })
  assert.deepEqual(synced.slots.slice(0, 2).map((slot) => slot.symbol), ['TSLA', 'TSLA'])
  assert.deepEqual(synced.slots.slice(0, 2).map((slot) => slot.timeframe), ['15分', '15分'])
})

test('migrates the old boolean sync flag and rejects unsupported fake timeframes', () => {
  const migrated = normalizeWorkspace({
    layout: 'one',
    sync: true,
    slots: [{ ...initial, timeframe: '1秒' }],
  }, initial)
  assert.equal(migrated.sync.timeframe, true)
  assert.equal(migrated.sync.symbol, false)
  assert.equal(migrated.slots[0].timeframe, '日线')
})

test('persists explicit crosshair, time and visible-date synchronization choices', () => {
  const normalized = normalizeWorkspace({
    layout: 'two-columns',
    sync: { symbol: false, timeframe: false, crosshair: true, time: true, dateRange: true },
    slots: [initial, { ...initial, id: 'chart-2', symbol: 'MSFT' }],
  }, initial)

  assert.equal(normalized.sync.crosshair, true)
  assert.equal(normalized.sync.time, true)
  assert.equal(normalized.sync.dateRange, true)
})
