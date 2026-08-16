import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  createSeriesMarkers,
  type SeriesMarker,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LogicalRange,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { MarketQuotePayload, PortfolioActivity } from '../api/client'
import { applyFormingBarOverlay, shouldShowRealtimeLabel, type FormingMarketBar, type MarketStreamConnectionState } from '../api/marketStream'
import { buildChartTradeView, type ChartTradeInterval } from '../data/chartTrades'
import type { ChartDrawingPoint } from '../data/chartDrawings'
import type { Candle } from '../types'
import { getFormatLocale, localizeText } from '../i18n/runtime'
import {
  ChartDrawingLayer,
  type ChartCoordinateApi,
  type ChartPlotBounds,
  type DrawingCommand,
  type DrawingHistoryStatus,
  type DrawingToolState,
} from './ChartDrawingLayer'
import {
  CHART_TOUCH_LONG_PRESS_MS,
  chartTouchReleaseAction,
  createChartTouchSession,
  updateChartTouchSession,
  type ChartTouchSession,
} from './marketChartTouch'

export interface MarketChartHandle {
  zoomIn: () => void
  zoomOut: () => void
  panLeft: () => void
  panRight: () => void
  reset: () => void
  setRange: (range: '1D' | '5D' | '1M' | '3M' | '6M' | 'YTD' | '1Y' | 'ALL') => void
  viewport: () => LogicalRange | null
  syncCrosshair: (payload: ChartCrosshairSync | null, syncPrice: boolean) => void
  setVisibleTimeRange: (range: ChartTimeRange) => void
  reflow: () => void
}

