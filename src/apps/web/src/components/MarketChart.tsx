import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  createSeriesMarkers,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { PortfolioActivity } from '../api/client'
import { buildChartTradeView, type ChartTradeInterval } from '../data/chartTrades'
import type { Candle } from '../types'
import { getFormatLocale, localizeText } from '../i18n/runtime'

interface MarketChartProps {
  candles: Candle[]
  market: 'US' | 'CN'
  symbol: string
  timeframe: string
  showGrid?: boolean
  showVolume?: boolean
  dataStatus?: string
  paperActivity?: PortfolioActivity | null
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
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date)
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

export function MarketChart({
  candles,
  market,
  symbol,
  timeframe,
  showGrid = true,
  showVolume = true,
  dataStatus = '界面演示数据',
  paperActivity,
}: MarketChartProps) {
  const chartHost = useRef<HTMLDivElement>(null)
  const [activeRange, setActiveRange] = useState<ActiveRange | null>(null)
  const chartView = useMemo(
    () => buildChartTradeView(candles, paperActivity, symbol),
    [candles, paperActivity, symbol],
  )
  const chartDescription = `${symbol} ${localizeText(timeframe)}${localizeText('蜡烛图')}${showVolume ? localizeText('与成交量') : ''}，${localizeText(dataStatus)}${chartView.executions.length ? `，${localizeText('包含')} ${chartView.executions.length} ${localizeText('个模拟成交标记')}` : ''}`

  useEffect(() => {
    if (!chartHost.current) return
    const host = chartHost.current
    setActiveRange(null)

    const upColor = market === 'CN' ? '#e4606b' : '#27b487'
    const downColor = market === 'CN' ? '#27b487' : '#e4606b'
    const chart = createChart(chartHost.current, {
      autoSize: false,
      layout: {
        background: { type: ColorType.Solid, color: '#12161c' },
        textColor: '#8f9aa7',
        fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
        fontSize: 12,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: showGrid ? '#1c222a' : '#12161c' },
        horzLines: { color: showGrid ? '#1c222a' : '#12161c' },
      },
      crosshair: {
        vertLine: { color: '#65717f', labelBackgroundColor: '#2a323d' },
        horzLine: { color: '#65717f', labelBackgroundColor: '#2a323d' },
      },
      rightPriceScale: { borderColor: '#29313b' },
      timeScale: { borderColor: '#29313b', timeVisible: true },
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
    candleSeries.setData(candles.map(({ time, open, high, low, close }) => ({
      time: time as unknown as UTCTimestamp,
      open,
      high,
      low,
      close,
    })))

    if (showVolume) {
      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceScaleId: '',
        lastValueVisible: false,
        priceLineVisible: false,
      })
      volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
      volumeSeries.setData(candles.map(({ time, volume, open, close }) => ({
        time: time as unknown as UTCTimestamp,
        value: volume,
        color: close >= open ? `${upColor}66` : `${downColor}66`,
      })))
    }

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
    if (markers.length) createSeriesMarkers(candleSeries, markers, { autoScale: true })

    const intervalById = new Map(chartView.intervals.map((item) => [item.interval.interval_id, item]))
    const markerIntervals = new Map(chartView.executions.map((item) => [item.execution_id, item.interval_id]))
    const latestPrice = candles.at(-1)?.close ?? 0
    const coarsePointer = window.matchMedia('(hover: none), (pointer: coarse)').matches
    let activeId: string | null = null
    let lockedId: string | null = null

    const hideRange = () => {
      activeId = null
      setActiveRange(null)
    }
    const showRange = (intervalId: string) => {
      const trade = intervalById.get(intervalId)
      if (!trade) return
      const start = chart.timeScale().timeToCoordinate(trade.startTime as Time)
      const end = chart.timeScale().timeToCoordinate(trade.endTime as Time)
      if (start === null || end === null) {
        hideRange()
        return
      }
      activeId = intervalId
      const left = Math.max(0, Math.min(start, end))
      const right = Math.max(start, end)
      setActiveRange({
        trade,
        left,
        width: Math.max(8, right - left),
        ...rangeResult(trade, latestPrice),
      })
    }
    const hoveredInterval = (objectId: unknown) => (
      typeof objectId === 'string' ? markerIntervals.get(objectId) : undefined
    )
    const onCrosshairMove = (parameter: { hoveredInfo?: { objectId?: unknown }; hoveredObjectId?: unknown }) => {
      if (coarsePointer || lockedId) return
      const intervalId = hoveredInterval(parameter.hoveredInfo?.objectId ?? parameter.hoveredObjectId)
      if (intervalId) showRange(intervalId)
      else if (activeId) hideRange()
    }
    const onClick = (parameter: { hoveredInfo?: { objectId?: unknown }; hoveredObjectId?: unknown }) => {
      const intervalId = hoveredInterval(parameter.hoveredInfo?.objectId ?? parameter.hoveredObjectId)
      if (!intervalId) return
      if (lockedId === intervalId) {
        lockedId = null
        hideRange()
        return
      }
      lockedId = intervalId
      showRange(intervalId)
    }
    const onVisibleRangeChange = () => {
      if (activeId) showRange(activeId)
    }
    let resizeFrame = 0
    const resizeChart = () => {
      window.cancelAnimationFrame(resizeFrame)
      resizeFrame = window.requestAnimationFrame(() => {
        const width = Math.floor(host.clientWidth)
        const height = Math.floor(host.clientHeight)
        if (width > 0 && height > 0) chart.resize(width, height)
        if (activeId) showRange(activeId)
      })
    }
    const resizeObserver = new ResizeObserver(resizeChart)
    resizeObserver.observe(host)
    window.addEventListener('resize', resizeChart)
    chart.subscribeCrosshairMove(onCrosshairMove)
    chart.subscribeClick(onClick)
    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRangeChange)
    chart.timeScale().fitContent()
    resizeChart()

    return () => {
      resizeObserver.disconnect()
      window.removeEventListener('resize', resizeChart)
      window.cancelAnimationFrame(resizeFrame)
      chart.unsubscribeCrosshairMove(onCrosshairMove)
      chart.unsubscribeClick(onClick)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleRangeChange)
      chart.remove()
    }
  }, [candles, chartView, market, showGrid, showVolume])

  return (
    <div className="market-chart-stage">
      <div
        className="market-chart-canvas"
        ref={chartHost}
        role="img"
        aria-label={chartDescription}
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
}
