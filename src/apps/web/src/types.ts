export type Market = 'US' | 'CN'

export type RecommendationState =
  | 'official'
  | 'research'
  | 'wait'
  | 'blocked'

export type ActionKind = 'buy' | 'hold' | 'reduce' | 'exit' | 'short' | 'cover' | 'wait'

export interface Instrument {
  symbol: string
  name: string
  market: Market
  price: number
  changePct: number
  currency: 'USD' | 'CNY'
}

export interface Decision {
  state: RecommendationState
  action: ActionKind
  instrument: Instrument
  title: string
  summary: string
  entry: string
  stop: string
  target: string
  maxLoss: string
  horizon: string
  confidence: string
  evidence: string[]
  counterEvidence: string[]
  eventId: string
  officialEventId?: number
  modelVersion: string
  updatedAt: string
  actionable: boolean
  currentInstruction: string
  currentPrice: string
  quantityHint: string
  quoteUpdatedAt: string
  quoteFreshness: 'fresh' | 'stale' | 'missing'
  actionBlockReason?: string
  plainLanguage?: {
    reason: string
    setup: string
    rebound?: string
    reboundSince?: string
    takeProfit?: string
    quantityHint: string
  }
}

export interface Candle {
  time: string | number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export type DeliveryState = 'sent' | 'pending' | 'failed' | 'locked'

export interface Position {
  symbol: string
  name: string
  market: Market
  instrumentType?: 'stock' | 'option'
  quantity: number
  averagePrice: number
  lastPrice: number
  marketValue: number
  unrealizedPnl: number
  unrealizedPnlPct: number
  action: ActionKind
}

export interface OrderRecord {
  id: string
  symbol: string
  market?: Market
  instrumentType?: 'stock' | 'option'
  side: 'BUY' | 'SELL'
  quantity: number
  price: number
  status: 'FILLED' | 'PENDING' | 'REJECTED'
  mode: 'paper' | 'official' | 'live'
  createdAt: string
}

export interface DeliveryRecord {
  id: string
  event: string
  channel: 'Telegram'
  status: DeliveryState
  deliveredAt: string
}

export interface ModelReport {
  name: string
  version: string
  state: 'active' | 'shadow' | 'blocked'
  sampleSize: number
  winRate: number
  maxDrawdown: number
  stressExpectancy: number
  stability: number
}
