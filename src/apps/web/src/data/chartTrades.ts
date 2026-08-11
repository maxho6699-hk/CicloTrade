import type { PortfolioActivity, PortfolioExecution, PortfolioInterval } from '../api/client'
import type { Candle } from '../types'

export interface SnappedExecution extends PortfolioExecution {
  chartTime: Candle['time']
}

export interface ChartTradeInterval {
  interval: PortfolioInterval
  executions: SnappedExecution[]
  startTime: Candle['time']
  endTime: Candle['time']
}

export interface ChartTradeView {
  executions: SnappedExecution[]
  intervals: ChartTradeInterval[]
  hiddenExecutionCount: number
}

function epochSeconds(value: Candle['time'] | string): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed / 1000 : null
}

function nearestCandleTime(candles: Candle[], value: string): Candle['time'] | null {
  const target = epochSeconds(value)
  const points = candles
    .map((candle) => ({ time: candle.time, epoch: epochSeconds(candle.time) }))
    .filter((point): point is { time: Candle['time']; epoch: number } => point.epoch !== null)
    .sort((left, right) => left.epoch - right.epoch)
  if (target === null || !points.length) return null
  const gaps = points.slice(1).map((point, index) => point.epoch - points[index].epoch).filter((gap) => gap > 0)
  const interval = gaps.length ? gaps.sort((left, right) => left - right)[Math.floor(gaps.length / 2)] : 86_400
  const tolerance = Math.max(60, interval * 0.75)
  if (target < points[0].epoch || target > points.at(-1)!.epoch + tolerance) return null

  let closest = points[0]
  for (const point of points.slice(1)) {
    if (Math.abs(point.epoch - target) < Math.abs(closest.epoch - target)) closest = point
  }
  return Math.abs(closest.epoch - target) <= tolerance ? closest.time : null
}

export function buildChartTradeView(
  candles: Candle[],
  activity: PortfolioActivity | null | undefined,
  market: 'US' | 'CN',
  symbol: string,
): ChartTradeView {
  if (!candles.length || !activity) return { executions: [], intervals: [], hiddenExecutionCount: 0 }
  const symbolExecutions = activity.executions.filter(
    (execution) => execution.market === market && execution.symbol === symbol,
  )
  const executions = symbolExecutions.flatMap((execution) => {
    const chartTime = nearestCandleTime(candles, execution.executed_at)
    return chartTime === null ? [] : [{ ...execution, chartTime }]
  })
  const byId = new Map(executions.map((execution) => [execution.execution_id, execution]))
  const intervals = activity.intervals.flatMap((interval) => {
    if (interval.market !== market || interval.symbol !== symbol) return []
    const visible = interval.execution_ids.flatMap((id) => byId.get(id) ?? [])
    const opened = visible.find((execution) => execution.effect === 'OPEN')
    const closed = visible.findLast((execution) => execution.effect === 'CLOSE')
    if (!opened || (interval.status === 'CLOSED' && !closed)) return []
    return [{
      interval,
      executions: visible,
      startTime: opened.chartTime,
      endTime: interval.status === 'CLOSED' ? closed!.chartTime : candles.at(-1)!.time,
    }]
  })
  return {
    executions,
    intervals,
    hiddenExecutionCount: symbolExecutions.length - executions.length,
  }
}
