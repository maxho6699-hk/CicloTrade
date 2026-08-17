import {
  ArrowLeft,
  ArrowRight,
  Expand,
  LayoutGrid,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  PencilRuler,
  PanelRightClose,
  PanelRightOpen,
  RotateCcw,
  Search,
  Shrink,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { MarketQuotePayload, PortfolioActivity } from '../api/client'
import type { FormingMarketBar, MarketStreamConnectionState, MarketStreamSubscription } from '../api/marketStream'
import type { Candle, Market } from '../types'
import {
  SharedDrawingToolbar,
  type DrawingCommand,
  type DrawingHistoryStatus,
  type DrawingToolState,
} from './ChartDrawingLayer'
import { MarketChart, type ChartCrosshairSync, type ChartTimeRange, type MarketChartHandle } from './MarketChart'
import { WatchlistToggle } from './WatchlistToggle'
import { TimeframeDropdown } from './ui/TimeframeDropdown'
import { createVisibilityPolling, deliveryAllowsImmediateAction, displayDataSource, displayDeliveryDelay, displayFreshness, safeDataError } from '../domain/dataSourcePresentation'
import {
  CHART_LAYOUTS,
  TIMEFRAME_OPTIONS,
  createInitialWorkspace,
  ensureLayoutSlots,
  layoutDefinition,
  normalizeWorkspace,
  updateChartSlot,
  type ChartLayoutId,
  type ChartSlotState,
  type ChartWorkspaceState,
} from './chartWorkspaceModel'

interface ChartWorkspaceProps {
  userId?: number | null
  initialSymbol: string
  initialMarket: Market
  initialTimeframe: string
  candles: Candle[]
  dataStatus: string
  showGrid: boolean
  showVolume: boolean
  upColor?: string
  downColor?: string
  textColor?: string
  officialActivity?: PortfolioActivity | null
  alertPrices?: number[] | ((market: Market, symbol: string) => number[])
  loadCandles?: (symbol: string, timeframe: string, market: Market) => Promise<Candle[]>
  initialQuote?: MarketQuotePayload | null
  loadQuote?: (symbol: string, market: Market) => Promise<MarketQuotePayload>
  subscribeMarketStream?: MarketStreamSubscription
  onSymbolChange?: (symbol: string, market: Market) => void
  onTimeframeChange?: (timeframe: string) => void
  watchlistSymbols?: Partial<Record<Market, readonly string[]>> | ((market: Market) => readonly string[])
  isWatchlisted?: (market: Market, symbol: string) => boolean
  onWatchlistToggle?: (market: Market, symbol: string, remove: boolean) => void | Promise<void>
  watchBusy?: string
  inspectorOpen?: boolean
  onInspectorOpenChange?: (open: boolean) => void
  inspectorExtra?: ReactNode
  toolbarActions?: ReactNode
  toolPanel?: ReactNode
  toolPanelLabel?: string
}

const STORAGE_KEY = 'ciclotrade:chart-workspace:v2'
const LEGACY_STORAGE_KEY = 'ciclotrade:chart-workspace:v1'
const TOOL_PANEL_STORAGE_KEY = 'ciclotrade:chart-tool-panel-open:v1'
const NARROW_CHART_QUERY = '(max-width: 760px), (max-width: 980px) and (max-height: 560px) and (orientation: landscape)'
const MULTI_CHART_POPULAR: Record<Market, string[]> = {
  US: ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'TSLA', 'SPY', 'QQQ', 'NBIS', 'CRWV', 'COHR', 'CSCO', 'AMAT'],
  CN: ['600519', '000001', '000858', '300750', '601318', '600036', '510300', '159915'],
}

function candleIdentity(slot: Pick<ChartSlotState, 'market' | 'symbol' | 'timeframe'>) {
  return `${slot.market}:${slot.symbol}:${slot.timeframe}`
}

function quoteIdentity(slot: Pick<ChartSlotState, 'market' | 'symbol'>) {
  return `${slot.market}:${slot.symbol}`
}

function quoteNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—'
}

function quoteStatusLabel(quote: MarketQuotePayload | null | undefined) {
  if (!quote) return ''
  const metadata = quote as MarketQuotePayload & {
    actionable_quote?: boolean
    freshness?: string
    is_realtime?: boolean
  }
  const delivery = displayDeliveryDelay(metadata.delivery_delay_minutes)
  const freshness = delivery || displayFreshness(metadata.freshness)
  const access = !deliveryAllowsImmediateAction(metadata)
    ? '研究参考'
    : metadata.is_realtime === false && freshness !== '延迟行情'
      ? '延迟行情'
      : metadata.is_realtime === true && freshness === '状态未记录'
        ? '实时行情'
        : ''
  return [displayDataSource(quote.source), freshness, access, quote.quote_at].filter(Boolean).join(' · ')
}

function loadWorkspace(initial: ChartSlotState) {
  try {
    const saved = localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem(LEGACY_STORAGE_KEY)
    return normalizeWorkspace(saved ? JSON.parse(saved) : null, initial)
  } catch {
    return createInitialWorkspace(initial)
  }
}

function loadToolPanelOpen() {
  try {
    return localStorage.getItem(TOOL_PANEL_STORAGE_KEY) !== 'collapsed'
  } catch {
    return true
  }
}

function marketLabel(market: Market) {
  return market === 'US' ? '美股' : 'A股'
}

function slotLatest(candles: Candle[]) {
  return candles.at(-1)
}

