export interface SessionUser {
  id: number
  display_name: string
  plan: string
  plan_display_name: string
  subscription_expire: string | null
}

export interface MembershipOrder {
  order_no: string
  plan_type: string
  billing_cycle: string
  amount: number
  currency: string
  status: string
  created_at: string
  paid_at: string | null
  refunded_at: string | null
  expires_at: string | null
  pay_method: 'fps' | 'alipay' | 'wechat' | 'paypal' | 'paddle'
  proof_status: 'submitted' | 'approved' | 'rejected' | null
  payment_instructions?: string
  payment_qr_available?: boolean
}

export interface MembershipPlan {
  key: string
  display_name: string
  prices: Record<string, number>
  summary: string
  features: string[]
}

export interface PortfolioPosition {
  symbol: string
  quantity: number
  average_price: number
  last_trade_price: number
  market_value: number
  unrealized_pnl: number
}

export interface PortfolioOrder {
  order_id: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  price: number
  status: string
  account_mode: 'paper'
  created_at: string
}

export interface PortfolioExecution {
  execution_id: string
  trade_id: string
  order_id: string
  interval_id: string
  symbol: string
  market: 'US' | 'CN'
  currency: 'USD' | 'CNY'
  side: 'BUY' | 'SELL'
  effect: 'OPEN' | 'ADD' | 'REDUCE' | 'CLOSE'
  quantity: number
  price: number
  commission: number
  executed_at: string
  position_after: number
}

export interface PortfolioInterval {
  interval_id: string
  symbol: string
  market: 'US' | 'CN'
  currency: 'USD' | 'CNY'
  direction: 'LONG' | 'SHORT'
  opened_at: string
  closed_at: string | null
  average_entry_price: number
  average_exit_price: number | null
  average_cost: number
  opened_quantity: number
  closed_quantity: number
  current_quantity: number
  entry_notional: number
  net_cash: number
  commission: number
  mark_price: number | null
  realized_pnl: number | null
  realized_return_pct: number | null
  estimated_pnl: number | null
  estimated_return_pct: number | null
  status: 'OPEN' | 'CLOSED'
  result: 'profit' | 'loss' | 'breakeven' | 'open'
  execution_ids: string[]
}

export interface PortfolioActivity {
  pnl_method: 'weighted_average'
  pnl_net_of_commission: true
  executions: PortfolioExecution[]
  intervals: PortfolioInterval[]
  returned_execution_limit: number
  truncated: boolean
}

export interface RecommendationItem {
  event_id: number
  state: 'official' | 'locked'
  action?: 'BUY' | 'REDUCE' | 'EXIT'
  market?: string
  instrument_type: 'stock' | 'option'
  symbol?: string
  currency?: string
  reference_price?: number | null
  strategy_name: string
  strategy_version: string
  occurred_at: string
  recorded_at?: string
}

export interface PerformanceSnapshot {
  id: number
  captured_at: string
  currency: string
  initial_cash: number
  cash: number
  market_value: number
  realized_pnl: number
  unrealized_pnl: number
  total_equity: number
  total_pnl: number
  recorded_at: string
}

export interface PriceAlert {
  id?: number
  symbol: string
  conditions?: unknown[]
  enabled?: boolean
  created_at?: string
}

export interface RiskSettings {
  max_position_per_symbol: number
  max_total_position: number
  max_daily_loss: number
  max_position_per_symbol_cny: number
  max_total_position_cny: number
  max_daily_loss_cny: number
  cooldown_minutes: number
  consecutive_loss_limit: number
}

export interface BootstrapPayload {
  me: SessionUser
  membership: {
    auto_renewal: false
    capabilities: string[]
    plans: MembershipPlan[]
    orders: MembershipOrder[]
    payment_methods: Record<'fps' | 'alipay' | 'wechat', { available: boolean; has_text: boolean; has_qr: boolean }>
  }
  telegram: {
    bound: boolean
    verified: boolean
    consented: boolean
    chat_id_masked: string
    events: Record<string, boolean>
    updated_at?: string | null
  }
  portfolio: {
    account_mode: 'paper'
    positions: PortfolioPosition[]
    orders: PortfolioOrder[]
    realized_pnl: number
    fresh_marks: false
    mark_source: string
    activity?: PortfolioActivity
  }
  recommendations: { items: RecommendationItem[]; source: string; fresh_marks: false }
  performance: { items: PerformanceSnapshot[]; fresh_marks: false; mark_source: string }
  settings: {
    risk: Partial<RiskSettings>
    telegram_events: Record<string, boolean>
    watchlists: { us: string[]; a_share: string[] }
    ui_locale: 'zh-Hant' | 'zh-Hans' | null
  }
  alerts: { items: PriceAlert[] }
  market_data: {
    display_source: string
    is_realtime: boolean
    freshness: string
    detail: string
  }
  mode: 'compatibility'
}

export interface MarketSearchItem {
  symbol: string
  name: string
  exchange: string
  type: string
  market: 'US' | 'CN'
}

export interface MarketCandlePayload {
  symbol: string
  timeframe: string
  items: Array<{ time: string | number; open: number; high: number; low: number; close: number; volume: number }>
  status: BootstrapPayload['market_data']
}

interface SessionResponse {
  access_token: string
  user: SessionUser
  new_ip: boolean
}

