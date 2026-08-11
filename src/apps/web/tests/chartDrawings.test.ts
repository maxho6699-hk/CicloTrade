import assert from 'node:assert/strict'
import test from 'node:test'
import {
  decideConflictRefresh,
  chartDrawingKey,
  drawingOperations,
  drawingScopeToken,
  hasLegacyChartDrawings,
  isCurrentDrawingScope,
  isValidChartDrawing,
  legacyChartDrawingKey,
  mergeDrawingTombstoneRevisions,
  readCachedDrawings,
  writeCachedDrawings,
  type DrawingScope,
} from '../src/data/chartDrawings.ts'

const scope: DrawingScope = { userId: 7, market: 'US', symbol: 'AAPL', timeframe: '日线', crossTimeframe: false }
const id = '123e4567-e89b-42d3-a456-426614174000'
const drawing = { id, tool: 'segment', points: [{ time: '2026-08-01', price: 100 }, { time: '2026-08-02', price: 101 }], revision: 1 }

function memory(): Storage {
  const values = new Map<string, string>()
  return { get length() { return values.size }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => values.delete(key), setItem: (key, value) => values.set(key, value) } as Storage
}

test('v3 key separates all five scope dimensions', () => {
  const variants = [
    scope, { ...scope, userId: 8 }, { ...scope, market: 'CN' as const }, { ...scope, symbol: 'MSFT' }, { ...scope, timeframe: '周线' }, { ...scope, crossTimeframe: true },
  ]
  assert.equal(new Set(variants.map(chartDrawingKey)).size, variants.length)
})

test('recognizes v2 local data but does not read or import it', () => {
  const storage = memory()
  storage.setItem(legacyChartDrawingKey(scope.symbol, scope.timeframe, scope.crossTimeframe), JSON.stringify([drawing]))
  assert.equal(hasLegacyChartDrawings(storage, scope), true)
  assert.equal(readCachedDrawings(storage, scope), null)
})

test('scope token rejects an asynchronous response for a newly selected chart', () => {
  const token = drawingScopeToken(scope)
  assert.equal(isCurrentDrawingScope(token, scope), true)
  assert.equal(isCurrentDrawingScope(token, { ...scope, symbol: 'MSFT' }), false)
})

test('diff creates add, delete, and validates restore-safe drawing input', () => {
  const created = { ...drawing, revision: undefined }
  assert.deepEqual(drawingOperations([], [created], scope), [{ op: 'upsert', origin_timeframe: '日线', cross_timeframe: false, revision: null, drawing: { id, tool: 'segment', points: created.points } }])
  assert.deepEqual(drawingOperations([drawing], [], scope), [{ op: 'delete', origin_timeframe: '日线', cross_timeframe: false, revision: 1, drawing_id: id }])
  const storage = memory()
  writeCachedDrawings(storage, scope, [drawing])
  assert.deepEqual(readCachedDrawings(storage, scope)?.drawings, [drawing])
})

test('cross view retains each record origin when deleting mixed scopes', () => {
  const normal = { ...drawing, origin_timeframe: '日线', cross_timeframe: false }
  const weekly = { ...drawing, id: '123e4567-e89b-42d3-a456-426614174001', origin_timeframe: '周线', cross_timeframe: true }
  const operations = drawingOperations([normal, weekly], [], { ...scope, crossTimeframe: true })
  assert.deepEqual(operations.map(({ origin_timeframe, cross_timeframe, drawing_id }) => ({ origin_timeframe, cross_timeframe, drawing_id })), [
    { origin_timeframe: '日线', cross_timeframe: false, drawing_id: normal.id },
    { origin_timeframe: '周线', cross_timeframe: true, drawing_id: weekly.id },
  ])
})

test('synced delete undo restores with tombstone revision and redo deletes latest revision', () => {
  const scoped = { ...drawing, origin_timeframe: '日线', cross_timeframe: false }
  const deleted = drawingOperations([scoped], [], scope)
  assert.deepEqual(deleted, [{ op: 'delete', origin_timeframe: '日线', cross_timeframe: false, revision: 1, drawing_id: id }])
  const restored = drawingOperations([], [scoped], scope, [{ drawing: scoped, revision: 2, origin_timeframe: '日线', cross_timeframe: false }])
  assert.deepEqual(restored, [{ op: 'restore', origin_timeframe: '日线', cross_timeframe: false, revision: 2, drawing_id: id }])
  assert.deepEqual(drawingOperations([{ ...scoped, revision: 3 }], [], scope), [{ op: 'delete', origin_timeframe: '日线', cross_timeframe: false, revision: 3, drawing_id: id }])
})