function candleEpoch(value: Candle['time']) {
  if (typeof value === 'number') return value > 10_000_000_000 ? value : value * 1000
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function isRenderableCandle(value: unknown): value is Candle {
  if (!value || typeof value !== 'object') return false
  const candle = value as Partial<Candle>
  return (typeof candle.time === 'string' || typeof candle.time === 'number')
    && [candle.open, candle.high, candle.low, candle.close, candle.volume].every((item) => typeof item === 'number' && Number.isFinite(item))
}

function drawingTimeToChartTime(time: ChartDrawingPoint['time']): Time {
  return time as Time
}

export interface ChartCrosshairSync { time: Time; price: number }
export interface ChartTimeRange { from: Time; to: Time }

function readChartPlotBounds(host: HTMLElement, width: number, height: number): ChartPlotBounds {
  const hostRect = host.getBoundingClientRect()
  for (const table of host.querySelectorAll('table')) {
    const firstRow = table.querySelector('tr')
    if (!firstRow) continue
    const cell = Array.from(firstRow.children).find((candidate) => {
      const rect = candidate.getBoundingClientRect()
      return rect.width > 0 && rect.height > 0
    })
    if (!cell) continue
    const rect = cell.getBoundingClientRect()
    return {
      left: Math.max(0, rect.left - hostRect.left),
      top: Math.max(0, rect.top - hostRect.top),
      width: Math.max(1, rect.width),
      height: Math.max(1, rect.height),
    }
  }
  return { left: 0, top: 0, width: Math.max(1, width), height: Math.max(1, height) }
}

interface MarketChartProps {
  candles: Candle[]
  userId?: number | null
  market: 'US' | 'CN'
  symbol: string
  timeframe: string
  candleDataIdentity?: string
  showGrid?: boolean
  showVolume?: boolean
  upColor?: string
  downColor?: string
  textColor?: string
  dataStatus?: string
  quote?: MarketQuotePayload | null
  formingBar?: FormingMarketBar | null
  streamConnectionState?: MarketStreamConnectionState
  officialActivity?: PortfolioActivity | null
  alertPrices?: number[]
  drawingActive: boolean
  drawingToolState: DrawingToolState
  drawingCommand: DrawingCommand
  drawingMarkerId: string
  initialViewport?: { from: number; to: number }
  onDrawingHistoryChange: (status: DrawingHistoryStatus) => void
  onDrawingToolComplete: () => void
  onViewportChange: (range: { from: number; to: number }) => void
  onCrosshairChange?: (payload: ChartCrosshairSync | null) => void
  onVisibleTimeRangeChange?: (range: ChartTimeRange) => void
}

interface ActiveRange {
  trade: ChartTradeInterval
  left: number
  width: number
  pnl: number | null
  returnPct: number | null
  result: 'profit' | 'loss' | 'breakeven' | 'open'
}

function currency(value: number, code: string) {
  return new Intl.NumberFormat(getFormatLocale(), {
    style: 'currency', currency: code, signDisplay: 'always', maximumFractionDigits: 2,
  }).format(value)
}

function percentage(value: number) {
  return new Intl.NumberFormat(getFormatLocale(), {
    style: 'percent', signDisplay: 'always', minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(value / 100)
}

function shortTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat(getFormatLocale(), {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Hong_Kong',
  }).format(date)
}

function hongKongTime(value: Time | string | number | undefined, daily = false) {
  if (value === undefined) return '未记录'
  const source = typeof value === 'object'
    ? Date.UTC(value.year, value.month - 1, value.day, 12)
    : typeof value === 'number'
      ? value * 1000
      : Date.parse(value)
  const date = new Date(source)
  if (Number.isNaN(date.valueOf())) return String(value)
  return new Intl.DateTimeFormat(getFormatLocale(), daily
    ? { timeZone: 'Asia/Hong_Kong', year: 'numeric', month: '2-digit', day: '2-digit' }
    : { timeZone: 'Asia/Hong_Kong', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false },
  ).format(date)
}

function rangeResult(trade: ChartTradeInterval, latestPrice: number) {
  const interval = trade.interval
  const pnl = interval.status === 'CLOSED'
    ? interval.realized_pnl
    : interval.net_cash + interval.current_quantity * latestPrice
  const returnPct = interval.status === 'CLOSED'
    ? interval.realized_return_pct
    : pnl !== null && interval.entry_notional > 0 ? pnl / interval.entry_notional * 100 : null
  const result = pnl === null
    ? 'open'
    : pnl > 1e-9 ? 'profit' : pnl < -1e-9 ? 'loss' : 'breakeven'
  return { pnl, returnPct, result } as const
}

function resultLabel(active: ActiveRange) {
  const closed = active.trade.interval.status === 'CLOSED'
  if (active.result === 'profit') return closed ? '已实现盈利' : '持仓中 · 估算浮盈'
  if (active.result === 'loss') return closed ? '已实现亏损' : '持仓中 · 估算浮亏'
  if (active.result === 'breakeven') return closed ? '已平仓 · 持平' : '持仓中 · 暂时持平'
  return '持仓中 · 等待最新价格'
}

export const MarketChart = forwardRef<MarketChartHandle, MarketChartProps>(function MarketChart({
  candles: candleInput,
  userId,
  market,
  symbol,
  timeframe,
  candleDataIdentity,
  showGrid = true,
  showVolume = true,
  upColor: preferredUpColor,
  downColor: preferredDownColor,
  textColor: preferredTextColor,
  dataStatus = '界面演示数据',
  quote,
  formingBar,
  streamConnectionState,
  officialActivity,
  alertPrices = [],
  drawingActive,
  drawingToolState,
  drawingCommand,
  drawingMarkerId,
  initialViewport,
  onDrawingHistoryChange,
  onDrawingToolComplete,
  onViewportChange,
  onCrosshairChange,
  onVisibleTimeRangeChange,
}: MarketChartProps, ref) {
  const candles = useMemo(() => Array.isArray(candleInput) ? candleInput.filter(isRenderableCandle) : [], [candleInput])
  const chartHost = useRef<HTMLDivElement>(null)
  const chartApi = useRef<IChartApi | null>(null)
  const candleSeriesApi = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesApi = useRef<ISeriesApi<'Histogram'> | null>(null)
  const markerApi = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const alertLineApis = useRef<IPriceLine[]>([])
  const intervalById = useRef(new Map<string, ChartTradeInterval>())
  const markerIntervals = useRef(new Map<string, string>())
  const latestPriceRef = useRef(0)
  const activeIntervalId = useRef<string | null>(null)
  const lockedIntervalId = useRef<string | null>(null)
  const dataIdentity = useRef(candleDataIdentity ?? `${market}:${symbol}:${timeframe}`)
  const dataRangeFrame = useRef(0)
  const resizeChartRef = useRef<() => void>(() => {})
  const plotBoundsRef = useRef<ChartPlotBounds>({ left: 0, top: 0, width: 1, height: 1 })
  const viewportCallback = useRef(onViewportChange)
  const crosshairCallback = useRef(onCrosshairChange)
  const timeRangeCallback = useRef(onVisibleTimeRangeChange)
  const initialViewportRef = useRef(initialViewport)
  const initialCandlesRef = useRef(candles)
  const initialCandleDataIdentity = useRef(candleDataIdentity)
  const initialChartConfig = useRef({
    symbol,
    timeframe,
    market,
    showGrid,
    preferredUpColor,
    preferredDownColor,
    preferredTextColor,
  })
  const coarsePointerRef = useRef(false)
  const touchSessionRef = useRef<ChartTouchSession | null>(null)
  const touchLongPressTimer = useRef(0)
  const [activeRange, setActiveRange] = useState<ActiveRange | null>(null)
  const [hoveredCandle, setHoveredCandle] = useState<Candle | null>(null)
  const [coordinateApi, setCoordinateApi] = useState<ChartCoordinateApi | null>(null)
  const [coordinateVersion, setCoordinateVersion] = useState(0)
  const [plotBounds, setPlotBounds] = useState<ChartPlotBounds>(plotBoundsRef.current)
  const [themeToken, setThemeToken] = useState(() => document.documentElement.dataset.theme ?? 'dark')
  const displayCandles = useMemo(() => applyFormingBarOverlay(candles, formingBar), [candles, formingBar])
  const realtimeLabelVisible = shouldShowRealtimeLabel(formingBar, streamConnectionState)
  const chartView = useMemo(
    () => buildChartTradeView(candles, officialActivity, market, symbol),
    [candles, market, officialActivity, symbol],
  )
  const chartDescription = `${symbol} ${localizeText(timeframe)}${localizeText('蜡烛图')}${showVolume ? localizeText('与成交量') : ''}，${localizeText(dataStatus)}${chartView.executions.length ? `，${localizeText('包含')} ${chartView.executions.length} ${localizeText('个模拟成交标记')}` : ''}`
  const latest = displayCandles.at(-1)

  const hideActiveRange = useCallback(() => {
    activeIntervalId.current = null
    setActiveRange(null)
  }, [])

  const showActiveRange = useCallback((intervalId: string) => {
    const chart = chartApi.current
    const trade = intervalById.current.get(intervalId)
    if (!chart || !trade) return
    const start = chart.timeScale().timeToCoordinate(trade.startTime as Time)
    const end = chart.timeScale().timeToCoordinate(trade.endTime as Time)
    if (start === null || end === null) {
      hideActiveRange()
      return
    }
    activeIntervalId.current = intervalId
    const left = Math.max(0, Math.min(start, end))
    const right = Math.max(start, end)
    setActiveRange({
      trade,
      left,
      width: Math.max(8, right - left),
      ...rangeResult(trade, latestPriceRef.current),
    })
  }, [hideActiveRange])

  const zoomDrawingLayer = useCallback((deltaY: number) => {
    const scale = chartApi.current?.timeScale()
    const range = scale?.getVisibleLogicalRange()
    if (!scale || !range) return
    const center = (range.from + range.to) / 2
    const factor = deltaY < 0 ? 0.86 : 1.16
    const half = Math.max(3, (range.to - range.from) * factor / 2)
    scale.setVisibleLogicalRange({ from: center - half, to: center + half })
  }, [])

  useEffect(() => {
    viewportCallback.current = onViewportChange
  }, [onViewportChange])

  useEffect(() => {
    crosshairCallback.current = onCrosshairChange
  }, [onCrosshairChange])

  useEffect(() => {
    timeRangeCallback.current = onVisibleTimeRangeChange
  }, [onVisibleTimeRangeChange])

  useEffect(() => {
    initialViewportRef.current = initialViewport
    const scale = chartApi.current?.timeScale()
    const current = scale?.getVisibleLogicalRange()
    if (!scale || !initialViewport || !current) return
    if (Math.abs(Number(current.from) - initialViewport.from) < 0.05 && Math.abs(Number(current.to) - initialViewport.to) < 0.05) return
    scale.setVisibleLogicalRange(initialViewport)
  }, [initialViewport])

  useImperativeHandle(ref, () => {
    const scaleRange = (factor: number) => {
      const scale = chartApi.current?.timeScale()
      const range = scale?.getVisibleLogicalRange()
      if (!scale || !range) return
      const center = (range.from + range.to) / 2
      const half = Math.max(3, (range.to - range.from) * factor / 2)
      scale.setVisibleLogicalRange({ from: center - half, to: center + half })
    }
    const pan = (bars: number) => {
      const scale = chartApi.current?.timeScale()
      const range = scale?.getVisibleLogicalRange()
      if (!scale || !range) return
      scale.setVisibleLogicalRange({ from: range.from + bars, to: range.to + bars })
    }
    const setRange = (range: '1D' | '5D' | '1M' | '3M' | '6M' | 'YTD' | '1Y' | 'ALL') => {
      const scale = chartApi.current?.timeScale()
      if (!scale || !candles.length) return
      if (range === 'ALL') {
        scale.fitContent()
        return
      }
      const latestTime = candleEpoch(candles.at(-1)!.time)
      const latestDate = new Date(latestTime)
      const startTime = range === 'YTD'
        ? new Date(latestDate.getFullYear(), 0, 1).getTime()
        : latestTime - ({ '1D': 1, '5D': 5, '1M': 31, '3M': 93, '6M': 186, '1Y': 366 }[range] * 86_400_000)
      const firstVisible = Math.max(0, candles.findIndex((item) => candleEpoch(item.time) >= startTime))
      scale.setVisibleLogicalRange({ from: Math.max(-2, firstVisible - 1), to: candles.length + 1 })
    }
    return {
      zoomIn: () => scaleRange(0.78),
      zoomOut: () => scaleRange(1.28),
      panLeft: () => pan(-5),
      panRight: () => pan(5),
      reset: () => chartApi.current?.timeScale().fitContent(),
      setRange,
      viewport: () => chartApi.current?.timeScale().getVisibleLogicalRange() ?? null,
      syncCrosshair: (payload, syncPrice) => {
        const chart = chartApi.current
        const series = candleSeriesApi.current
        if (!chart || !series) return
        if (!payload) {
          chart.clearCrosshairPosition()
          return
        }
        const localPrice = candles.find((item) => String(item.time) === String(payload.time))?.close ?? candles.at(-1)?.close ?? payload.price
        chart.setCrosshairPosition(syncPrice ? payload.price : localPrice, payload.time, series)
      },
      setVisibleTimeRange: (range) => chartApi.current?.timeScale().setVisibleRange(range),
      reflow: () => resizeChartRef.current(),
    }
  }, [candles])

  useEffect(() => {
    const observer = new MutationObserver(() => setThemeToken(document.documentElement.dataset.theme ?? 'dark'))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])

  useLayoutEffect(() => {
    if (!chartHost.current) return
    const host = chartHost.current
    const initialData = initialCandlesRef.current
    const initialConfig = initialChartConfig.current
    const styles = getComputedStyle(document.documentElement)
    const chartBackground = styles.getPropertyValue('--chart-background').trim() || '#12161c'
    const chartGrid = styles.getPropertyValue('--chart-grid').trim() || '#1c222a'
    const chartText = initialConfig.preferredTextColor || styles.getPropertyValue('--text').trim() || '#f2f4f7'
    const chartBorder = styles.getPropertyValue('--border-strong').trim() || '#5c6875'
    const chartCrosshairLabel = styles.getPropertyValue('--surface-raised').trim() || chartBackground
    const chartFont = styles.getPropertyValue('--font-ui').trim() || "'Noto Sans TC', 'PingFang TC', sans-serif"
    const upColor = initialConfig.preferredUpColor || (initialConfig.market === 'CN' ? '#e4606b' : '#27b487')
    const downColor = initialConfig.preferredDownColor || (initialConfig.market === 'CN' ? '#27b487' : '#e4606b')
    const chart = createChart(host, {
      autoSize: false,
      layout: {
        background: { type: ColorType.Solid, color: chartBackground },
        textColor: chartText,
        fontFamily: chartFont,
        fontSize: 12,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: initialConfig.showGrid ? chartGrid : chartBackground },
        horzLines: { color: initialConfig.showGrid ? chartGrid : chartBackground },
      },
      crosshair: {
        vertLine: { color: chartBorder, labelBackgroundColor: chartCrosshairLabel },
        horzLine: { color: chartBorder, labelBackgroundColor: chartCrosshairLabel },
      },
      rightPriceScale: { borderColor: chartBorder, minimumWidth: 62 },
      localization: { locale: getFormatLocale(), timeFormatter: (value: Time) => hongKongTime(value) },
      timeScale: { borderColor: chartBorder, timeVisible: true, tickMarkFormatter: (value: Time) => hongKongTime(value) },
    })
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor,
      downColor,
      wickUpColor: upColor,
      wickDownColor: downColor,
      borderVisible: false,
      priceLineVisible: true,
      lastValueVisible: true,
    })
    candleSeries.setData(initialData.map(({ time, open, high, low, close }) => ({
      time: time as unknown as UTCTimestamp, open, high, low, close,
    })))
    latestPriceRef.current = initialData.at(-1)?.close ?? 0
    dataIdentity.current = initialCandleDataIdentity.current ?? `${initialConfig.market}:${initialConfig.symbol}:${initialConfig.timeframe}`
    chartApi.current = chart
    candleSeriesApi.current = candleSeries
    markerApi.current = createSeriesMarkers(candleSeries, [], { autoScale: true })
    setCoordinateApi({
      coordinateToPoint: (x, y) => {
        const bounds = plotBoundsRef.current
        const time = chart.timeScale().coordinateToTime(x + bounds.left)
        const price = candleSeries.coordinateToPrice(y + bounds.top)
        return time === null || price === null ? null : { time, price: Number(price) }
      },
      pointToCoordinate: (point) => {
        const bounds = plotBoundsRef.current
        const x = chart.timeScale().timeToCoordinate(drawingTimeToChartTime(point.time))
        const y = candleSeries.priceToCoordinate(point.price)
        return x === null || y === null ? null : { x: Number(x) - bounds.left, y: Number(y) - bounds.top }
      },
    })

    const coarsePointer = window.matchMedia('(hover: none), (pointer: coarse)').matches
    coarsePointerRef.current = coarsePointer
    const hoveredInterval = (objectId: unknown) => (
      typeof objectId === 'string' ? markerIntervals.current.get(objectId) : undefined
    )
    const onCrosshairMove = (parameter: MouseEventParams<Time>) => {
      const seriesValue = parameter.seriesData.get(candleSeries) as { open?: number; high?: number; low?: number; close?: number } | undefined
      const touchObservationInProgress = coarsePointer && touchSessionRef.current?.observing === true
      const acceptsSourceObservation = !coarsePointer || !parameter.sourceEvent || touchObservationInProgress
      if (acceptsSourceObservation && parameter.time !== undefined && seriesValue && [seriesValue.open, seriesValue.high, seriesValue.low, seriesValue.close].every(Number.isFinite)) {
        setHoveredCandle((current) => current?.time === parameter.time ? current : {
          time: parameter.time as unknown as Candle['time'],
          open: Number(seriesValue.open),
          high: Number(seriesValue.high),
          low: Number(seriesValue.low),
          close: Number(seriesValue.close),
          volume: 0,
        })
      } else if (acceptsSourceObservation) {
        setHoveredCandle(null)
      }
      if (parameter.sourceEvent && acceptsSourceObservation) {
        const price = parameter.point ? candleSeries.coordinateToPrice(parameter.point.y) : null
        crosshairCallback.current?.(parameter.time !== undefined && price !== null ? { time: parameter.time, price: Number(price) } : null)
      }
      if (touchObservationInProgress) return
      if (coarsePointer || lockedIntervalId.current) return
      const intervalId = hoveredInterval(parameter.hoveredInfo?.objectId ?? parameter.hoveredObjectId)
      if (intervalId) showActiveRange(intervalId)
      else if (activeIntervalId.current) hideActiveRange()
    }
    const onClick = (parameter: { hoveredInfo?: { objectId?: unknown }; hoveredObjectId?: unknown }) => {
      const intervalId = hoveredInterval(parameter.hoveredInfo?.objectId ?? parameter.hoveredObjectId)
      if (!intervalId) return
      if (lockedIntervalId.current === intervalId) {
        lockedIntervalId.current = null
        hideActiveRange()
        return
      }
      lockedIntervalId.current = intervalId
      showActiveRange(intervalId)
    }
    let coordinateFrame = 0
    const refreshCoordinates = () => {
      window.cancelAnimationFrame(coordinateFrame)
      coordinateFrame = window.requestAnimationFrame(() => setCoordinateVersion((value) => value + 1))
    }
    let rangeFrame = 0
    let pendingLogicalRange: LogicalRange | null = null
    let pendingTimeRange: ChartTimeRange | null = null
    const flushVisibleRange = () => {
      rangeFrame = 0
      if (activeIntervalId.current) showActiveRange(activeIntervalId.current)
      if (pendingLogicalRange) viewportCallback.current({ from: Number(pendingLogicalRange.from), to: Number(pendingLogicalRange.to) })
      if (pendingTimeRange) timeRangeCallback.current?.(pendingTimeRange)
      pendingLogicalRange = null
      pendingTimeRange = null
    }
    const onVisibleRangeChange = (range: LogicalRange | null) => {
      pendingLogicalRange = range
      const timeRange = chart.timeScale().getVisibleRange()
      pendingTimeRange = timeRange ? { from: timeRange.from, to: timeRange.to } : null
      refreshCoordinates()
      if (!rangeFrame) rangeFrame = window.requestAnimationFrame(flushVisibleRange)
    }
    let resizeFrame = 0
    let chartWidth = 0
    let chartHeight = 0
    const commitPlotBounds = (width: number, height: number) => {
      const nextPlotBounds = readChartPlotBounds(host, width, height)
      plotBoundsRef.current = nextPlotBounds
      setPlotBounds((current) => (
        Math.abs(current.left - nextPlotBounds.left) < 0.5
        && Math.abs(current.top - nextPlotBounds.top) < 0.5
        && Math.abs(current.width - nextPlotBounds.width) < 0.5
        && Math.abs(current.height - nextPlotBounds.height) < 0.5
      ) ? current : nextPlotBounds)
    }
    const applyChartSize = () => {
      const width = Math.floor(host.clientWidth)
      const height = Math.floor(host.clientHeight)
      if (width <= 0 || height <= 0) return
      if (width === chartWidth && height === chartHeight) return
      chartWidth = width
      chartHeight = height
      chart.resize(width, height)
      commitPlotBounds(width, height)
      if (activeIntervalId.current) showActiveRange(activeIntervalId.current)
      refreshCoordinates()
    }
    const scheduleChartResize = () => {
      if (resizeFrame) return
      resizeFrame = window.requestAnimationFrame(() => {
        resizeFrame = 0
        applyChartSize()
      })
    }
    resizeChartRef.current = applyChartSize
    const resizeObserver = new ResizeObserver(scheduleChartResize)
    resizeObserver.observe(host)
    chart.subscribeCrosshairMove(onCrosshairMove)
    chart.subscribeClick(onClick)
    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRangeChange)
    const savedViewport = initialViewportRef.current
    if (savedViewport && savedViewport.to > savedViewport.from) chart.timeScale().setVisibleLogicalRange(savedViewport)
    else chart.timeScale().fitContent()
    applyChartSize()

    return () => {
      resizeObserver.disconnect()
      window.cancelAnimationFrame(resizeFrame)
      window.cancelAnimationFrame(coordinateFrame)
      window.cancelAnimationFrame(rangeFrame)
      chart.unsubscribeCrosshairMove(onCrosshairMove)
      chart.unsubscribeClick(onClick)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleRangeChange)
      chartApi.current = null
      candleSeriesApi.current = null
      volumeSeriesApi.current = null
      markerApi.current = null
      alertLineApis.current = []
      intervalById.current.clear()
      markerIntervals.current.clear()
      activeIntervalId.current = null
      lockedIntervalId.current = null
      coarsePointerRef.current = false
      resizeChartRef.current = () => {}
      setCoordinateApi(null)
      chart.remove()
    }
  }, [hideActiveRange, showActiveRange])

  useEffect(() => () => window.clearTimeout(touchLongPressTimer.current), [])

  useEffect(() => {
    const chart = chartApi.current
    const candleSeries = candleSeriesApi.current
    if (!chart || !candleSeries) return
    const styles = getComputedStyle(document.documentElement)
    const chartBackground = styles.getPropertyValue('--chart-background').trim() || '#12161c'
    const chartGrid = styles.getPropertyValue('--chart-grid').trim() || '#1c222a'
    const chartText = preferredTextColor || styles.getPropertyValue('--text').trim() || '#f2f4f7'
    const chartBorder = styles.getPropertyValue('--border-strong').trim() || '#5c6875'
    const chartCrosshairLabel = styles.getPropertyValue('--surface-raised').trim() || chartBackground
    const chartFont = styles.getPropertyValue('--font-ui').trim() || "'Noto Sans TC', 'PingFang TC', sans-serif"
    const upColor = preferredUpColor || (market === 'CN' ? '#e4606b' : '#27b487')
    const downColor = preferredDownColor || (market === 'CN' ? '#27b487' : '#e4606b')
    chart.applyOptions({
      layout: { background: { type: ColorType.Solid, color: chartBackground }, textColor: chartText, fontFamily: chartFont },
      grid: {
        vertLines: { color: showGrid ? chartGrid : chartBackground },
        horzLines: { color: showGrid ? chartGrid : chartBackground },
      },
      crosshair: {
        vertLine: { color: chartBorder, labelBackgroundColor: chartCrosshairLabel },
        horzLine: { color: chartBorder, labelBackgroundColor: chartCrosshairLabel },
      },
      rightPriceScale: { borderColor: chartBorder, minimumWidth: 62 },
      timeScale: { borderColor: chartBorder, tickMarkFormatter: (value: Time) => hongKongTime(value) },
    })
    candleSeries.applyOptions({ upColor, downColor, wickUpColor: upColor, wickDownColor: downColor })
  }, [market, preferredDownColor, preferredTextColor, preferredUpColor, showGrid, themeToken])

  useEffect(() => {
    const chart = chartApi.current
    const candleSeries = candleSeriesApi.current
    if (!chart || !candleSeries) return
    const scale = chart.timeScale()
    const previousRange = scale.getVisibleLogicalRange()
    const nextIdentity = candleDataIdentity ?? `${market}:${symbol}:${timeframe}`
    const identityChanged = dataIdentity.current !== nextIdentity
    if (identityChanged) setHoveredCandle(null)
    dataIdentity.current = nextIdentity
    latestPriceRef.current = displayCandles.at(-1)?.close ?? 0
    candleSeries.setData(displayCandles.map(({ time, open, high, low, close }) => ({
      time: time as unknown as UTCTimestamp, open, high, low, close,
    })))
    const upColor = preferredUpColor || (market === 'CN' ? '#e4606b' : '#27b487')
    const downColor = preferredDownColor || (market === 'CN' ? '#27b487' : '#e4606b')
    if (showVolume) {
      if (!volumeSeriesApi.current) {
        volumeSeriesApi.current = chart.addSeries(HistogramSeries, {
          priceFormat: { type: 'volume' }, priceScaleId: '', lastValueVisible: false, priceLineVisible: false,
        })
        volumeSeriesApi.current.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
      }
      volumeSeriesApi.current.setData(displayCandles.map(({ time, volume, open, close }) => ({
        time: time as unknown as UTCTimestamp,
        value: volume,
        color: close >= open ? `${upColor}66` : `${downColor}66`,
      })))
    } else if (volumeSeriesApi.current) {
      chart.removeSeries(volumeSeriesApi.current)
      volumeSeriesApi.current = null
    }
    window.cancelAnimationFrame(dataRangeFrame.current)
    dataRangeFrame.current = window.requestAnimationFrame(() => {
      if (identityChanged) {
        lockedIntervalId.current = null
        hideActiveRange()
        const savedViewport = initialViewportRef.current
        if (savedViewport && savedViewport.to > savedViewport.from) scale.setVisibleLogicalRange(savedViewport)
        else if (displayCandles.length) scale.fitContent()
      } else if (previousRange) {
        scale.setVisibleLogicalRange(previousRange)
      }
      setCoordinateVersion((value) => value + 1)
    })
  }, [candleDataIdentity, displayCandles, hideActiveRange, market, preferredDownColor, preferredUpColor, showVolume, symbol, timeframe])

  useEffect(() => {
    intervalById.current = new Map(chartView.intervals.map((item) => [item.interval.interval_id, item]))
    markerIntervals.current = new Map(chartView.executions.map((item) => [item.execution_id, item.interval_id]))
    const timeOrder = new Map(candles.map((candle, index) => [String(candle.time), index]))
    const markers = chartView.executions.map<SeriesMarker<Time>>((execution) => ({
      id: execution.execution_id,
      time: execution.chartTime as Time,
      position: execution.side === 'BUY' ? 'belowBar' : 'aboveBar',
      shape: execution.side === 'BUY' ? 'arrowUp' : 'arrowDown',
      color: execution.side === 'BUY' ? '#35c997' : '#f1717d',
      text: execution.side === 'BUY' ? 'Buy' : 'Sell',
      size: 1.2,
    })).sort((left, right) => (timeOrder.get(String(left.time)) ?? 0) - (timeOrder.get(String(right.time)) ?? 0))
    markerApi.current?.setMarkers(markers)
    if (activeIntervalId.current && intervalById.current.has(activeIntervalId.current)) showActiveRange(activeIntervalId.current)
    else if (activeIntervalId.current) hideActiveRange()
  }, [candles, chartView, hideActiveRange, showActiveRange])

  useEffect(() => {
    const candleSeries = candleSeriesApi.current
    if (!candleSeries) return
    alertLineApis.current.forEach((line) => candleSeries.removePriceLine(line))
    alertLineApis.current = alertPrices.filter(Number.isFinite).map((price) => candleSeries.createPriceLine({
      price, color: '#e0a33c', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '预警',
    }))
  }, [alertPrices])

  useEffect(() => () => window.cancelAnimationFrame(dataRangeFrame.current), [])

  const displayedCandle = hoveredCandle ?? latest
  const displayedCandleTone = displayedCandle && displayedCandle.close >= displayedCandle.open ? 'up' : 'down'
  const crosshairCardStyle = {
    '--chart-card-text': preferredTextColor || 'var(--text)',
    '--chart-card-up': preferredUpColor || (market === 'CN' ? '#e4606b' : '#27b487'),
    '--chart-card-down': preferredDownColor || (market === 'CN' ? '#27b487' : '#e4606b'),
  } as CSSProperties

  const observeTouchPoint = (clientX: number) => {
    const host = chartHost.current
    const chart = chartApi.current
    const series = candleSeriesApi.current
    if (!host || !chart || !series) return
    const rect = host.getBoundingClientRect()
    const x = Math.max(0, Math.min(rect.width, clientX - rect.left))
    const time = chart.timeScale().coordinateToTime(x)
    if (time === null) return
    const candle = displayCandles.find((item) => String(item.time) === String(time))
    if (!candle) return
    chart.setCrosshairPosition(candle.close, time, series)
    setHoveredCandle(candle)
    crosshairCallback.current?.({ time, price: candle.close })
  }

  const clearTouchObservation = () => {
    window.clearTimeout(touchLongPressTimer.current)
    touchSessionRef.current = null
  }

  const onChartPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!coarsePointerRef.current || event.pointerType === 'mouse' || drawingToolState.tool !== 'cursor') return
    clearTouchObservation()
    const session = createChartTouchSession(event.pointerId, { x: event.clientX, y: event.clientY }, event.timeStamp)
    touchSessionRef.current = session
    touchLongPressTimer.current = window.setTimeout(() => {
      const current = touchSessionRef.current
      if (!current || current.pointerId !== event.pointerId || current.moved) return
      touchSessionRef.current = { ...current, observing: true }
      chartHost.current?.setPointerCapture?.(event.pointerId)
      observeTouchPoint(current.latest.x)
    }, CHART_TOUCH_LONG_PRESS_MS)
  }

  const onChartPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const current = touchSessionRef.current
    if (!current || current.pointerId !== event.pointerId) return
    const next = updateChartTouchSession(current, { x: event.clientX, y: event.clientY })
    touchSessionRef.current = next
    if (next.moved && !next.observing) window.clearTimeout(touchLongPressTimer.current)
    if (!next.observing) return
    event.preventDefault()
    event.stopPropagation()
    observeTouchPoint(event.clientX)
  }

  const onChartPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const current = touchSessionRef.current
    if (!current || current.pointerId !== event.pointerId) return
    const action = chartTouchReleaseAction(current)
    if (action === 'observe') observeTouchPoint(event.clientX)
    if (action === 'release') {
      event.preventDefault()
      event.stopPropagation()
    }
    if (chartHost.current?.hasPointerCapture?.(event.pointerId)) chartHost.current.releasePointerCapture(event.pointerId)
    clearTouchObservation()
  }

  const onChartPointerCancel = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (touchSessionRef.current?.pointerId !== event.pointerId) return
    if (chartHost.current?.hasPointerCapture?.(event.pointerId)) chartHost.current.releasePointerCapture(event.pointerId)
    clearTouchObservation()
  }

  return (
    <div
      className="market-chart-stage"
      onPointerDownCapture={onChartPointerDown}
      onPointerMoveCapture={onChartPointerMove}
      onPointerUpCapture={onChartPointerUp}
      onPointerCancelCapture={onChartPointerCancel}
    >
      <div
        className="market-chart-canvas"
        ref={chartHost}
        role="img"
        aria-label={chartDescription}
      />
      {displayedCandle && <div className="chart-crosshair-card" style={crosshairCardStyle} aria-label="观察 K 线与当前盘口"><header><b>{hongKongTime(displayedCandle.time, timeframe === '日线')}</b><small>{timeframe === '日线' ? '日线以美国交易日归档' : 'Asia/Hong_Kong'}</small></header><div className={`chart-observed-ohlc ${displayedCandleTone}`}><span>开 <b>{displayedCandle.open.toFixed(2)}</b></span><span>高 <b>{displayedCandle.high.toFixed(2)}</b></span><span>低 <b>{displayedCandle.low.toFixed(2)}</b></span><span>收 <b>{displayedCandle.close.toFixed(2)}</b></span></div><div className="chart-live-quote"><small>当前盘口</small><span className="bid">Bid <b>{quote?.bid?.toFixed(2) ?? '—'}</b></span><span className="spread">Spread <b>{quote?.spread?.toFixed(2) ?? '—'}</b></span><span className="ask">Ask <b>{quote?.ask?.toFixed(2) ?? '—'}</b></span><time>{quote?.quote_at ? hongKongTime(quote.quote_at) : '报价时间未记录'}</time></div>{hoveredCandle && <small className="chart-historical-quote">盘口为当前报价，不代表所观察历史 K 线</small>}</div>}
      {realtimeLabelVisible && <span className="chart-realtime-label" role="status" aria-live="polite">实时成形 K 线</span>}
      <ChartDrawingLayer
        active={drawingActive}
        userId={userId}
        candles={candles}
        market={market}
        symbol={symbol}
        timeframe={timeframe}
        coordinateApi={coordinateApi}
        coordinateVersion={coordinateVersion}
        plotBounds={plotBounds}
        toolState={drawingToolState}
        command={drawingCommand}
        markerId={drawingMarkerId}
        onHistoryChange={onDrawingHistoryChange}
        onToolComplete={onDrawingToolComplete}
        onWheelZoom={zoomDrawingLayer}
      />
      {chartView.executions.length > 0 && (
        <div className="trade-marker-legend" aria-label={`模拟成交：${chartView.executions.length} 笔`}>
          <span className="buy">Buy</span><span className="sell">Sell</span><small>模拟成交</small>
        </div>
      )}
      {activeRange && (
        <>
          <div
            className={`trade-range-overlay ${activeRange.result}`}
            style={{ left: activeRange.left, width: activeRange.width }}
            aria-hidden="true"
          />
          <div className={`trade-range-summary ${activeRange.result}`} role="status" aria-live="polite">
            <strong>{resultLabel(activeRange)}</strong>
            {activeRange.pnl !== null && <b>{currency(activeRange.pnl, activeRange.trade.interval.currency)}</b>}
            {activeRange.returnPct !== null && <span>{percentage(activeRange.returnPct)}</span>}
            <small>
              {shortTime(activeRange.trade.interval.opened_at)} → {activeRange.trade.interval.closed_at ? shortTime(activeRange.trade.interval.closed_at) : '最新 K 线'}
            </small>
          </div>
        </>
      )}
      {chartView.hiddenExecutionCount > 0 && (
        <span className="trade-marker-hidden">另有 {chartView.hiddenExecutionCount} 笔成交不在当前范围</span>
      )}
    </div>
  )
})
