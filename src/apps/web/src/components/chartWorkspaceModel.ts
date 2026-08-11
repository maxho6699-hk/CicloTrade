import type { Market } from '../types'

export type ChartLayoutId =
  | 'one'
  | 'two-columns'
  | 'two-rows'
  | 'three-left'
  | 'three-top'
  | 'four-grid'
  | 'six-grid'
  | 'eight-grid'

export interface ChartLayoutDefinition {
  id: ChartLayoutId
  label: string
  shortLabel: string
  count: 1 | 2 | 3 | 4 | 6 | 8
  desktopOnly?: boolean
}

export const CHART_LAYOUTS: ChartLayoutDefinition[] = [
  { id: 'one', label: '单图', shortLabel: '1', count: 1 },
  { id: 'two-columns', label: '左右双图', shortLabel: '2A', count: 2 },
  { id: 'two-rows', label: '上下双图', shortLabel: '2B', count: 2 },
  { id: 'three-left', label: '左主图加右双图', shortLabel: '3A', count: 3 },
  { id: 'three-top', label: '上主图加下双图', shortLabel: '3B', count: 3 },
  { id: 'four-grid', label: '四宫格', shortLabel: '4', count: 4, desktopOnly: true },
  { id: 'six-grid', label: '六宫格', shortLabel: '6', count: 6, desktopOnly: true },
  { id: 'eight-grid', label: '八宫格', shortLabel: '8', count: 8, desktopOnly: true },
]

export interface TimeframeOption {
  value: string
  label: string
  group: '分钟' | '小时' | '日/周/月'
}

export const TIMEFRAME_OPTIONS: TimeframeOption[] = [
  ...[
    ['1分', '1 分钟'], ['2分', '2 分钟'], ['3分', '3 分钟'], ['4分', '4 分钟'],
    ['5分', '5 分钟'], ['10分', '10 分钟'], ['15分', '15 分钟'], ['20分', '20 分钟'],
    ['30分', '30 分钟'], ['45分', '45 分钟'],
  ].map(([value, label]) => ({ value, label, group: '分钟' as const })),
  ...[
    ['1小时', '1 小时'], ['2小时', '2 小时'], ['3小时', '3 小时'],
    ['4小时', '4 小时'], ['6小时', '6 小时'], ['8小时', '8 小时'],
  ].map(([value, label]) => ({ value, label, group: '小时' as const })),
  ...[
    ['日线', '1 日'], ['周线', '1 周'], ['月线', '1 月'],
  ].map(([value, label]) => ({ value, label, group: '日/周/月' as const })),
]

export interface ChartViewportState {
  from: number
  to: number
}

export interface ChartSlotState {
  id: string
  symbol: string
  market: Market
  timeframe: string
  viewport?: ChartViewportState
}

export interface ChartSyncState {
  symbol: boolean
  timeframe: boolean
  crosshair: boolean
  time: boolean
  dateRange: boolean
}

export interface ChartWorkspaceState {
  layout: ChartLayoutId
  activeSlotId: string
  slots: ChartSlotState[]
  sync: ChartSyncState
  inspectorOpen: boolean
}

export const DEFAULT_CHART_SYNC: ChartSyncState = {
  symbol: false,
  timeframe: false,
  crosshair: false,
  time: false,
  dateRange: false,
}

const DEFAULT_SLOT_TIMEFRAMES = ['日线', '3小时', '15分', '周线', '1小时', '5分', '月线', '30分']

export function layoutDefinition(layout: ChartLayoutId) {
  return CHART_LAYOUTS.find((item) => item.id === layout) ?? CHART_LAYOUTS[0]
}

export function createInitialWorkspace(initial: ChartSlotState): ChartWorkspaceState {
  return {
    layout: 'one',
    activeSlotId: initial.id,
    slots: [initial],
    sync: { ...DEFAULT_CHART_SYNC },
    inspectorOpen: false,
  }
}

function isMarket(value: unknown): value is Market {
  return value === 'US' || value === 'CN'
}

function isLayout(value: unknown): value is ChartLayoutId {
  return CHART_LAYOUTS.some((item) => item.id === value)
}