test('ordinary 409 advances baseline, retries once, and can become synced', () => {
  const localA = { ...drawing, origin_timeframe: '日线', cross_timeframe: false, points: [{ time: '2026-08-01', price: 105 }, drawing.points[1]] }
  const refreshed = [{ ...drawing, origin_timeframe: '日线', cross_timeframe: false, revision: 2, points: [{ time: '2026-08-01', price: 99 }, drawing.points[1]] }]
  const decision = decideConflictRefresh({
    capturedScopeToken: drawingScopeToken(scope), currentScopeToken: drawingScopeToken(scope),
    capturedRequestGeneration: 1, currentRequestGeneration: 1, currentEditGeneration: 1,
    refreshed, latest: [localA], fallback: scope, previousRetryKey: null,
  })
  assert.equal(decision.kind, 'retry')
  assert.equal(drawingOperations(refreshed, [localA], scope)[0].revision, 2)
  assert.deepEqual(decideConflictRefresh({
    capturedScopeToken: drawingScopeToken(scope), currentScopeToken: drawingScopeToken(scope),
    capturedRequestGeneration: 2, currentRequestGeneration: 2, currentEditGeneration: 1,
    refreshed: [{ ...localA, revision: 3 }], latest: [{ ...localA, revision: 3 }], fallback: scope, previousRetryKey: null,
  }), { kind: 'replace' })
})

test('A to B edits survive two 409 refreshes while baselines advance', () => {
  const editB = { ...drawing, origin_timeframe: '日线', cross_timeframe: false, points: [{ time: '2026-08-01', price: 110 }, drawing.points[1]] }
  let visible = [editB]
  const first = decideConflictRefresh({
    capturedScopeToken: drawingScopeToken(scope), currentScopeToken: drawingScopeToken(scope),
    capturedRequestGeneration: 1, currentRequestGeneration: 1, currentEditGeneration: 2,
    refreshed: [{ ...drawing, origin_timeframe: '日线', cross_timeframe: false, revision: 2 }], latest: visible, fallback: scope, previousRetryKey: null,
  })
  assert.equal(first.kind, 'retry')
  assert.deepEqual(visible, [editB])
  const second = decideConflictRefresh({
    capturedScopeToken: drawingScopeToken(scope), currentScopeToken: drawingScopeToken(scope),
    capturedRequestGeneration: 2, currentRequestGeneration: 2, currentEditGeneration: 2,
    refreshed: [{ ...drawing, origin_timeframe: '日线', cross_timeframe: false, revision: 3 }], latest: visible, fallback: scope,
    previousRetryKey: first.kind === 'retry' ? first.retryKey : null,
  })
  assert.equal(second.kind, 'retry')
  assert.deepEqual(visible, [editB])
})

test('same edit generation and remote revisions cannot retry forever', () => {
  const editB = { ...drawing, origin_timeframe: '日线', cross_timeframe: false, points: [{ time: '2026-08-01', price: 110 }, drawing.points[1]] }
  const input = {
    capturedScopeToken: drawingScopeToken(scope), currentScopeToken: drawingScopeToken(scope),
    capturedRequestGeneration: 3, currentRequestGeneration: 3, currentEditGeneration: 2,
    refreshed: [{ ...drawing, origin_timeframe: '日线', cross_timeframe: false, revision: 3 }], latest: [editB], fallback: scope,
  }
  const once = decideConflictRefresh({ ...input, previousRetryKey: null })
  assert.equal(once.kind, 'retry')
  assert.deepEqual(decideConflictRefresh({ ...input, previousRetryKey: once.kind === 'retry' ? once.retryKey : null }), { kind: 'failed' })
})

test('tombstone refresh advances restore revision after another device restore-delete cycle', () => {
  const restoredLocal = { ...drawing, origin_timeframe: '日线', cross_timeframe: false }
  const refreshedTombstones = [{ drawing_id: id, revision: 4, origin_timeframe: '日线', cross_timeframe: false }]
  const merged = mergeDrawingTombstoneRevisions(
    [{ drawing: restoredLocal, revision: 2, origin_timeframe: '日线', cross_timeframe: false }],
    refreshedTombstones,
    [restoredLocal],
    scope,
  )
  assert.equal(merged[0].revision, 4)
  assert.deepEqual(drawingOperations([], [restoredLocal], scope, merged), [
    { op: 'restore', origin_timeframe: '日线', cross_timeframe: false, revision: 4, drawing_id: id },
  ])
  const decision = decideConflictRefresh({
    capturedScopeToken: drawingScopeToken(scope), currentScopeToken: drawingScopeToken(scope),
    capturedRequestGeneration: 4, currentRequestGeneration: 4, currentEditGeneration: 3,
    refreshed: [], latest: [restoredLocal], fallback: scope, tombstones: merged,
    refreshedTombstones, previousRetryKey: null,
  })
  assert.equal(decision.kind, 'retry')
})

test('rejects malicious unknown fields, non-finite prices, and oversized cache payloads', () => {
  assert.equal(isValidChartDrawing({ ...drawing, evil: true }), false)
  assert.equal(isValidChartDrawing({ ...drawing, points: [{ time: '2026-08-01', price: Number.NaN }, drawing.points[1]] }), false)
  assert.equal(isValidChartDrawing({ ...drawing, points: [{ time: '2026-08-01T12:00:00Z', price: 100 }, drawing.points[1]] }), false)
  assert.equal(isValidChartDrawing({ ...drawing, points: [{ time: 1.5, price: 100 }, drawing.points[1]] }), false)
  assert.equal(isValidChartDrawing({ ...drawing, points: [{ time: { year: 2026, month: 8, day: 1 }, price: 100 }, drawing.points[1]] }), true)
  assert.throws(() => drawingOperations([], Array.from({ length: 201 }, () => drawing), scope))
})
