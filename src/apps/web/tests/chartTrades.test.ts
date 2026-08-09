import assert from 'node:assert/strict'
import test from 'node:test'
import { buildChartTradeView } from '../src/data/chartTrades.ts'

const candles = [
  { time: '2026-08-01', open: 99, high: 102, low: 98, close: 101, volume: 10 },
  { time: '2026-08-02', open: 101, high: 112, low: 100, close: 110, volume: 12 },
  { time: '2026-08-03', open: 110, high: 121, low: 109, close: 120, volume: 14 },
]

const interval = {
  interval_id: 'AAPL-LONG-1', symbol: 'AAPL', market: 'US' as const, currency: 'USD' as const,
  direction: 'LONG' as const, opened_at: '2026-08-01T10:00:00Z', closed_at: '2026-08-03T10:00:00Z',
  average_entry_price: 100, average_exit_price: 120, average_cost: 0, opened_quantity: 1,
  closed_quantity: 1, current_quantity: 0, entry_notional: 100, net_cash: 20, commission: 0, mark_price: 120,
  realized_pnl: 20, realized_return_pct: 20, estimated_pnl: null, estimated_return_pct: null,
  status: 'CLOSED' as const, result: 'profit' as const, execution_ids: ['buy', 'sell'],
}

const activity = {
  pnl_method: 'weighted_average' as const, pnl_net_of_commission: true as const,
  returned_execution_limit: 500, truncated: false,
  intervals: [interval],
  executions: [
    { execution_id: 'buy', trade_id: 't1', order_id: 'o1', interval_id: interval.interval_id,
      symbol: 'AAPL', market: 'US' as const, currency: 'USD' as const, side: 'BUY' as const,
      effect: 'OPEN' as const, quantity: 1, price: 100, commission: 0,
      executed_at: '2026-08-01T10:00:00Z', position_after: 1 },
    { execution_id: 'sell', trade_id: 't2', order_id: 'o2', interval_id: interval.interval_id,
      symbol: 'AAPL', market: 'US' as const, currency: 'USD' as const, side: 'SELL' as const,
      effect: 'CLOSE' as const, quantity: 1, price: 120, commission: 0,
      executed_at: '2026-08-03T10:00:00Z', position_after: 0 },
    { execution_id: 'outside', trade_id: 't3', order_id: 'o3', interval_id: 'old',
      symbol: 'AAPL', market: 'US' as const, currency: 'USD' as const, side: 'BUY' as const,
      effect: 'OPEN' as const, quantity: 1, price: 80, commission: 0,
      executed_at: '2025-01-01T10:00:00Z', position_after: 1 },
  ],
}

test('snaps visible executions and builds one complete interval', () => {
  const view = buildChartTradeView(candles, activity, 'AAPL')
  assert.deepEqual(view.executions.map((item) => item.chartTime), ['2026-08-01', '2026-08-03'])
  assert.equal(view.intervals.length, 1)
  assert.equal(view.intervals[0].startTime, '2026-08-01')
  assert.equal(view.intervals[0].endTime, '2026-08-03')
  assert.equal(view.hiddenExecutionCount, 1)
})

test('does not draw a closed interval when its opening execution is outside the chart', () => {
  const view = buildChartTradeView(candles.slice(1), activity, 'AAPL')
  assert.equal(view.intervals.length, 0)
  assert.deepEqual(view.executions.map((item) => item.execution_id), ['sell'])
})