export function ChartWorkspace({
  userId,
  initialSymbol,
  initialMarket,
  initialTimeframe,
  candles,
  dataStatus,
  showGrid,
  showVolume,
  upColor,
  downColor,
  textColor,
  officialActivity,
  alertPrices = [],
  loadCandles,
  initialQuote,
  loadQuote,
  subscribeMarketStream,
  onSymbolChange,
  onTimeframeChange,
  watchlistSymbols,
  isWatchlisted,
  onWatchlistToggle,
  watchBusy = '',
  inspectorOpen: controlledInspectorOpen,
  onInspectorOpenChange,
  inspectorExtra,
  toolbarActions,
  toolPanel,
  toolPanelLabel = '研究工具',
}: ChartWorkspaceProps) {
  const initial = useMemo<ChartSlotState>(() => ({
    id: 'chart-1', symbol: initialSymbol, market: initialMarket, timeframe: initialTimeframe,
  }), [initialMarket, initialSymbol, initialTimeframe])
  const [workspace, setWorkspace] = useState<ChartWorkspaceState>(() => {
    const restored = loadWorkspace(initial)
    return {
      ...restored,
      slots: restored.slots.map((slot, index) => index === 0
        ? { ...slot, symbol: initial.symbol, market: initial.market, timeframe: initial.timeframe }
        : slot),
    }
  })
  const inspectorVisible = controlledInspectorOpen ?? workspace.inspectorOpen
  const setInspectorVisible = useCallback((open: boolean) => {
    onInspectorOpenChange?.(open)
    if (controlledInspectorOpen === undefined) {
      setWorkspace((current) => current.inspectorOpen === open ? current : { ...current, inspectorOpen: open })
    }
  }, [controlledInspectorOpen, onInspectorOpenChange])
  const [workbenchOpen, setWorkbenchOpen] = useState(false)
  const [layoutPickerOpen, setLayoutPickerOpen] = useState(false)
  const [toolPanelOpen, setToolPanelOpen] = useState(loadToolPanelOpen)
  const [mobileDrawingToolsOpen, setMobileDrawingToolsOpen] = useState(false)
  const [isNarrowViewport, setIsNarrowViewport] = useState(() => window.matchMedia(NARROW_CHART_QUERY).matches)
  const [focusedSlotId, setFocusedSlotId] = useState<string | null>(null)
  const [symbolEditorId, setSymbolEditorId] = useState<string | null>(null)
  const [symbolDraft, setSymbolDraft] = useState('')
  const [marketDraft, setMarketDraft] = useState<Market>('US')
  const [slotCandles, setSlotCandles] = useState<Record<string, Candle[]>>({ [initial.id]: candles })
  const [slotCandleIdentity, setSlotCandleIdentity] = useState<Record<string, string>>({ [initial.id]: candleIdentity(initial) })
  const [slotStatus, setSlotStatus] = useState<Record<string, string>>({})
  const [slotQuotes, setSlotQuotes] = useState<Record<string, MarketQuotePayload | null>>({ [initial.id]: initialQuote ?? null })
  const [slotQuoteIdentity, setSlotQuoteIdentity] = useState<Record<string, string>>({ [initial.id]: quoteIdentity(initial) })
  const [slotQuoteStatus, setSlotQuoteStatus] = useState<Record<string, string>>({})
  const [slotFormingBars, setSlotFormingBars] = useState<Record<string, FormingMarketBar | null>>({})
  const [slotStreamState, setSlotStreamState] = useState<Record<string, MarketStreamConnectionState>>({})
  const [drawingToolState, setDrawingToolState] = useState<DrawingToolState>({
    tool: 'cursor', continuous: false, magnet: 'off', visible: true, crossTimeframe: false,
  })
  const [drawingHistory, setDrawingHistory] = useState<DrawingHistoryStatus>({ drawingCount: 0, undoCount: 0, redoCount: 0, persistence: 'syncing' })
  const [drawingCommand, setDrawingCommand] = useState<DrawingCommand>({ id: 0, type: 'undo' })
  const chartRefs = useRef<Record<string, MarketChartHandle | null>>({})
  const shellRef = useRef<HTMLElement>(null)
  const slotCandlesRef = useRef(slotCandles)
  const slotQuotesRef = useRef(slotQuotes)
  const slotQuoteIdentityRef = useRef(slotQuoteIdentity)
  const layoutPickerRef = useRef<HTMLDivElement>(null)
  const symbolEditorRef = useRef<HTMLElement>(null)
  const timeRangeSyncLock = useRef(false)
  const focusOpenedWorkbench = useRef(false)
  const viewportDrafts = useRef<Record<string, { from: number; to: number }>>({})
  const viewportTimers = useRef<Record<string, number>>({})
  const slotLoadSequence = useRef<Record<string, number>>({})
  const quoteLoadSequence = useRef<Record<string, number>>({})
  const quoteInFlightRef = useRef<Record<string, { identity: string; request: Promise<MarketQuotePayload> }>>({})
  const slotStreamStateRef = useRef<Record<string, MarketStreamConnectionState>>({})
  const slotStreamSequenceRef = useRef<Record<string, number>>({})

  const definition = layoutDefinition(workspace.layout)
  const visibleSlots = useMemo(() => workspace.slots.slice(0, definition.count), [definition.count, workspace.slots])
  const activeSlot = visibleSlots.find((slot) => slot.id === workspace.activeSlotId) ?? visibleSlots[0]
  const requestedSlots = useMemo(
    () => isNarrowViewport ? visibleSlots.filter((slot) => slot.id === activeSlot.id) : visibleSlots,
    [activeSlot.id, isNarrowViewport, visibleSlots],
  )
  const requestedSlotIds = useMemo(() => new Set(requestedSlots.map((slot) => slot.id)), [requestedSlots])
  const activeCandles = activeSlot ? slotCandles[activeSlot.id] ?? [] : []
  const activeQuote = activeSlot && slotQuoteIdentity[activeSlot.id] === quoteIdentity(activeSlot)
    ? slotQuotes[activeSlot.id]
    : null
  const primarySlotId = workspace.slots[0]?.id ?? initial.id
  const primaryQuoteExternallyManaged = initialQuote !== undefined
  const fetchSignature = JSON.stringify(requestedSlots.map(({ id, market, symbol, timeframe }) => ({ id, market, symbol, timeframe })))
  const quoteFetchSignature = JSON.stringify(requestedSlots.map(({ id, market, symbol }) => ({ id, market, symbol })))

  useEffect(() => {
    setWorkspace((current) => {
      const first = current.slots[0]
      if (first?.symbol === initialSymbol && first.market === initialMarket && first.timeframe === initialTimeframe) return current
      return {
        ...current,
        slots: current.slots.map((slot, index) => index === 0
          ? { ...slot, symbol: initialSymbol, market: initialMarket, timeframe: initialTimeframe }
          : slot),
      }
    })
  }, [initialMarket, initialSymbol, initialTimeframe])

  useEffect(() => {
    const timer = window.setTimeout(() => localStorage.setItem(STORAGE_KEY, JSON.stringify(workspace)), 180)
    return () => window.clearTimeout(timer)
  }, [workspace])

  useEffect(() => {
    slotCandlesRef.current = slotCandles
  }, [slotCandles])

  useEffect(() => {
    slotQuotesRef.current = slotQuotes
  }, [slotQuotes])

  useEffect(() => {
    slotQuoteIdentityRef.current = slotQuoteIdentity
  }, [slotQuoteIdentity])

  useEffect(() => {
    slotStreamStateRef.current = slotStreamState
  }, [slotStreamState])

  useEffect(() => {
    setSlotCandles((current) => current[primarySlotId] === candles ? current : { ...current, [primarySlotId]: candles })
    setSlotCandleIdentity((current) => ({ ...current, [primarySlotId]: candleIdentity(initial) }))
  }, [candles, initial, primarySlotId])

  useEffect(() => {
    setSlotQuotes((current) => ({ ...current, [primarySlotId]: initialQuote ?? null }))
    setSlotQuoteIdentity((current) => ({ ...current, [primarySlotId]: quoteIdentity(initial) }))
  }, [initial, initialQuote, primarySlotId])

  useEffect(() => {
    if (!loadCandles) return
    let active = true
    const targets = JSON.parse(fetchSignature) as Array<Pick<ChartSlotState, 'id' | 'market' | 'symbol' | 'timeframe'>>
    const stopPolling = createVisibilityPolling(async () => {
      await Promise.all(targets.map(async (slot, index) => {
        if (index === 0 && slot.symbol === initialSymbol && slot.timeframe === initialTimeframe && slot.market === initialMarket) return
        const sequence = (slotLoadSequence.current[slot.id] ?? 0) + 1
        slotLoadSequence.current[slot.id] = sequence
        const hasPreviousData = Boolean(slotCandlesRef.current[slot.id]?.length)
        setSlotStatus((current) => ({ ...current, [slot.id]: hasPreviousData ? '正在更新，保留上一份 K 线' : '读取中…' }))
        try {
          const next = await loadCandles(slot.symbol, slot.timeframe, slot.market)
          if (!active || slotLoadSequence.current[slot.id] !== sequence) return
          setSlotCandleIdentity((current) => ({ ...current, [slot.id]: candleIdentity(slot) }))
          setSlotCandles((current) => ({ ...current, [slot.id]: next }))
          setSlotStatus((current) => ({ ...current, [slot.id]: next.length ? '已更新' : '暂无数据' }))
        } catch {
          if (active && slotLoadSequence.current[slot.id] === sequence) {
            const hasPreviousData = Boolean(slotCandlesRef.current[slot.id]?.length)
            setSlotStatus((current) => ({ ...current, [slot.id]: hasPreviousData ? '暂时无法读取，继续显示上一份 K 线' : '暂时无法读取' }))
          }
        }
      }))
    }, 15_000)
    return () => { active = false; stopPolling() }
  }, [fetchSignature, initialMarket, initialSymbol, initialTimeframe, loadCandles])

  useEffect(() => {
    if (!loadQuote) return
    let active = true
    const targets = JSON.parse(quoteFetchSignature) as Array<Pick<ChartSlotState, 'id' | 'market' | 'symbol'>>
    const stopPolling = createVisibilityPolling(async () => {
      await Promise.all(targets.map(async (slot, index) => {
        const identity = quoteIdentity(slot)
        if (index === 0 && slot.id === primarySlotId && slot.symbol === initialSymbol && slot.market === initialMarket && primaryQuoteExternallyManaged) return
        const sequence = (quoteLoadSequence.current[slot.id] ?? 0) + 1
        quoteLoadSequence.current[slot.id] = sequence
        const identityChanged = slotQuoteIdentityRef.current[slot.id] !== identity
        if (identityChanged) {
          setSlotQuoteIdentity((current) => ({ ...current, [slot.id]: identity }))
          setSlotQuotes((current) => ({ ...current, [slot.id]: null }))
        }
        const hasPreviousQuote = !identityChanged && Boolean(slotQuotesRef.current[slot.id])
        if (!hasPreviousQuote) {
          setSlotQuoteStatus((current) => ({ ...current, [slot.id]: slot.market === 'US' ? '读取报价中…' : 'A 股买卖盘未接入' }))
        }
        const previousRequest = quoteInFlightRef.current[slot.id]
        const request = previousRequest?.identity === identity
          ? previousRequest.request
          : Promise.resolve().then(() => loadQuote(slot.symbol, slot.market))
        if (previousRequest?.identity !== identity) quoteInFlightRef.current[slot.id] = { identity, request }
        try {
          const quote = await request
          if (!active || quoteLoadSequence.current[slot.id] !== sequence) return
          setSlotQuoteIdentity((current) => ({ ...current, [slot.id]: identity }))
          setSlotQuotes((current) => ({ ...current, [slot.id]: quote }))
          setSlotQuoteStatus((current) => ({ ...current, [slot.id]: quoteStatusLabel(quote) || '报价没有来源或时间' }))
        } catch {
          if (!active || quoteLoadSequence.current[slot.id] !== sequence) return
          setSlotQuoteStatus((current) => ({ ...current, [slot.id]: safeDataError() }))
        } finally {
          if (quoteInFlightRef.current[slot.id]?.request === request) delete quoteInFlightRef.current[slot.id]
        }
      }))
    }, 5_000)
    return () => { active = false; stopPolling() }
  }, [initialMarket, initialSymbol, loadQuote, primaryQuoteExternallyManaged, primarySlotId, quoteFetchSignature])

  useEffect(() => {
    if (!subscribeMarketStream) return
    let active = true
    const targets = JSON.parse(fetchSignature) as Array<Pick<ChartSlotState, 'id' | 'market' | 'symbol' | 'timeframe'>>
    const stops = targets.map((slot) => {
      setSlotStreamState((current) => ({ ...current, [slot.id]: 'connecting' }))
      setSlotFormingBars((current) => ({ ...current, [slot.id]: null }))
      slotStreamStateRef.current = { ...slotStreamStateRef.current, [slot.id]: 'connecting' }
      slotStreamSequenceRef.current[slot.id] = -1
      return subscribeMarketStream(slot.symbol, slot.timeframe, (event) => {
        if (!active) return
        if (event.type === 'status') {
          slotStreamStateRef.current = { ...slotStreamStateRef.current, [slot.id]: event.state }
          setSlotStreamState((current) => ({ ...current, [slot.id]: event.state }))
          if (event.state !== 'connected') setSlotFormingBars((current) => ({ ...current, [slot.id]: null }))
          return
        }
        const validForSlot = event.bar.symbol === slot.symbol && event.bar.timeframe === slot.timeframe
          && slotStreamStateRef.current[slot.id] === 'connected'
          && event.bar.realtime && event.bar.authorized && !event.bar.stale
        if (validForSlot && event.bar.sequence > (slotStreamSequenceRef.current[slot.id] ?? -1)) {
          slotStreamSequenceRef.current[slot.id] = event.bar.sequence
          setSlotFormingBars((current) => ({ ...current, [slot.id]: event.bar }))
        } else if (!validForSlot) {
          setSlotFormingBars((current) => ({ ...current, [slot.id]: null }))
        }
      })
    })
    return () => {
      active = false
      stops.forEach((stop) => stop())
      setSlotFormingBars((current) => ({ ...current, ...Object.fromEntries(targets.map((slot) => [slot.id, null])) }))
    }
  }, [fetchSignature, subscribeMarketStream])

  useEffect(() => {
    const query = window.matchMedia(NARROW_CHART_QUERY)
    const update = () => setIsNarrowViewport(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    if (!layoutPickerOpen) return
    const closeOutside = (event: PointerEvent) => {
      if (!layoutPickerRef.current?.contains(event.target as Node)) setLayoutPickerOpen(false)
    }
    document.addEventListener('pointerdown', closeOutside)
    return () => document.removeEventListener('pointerdown', closeOutside)
  }, [layoutPickerOpen])

  useEffect(() => {
    if (!symbolEditorId) return
    const closeOutside = (event: PointerEvent) => {
      if (!symbolEditorRef.current?.contains(event.target as Node)) setSymbolEditorId(null)
    }
    document.addEventListener('pointerdown', closeOutside)
    return () => document.removeEventListener('pointerdown', closeOutside)
  }, [symbolEditorId])

  useLayoutEffect(() => {
    if (!workbenchOpen) return
    const previousOverflow = document.body.style.overflow
    const previousGutter = document.documentElement.style.scrollbarGutter
    document.body.style.overflow = 'hidden'
    document.documentElement.style.scrollbarGutter = 'auto'
    return () => {
      document.body.style.overflow = previousOverflow
      document.documentElement.style.scrollbarGutter = previousGutter
    }
  }, [workbenchOpen])

  useEffect(() => {
    const syncFullscreenState = () => {
      if (document.fullscreenElement === shellRef.current) {
        setWorkbenchOpen(true)
        return
      }
      if (!document.fullscreenElement) {
        focusOpenedWorkbench.current = false
        setFocusedSlotId(null)
        setWorkbenchOpen(false)
      }
    }
    document.addEventListener('fullscreenchange', syncFullscreenState)
    return () => document.removeEventListener('fullscreenchange', syncFullscreenState)
  }, [])

  useLayoutEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const slotsToReflow = focusedSlotId
        ? requestedSlots.filter((slot) => slot.id === focusedSlotId)
        : requestedSlots
      slotsToReflow.forEach((slot) => chartRefs.current[slot.id]?.reflow())
    })
    return () => window.cancelAnimationFrame(frame)
  }, [focusedSlotId, inspectorVisible, requestedSlots, toolPanelOpen, workbenchOpen, workspace.layout])

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (workbenchOpen) {
        focusOpenedWorkbench.current = false
        setFocusedSlotId(null)
        setInspectorVisible(false)
        setWorkbenchOpen(false)
        return
      }
      if (layoutPickerOpen) {
        setLayoutPickerOpen(false)
        return
      }
      if (symbolEditorId) {
        setSymbolEditorId(null)
        return
      }
      if (inspectorVisible) {
        setInspectorVisible(false)
        return
      }
      if (focusedSlotId) {
        setFocusedSlotId(null)
        if (focusOpenedWorkbench.current) setWorkbenchOpen(false)
        focusOpenedWorkbench.current = false
        return
      }
    }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [focusedSlotId, inspectorVisible, layoutPickerOpen, setInspectorVisible, symbolEditorId, workbenchOpen])

  const setLayout = (layout: ChartLayoutId) => {
    if (isNarrowViewport && layoutDefinition(layout).desktopOnly) return
    setWorkspace((current) => ensureLayoutSlots(current, layout))
    setFocusedSlotId(null)
    setSymbolEditorId(null)
    setLayoutPickerOpen(false)
  }

  const activateSlot = (slotId: string) => {
    setWorkspace((current) => current.activeSlotId === slotId ? current : { ...current, activeSlotId: slotId })
  }

  const updateSlot = (slotId: string, patch: Partial<Omit<ChartSlotState, 'id'>>) => {
    setWorkspace((current) => updateChartSlot(current, slotId, patch))
    if (patch.symbol || patch.market || patch.timeframe) {
      const currentSlot = workspace.slots.find((slot) => slot.id === slotId)
      if (currentSlot) {
        const nextSlot = { ...currentSlot, ...patch }
        const hasPreviousData = Boolean(slotCandlesRef.current[slotId]?.length)
        setSlotStatus((current) => ({ ...current, [slotId]: hasPreviousData ? '正在更新，保留上一份 K 线' : '读取中…' }))
        if (patch.symbol || patch.market) {
          setSlotQuoteIdentity((current) => ({ ...current, [slotId]: quoteIdentity(nextSlot) }))
          setSlotQuotes((current) => ({ ...current, [slotId]: null }))
        }
      }
    }
    const primary = workspace.slots[0]?.id === slotId
    if (primary && patch.symbol && patch.market) onSymbolChange?.(patch.symbol, patch.market)
    if (primary && patch.timeframe) onTimeframeChange?.(patch.timeframe)
  }

  const saveViewport = useCallback((slotId: string, viewport: { from: number; to: number }) => {
    viewportDrafts.current[slotId] = viewport
    window.clearTimeout(viewportTimers.current[slotId])
    viewportTimers.current[slotId] = window.setTimeout(() => {
      const pending = viewportDrafts.current[slotId]
      if (!pending) return
      setWorkspace((current) => {
        const slot = current.slots.find((item) => item.id === slotId)
        if (slot?.viewport && Math.abs(slot.viewport.from - pending.from) < 0.05 && Math.abs(slot.viewport.to - pending.to) < 0.05) return current
        return {
          ...current,
          slots: current.slots.map((item) => item.id === slotId ? { ...item, viewport: pending } : item),
        }
      })
    }, 320)
  }, [])

  useEffect(() => () => {
    Object.values(viewportTimers.current).forEach((timer) => window.clearTimeout(timer))
  }, [])

  const syncCrosshair = useCallback((sourceSlotId: string, payload: ChartCrosshairSync | null) => {
    if (!workspace.sync.crosshair && !workspace.sync.time) return
    Object.entries(chartRefs.current).forEach(([slotId, chart]) => {
      if (slotId !== sourceSlotId && requestedSlotIds.has(slotId)) chart?.syncCrosshair(payload, workspace.sync.crosshair)
    })
  }, [requestedSlotIds, workspace.sync.crosshair, workspace.sync.time])

  const syncVisibleTimeRange = useCallback((sourceSlotId: string, range: ChartTimeRange) => {
    if (!workspace.sync.dateRange || timeRangeSyncLock.current) return
    timeRangeSyncLock.current = true
    Object.entries(chartRefs.current).forEach(([slotId, chart]) => {
      if (slotId !== sourceSlotId && requestedSlotIds.has(slotId)) chart?.setVisibleTimeRange(range)
    })
    window.requestAnimationFrame(() => { timeRangeSyncLock.current = false })
  }, [requestedSlotIds, workspace.sync.dateRange])

  const openSymbolEditor = (slot: ChartSlotState) => {
    activateSlot(slot.id)
    if (inspectorVisible) setInspectorVisible(false)
    setSymbolDraft(slot.symbol)
    setMarketDraft(slot.market)
    setSymbolEditorId(slot.id)
  }

  const applySymbol = () => {
    const normalized = symbolDraft.trim().toUpperCase()
    if (!symbolEditorId || !/^(?:[A-Z][A-Z0-9.-]{0,11}|\d{6})$/.test(normalized)) return
    updateSlot(symbolEditorId, { symbol: normalized, market: marketDraft, viewport: undefined })
    setSymbolEditorId(null)
  }

  const chooseSymbol = (slotId: string, symbol: string, market: Market) => {
    updateSlot(slotId, { symbol, market, viewport: undefined })
    setSymbolEditorId(null)
  }

  const toggleFocus = (slotId: string) => {
    activateSlot(slotId)
    if (focusedSlotId === slotId) {
      setFocusedSlotId(null)
      if (focusOpenedWorkbench.current) setWorkbenchOpen(false)
      focusOpenedWorkbench.current = false
      return
    }
    focusOpenedWorkbench.current = !workbenchOpen
    if (!workbenchOpen) setWorkbenchOpen(true)
    setFocusedSlotId(slotId)
  }

  const exitWorkbench = () => {
    focusOpenedWorkbench.current = false
    setFocusedSlotId(null)
    setWorkbenchOpen(false)
    if (document.fullscreenElement === shellRef.current) void document.exitFullscreen().catch(() => undefined)
  }

  const enterWorkbench = () => {
    setInspectorVisible(false)
    setLayoutPickerOpen(false)
    setMobileDrawingToolsOpen(false)
    if (!document.fullscreenElement && shellRef.current?.requestFullscreen) void shellRef.current.requestFullscreen().catch(() => undefined)
    setWorkbenchOpen(true)
  }

  const toggleToolPanel = () => {
    setToolPanelOpen((current) => {
      const next = !current
      try { localStorage.setItem(TOOL_PANEL_STORAGE_KEY, next ? 'open' : 'collapsed') } catch { /* storage can be disabled */ }
      return next
    })
  }

  const sendDrawingCommand = (type: DrawingCommand['type']) => {
    if (!activeSlot) return
    setDrawingCommand((current) => ({ id: current.id + 1, type, targetMarkerId: `drawing-arrow-${activeSlot.id}` }))
  }

  const completeDrawing = useCallback(() => setDrawingToolState((current) => ({ ...current, tool: 'cursor' })), [])
  const toggleMobileDrawingTools = () => {
    setMobileDrawingToolsOpen((current) => !current)
  }
  const latest = slotLatest(activeCandles)

  return (
    <section ref={shellRef} className={`chart-workspace-shell ${workbenchOpen ? 'is-workbench-open' : ''} ${focusedSlotId ? 'is-pane-focused' : ''} ${inspectorVisible ? 'has-workbench-inspector' : ''} ${toolPanel ? 'has-tool-panel' : ''} ${toolPanelOpen ? 'is-tool-panel-open' : 'is-tool-panel-collapsed'} ${mobileDrawingToolsOpen ? 'mobile-drawing-tools-open' : ''}`} aria-label="多图 K线工作图">
      <header className="multi-chart-toolbar">
        <div className="workbench-title"><span className="workbench-title-label"><LayoutGrid size={16} /><strong>K线工作图</strong></span>{onWatchlistToggle && <WatchlistToggle symbol={activeSlot.symbol} saved={isWatchlisted?.(activeSlot.market, activeSlot.symbol) ?? false} busy={watchBusy === activeSlot.symbol} variant="label" className="workbench-watchlist-toggle" onToggle={(remove) => onWatchlistToggle(activeSlot.market, activeSlot.symbol, remove)} />}<div className="multi-chart-layout-picker" ref={layoutPickerRef}>
          <button className="layout-picker-trigger" type="button" aria-haspopup="dialog" aria-expanded={layoutPickerOpen} aria-label="选择 K 线多图布局" title="选择多图布局" onClick={() => setLayoutPickerOpen((current) => !current)}><LayoutGrid size={15} /><span>{definition.label}</span></button>
          {layoutPickerOpen && <div className="layout-picker-popover" role="dialog" aria-label="K 线多图布局选择"><header><strong>分割视图</strong><small>{isNarrowViewport ? '手机版使用图表标签切换；四图以上请使用桌面版' : '选择后保持每张图的股票和周期'}</small></header><div className="layout-picker-grid">{CHART_LAYOUTS.map((layout) => { const unavailable = isNarrowViewport && layout.desktopOnly; return <button type="button" disabled={unavailable} className={workspace.layout === layout.id ? 'active' : ''} title={unavailable ? `${layout.label} · 仅桌面版` : layout.label} aria-label={`${layout.label}${unavailable ? '，仅桌面版' : ''}`} onClick={() => setLayout(layout.id)} key={layout.id}><span className={`layout-preview layout-preview-${layout.id}`}>{Array.from({ length: layout.count }, (_, index) => <i key={index} />)}</span><b>{layout.label}</b></button> })}</div></div>}
        </div></div>
        <div className="multi-chart-actions">
          {toolbarActions && <span className="multi-chart-utility-actions">{toolbarActions}</span>}
          {toolPanel && <button className="chart-tool-panel-toggle-top" type="button" aria-label={toolPanelOpen ? `收起${toolPanelLabel}` : `展开${toolPanelLabel}`} aria-expanded={toolPanelOpen} title={toolPanelOpen ? `收起${toolPanelLabel}` : `展开${toolPanelLabel}`} onClick={toggleToolPanel}>{toolPanelOpen ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}</button>}
          <button type="button" title={inspectorVisible ? '收起检查器' : '展开检查器'} aria-label={inspectorVisible ? '收起检查器' : '展开检查器'} aria-expanded={inspectorVisible} onClick={() => { setSymbolEditorId(null); setInspectorVisible(!inspectorVisible) }}>{inspectorVisible ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}</button>
          <button type="button" title={workbenchOpen ? '返回行情页' : '打开全屏K线工作图'} aria-label={workbenchOpen ? '返回行情页' : '打开全屏K线工作图'} onClick={() => workbenchOpen ? exitWorkbench() : enterWorkbench()}>{workbenchOpen ? <Shrink size={16} /> : <Expand size={16} />}</button>
        </div>
      </header>

      <div className="chart-workbench-body">
        {toolPanel && toolPanelOpen && <aside className="chart-tool-panel" aria-label={toolPanelLabel}><div className="chart-tool-panel-content">{toolPanel}</div></aside>}

        <SharedDrawingToolbar
          state={drawingToolState}
          history={drawingHistory}
          onChange={(patch) => setDrawingToolState((current) => ({ ...current, ...patch }))}
          onCommand={sendDrawingCommand}
        />

        <div className={`multi-chart-grid layout-${workspace.layout}`}>
          {definition.count > 1 && <nav className="mobile-chart-tabs" aria-label="切换图表">
            {visibleSlots.map((slot, index) => <button className={slot.id === workspace.activeSlotId ? 'active' : ''} type="button" onClick={() => activateSlot(slot.id)} key={slot.id}>{index + 1} · {slot.symbol}</button>)}
          </nav>}
          {workspace.slots.map((slot, slotIndex) => {
            const chartData = slotCandles[slot.id] ?? (slotIndex === 0 ? candles : [])
            const quote = slotQuoteIdentity[slot.id] === quoteIdentity(slot) ? slotQuotes[slot.id] : null
            const slotIsFocused = focusedSlotId === slot.id
            const slotIsActive = workspace.activeSlotId === slot.id
            const slotIsVisible = slotIndex < definition.count && (!isNarrowViewport || slotIsActive)
            const visibleDataStatus = slotStatus[slot.id]?.includes('K 线') ? slotStatus[slot.id] : null
            const authoritativeWatchlist = typeof watchlistSymbols === 'function'
              ? watchlistSymbols(marketDraft)
              : watchlistSymbols?.[marketDraft] ?? []
            const symbolCandidates = Array.from(new Set([
              ...workspace.slots.filter((item) => item.market === marketDraft).map((item) => item.symbol),
              initialMarket === marketDraft ? initialSymbol : '',
              ...MULTI_CHART_POPULAR[marketDraft],
            ].filter(Boolean)))
            const savedSymbols = Array.from(new Set(authoritativeWatchlist
              .map((symbol) => symbol.trim().toUpperCase())
              .filter(Boolean)))
            const fallbackSavedSymbols = savedSymbols.length || watchlistSymbols
              ? savedSymbols
              : symbolCandidates.filter((symbol) => isWatchlisted?.(marketDraft, symbol))
            const popularSymbols = symbolCandidates.filter((symbol) => !fallbackSavedSymbols.includes(symbol))
            return (
              <article
                className={`chart-slot ${slotIsActive ? 'is-active' : ''} ${slotIsFocused ? 'is-focused' : ''} ${slotIsVisible ? '' : 'is-layout-hidden'}`}
                data-slot-index={slotIndex + 1}
                key={slot.id}
                onPointerDownCapture={() => activateSlot(slot.id)}
              >
                <header className="chart-slot-toolbar">
                  {definition.count > 1
                    ? <button className="chart-symbol-trigger" type="button" aria-haspopup="dialog" aria-expanded={symbolEditorId === slot.id} aria-label={`更换 ${slot.symbol}`} title="从自选或热门股票更换" onClick={(event) => { event.stopPropagation(); openSymbolEditor(slot) }}><Search size={13} /><strong>{slot.symbol}</strong></button>
                    : <span className="chart-symbol-label" aria-label={`当前股票 ${slot.symbol}`}><strong>{slot.symbol}</strong></span>}
                  <TimeframeDropdown value={slot.timeframe} options={TIMEFRAME_OPTIONS} ariaLabel={`${slot.symbol} 时间周期`} onChange={(timeframe) => updateSlot(slot.id, { timeframe, viewport: undefined })} />
                  {definition.count > 1 && <button className="chart-focus-control" type="button" title={slotIsFocused ? '恢复多图布局' : '最大化当前图'} aria-label={slotIsFocused ? '恢复多图布局' : '最大化当前图'} onClick={() => toggleFocus(slot.id)}>{slotIsFocused ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>}
                  <details className="chart-view-menu">
                    <summary title="缩放与适配" aria-label="打开缩放与适配工具"><MoreHorizontal size={16} /></summary>
                    <div className="chart-view-popover" role="group" aria-label="缩放与适配">
                      <button type="button" onClick={() => chartRefs.current[slot.id]?.panLeft()}><ArrowLeft size={14} /><span>左移</span></button>
                      <button type="button" onClick={() => chartRefs.current[slot.id]?.panRight()}><ArrowRight size={14} /><span>右移</span></button>
                      <button type="button" onClick={() => chartRefs.current[slot.id]?.zoomOut()}><ZoomOut size={14} /><span>缩小</span></button>
                      <button type="button" onClick={() => chartRefs.current[slot.id]?.zoomIn()}><ZoomIn size={14} /><span>放大</span></button>
                      <button type="button" onClick={() => chartRefs.current[slot.id]?.reset()}><RotateCcw size={14} /><span>适配全部</span></button>
                    </div>
                  </details>
                  {slotIsActive && <span className="mobile-chart-inline-actions" role="group" aria-label="图表工具">
                    {toolbarActions}
                    <button className={mobileDrawingToolsOpen ? 'active' : ''} type="button" aria-label={mobileDrawingToolsOpen ? '收起画图工具' : '打开画图工具'} title={mobileDrawingToolsOpen ? '收起画图' : '画图'} aria-expanded={mobileDrawingToolsOpen} onClick={toggleMobileDrawingTools}><PencilRuler size={16} /><span>画图</span></button>
                  </span>}
                </header>
                <div className="chart-slot-canvas">
                  {visibleDataStatus && <span className="chart-slot-data-status" role="status" title={visibleDataStatus}>{visibleDataStatus}</span>}
                  {chartData.length ? (
                    <MarketChart
                      ref={(handle) => { chartRefs.current[slot.id] = handle }}
                      candles={chartData}
                      userId={userId}
                      market={slot.market}
                      symbol={slot.symbol}
                      timeframe={slot.timeframe}
                      candleDataIdentity={slotCandleIdentity[slot.id] ?? candleIdentity(slot)}
                      showGrid={showGrid}
                      showVolume={showVolume}
                      upColor={upColor}
                      downColor={downColor}
                      textColor={textColor}
                      dataStatus={slotStatus[slot.id] ?? dataStatus}
                      quote={quote}
                      formingBar={slotFormingBars[slot.id]}
                      streamConnectionState={slotStreamState[slot.id]}
                      officialActivity={officialActivity}
                      alertPrices={typeof alertPrices === 'function' ? alertPrices(slot.market, slot.symbol) : alertPrices}
                      drawingActive={slotIsActive}
                      drawingToolState={drawingToolState}
                      drawingCommand={drawingCommand}
                      drawingMarkerId={`drawing-arrow-${slot.id}`}
                      initialViewport={slot.viewport}
                      onDrawingHistoryChange={setDrawingHistory}
                      onDrawingToolComplete={completeDrawing}
                      onViewportChange={(range) => saveViewport(slot.id, range)}
                      onCrosshairChange={(payload) => syncCrosshair(slot.id, payload)}
                      onVisibleTimeRangeChange={(range) => syncVisibleTimeRange(slot.id, range)}
                    />
                  ) : (
                    <div className="chart-pane-state" role="status"><strong>{slotStatus[slot.id] ?? `${marketLabel(slot.market)} ${slot.symbol} 暂无可验证 K 线`}</strong><span>{marketLabel(slot.market)} · {slot.symbol} · {slot.timeframe} · 市场未连接或数据尚未返回</span></div>
                  )}
                </div>
                {symbolEditorId === slot.id && (
                  <aside className="chart-symbol-popover" ref={symbolEditorRef} role="dialog" aria-label={`更换 ${slot.symbol}`}>
                    <header><strong>更换图表</strong><button className="icon-button" type="button" aria-label="关闭" onClick={() => setSymbolEditorId(null)}>×</button></header>
                    <div className="market-tabs"><button className={marketDraft === 'US' ? 'active' : ''} type="button" onClick={() => setMarketDraft('US')}>美股</button><button className={marketDraft === 'CN' ? 'active' : ''} type="button" onClick={() => setMarketDraft('CN')}>A股</button></div>
                    {fallbackSavedSymbols.length > 0 && <section><small>我的自选</small><div className="chart-symbol-chips">{fallbackSavedSymbols.map((symbol) => <button type="button" className={symbol === slot.symbol && marketDraft === slot.market ? 'active' : ''} onClick={() => chooseSymbol(slot.id, symbol, marketDraft)} key={`saved-${symbol}`}>{symbol}</button>)}</div></section>}
                    <section><small>热门股票</small><div className="chart-symbol-chips">{popularSymbols.slice(0, 10).map((symbol) => <button type="button" className={symbol === slot.symbol && marketDraft === slot.market ? 'active' : ''} onClick={() => chooseSymbol(slot.id, symbol, marketDraft)} key={symbol}>{symbol}</button>)}</div></section>
                    <details className="chart-symbol-manual"><summary>输入其他股票代码</summary><form onSubmit={(event) => { event.preventDefault(); applySymbol() }}><input aria-label="股票代码" value={symbolDraft} onChange={(event) => setSymbolDraft(event.target.value.toUpperCase())} placeholder={marketDraft === 'US' ? '例如 AAPL' : '例如 600519'} /><button className="button primary" type="submit">打开</button></form></details>
                  </aside>
                )}
              </article>
            )
          })}
        </div>

        {inspectorVisible && (
          <aside className={`chart-workbench-inspector ${inspectorExtra ? 'has-inspector-extra' : ''}`}>
            {inspectorExtra ? <><button className="chart-inspector-dismiss icon-button" type="button" aria-label="收起检查器" onClick={() => setInspectorVisible(false)}><PanelRightClose size={16} /></button>{inspectorExtra}</> : <header><span><small>当前图表</small><strong>{activeSlot.symbol} · {activeSlot.timeframe}</strong></span><button className="icon-button" type="button" aria-label="收起检查器" onClick={() => setInspectorVisible(false)}><PanelRightClose size={16} /></button></header>}
            <details className="chart-technical-details" open={inspectorExtra ? undefined : true}>
              <summary>技术状态与图表同步</summary>
              <dl>
                <div><dt>市场</dt><dd>{marketLabel(activeSlot.market)}</dd></div>
                <div><dt>K 线状态</dt><dd>{slotStatus[activeSlot.id] ?? dataStatus}</dd></div>
                <div><dt>报价状态</dt><dd>{slotQuoteStatus[activeSlot.id] || quoteStatusLabel(activeQuote) || (activeSlot.market === 'CN' ? 'A 股买卖盘未提供' : '暂时没有报价')}</dd></div>
                <div><dt>现价 Last</dt><dd>{quoteNumber(activeQuote?.last)}</dd></div>
                <div><dt>卖出价 Bid</dt><dd>{quoteNumber(activeQuote?.bid)}</dd></div>
                <div><dt>买入价 Ask</dt><dd>{quoteNumber(activeQuote?.ask)}</dd></div>
                <div><dt>买卖价差</dt><dd>{quoteNumber(activeQuote?.spread)}</dd></div>
                {latest && <><div><dt>开盘</dt><dd>{latest.open.toFixed(2)}</dd></div><div><dt>最高</dt><dd>{latest.high.toFixed(2)}</dd></div><div><dt>最低</dt><dd>{latest.low.toFixed(2)}</dd></div><div><dt>收盘</dt><dd>{latest.close.toFixed(2)}</dd></div></>}
                <div><dt>画线</dt><dd>{drawingHistory.drawingCount} 项 · {drawingHistory.persistence === 'synced' ? '已同步到账号' : drawingHistory.persistence === 'syncing' ? '正在同步' : drawingHistory.persistence === 'device-only' ? '仅此设备' : drawingHistory.persistence === 'conflict' ? '同步冲突' : '保存失败'}</dd></div>
              </dl>
              <div className="sync-settings"><strong>图表同步</strong>{([
                ['symbol', '商品代码'], ['timeframe', '周期'], ['crosshair', '十字线'], ['time', '时间'], ['dateRange', '日期范围'],
              ] as const).map(([key, label]) => <label key={key}><input type="checkbox" checked={workspace.sync[key]} onChange={(event) => setWorkspace((current) => ({ ...current, sync: { ...current.sync, [key]: event.target.checked } }))} />{label}</label>)}</div>
              <p className="quote-data-boundary">行情源未提供 Bid / Ask 时不计算价差；滑点只在成交后计算。</p>
            </details>
          </aside>
        )}
        {workbenchOpen && <button className="chart-fullscreen-exit" type="button" aria-label="退出 K 线全屏" title="退出全屏（Esc）" onClick={exitWorkbench}><Shrink size={18} /></button>}
      </div>
    </section>
  )
}