function normalizeSlot(value: unknown, index: number, fallback: ChartSlotState): ChartSlotState | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as Partial<ChartSlotState>
  const symbol = typeof candidate.symbol === 'string' && candidate.symbol.trim()
    ? candidate.symbol.trim().toUpperCase().slice(0, 12)
    : fallback.symbol
  const timeframe = TIMEFRAME_OPTIONS.some((option) => option.value === candidate.timeframe)
    ? candidate.timeframe!
    : DEFAULT_SLOT_TIMEFRAMES[index] ?? fallback.timeframe
  const viewport = candidate.viewport
    && Number.isFinite(candidate.viewport.from)
    && Number.isFinite(candidate.viewport.to)
    && candidate.viewport.to > candidate.viewport.from
    ? { from: Number(candidate.viewport.from), to: Number(candidate.viewport.to) }
    : undefined
  return {
    id: typeof candidate.id === 'string' && candidate.id ? candidate.id : `chart-${index + 1}`,
    symbol,
    market: isMarket(candidate.market) ? candidate.market : fallback.market,
    timeframe,
    ...(viewport ? { viewport } : {}),
  }
}

export function normalizeWorkspace(value: unknown, initial: ChartSlotState): ChartWorkspaceState {
  const fallback = createInitialWorkspace(initial)
  if (!value || typeof value !== 'object') return fallback
  const candidate = value as Partial<ChartWorkspaceState> & { sync?: Partial<ChartSyncState> | boolean }
  const slots = Array.isArray(candidate.slots)
    ? candidate.slots.slice(0, 8).map((slot, index) => normalizeSlot(slot, index, initial)).filter((slot): slot is ChartSlotState => Boolean(slot))
    : []
  if (!slots.length) return fallback
  const layout = isLayout(candidate.layout) ? candidate.layout : 'one'
  const legacySync = typeof candidate.sync === 'boolean' ? candidate.sync : false
  const syncCandidate: Partial<ChartSyncState> = typeof candidate.sync === 'object' && candidate.sync ? candidate.sync : {}
  const sync = Object.fromEntries(Object.keys(DEFAULT_CHART_SYNC).map((key) => [
    key,
    legacySync ? key === 'timeframe' : Boolean(syncCandidate[key as keyof ChartSyncState]),
  ])) as unknown as ChartSyncState
  const activeSlotId = slots.some((slot) => slot.id === candidate.activeSlotId)
    ? candidate.activeSlotId!
    : slots[0].id
  return {
    layout,
    activeSlotId,
    slots,
    sync,
    inspectorOpen: Boolean(candidate.inspectorOpen),
  }
}

export function ensureLayoutSlots(state: ChartWorkspaceState, layout: ChartLayoutId): ChartWorkspaceState {
  const count = layoutDefinition(layout).count
  const slots = [...state.slots]
  const source = slots[0]
  while (slots.length < count) {
    const index = slots.length
    slots.push({
      ...source,
      id: `chart-${index + 1}`,
      timeframe: DEFAULT_SLOT_TIMEFRAMES[index] ?? source.timeframe,
      viewport: undefined,
    })
  }
  const visible = slots.slice(0, count)
  const activeSlotId = visible.some((slot) => slot.id === state.activeSlotId) ? state.activeSlotId : visible[0].id
  return { ...state, layout, activeSlotId, slots }
}

export function updateChartSlot(
  state: ChartWorkspaceState,
  slotId: string,
  patch: Partial<Omit<ChartSlotState, 'id'>>,
): ChartWorkspaceState {
  const slots = state.slots.map((slot) => {
    if (slot.id === slotId) return { ...slot, ...patch }
    return {
      ...slot,
      ...(state.sync.symbol && patch.symbol ? { symbol: patch.symbol, market: patch.market ?? slot.market } : {}),
      ...(state.sync.timeframe && patch.timeframe ? { timeframe: patch.timeframe } : {}),
      ...(state.sync.dateRange && patch.viewport ? { viewport: patch.viewport } : {}),
    }
  })
  return { ...state, activeSlotId: slotId, slots }
}
