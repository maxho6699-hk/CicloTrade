import {
  ArrowLeft,
  ArrowRight,
  Expand,
  LayoutGrid,
  Maximize2,
  Minimize2,
  PanelRightClose,
  PanelRightOpen,
  PanelLeftClose,
  PanelLeftOpen,
  RotateCcw,
  Search,
  Shrink,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { MarketQuotePayload, PortfolioActivity } from '../api/client'
import type { Candle, Instrument, Market } from '../types'
import {
  SharedDrawingToolbar,
  type DrawingCommand,
  type DrawingHistoryStatus,
  type DrawingToolState,
} from './ChartDrawingLayer'
import { MarketChart, type ChartCrosshairSync, type ChartTimeRange, type MarketChartHandle } from './MarketChart'
import { WatchlistToggle } from './WatchlistToggle'
import { TimeframeDropdown } from './ui/TimeframeDropdown'
import { displayDataSource, displayFreshness, safeDataError } from '../domain/dataSourcePresentation'
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
  alertPrices?: number[]
  instruments?: Instrument[]
  loadCandles?: (symbol: string, timeframe: string, market: Market) => Promise<Candle[]>
  initialQuote?: MarketQuotePayload | null
  loadQuote?: (symbol: string, market: Market) => Promise<MarketQuotePayload>
  onSymbolChange?: (symbol: string, market: Market) => void
  onTimeframeChange?: (timeframe: string) => void
  isWatchlisted?: (market: Market, symbol: string) => boolean
  onWatchlistToggle?: (market: Market, symbol: string, remove: boolean) => void | Promise<void>
  watchBusy?: string
  inspectorOpen?: boolean
  onInspectorOpenChange?: (open: boolean) => void
  inspectorExtra?: ReactNode
  footerActions?: ReactNode
}