let accessToken: string | null = null
let restorePromise: Promise<boolean> | null = null

export class BrowserApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  const payload = await response.json().catch(() => ({})) as { error?: string }
  if (!response.ok) throw new BrowserApiError(payload.error ?? '服务暂时不可用。', response.status)
  return payload as T
}

export async function login(email: string, password: string): Promise<SessionResponse> {
  const session = await request<SessionResponse>('/api/rewrite/v1/session', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
  accessToken = session.access_token
  return session
}

export async function restoreSession(): Promise<boolean> {
  if (restorePromise) return restorePromise
  restorePromise = (async () => {
    try {
      const session = await request<{ authenticated: boolean; access_token?: string }>('/api/rewrite/v1/session/refresh', { method: 'POST' })
      accessToken = session.access_token ?? null
      return session.authenticated && Boolean(session.access_token)
    } catch (error) {
      accessToken = null
      if (error instanceof BrowserApiError && error.status === 401) return false
      throw error
    }
  })()
  try {
    return await restorePromise
  } finally {
    restorePromise = null
  }
}

export function fetchBootstrap(): Promise<BootstrapPayload> {
  return request<BootstrapPayload>('/api/rewrite/v1/bootstrap')
}

export async function fetchMarketCandles(symbol: string, timeframe: string) {
  const payload = await request<MarketCandlePayload>(`/api/rewrite/v1/market/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`)
  const valid = Array.isArray(payload.items) && payload.items.every((item) => (
    (typeof item.time === 'string' || typeof item.time === 'number')
    && [item.open, item.high, item.low, item.close, item.volume].every(Number.isFinite)
  ))
  if (!valid) throw new BrowserApiError('行情响应格式无效。', 502)
  return payload
}

export function searchMarket(query: string, market: '美股' | 'A股' | '全部') {
  return request<{ items: MarketSearchItem[] }>(`/api/rewrite/v1/market/search?q=${encodeURIComponent(query)}&market=${encodeURIComponent(market)}`)
}

export function updateWatchlist(market: 'US' | 'CN', symbol: string, remove = false) {
  return request<{ watchlists: { us: string[]; a_share: string[] } }>('/api/rewrite/v1/watchlist', {
    method: remove ? 'DELETE' : 'POST',
    body: JSON.stringify({ market, symbol }),
  })
}

export function saveLocale(locale: 'zh-Hant' | 'zh-Hans') {
  return request<{ locale: 'zh-Hant' | 'zh-Hans' }>('/api/rewrite/v1/settings/locale', {
    method: 'PUT',
    body: JSON.stringify({ locale }),
  })
}

export async function logout(): Promise<void> {
  try {
    await request('/api/rewrite/v1/session', { method: 'DELETE' })
  } finally {
    accessToken = null
  }
}

export function saveRiskSettings(risk: Record<string, number>) {
  return request<{ risk: Record<string, number> }>('/api/rewrite/v1/settings/risk', {
    method: 'PUT',
    body: JSON.stringify(risk),
  })
}

export function saveTelegramEvents(events: Record<string, boolean>) {
  return request<{ events: Record<string, boolean> }>('/api/rewrite/v1/settings/telegram', {
    method: 'PUT',
    body: JSON.stringify(events),
  })
}

export function createPriceAlert(symbol: string, value: number) {
  return request<{ items: PriceAlert[] }>('/api/rewrite/v1/alerts', {
    method: 'POST',
    body: JSON.stringify({ symbol, conditions: [{ type: 'price', operator: '>=', value }], logic: 'AND' }),
  })
}

export function createPaperOrder(payload: { symbol: string; side: 'BUY' | 'SELL'; quantity: number; price: number }) {
  return request<{ order_id: string; status: string }>('/api/rewrite/v1/paper/orders', {
    method: 'POST',
    body: JSON.stringify({ ...payload, instrument_type: 'stock' }),
  })
}

export function createMembershipOrder(
  payload: { plan: string; cycle: string; method: string; terms_accepted: boolean },
  idempotencyKey: string,
) {
  return request<{ order_no: string; status: string; amount: number; currency: string; payment_instructions: string; payment_qr_available: boolean }>(
    '/api/rewrite/v1/membership/orders',
    {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    },
  )
}

export async function fetchMembershipPaymentQr(orderNo: string): Promise<Blob> {
  const headers = new Headers()
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  const response = await fetch(
    `/api/rewrite/v1/membership/orders/${encodeURIComponent(orderNo)}/payment-qr`,
    { headers, credentials: 'same-origin' },
  )
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { error?: string }
    throw new BrowserApiError(payload.error ?? '收款二维码暂时不可用。', response.status)
  }
  const contentType = response.headers.get('content-type')?.split(';', 1)[0]
  const blob = await response.blob()
  if (contentType !== 'image/jpeg' || blob.size < 1 || blob.size > 4 * 1024 * 1024) {
    throw new BrowserApiError('收款二维码响应无效。', 502)
  }
  return blob
}

export function submitMembershipProof(orderNo: string, file: File) {
  const body = new FormData()
  body.append('proof', file)
  return request<{ claim_id: number; order_no: string; status: string; attempt: number; created_at: string }>(
    `/api/rewrite/v1/membership/orders/${encodeURIComponent(orderNo)}/proof`,
    { method: 'POST', body },
  )
}
