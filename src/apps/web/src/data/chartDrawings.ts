/** Pure chart-drawing storage and synchronization primitives. */

export type DrawingTime = string | number | { year: number; month: number; day: number }
export interface ChartDrawingPoint { time: DrawingTime; price: number }
export interface ChartDrawing { id: string; tool: string; points: ChartDrawingPoint[]; origin_timeframe?: string; cross_timeframe?: boolean; revision?: number }
export type DrawingPersistence = 'syncing' | 'synced' | 'device-only' | 'conflict' | 'failed'
export interface DrawingScope { userId: number; market: 'US' | 'CN'; symbol: string; timeframe: string; crossTimeframe: boolean }
export interface CachedDrawings { drawings: ChartDrawing[]; savedAt: number }
export interface DrawingTombstone { drawing: ChartDrawing; revision: number; origin_timeframe: string; cross_timeframe: boolean }
export interface DrawingTombstoneRevision { drawing_id: string; revision: number; origin_timeframe: string; cross_timeframe: boolean }
export interface ConflictRefreshDecisionInput {
  capturedScopeToken: string
  currentScopeToken: string
  capturedRequestGeneration: number
  currentRequestGeneration: number
  currentEditGeneration: number
  refreshed: ChartDrawing[]
  latest: ChartDrawing[]
  fallback: Pick<DrawingScope, 'timeframe' | 'crossTimeframe'>
  tombstones?: DrawingTombstone[]
  refreshedTombstones?: DrawingTombstoneRevision[]
  previousRetryKey: string | null
}
export type DrawingOperation =
  | { op: 'upsert'; origin_timeframe: string; cross_timeframe: boolean; revision: number | null; drawing: ChartDrawing }
  | { op: 'delete' | 'restore'; origin_timeframe: string; cross_timeframe: boolean; revision: number; drawing_id: string }

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const TIMEFRAME = /^[\p{L}\p{N}_-]{1,16}$/u
const DATE = /^\d{4}-\d{2}-\d{2}$/
const TOOL_POINTS: Record<string, number> = {
  segment: 2, horizontal: 1, 'horizontal-segment': 2, vertical: 1, ray: 2, straight: 2, parallel: 3, channel: 3, periodic: 2, 'info-line': 2, 'smooth-top': 3, cross: 1,
  rectangle: 2, triangle: 3, parallelogram: 3, circle: 2, ellipse: 2, path: 5,
  wave3: 4, wave5: 6, wave8: 9, 'head-shoulders': 5, 'triangle-pattern': 5, mw: 5, abcd: 4, xabcd: 5, 'three-drive': 7, sine: 6,
  'fib-retracement': 2, 'fib-time': 2, 'fib-extension': 3, 'speed-resistance': 2, 'gann-box': 2, 'gann-angle': 2, 'grid-line': 2, pitchfork: 3, schiff: 3, 'modified-schiff': 3, 'inside-pitchfork': 3, fan: 2,
  'time-ruler': 2, 'space-ruler': 2, 'time-space-ruler': 2, 'long-position': 2, 'short-position': 2, 'price-label': 1, arrow: 2, 'up-arrow': 2, 'down-arrow': 2,
}

function isCalendarDate(value: string) {
  if (!DATE.test(value)) return false
  const parsed = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value
}

export function chartDrawingKey(scope: DrawingScope) {
  return `ciclotrade:drawings:v3:${scope.userId}:${scope.market}:${scope.symbol.toUpperCase()}:${scope.timeframe}:${scope.crossTimeframe ? 'all' : 'current'}`
}

export function legacyChartDrawingKey(symbol: string, timeframe: string, crossTimeframe: boolean) {
  return `ciclotrade:drawings:v2:${symbol.toUpperCase()}:${crossTimeframe ? 'all' : timeframe}`
}

export function hasLegacyChartDrawings(storage: Pick<Storage, 'getItem'>, scope: Pick<DrawingScope, 'symbol' | 'timeframe' | 'crossTimeframe'>) {
  return storage.getItem(legacyChartDrawingKey(scope.symbol, scope.timeframe, scope.crossTimeframe)) !== null
}

export function drawingScopeToken(scope: Pick<DrawingScope, 'userId' | 'market' | 'symbol' | 'timeframe' | 'crossTimeframe'>) {
  return chartDrawingKey(scope)
}

export function isCurrentDrawingScope(token: string, scope: DrawingScope) {
  return token === drawingScopeToken(scope)
}