const STORAGE_KEY = 'ciclotrade:chart-workspace:v2'
const LEGACY_STORAGE_KEY = 'ciclotrade:chart-workspace:v1'
const RANGE_PRESETS = [
  ['1D', '1 天'], ['5D', '5 天'], ['1M', '1 个月'], ['3M', '3 个月'],
  ['6M', '6 个月'], ['YTD', '年初至今'], ['1Y', '1 年'], ['ALL', '全部'],
] as const
const QUICK_TIMEFRAMES = ['1分', '5分', '15分', '1小时', '日线'] as const
const NARROW_CHART_QUERY = '(max-width: 760px), (max-width: 980px) and (max-height: 560px) and (orientation: landscape)'

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
  const freshness = displayFreshness(metadata.freshness)
  const access = metadata.actionable_quote === false
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
  instruments = [],
  loadCandles,
  initialQuote,
  loadQuote,
  onSymbolChange,
  onTimeframeChange,
  isWatchlisted,
  onWatchlistToggle,
  watchBusy = '',
  inspectorOpen: controlledInspectorOpen,
  onInspectorOpenChange,
  inspectorExtra,
  footerActions,
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
  const [workbenchOpen, setWorkbenchOpen] = useState(() => layoutDefinition(workspace.layout).count > 1)
  const [marketBrowserOpen, setMarketBrowserOpen] = useState(false)
  const [layoutPickerOpen, setLayoutPickerOpen] = useState(false)
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
  const [drawingToolState, setDrawingToolState] = useState<DrawingToolState>({
    tool: 'cursor', continuous: false, magnet: 'off', visible: true, crossTimeframe: false,
  })
  const [drawingHistory, setDrawingHistory] = useState<DrawingHistoryStatus>({ drawingCount: 0, undoCount: 0, redoCount: 0, persistence: 'syncing' })
  const [drawingCommand, setDrawingCommand] = useState<DrawingCommand>({ id: 0, type: 'undo' })
  const chartRefs = useRef<Record<string, MarketChartHandle | null>>({})
  const slotCandlesRef = useRef(slotCandles)
  const layoutPickerRef = useRef<HTMLDivElement>(null)
  const timeRangeSyncLock = useRef(false)
  const focusOpenedWorkbench = useRef(false)
  const viewportDrafts = useRef<Record<string, { from: number; to: number }>>({})
  const viewportTimers = useRef<Record<string, number>>({})
  const slotLoadSequence = useRef<Record<string, number>>({})
  const quoteLoadSequence = useRef<Record<string, number>>({})

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
    targets.forEach((slot, index) => {
      if (index === 0 && slot.symbol === initialSymbol && slot.timeframe === initialTimeframe && slot.market === initialMarket) return
      const sequence = (slotLoadSequence.current[slot.id] ?? 0) + 1
      slotLoadSequence.current[slot.id] = sequence
      const hasPreviousData = Boolean(slotCandlesRef.current[slot.id]?.length)
      setSlotStatus((current) => ({ ...current, [slot.id]: hasPreviousData ? '正在更新，保留上一份 K 线' : '读取中…' }))
      void loadCandles(slot.symbol, slot.timeframe, slot.market).then((next) => {
        if (!active || slotLoadSequence.current[slot.id] !== sequence) return
        setSlotCandleIdentity((current) => ({ ...current, [slot.id]: candleIdentity(slot) }))
        setSlotCandles((current) => ({ ...current, [slot.id]: next }))
        setSlotStatus((current) => ({ ...current, [slot.id]: next.length ? '已更新' : '暂无数据' }))
      }).catch(() => {
        if (active && slotLoadSequence.current[slot.id] === sequence) {
          const hasPreviousData = Boolean(slotCandlesRef.current[slot.id]?.length)
          setSlotStatus((current) => ({ ...current, [slot.id]: hasPreviousData ? '暂时无法读取，继续显示上一份 K 线' : '暂时无法读取' }))
        }
      })
    })
    return () => { active = false }
  }, [fetchSignature, initialMarket, initialSymbol, initialTimeframe, loadCandles])

  useEffect(() => {
    if (!loadQuote) return
    let active = true
    const targets = JSON.parse(quoteFetchSignature) as Array<Pick<ChartSlotState, 'id' | 'market' | 'symbol'>>
    targets.forEach((slot, index) => {
      const identity = quoteIdentity(slot)
      if (index === 0 && slot.id === primarySlotId && slot.symbol === initialSymbol && slot.market === initialMarket && initialQuote !== undefined) return
      const sequence = (quoteLoadSequence.current[slot.id] ?? 0) + 1
      quoteLoadSequence.current[slot.id] = sequence
      setSlotQuoteIdentity((current) => ({ ...current, [slot.id]: identity }))
      setSlotQuotes((current) => ({ ...current, [slot.id]: null }))
      setSlotQuoteStatus((current) => ({ ...current, [slot.id]: slot.market === 'US' ? '读取报价中…' : 'A 股买卖盘未接入' }))
      void loadQuote(slot.symbol, slot.market).then((quote) => {
        if (!active || quoteLoadSequence.current[slot.id] !== sequence) return
        setSlotQuoteIdentity((current) => ({ ...current, [slot.id]: identity }))
        setSlotQuotes((current) => ({ ...current, [slot.id]: quote }))
        setSlotQuoteStatus((current) => ({ ...current, [slot.id]: quoteStatusLabel(quote) || '报价没有来源或时间' }))
      }).catch(() => {
        if (!active || quoteLoadSequence.current[slot.id] !== sequence) return
        setSlotQuoteStatus((current) => ({ ...current, [slot.id]: safeDataError() }))
      })
    })
    return () => { active = false }
  }, [initialMarket, initialQuote, initialSymbol, loadQuote, primarySlotId, quoteFetchSignature])

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

  useLayoutEffect(() => {
    if (!workbenchOpen) return
    const previousOverflow = document.body.style.overflow
    const previousGutter = document.documentElement.style.scrollbarGutter
    document.body.style.overflow = 'hidden'
    document.documentElement.style.scrollbarGutter = 'stable'
    return () => {
      document.body.style.overflow = previousOverflow
      document.documentElement.style.scrollbarGutter = previousGutter
    }
  }, [workbenchOpen])

  useLayoutEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const slotsToReflow = focusedSlotId
        ? requestedSlots.filter((slot) => slot.id === focusedSlotId)
        : requestedSlots
      slotsToReflow.forEach((slot) => chartRefs.current[slot.id]?.reflow())
    })
    return () => window.cancelAnimationFrame(frame)
  }, [focusedSlotId, inspectorVisible, marketBrowserOpen, requestedSlots, workbenchOpen, workspace.layout])

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
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
      if (workbenchOpen) {
        setWorkbenchOpen(false)
      }
    }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [focusedSlotId, inspectorVisible, layoutPickerOpen, setInspectorVisible, symbolEditorId, workbenchOpen])

  const setLayout = (layout: ChartLayoutId) => {
    if (isNarrowViewport && layoutDefinition(layout).desktopOnly) return
    setWorkspace((current) => ensureLayoutSlots(current, layout))
    setFocusedSlotId(null)
    setLayoutPickerOpen(false)
    if (layoutDefinition(layout).count > 1) setWorkbenchOpen(true)
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
    setMarketBrowserOpen(false)
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
  }

  const sendDrawingCommand = (type: DrawingCommand['type']) => {
    if (!activeSlot) return
    setDrawingCommand((current) => ({ id: current.id + 1, type, targetMarkerId: `drawing-arrow-${activeSlot.id}` }))
  }

  const completeDrawing = useCallback(() => setDrawingToolState((current) => ({ ...current, tool: 'cursor' })), [])
  const latest = slotLatest(activeCandles)

  return (
    <section className={`chart-workspace-shell ${workbenchOpen ? 'is-workbench-open' : ''} ${focusedSlotId ? 'is-pane-focused' : ''} ${inspectorVisible ? 'has-workbench-inspector' : ''}`} aria-label="多图 K线工作图">
      <header className="multi-chart-toolbar">
        <div className="workbench-title"><span className="workbench-title-label"><LayoutGrid size={16} /><strong>K线工作图</strong></span>{onWatchlistToggle && <WatchlistToggle symbol={activeSlot.symbol} saved={isWatchlisted?.(activeSlot.market, activeSlot.symbol) ?? false} busy={watchBusy === activeSlot.symbol} variant="label" className="workbench-watchlist-toggle" onToggle={(remove) => onWatchlistToggle(activeSlot.market, activeSlot.symbol, remove)} />}</div>
        <div className="multi-chart-layout-picker" ref={layoutPickerRef}>
          <button className="layout-picker-trigger" type="button" aria-haspopup="dialog" aria-expanded={layoutPickerOpen} aria-label="选择 K 线多图布局" title="选择多图布局" onClick={() => setLayoutPickerOpen((current) => !current)}><LayoutGrid size={15} /><span>{definition.label}</span></button>
          {layoutPickerOpen && <div className="layout-picker-popover" role="dialog" aria-label="K 线多图布局选择"><header><strong>分割视图</strong><small>{isNarrowViewport ? '手机版使用图表标签切换；四图以上请使用桌面版' : '选择后保持每张图的股票和周期'}</small></header><div className="layout-picker-grid">{CHART_LAYOUTS.map((layout) => { const unavailable = isNarrowViewport && layout.desktopOnly; return <button type="button" disabled={unavailable} className={workspace.layout === layout.id ? 'active' : ''} title={unavailable ? `${layout.label} · 仅桌面版` : layout.label} aria-label={`${layout.label}${unavailable ? '，仅桌面版' : ''}`} onClick={() => setLayout(layout.id)} key={layout.id}><span className={`layout-preview layout-preview-${layout.id}`}>{Array.from({ length: layout.count }, (_, index) => <i key={index} />)}</span><b>{layout.label}</b></button> })}</div></div>}
        </div>
        <div className="multi-chart-actions">
          <button type="button" title={marketBrowserOpen ? '收起市场列表' : '展开市场列表'} aria-label={marketBrowserOpen ? '收起市场列表' : '展开市场列表'} onClick={() => { setMarketBrowserOpen((current) => !current); if (inspectorVisible) setInspectorVisible(false); setSymbolEditorId(null) }}>{marketBrowserOpen ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}</button>
          <button type="button" title={inspectorVisible ? '收起检查器' : '展开检查器'} aria-label={inspectorVisible ? '收起检查器' : '展开检查器'} aria-expanded={inspectorVisible} onClick={() => { setMarketBrowserOpen(false); setSymbolEditorId(null); setInspectorVisible(!inspectorVisible) }}>{inspectorVisible ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}</button>
          <button type="button" title={workbenchOpen ? '返回行情页' : '打开全屏K线工作图'} aria-label={workbenchOpen ? '返回行情页' : '打开全屏K线工作图'} onClick={() => workbenchOpen ? exitWorkbench() : setWorkbenchOpen(true)}>{workbenchOpen ? <Shrink size={16} /> : <Expand size={16} />}</button>
        </div>
      </header>

      <div className="chart-workbench-body">
        <SharedDrawingToolbar
          state={drawingToolState}
          history={drawingHistory}
          onChange={(patch) => setDrawingToolState((current) => ({ ...current, ...patch }))}
          onCommand={sendDrawingCommand}
        />

        {marketBrowserOpen && (
          <aside className="chart-market-browser">
            <header><span><small>MARKET</small><strong>市场列表</strong></span><button className="icon-button" type="button" aria-label="收起市场列表" onClick={() => setMarketBrowserOpen(false)}><PanelLeftClose size={16} /></button></header>
            <div className="chart-market-browser-list">
              {instruments.slice(0, 12).map((instrument) => <button className={instrument.symbol === activeSlot.symbol ? 'active' : ''} type="button" onClick={() => { updateSlot(activeSlot.id, { symbol: instrument.symbol, market: instrument.market, viewport: undefined }); setMarketBrowserOpen(false) }} key={`${instrument.market}-${instrument.symbol}`}><span><strong>{instrument.symbol}</strong><small>{instrument.name}</small></span><span><b>{instrument.price ? instrument.price.toFixed(2) : '—'}</b><small className={instrument.changePct >= 0 ? 'positive-text' : 'negative-text'}>{instrument.changePct >= 0 ? '+' : ''}{instrument.changePct.toFixed(2)}%</small></span><i className={instrument.changePct >= 0 ? 'positive' : 'negative'} style={{ width: `${Math.min(100, Math.max(8, Math.abs(instrument.changePct) * 18))}%` }} /></button>)}
              {!instruments.length && <div className="chart-pane-state"><strong>暂无市场列表</strong><span>行情来源未连接</span></div>}
            </div>
          </aside>
        )}

        <div className={`multi-chart-grid layout-${workspace.layout}`}>
          <nav className="mobile-chart-tabs" aria-label="切换图表">
            {visibleSlots.map((slot, index) => <button className={slot.id === workspace.activeSlotId ? 'active' : ''} type="button" onClick={() => activateSlot(slot.id)} key={slot.id}>{index + 1} · {slot.symbol}</button>)}
          </nav>
          {workspace.slots.map((slot, slotIndex) => {
            const chartData = slotCandles[slot.id] ?? (slotIndex === 0 ? candles : [])
            const quote = slotQuoteIdentity[slot.id] === quoteIdentity(slot) ? slotQuotes[slot.id] : null
            const slotIsFocused = focusedSlotId === slot.id
            const slotIsActive = workspace.activeSlotId === slot.id
            const slotIsVisible = slotIndex < definition.count && (!isNarrowViewport || slotIsActive)
            const visibleDataStatus = slotStatus[slot.id]?.includes('K 线') ? slotStatus[slot.id] : null
            return (
              <article
                className={`chart-slot ${slotIsActive ? 'is-active' : ''} ${slotIsFocused ? 'is-focused' : ''} ${slotIsVisible ? '' : 'is-layout-hidden'}`}
                data-slot-index={slotIndex + 1}
                key={slot.id}
                onPointerDownCapture={() => activateSlot(slot.id)}
              >
                <header className="chart-slot-toolbar">
                  <button className="chart-symbol-trigger" type="button" aria-label={`更换 ${slot.symbol}`} title="更换股票" onClick={() => openSymbolEditor(slot)}><Search size={13} /><strong>{slot.symbol}</strong></button>
                  <TimeframeDropdown value={slot.timeframe} options={TIMEFRAME_OPTIONS} ariaLabel={`${slot.symbol} 时间周期`} onChange={(timeframe) => updateSlot(slot.id, { timeframe, viewport: undefined })} />
                  <span className="chart-slot-meta">
                    <span className="chart-slot-market">{marketLabel(slot.market)}</span>
                    {visibleDataStatus && <span className="chart-slot-data-status" role="status" title={visibleDataStatus}>{visibleDataStatus}</span>}
                  </span>
                  <div className="quote-spread-strip" aria-label="当前价格和买卖报价">
                    <span>现价 <b>{quoteNumber(quote?.last)}</b></span>
                    <span>卖出 Bid <b>{quoteNumber(quote?.bid)}</b></span>
                    <span>买入 Ask <b>{quoteNumber(quote?.ask)}</b></span>
                    <span title="买入价减卖出价；不是成交滑点">价差 <b>{quoteNumber(quote?.spread)}</b></span>
                  </div>
                  <div className="chart-view-controls">
                    <button type="button" title="向左移动" aria-label="向左移动" onClick={() => chartRefs.current[slot.id]?.panLeft()}><ArrowLeft size={14} /></button>
                    <button type="button" title="向右移动" aria-label="向右移动" onClick={() => chartRefs.current[slot.id]?.panRight()}><ArrowRight size={14} /></button>
                    <button type="button" title="缩小 K 线" aria-label="缩小 K 线" onClick={() => chartRefs.current[slot.id]?.zoomOut()}><ZoomOut size={14} /></button>
                    <button type="button" title="放大 K 线" aria-label="放大 K 线" onClick={() => chartRefs.current[slot.id]?.zoomIn()}><ZoomIn size={14} /></button>
                    <button type="button" title="适配全部 K 线" aria-label="适配全部 K 线" onClick={() => chartRefs.current[slot.id]?.reset()}><RotateCcw size={14} /></button>
                    <button type="button" title={slotIsFocused ? '恢复多图布局' : '最大化当前图'} aria-label={slotIsFocused ? '恢复多图布局' : '最大化当前图'} onClick={() => toggleFocus(slot.id)}>{slotIsFocused ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>
                  </div>
                </header>
                <div className="chart-slot-canvas">
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
                      officialActivity={officialActivity}
                      alertPrices={alertPrices}
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
                    <div className="chart-pane-state" role="status"><strong>{slotStatus[slot.id] ?? '暂无 K 线'}</strong><span>{slot.symbol} · {slot.timeframe}</span></div>
                  )}
                </div>
                <nav className="chart-range-presets" aria-label={`${slot.symbol} 常用周期与查看范围`}>
                  <span className="chart-quick-timeframes">{QUICK_TIMEFRAMES.map((value) => <button className={slot.timeframe === value ? 'active' : ''} type="button" onClick={() => updateSlot(slot.id, { timeframe: value, viewport: undefined })} key={value}>{value}</button>)}</span>
                  <i aria-hidden="true" />
                  <span>{RANGE_PRESETS.map(([value, label]) => <button type="button" onClick={() => chartRefs.current[slot.id]?.setRange(value)} key={value}>{label}</button>)}</span>
                </nav>
                {symbolEditorId === slot.id && (
                  <form className="chart-symbol-editor" onSubmit={(event) => { event.preventDefault(); applySymbol() }}>
                    <header><strong>更换当前图表</strong><button className="icon-button" type="button" aria-label="关闭" onClick={() => setSymbolEditorId(null)}>×</button></header>
                    <div className="market-tabs"><button className={marketDraft === 'US' ? 'active' : ''} type="button" onClick={() => setMarketDraft('US')}>美股</button><button className={marketDraft === 'CN' ? 'active' : ''} type="button" onClick={() => setMarketDraft('CN')}>A股</button></div>
                    <label><span>股票代码</span><input autoFocus value={symbolDraft} onChange={(event) => setSymbolDraft(event.target.value.toUpperCase())} placeholder={marketDraft === 'US' ? '例如 AAPL' : '例如 600519'} /></label>
                    <button className="button primary wide" type="submit">打开图表</button>
                  </form>
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
      </div>

      <footer className="multi-chart-footer"><span className="multi-chart-footer-context"><b>{visibleSlots.length} 图 · {activeSlot.symbol} · {activeSlot.timeframe}</b><small>{focusedSlotId ? '单图检视' : workbenchOpen ? '全屏K线工作图' : '行情页面'}</small></span>{footerActions && <span className="multi-chart-footer-actions">{footerActions}</span>}</footer>
    </section>
  )
}
