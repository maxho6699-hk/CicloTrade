import type { DeliveryRecord, ModelReport, OrderRecord, Position } from '../types'

export const portfolioSummary = {
  equity: 42860.42,
  cash: 17640.18,
  marketValue: 25220.24,
  dailyPnl: 382.16,
  totalPnl: 2860.42,
  buyingPower: 17640.18,
  currency: 'USD',
  freshness: '14:32:18 TPE',
}

export const positions: Position[] = [
  { symbol: 'AAPL', name: 'Apple', market: 'US', quantity: 60, averagePrice: 198.4, lastPrice: 213.45, marketValue: 12807, unrealizedPnl: 903, unrealizedPnlPct: 7.59, action: 'hold' },
  { symbol: 'MSFT', name: 'Microsoft', market: 'US', quantity: 18, averagePrice: 430.1, lastPrice: 451.32, marketValue: 8123.76, unrealizedPnl: 381.96, unrealizedPnlPct: 4.93, action: 'hold' },
  { symbol: 'TSLA', name: 'Tesla', market: 'US', quantity: 10, averagePrice: 332.7, lastPrice: 318.41, marketValue: 3184.1, unrealizedPnl: -142.9, unrealizedPnlPct: -4.3, action: 'reduce' },
  { symbol: '600519', name: '贵州茅台', market: 'CN', quantity: 10, averagePrice: 1390, lastPrice: 1428.6, marketValue: 14286, unrealizedPnl: 386, unrealizedPnlPct: 2.78, action: 'hold' },
]

export const orders: OrderRecord[] = [
  { id: 'PAPER-0809-A14', symbol: 'AAPL', side: 'BUY', quantity: 20, price: 211.2, status: 'FILLED', mode: 'paper', createdAt: '今天 14:18' },
  { id: 'PAPER-0808-T09', symbol: 'TSLA', side: 'SELL', quantity: 5, price: 321.8, status: 'FILLED', mode: 'paper', createdAt: '昨天 15:42' },
  { id: 'PAPER-0808-N03', symbol: 'NVDA', side: 'BUY', quantity: 10, price: 178.4, status: 'REJECTED', mode: 'paper', createdAt: '昨天 11:06' },
]

export const deliveryRecords: DeliveryRecord[] = [
  { id: 'TG-8942', event: 'AAPL 正式买入建议', channel: 'Telegram', status: 'sent', deliveredAt: '14:32:21' },
  { id: 'TG-8941', event: 'TSLA 风险升高提醒', channel: 'Telegram', status: 'sent', deliveredAt: '13:48:06' },
  { id: 'TG-8940', event: '模拟订单成交', channel: 'Telegram', status: 'sent', deliveredAt: '11:16:42' },
  { id: 'TG-8939', event: '会员到期预告', channel: 'Telegram', status: 'pending', deliveredAt: '等待发送' },
]

export const modelReports: ModelReport[] = [
  { name: '正股稳健推荐', version: 'stability-v3', state: 'active', sampleSize: 684, winRate: 64.8, maxDrawdown: 12.4, stressExpectancy: 1.18, stability: 84 },
  { name: '正股挑战模型', version: 'challenger-v7', state: 'shadow', sampleSize: 148, winRate: 66.2, maxDrawdown: 14.1, stressExpectancy: 1.09, stability: 78 },
  { name: '期权价差模型', version: 'options-v2', state: 'shadow', sampleSize: 96, winRate: 58.4, maxDrawdown: 18.7, stressExpectancy: 0.92, stability: 69 },
]

export const riskSettings = {
  max_position_per_symbol: 5_000,
  max_total_position: 50_000,
  max_daily_loss: 2_000,
  max_position_per_symbol_cny: 35_000,
  max_total_position_cny: 350_000,
  max_daily_loss_cny: 14_000,
  cooldown_minutes: 30,
  consecutive_loss_limit: 3,
}

export const reportReturns = [4, 7, 5, 10, 8, 12, 15, 13, 19, 23, 21, 27, 31, 29, 36, 40]