export function isValidChartDrawing(value: unknown): value is ChartDrawing {
  if (!value || typeof value !== 'object') return false
  const drawing = value as Partial<ChartDrawing>
  if (Object.keys(drawing).some((key) => !['id', 'tool', 'points', 'origin_timeframe', 'cross_timeframe', 'revision'].includes(key))) return false
  if (typeof drawing.id !== 'string' || !UUID.test(drawing.id) || typeof drawing.tool !== 'string' || TOOL_POINTS[drawing.tool] === undefined || !Array.isArray(drawing.points) || drawing.points.length !== TOOL_POINTS[drawing.tool]) return false
  if (drawing.origin_timeframe !== undefined && (typeof drawing.origin_timeframe !== 'string' || !TIMEFRAME.test(drawing.origin_timeframe))) return false
  if (drawing.cross_timeframe !== undefined && typeof drawing.cross_timeframe !== 'boolean') return false
  if (drawing.revision !== undefined && (!Number.isInteger(drawing.revision) || drawing.revision < 1)) return false
  return drawing.points.every((point) => {
    if (!point || typeof point !== 'object' || Object.keys(point).some((key) => key !== 'time' && key !== 'price')) return false
    const typed = point as Partial<ChartDrawingPoint>
    const businessDay = typed.time && typeof typed.time === 'object' && Object.keys(typed.time).every((key) => ['year', 'month', 'day'].includes(key))
      && Number.isInteger(typed.time.year) && Number.isInteger(typed.time.month) && Number.isInteger(typed.time.day)
      && typed.time.year >= 1970 && typed.time.year <= 2100 && typed.time.month >= 1 && typed.time.month <= 12 && typed.time.day >= 1 && typed.time.day <= 31
      && new Date(Date.UTC(typed.time.year, typed.time.month - 1, typed.time.day)).getUTCFullYear() === typed.time.year
      && new Date(Date.UTC(typed.time.year, typed.time.month - 1, typed.time.day)).getUTCMonth() === typed.time.month - 1
      && new Date(Date.UTC(typed.time.year, typed.time.month - 1, typed.time.day)).getUTCDate() === typed.time.day
    return ((typeof typed.time === 'number' && Number.isSafeInteger(typed.time) && typed.time > 0 && typed.time < 4_102_444_800) || (typeof typed.time === 'string' && isCalendarDate(typed.time)) || businessDay)
      && typeof typed.price === 'number' && Number.isFinite(typed.price) && Math.abs(typed.price) <= 1_000_000_000
  })
}

export function readCachedDrawings(storage: Pick<Storage, 'getItem'>, scope: DrawingScope): CachedDrawings | null {
  try {
    const value = JSON.parse(storage.getItem(chartDrawingKey(scope)) ?? 'null') as CachedDrawings | null
    return value && Array.isArray(value.drawings) && value.drawings.length <= 200 && value.drawings.every(isValidChartDrawing) && Number.isFinite(value.savedAt) ? value : null
  } catch { return null }
}

export function writeCachedDrawings(storage: Pick<Storage, 'setItem'>, scope: DrawingScope, drawings: ChartDrawing[]) {
  if (drawings.length > 200 || !drawings.every(isValidChartDrawing)) throw new Error('本机画线数据无效。')
  storage.setItem(chartDrawingKey(scope), JSON.stringify({ drawings, savedAt: Date.now() } satisfies CachedDrawings))
}

function comparable(drawing: ChartDrawing) {
  return JSON.stringify({ id: drawing.id, tool: drawing.tool, points: drawing.points })
}

function wireDrawing(drawing: ChartDrawing): ChartDrawing {
  return { id: drawing.id, tool: drawing.tool, points: drawing.points }
}

function drawingScope(drawing: ChartDrawing, fallback: Pick<DrawingScope, 'timeframe' | 'crossTimeframe'>) {
  return { origin_timeframe: drawing.origin_timeframe ?? fallback.timeframe, cross_timeframe: drawing.cross_timeframe ?? fallback.crossTimeframe }
}

type DrawingOperationIdentity =
  | { drawing_id: string; origin_timeframe: string; cross_timeframe: boolean }
  | { drawing: Pick<ChartDrawing, 'id'>; origin_timeframe: string; cross_timeframe: boolean }

export function drawingOperationKey(value: DrawingOperationIdentity) {
  const drawingId = 'drawing_id' in value ? value.drawing_id : value.drawing.id
  return `${value.origin_timeframe}:${value.cross_timeframe ? '1' : '0'}:${drawingId}`
}

export function drawingOperations(previous: ChartDrawing[], next: ChartDrawing[], fallback: Pick<DrawingScope, 'timeframe' | 'crossTimeframe'>, tombstones: DrawingTombstone[] = []): DrawingOperation[] {
  if (next.length > 200 || !previous.every(isValidChartDrawing) || !next.every(isValidChartDrawing)) throw new Error('画线数据无效或超过上限。')
  const scopedId = (drawing: ChartDrawing) => {
    const location = drawingScope(drawing, fallback)
    return `${location.origin_timeframe}:${location.cross_timeframe ? '1' : '0'}:${drawing.id}`
  }
  const oldById = new Map(previous.map((drawing) => [scopedId(drawing), drawing]))
  const nextById = new Map(next.map((drawing) => [scopedId(drawing), drawing]))
  const deletedById = new Map(tombstones.map((tombstone) => [
    `${tombstone.origin_timeframe}:${tombstone.cross_timeframe ? '1' : '0'}:${tombstone.drawing.id}`,
    tombstone,
  ]))
  const operations: DrawingOperation[] = []
  for (const drawing of next) {
    const before = oldById.get(scopedId(drawing))
    const location = drawingScope(drawing, fallback)
    const tombstone = deletedById.get(scopedId(drawing))
    if (!before && tombstone) operations.push({ op: 'restore', ...location, revision: tombstone.revision, drawing_id: drawing.id })
    else if (!before) operations.push({ op: 'upsert', ...location, revision: null, drawing: wireDrawing(drawing) })
    else if (comparable(before) !== comparable(drawing)) operations.push({ op: 'upsert', ...location, revision: before.revision ?? null, drawing: wireDrawing(drawing) })
  }
  for (const drawing of previous) {
    if (!nextById.has(scopedId(drawing))) {
      if (!drawing.revision) throw new Error('本机离线画线尚未同步，不能删除。')
      operations.push({ op: 'delete', ...drawingScope(drawing, fallback), revision: drawing.revision, drawing_id: drawing.id })
    }
  }
  return operations
}

function remoteRevisionSignature(
  drawings: ChartDrawing[],
  fallback: Pick<DrawingScope, 'timeframe' | 'crossTimeframe'>,
  tombstones: DrawingTombstoneRevision[] = [],
) {
  const active = drawings.map((drawing) => {
    const location = drawingScope(drawing, fallback)
    return `a:${location.origin_timeframe}:${location.cross_timeframe ? '1' : '0'}:${drawing.id}:${drawing.revision ?? 0}`
  })
  const deleted = tombstones.map((tombstone) => `d:${drawingOperationKey(tombstone)}:${tombstone.revision}`)
  return [...active, ...deleted].sort().join('|')
}

export function mergeDrawingTombstoneRevisions(
  local: DrawingTombstone[],
  refreshed: DrawingTombstoneRevision[],
  latest: ChartDrawing[],
  fallback: Pick<DrawingScope, 'timeframe' | 'crossTimeframe'>,
) {
  const localByKey = new Map(local.map((tombstone) => [drawingOperationKey(tombstone), tombstone]))
  const latestByKey = new Map(latest.map((drawing) => {
    const location = drawingScope(drawing, fallback)
    return [drawingOperationKey({ drawing, ...location }), drawing] as const
  }))
  const merged: DrawingTombstone[] = []
  for (const revision of refreshed) {
    const key = drawingOperationKey(revision)
    const drawing = localByKey.get(key)?.drawing ?? latestByKey.get(key)
    if (!drawing) continue
    merged.push({
      drawing,
      revision: revision.revision,
      origin_timeframe: revision.origin_timeframe,
      cross_timeframe: revision.cross_timeframe,
    })
  }
  return merged
}

export function decideConflictRefresh(input: ConflictRefreshDecisionInput):
  | { kind: 'stale' | 'replace' | 'failed' }
  | { kind: 'retry'; retryKey: string } {
  if (input.capturedScopeToken !== input.currentScopeToken || input.capturedRequestGeneration !== input.currentRequestGeneration) return { kind: 'stale' }
  let pending = true
  try {
    pending = drawingOperations(input.refreshed, input.latest, input.fallback, input.tombstones).length > 0
  } catch { return { kind: 'failed' } }
  if (!pending) return { kind: 'replace' }
  const retryKey = `${input.currentScopeToken}:${input.currentEditGeneration}:${remoteRevisionSignature(input.refreshed, input.fallback, input.refreshedTombstones)}`
  return retryKey === input.previousRetryKey ? { kind: 'failed' } : { kind: 'retry', retryKey }
}
