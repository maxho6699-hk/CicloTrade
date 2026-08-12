import type { DrawingTime } from '../data/chartDrawings'

export interface SessionUser {
  id: number
  display_name: string
  plan: string
  plan_display_name: string
  subscription_expire: string | null
}

export type MembershipPlanKey = '免费版' | '标准版' | '高级版' | '专业版' | '定制版'
export type MembershipBillingCycle = 'monthly' | 'quarterly' | 'yearly' | 'project'
export type MembershipPurchaseAction = 'unavailable' | 'covered' | 'renew' | 'upgrade'

export interface MembershipOrder {
  order_no: string
  plan_type: MembershipPlanKey
  billing_cycle: MembershipBillingCycle
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
  can_purchase: boolean
  purchase_action: MembershipPurchaseAction
  can_submit_proof: boolean
  blocked_reason: string | null
}

export interface MembershipPlan {
  key: MembershipPlanKey
  display_name: string
  prices: Partial<Record<MembershipBillingCycle, number>>
  summary: string
  features: string[]
  can_purchase: boolean
  purchase_action: MembershipPurchaseAction
  blocked_reason: string | null
}

export interface PortfolioPosition {
  symbol: string
  market: 'US' | 'CN'
  currency: 'USD' | 'CNY'
  instrument_type: 'stock' | 'option'
  instrument_key: string
  option_expiry?: string | null
  option_right?: 'CALL' | 'PUT' | null
  option_strike?: number | null
  multiplier: number
  quantity: number
  average_price: number
  last_trade_price: number
  market_value: number
  unrealized_pnl: number
}

export interface PortfolioOrder {
  order_id: string
  symbol: string
  market: 'US' | 'CN'
  currency: 'USD' | 'CNY'
  instrument_type: 'stock' | 'option'
  side: 'BUY' | 'SELL'
  quantity: number
  price: number
  status: string
  account_mode: 'official'
  created_at: string
}

export interface PortfolioAccount {
  market: 'US' | 'CN' | 'HK'
  currency: 'USD' | 'CNY' | 'HKD'
  status: 'recorded' | 'not_recorded' | 'not_connected'
  captured_at: string | null
  initial_cash: number | null
  cash: number | null
  market_value: number | null
  realized_pnl: number | null
  unrealized_pnl: number | null
  total_equity: number | null
  total_pnl: number | null
}

export interface PortfolioExecution {
  execution_id: string
  trade_id: string
  order_id: string
  interval_id: string
  symbol: string
  market: 'US' | 'CN'
  currency: 'USD' | 'CNY'
  instrument_type: 'stock' | 'option'
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
  instrument_key: string
  instrument_type: 'stock' | 'option'
  symbol: string
  market: 'US' | 'CN'
  currency: 'USD' | 'CNY'
  option_expiry?: string | null
  option_right?: 'CALL' | 'PUT' | null
  option_strike?: number | null
  multiplier: number
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
  execution_counts_by_market?: Partial<Record<'US' | 'CN' | 'HK', number>>
  execution_previews_by_market?: Partial<Record<'US' | 'CN' | 'HK', PortfolioExecution[]>>
  returned_execution_limit: number
  truncated: boolean
}

export interface RecommendationItem {
  event_id: number
  state: 'official' | 'locked'
  action?: 'BUY' | 'REDUCE' | 'EXIT' | 'SHORT' | 'COVER'
  position_action?: 'open_long' | 'add_long' | 'reduce_long' | 'close_long' | 'open_short' | 'add_short' | 'reduce_short' | 'close_short' | 'reverse_to_long' | 'reverse_to_short'
  market?: string
  instrument_type: 'stock' | 'option'
  symbol?: string
  currency?: string
  reference_price?: number | null
  current_price?: number | null
  quantity_hint?: number | null
  quantity_delta?: number | null
  target_quantity?: number | null
  option_expiry?: string | null
  option_right?: 'CALL' | 'PUT' | null
  option_strike?: number | null
  multiplier?: number | null
  bid?: number | null
  ask?: number | null
  spread?: number | null
  implied_volatility?: number | null
  volume?: number | null
  open_interest?: number | null
  quote_at?: string | null
  actionable?: boolean
  contract_status?: 'complete' | 'incomplete'
  missing_fields?: string[]
  stop_price?: number | null
  target_price?: number | null
  max_loss?: number | null
  rationale?: string | null
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
  market?: 'US' | 'CN' | '美股' | 'A股'
  conditions?: unknown[]
  logic?: 'AND' | 'OR' | string
  trigger_mode?: 'at_or_above' | 'at_or_below' | 'crosses_above' | 'crosses_below' | string
  repeat_mode?: 'once' | 'repeat' | string
  expires_at?: string | null
  channels?: Array<'website' | 'telegram' | string>
  notify_only?: true
  metadata?: {
    trigger_mode: string
    repeat_mode: string
    expires_at: string | null
    channels: string[]
    notify_only: true
  }
  enabled?: boolean
  is_active?: boolean
  operator?: string
  target_price?: number | null
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
    annual_bonus_enabled?: boolean
    capabilities: string[]
    plans: MembershipPlan[]
    orders: MembershipOrder[]
    payment_methods: Record<'fps' | 'alipay' | 'wechat', { available: boolean; has_text: boolean; has_qr: boolean }>
    brokerage: {
      auto_control_account_limit: number
      accounts_used: number
      accounts: BrokerAccountSummary[]
      requires_user_authorization: true
      short_eligibility_source: 'broker'
      subscription_auto_connects_broker?: false
      us_short?: {
        requires_ciclotrade_manual_approval: false
        requires_broker_authorization: true
        requires_margin: true
        requires_borrowability: true
      }
    }
  }
  execution_control: ExecutionControl
  telegram: {
    bound: boolean
    verified: boolean
    consented: boolean
    chat_id_masked: string
    events: Record<string, boolean>
    updated_at?: string | null
  }
  portfolio: {
    account_mode: 'official'
    scope: 'ciclotrade_system_validation'
    positions: PortfolioPosition[]
    orders: PortfolioOrder[]
    accounts: Record<'US' | 'CN' | 'HK', PortfolioAccount>
    fresh_marks: false
    mark_source: string
    activity?: PortfolioActivity
  }
  recommendations: { items: RecommendationItem[]; source: string; fresh_marks: false }
  performance: { items: PerformanceSnapshot[]; fresh_marks: false; mark_source: string; scope?: 'system_model_validation' | string; user_id?: number }
  settings: {
    risk: Partial<RiskSettings>
    telegram_events: Record<string, boolean>
    watchlists: { us: string[]; a_share: string[] }
    watchlist_pins: { us: string[]; a_share: string[] }
    ui_locale: 'zh-Hant' | 'zh-Hans' | null
  }
  alerts: { items: PriceAlert[] }
  market_data: {
    display_source: string
    is_realtime: boolean
    freshness: string
    detail: string
  } & DeliveryVisibilityMetadata
  mode: 'compatibility'
}

export interface BrokerAccountSummary {
  id: number
  provider: string
  alias: string
  mode: string
  status: string
  authorized: boolean
  active: boolean
  last_checked: string | null
}

export interface ExecutionControl {
  global_opening_paused: boolean
  user_opening_paused: boolean
  effective_opening_paused: boolean
  auto_trading_service_enabled: boolean
  has_authorized_broker_account: boolean
  can_register_broker_account: boolean
  can_increase_exposure: boolean
  can_reduce_exposure: boolean
  account_limit: number
  accounts_used: number
  accounts: BrokerAccountSummary[]
  block_reasons: string[]
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
  status: BootstrapPayload['market_data'] & DeliveryVisibilityMetadata
}

/**
 * The API, rather than a data-provider entitlement, is authoritative for what
 * this account is allowed to see.  These fields are deliberately optional
 * while older API deployments are still rolling out.
 */
export interface DeliveryVisibilityMetadata {
  delivery_delay_minutes?: number
  visible_as_of?: string
  observed_at?: string
}

export interface MarketStatusPayload {
  status: 'available' | 'unavailable'
  upstream_connected: boolean
  provider_realtime: boolean
  configuration_allows_realtime: boolean
  equity_realtime_entitled: boolean
  option_realtime_entitled: boolean
  delivery_delay_minutes: number
  is_realtime: boolean
  visible_as_of: string
  observed_at: string
  refresh_after_seconds: number
}

export interface ChartDrawingPayload {
  id: string
  tool: string
  points: Array<{ time: DrawingTime; price: number }>
  origin_timeframe: string
  cross_timeframe: boolean
  revision: number
}

export interface ChartDrawingTombstonePayload {
  drawing_id: string
  origin_timeframe: string
  cross_timeframe: boolean
  revision: number
}

export type ChartDrawingOperation =
  | { op: 'upsert'; origin_timeframe: string; cross_timeframe: boolean; revision: number | null; drawing: Pick<ChartDrawingPayload, 'id' | 'tool' | 'points'> }
  | { op: 'delete' | 'restore'; origin_timeframe: string; cross_timeframe: boolean; revision: number; drawing_id: string }

export interface MarketQuotePayload extends DeliveryVisibilityMetadata {
  symbol: string
  last: number | null
  bid: number | null
  ask: number | null
  spread: number | null
  open: number | null
  high: number | null
  low: number | null
  prev_close: number | null
  volume: number | null
  quote_at: string | null
  source: string
  is_realtime: boolean
  actionable_quote: boolean
  freshness: string
  verification: string
  configuration_allows_realtime: boolean
  request_succeeded: boolean
  fallback_from?: string
  status: 'available'
}

export interface OptionGreeks {
  delta: number | null
  gamma: number | null
  theta: number | null
  vega: number | null
  rho: number | null
}

export interface OptionContract {
  expiry: string
  option_type: 'CALL' | 'PUT'
  contract_code: string
  strike: number
  last: number | null
  bid: number | null
  ask: number | null
  spread: number | null
  volume: number | null
  open_interest: number | null
  implied_volatility: number | null
  greeks: OptionGreeks
  quote_at: string | null
}

export interface OptionChainPayload extends DeliveryVisibilityMetadata {
  symbol: string
  expiry: string
  expiries: string[]
  calls: OptionContract[]
  puts: OptionContract[]
  items: OptionContract[]
  source: string
  is_realtime: boolean
  actionable_quote: boolean
  freshness: string
  verification: string
  configuration_allows_realtime: boolean
  qot_right: string
  missing_fields: string[]
  fallback_from?: string
  status: 'available'
}

export interface OptionCandlePayload extends DeliveryVisibilityMetadata {
  contract_code: string
  timeframe: string
  items: MarketCandlePayload['items']
  source: string
  is_realtime: boolean
  actionable_quote: boolean
  freshness: string
  verification: string
  configuration_allows_realtime: boolean
  qot_right: string
  missing_fields: string[]
  fallback_from?: string
  status: 'available'
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

export type SystemCycleResearchState = 'waiting' | 'healthy' | 'stale' | 'degraded'

export interface SystemCycleResearchStatus {
  available: boolean
  state: SystemCycleResearchState
  research_only: true
  actionable: false
  last_heartbeat_at: string | null
  last_result_at: string | null
  last_cycle_id: string | null
  stock_count: 0 | 13
  coverage_count: number
  no_data_count: number
  spool: { pending: number; claimed: number; retryable: number; delivered: number } | null
}

export interface SystemCycleResearchStock {
  market: 'US' | 'CN'
  symbol: string
  status: 'coverage' | 'no_data'
  rows: number
  dataset_end: string | null
  selected: boolean
  signal_state: 'long' | 'flat' | 'no_data'
  latest_price: number | null
  target_quantity: number
}

export interface SystemCycleResearchCycle {
  cycle_id: string
  evaluation_date: string
  cycle_slot: string
  strategy_key: string
  strategy_name: string
  strategy_version: string
  evaluated_at: string
  coverage_count: number
  no_data_count: number
  selected_symbols: string[]
  stocks: SystemCycleResearchStock[]
  evidence: {
    universe_sha256: string
    source_snapshot_sha256: string
    catalog_snapshot_sha256: string
    code_bundle_sha256: string
    result_sha256: string
  }
}

export interface SystemCycleResearchLatest {
  available: boolean
  research_only: true
  actionable: false
  validation_label: string
  cycle: SystemCycleResearchCycle | null
}

export interface SystemCycleResearchHistoryItem {
  cycle_id: string
  evaluation_date: string
  cycle_slot: string
  strategy_key: string
  strategy_name: string
  strategy_version: string
  evaluated_at: string
  received_at: string
  coverage_count: number
  no_data_count: number
  selected_count: number
}

export interface SystemCycleResearchHistory {
  available: boolean
  research_only: true
  actionable: false
  limit: 20
  items: SystemCycleResearchHistoryItem[]
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

export async function authenticatedJsonRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  return request<T>(path, init)
}

export async function login(email: string, password: string): Promise<SessionResponse> {
  const session = await request<SessionResponse>('/api/rewrite/v1/session', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
  accessToken = session.access_token
  return session
}

export interface RegistrationResponse {
  accepted: boolean
  verification_required: boolean
  message: string
}

export async function registerAccount(payload: {
  email: string
  password: string
  display_name: string
  terms_accepted: boolean
  referral?: string
}): Promise<RegistrationResponse> {
  return request<RegistrationResponse>('/api/rewrite/v1/session/register', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function requestEmailVerification(email: string): Promise<{ message?: string }> {
  return request<{ message?: string }>('/api/rewrite/v1/session/verification', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}

export async function verifyEmailToken(token: string): Promise<{ verified: boolean }> {
  return request<{ verified: boolean }>('/api/rewrite/v1/session/verify-email', {
    method: 'POST',
    body: JSON.stringify({ token }),
  })
}

export async function requestPasswordReset(email: string): Promise<{ message?: string }> {
  return request<{ message?: string }>('/api/rewrite/v1/session/password-reset', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}

export async function confirmPasswordReset(token: string, password: string): Promise<{ reset: boolean }> {
  return request<{ reset: boolean }>('/api/rewrite/v1/session/password-reset/confirm', {
    method: 'POST',
    body: JSON.stringify({ token, password }),
  })
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

export async function fetchSystemCycleResearchStatus(): Promise<SystemCycleResearchStatus> {
  const payload = await request<unknown>('/api/rewrite/v1/system-cycle-research/status')
  if (!validSystemCycleResearchStatus(payload)) throw new BrowserApiError('影子策略研究状态响应格式无效。', 502)
  return payload
}

export async function fetchSystemCycleResearchLatest(): Promise<SystemCycleResearchLatest> {
  const payload = await request<unknown>('/api/rewrite/v1/system-cycle-research/latest')
  if (!validSystemCycleResearchLatest(payload)) throw new BrowserApiError('影子策略研究最新周期响应格式无效。', 502)
  return payload
}

export async function fetchSystemCycleResearchHistory(): Promise<SystemCycleResearchHistory> {
  const payload = await request<unknown>('/api/rewrite/v1/system-cycle-research/history?limit=20')
  if (!validSystemCycleResearchHistory(payload)) throw new BrowserApiError('影子策略研究历史响应格式无效。', 502)
  return payload
}

function plainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function exactKeys(value: unknown, expected: readonly string[]): value is Record<string, unknown> {
  return plainObject(value)
    && Object.keys(value).length === expected.length
    && Object.keys(value).every((key) => expected.includes(key))
}

function finiteNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0
}

function finiteNonNegativeNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function validIsoDate(value: unknown): value is string {
  return typeof value === 'string'
    && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && Number.isFinite(Date.parse(`${value}T00:00:00Z`))
}

function validIsoTimestamp(value: unknown): value is string {
  return typeof value === 'string'
    && /^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && Number.isFinite(Date.parse(value))
}

function validSha256(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

function validText(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 256
}

function validStatusCounts(stockCount: number, coverageCount: unknown, noDataCount: unknown) {
  return finiteNonNegativeInteger(coverageCount)
    && finiteNonNegativeInteger(noDataCount)
    && coverageCount + noDataCount === stockCount
}

export function validSystemCycleResearchStatus(value: unknown): value is SystemCycleResearchStatus {
  if (!exactKeys(value, ['available', 'state', 'research_only', 'actionable', 'last_heartbeat_at', 'last_result_at', 'last_cycle_id', 'stock_count', 'coverage_count', 'no_data_count', 'spool'])) return false
  if (typeof value.available !== 'boolean') return false
  const expectedStockCount = value.available ? 13 : 0
  const spool = value.spool
  const validSpool = spool === null || (exactKeys(spool, ['pending', 'claimed', 'retryable', 'delivered'])
    && Object.values(spool).every(finiteNonNegativeInteger))
  return ['waiting', 'healthy', 'stale', 'degraded'].includes(value.state as string)
    && value.research_only === true
    && value.actionable === false
    && (value.last_heartbeat_at === null || validIsoTimestamp(value.last_heartbeat_at))
    && (value.last_result_at === null || validIsoTimestamp(value.last_result_at))
    && (value.last_cycle_id === null || validText(value.last_cycle_id))
    && value.stock_count === expectedStockCount
    && validStatusCounts(expectedStockCount, value.coverage_count, value.no_data_count)
    && validSpool
}

function validSystemCycleResearchStock(value: unknown): value is SystemCycleResearchStock {
  if (!exactKeys(value, ['market', 'symbol', 'status', 'rows', 'dataset_end', 'selected', 'signal_state', 'latest_price', 'target_quantity'])) return false
  const coverage = value.status === 'coverage'
  const noData = value.status === 'no_data'
  return (value.market === 'US' || value.market === 'CN')
    && validText(value.symbol)
    && (coverage || noData)
    && finiteNonNegativeInteger(value.rows)
    && typeof value.selected === 'boolean'
    && finiteNonNegativeNumber(value.target_quantity)
    && (coverage
      ? validIsoDate(value.dataset_end)
        && (value.signal_state === 'long' || value.signal_state === 'flat')
        && typeof value.latest_price === 'number' && Number.isFinite(value.latest_price) && value.latest_price > 0
        && value.selected === (value.signal_state === 'long' && value.target_quantity > 0)
        && (value.signal_state !== 'flat' || value.target_quantity === 0)
      : value.dataset_end === null
        && value.selected === false
        && value.signal_state === 'no_data'
        && value.latest_price === null
        && value.target_quantity === 0)
}

function validSystemCycleResearchCycle(value: unknown): value is SystemCycleResearchCycle {
  if (!exactKeys(value, ['cycle_id', 'evaluation_date', 'cycle_slot', 'strategy_key', 'strategy_name', 'strategy_version', 'evaluated_at', 'coverage_count', 'no_data_count', 'selected_symbols', 'stocks', 'evidence'])) return false
  const selectedSymbols = value.selected_symbols
  const stocks = value.stocks
  if (!validText(value.cycle_id) || !validIsoDate(value.evaluation_date) || !validText(value.cycle_slot)
    || !validText(value.strategy_key) || !validText(value.strategy_name) || !validText(value.strategy_version)
    || !validIsoTimestamp(value.evaluated_at) || !validStatusCounts(13, value.coverage_count, value.no_data_count)
    || !Array.isArray(selectedSymbols) || !selectedSymbols.every(validText)
    || new Set(selectedSymbols).size !== selectedSymbols.length
    || !Array.isArray(stocks) || stocks.length !== 13 || !stocks.every(validSystemCycleResearchStock)
    || new Set(stocks.map((stock) => `${stock.market}:${stock.symbol}`)).size !== 13) return false
  const evidence = value.evidence
  if (!exactKeys(evidence, ['universe_sha256', 'source_snapshot_sha256', 'catalog_snapshot_sha256', 'code_bundle_sha256', 'result_sha256'])
    || !Object.values(evidence).every(validSha256)) return false
  const selected = stocks.filter((stock) => stock.selected).map((stock) => stock.symbol)
  const coverageCount = stocks.filter((stock) => stock.status === 'coverage').length
  return coverageCount === value.coverage_count
    && 13 - coverageCount === value.no_data_count
    && selected.length === selectedSymbols.length
    && selected.every((symbol, index) => symbol === selectedSymbols[index])
}

export function validSystemCycleResearchLatest(value: unknown): value is SystemCycleResearchLatest {
  return exactKeys(value, ['available', 'research_only', 'actionable', 'validation_label', 'cycle'])
    && typeof value.available === 'boolean'
    && value.research_only === true
    && value.actionable === false
    && validText(value.validation_label)
    && (value.cycle === null || validSystemCycleResearchCycle(value.cycle))
}

function validSystemCycleResearchHistoryItem(value: unknown): value is SystemCycleResearchHistoryItem {
  return exactKeys(value, ['cycle_id', 'evaluation_date', 'cycle_slot', 'strategy_key', 'strategy_name', 'strategy_version', 'evaluated_at', 'received_at', 'coverage_count', 'no_data_count', 'selected_count'])
    && validText(value.cycle_id)
    && validIsoDate(value.evaluation_date)
    && validText(value.cycle_slot)
    && validText(value.strategy_key)
    && validText(value.strategy_name)
    && validText(value.strategy_version)
    && validIsoTimestamp(value.evaluated_at)
    && validIsoTimestamp(value.received_at)
    && validStatusCounts(13, value.coverage_count, value.no_data_count)
    && finiteNonNegativeInteger(value.selected_count)
    && Number(value.selected_count) <= Number(value.coverage_count)
}

export function validSystemCycleResearchHistory(value: unknown): value is SystemCycleResearchHistory {
  return exactKeys(value, ['available', 'research_only', 'actionable', 'limit', 'items'])
    && typeof value.available === 'boolean'
    && value.research_only === true
    && value.actionable === false
    && value.limit === 20
    && Array.isArray(value.items)
    && value.items.length <= 20
    && value.items.every(validSystemCycleResearchHistoryItem)
    && new Set(value.items.map((item) => item.cycle_id)).size === value.items.length
}

export async function fetchMarketStatus() {
  const payload = await request<unknown>('/api/rewrite/v1/market/status')
  if (!validMarketStatusPayload(payload)) throw new BrowserApiError('行情状态响应格式无效。', 502)
  return payload
}

export async function fetchMarketCandles(symbol: string, timeframe: string) {
  const payload = await request<MarketCandlePayload>(`/api/rewrite/v1/market/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`)
  const valid = Array.isArray(payload.items) && payload.items.every((item) => (
    (typeof item.time === 'string' || typeof item.time === 'number')
    && [item.open, item.high, item.low, item.close, item.volume].every(Number.isFinite)
  )) && validDeliveryVisibilityMetadata(payload.status)
  if (!valid) throw new BrowserApiError('行情响应格式无效。', 502)
  return payload
}

export async function fetchMarketQuote(symbol: string) {
  const payload = await request<MarketQuotePayload>(`/api/rewrite/v1/market/quote?symbol=${encodeURIComponent(symbol)}`)
  const valid = payload.status === 'available'
    && typeof payload.symbol === 'string'
    && typeof payload.source === 'string'
    && payload.source.length > 0
    && typeof payload.is_realtime === 'boolean'
    && typeof payload.actionable_quote === 'boolean'
    && typeof payload.freshness === 'string'
    && typeof payload.verification === 'string'
    && typeof payload.configuration_allows_realtime === 'boolean'
    && typeof payload.request_succeeded === 'boolean'
    && (payload.fallback_from === undefined || typeof payload.fallback_from === 'string')
    && [payload.last, payload.bid, payload.ask, payload.spread, payload.open, payload.high, payload.low, payload.prev_close, payload.volume]
      .every(nullableFinite)
    && (payload.quote_at === null || typeof payload.quote_at === 'string')
    && validDeliveryVisibilityMetadata(payload)
  if (!valid) throw new BrowserApiError('正股快照响应格式无效。', 502)
  return payload
}

function nullableFinite(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isFinite(value))
}

function validDeliveryVisibilityMetadata(value: unknown) {
  if (!value || typeof value !== 'object') return false
  const metadata = value as DeliveryVisibilityMetadata
  return (metadata.delivery_delay_minutes === undefined
      || (Number.isSafeInteger(metadata.delivery_delay_minutes) && metadata.delivery_delay_minutes >= 0))
    && (metadata.visible_as_of === undefined || typeof metadata.visible_as_of === 'string')
    && (metadata.observed_at === undefined || typeof metadata.observed_at === 'string')
}

export function validMarketStatusPayload(value: unknown): value is MarketStatusPayload {
  if (!value || typeof value !== 'object') return false
  const payload = value as Partial<MarketStatusPayload>
  const expectedFields = new Set([
    'status',
    'upstream_connected',
    'provider_realtime',
    'configuration_allows_realtime',
    'equity_realtime_entitled',
    'option_realtime_entitled',
    'delivery_delay_minutes',
    'is_realtime',
    'visible_as_of',
    'observed_at',
    'refresh_after_seconds',
  ])
  if (Object.keys(payload).length !== expectedFields.size || Object.keys(payload).some((key) => !expectedFields.has(key))) return false
  const booleans = [
    payload.upstream_connected,
    payload.provider_realtime,
    payload.configuration_allows_realtime,
    payload.equity_realtime_entitled,
    payload.option_realtime_entitled,
    payload.is_realtime,
  ]
  return (payload.status === 'available' || payload.status === 'unavailable')
    && booleans.every((item) => typeof item === 'boolean')
    && Number.isSafeInteger(payload.delivery_delay_minutes)
    && Number(payload.delivery_delay_minutes) >= 0
    && typeof payload.visible_as_of === 'string'
    && Number.isFinite(Date.parse(payload.visible_as_of))
    && typeof payload.observed_at === 'string'
    && Number.isFinite(Date.parse(payload.observed_at))
    && Number.isSafeInteger(payload.refresh_after_seconds)
    && Number(payload.refresh_after_seconds) > 0
}

function validOptionContract(value: unknown): value is OptionContract {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<OptionContract>
  const greeks = item.greeks as Partial<OptionGreeks> | undefined
  return typeof item.expiry === 'string'
    && (item.option_type === 'CALL' || item.option_type === 'PUT')
    && typeof item.contract_code === 'string'
    && typeof item.strike === 'number'
    && Number.isFinite(item.strike)
    && nullableFinite(item.last)
    && nullableFinite(item.bid)
    && nullableFinite(item.ask)
    && nullableFinite(item.spread)
    && nullableFinite(item.volume)
    && nullableFinite(item.open_interest)
    && nullableFinite(item.implied_volatility)
    && (item.quote_at === null || typeof item.quote_at === 'string')
    && Boolean(greeks)
    && nullableFinite(greeks?.delta)
    && nullableFinite(greeks?.gamma)
    && nullableFinite(greeks?.theta)
    && nullableFinite(greeks?.vega)
    && nullableFinite(greeks?.rho)
}

export async function fetchOptionChain(symbol: string, expiry?: string) {
  const params = new URLSearchParams({ symbol })
  if (expiry) params.set('expiry', expiry)
  const payload = await request<OptionChainPayload>(`/api/rewrite/v1/options/chain?${params}`)
  const arraysAreValid = [payload.calls, payload.puts, payload.items]
    .every((items) => Array.isArray(items) && items.every(validOptionContract))
  const metadataIsValid = typeof payload.source === 'string'
    && typeof payload.is_realtime === 'boolean'
    && typeof payload.actionable_quote === 'boolean'
    && typeof payload.freshness === 'string'
    && typeof payload.verification === 'string'
    && typeof payload.configuration_allows_realtime === 'boolean'
    && typeof payload.qot_right === 'string'
    && Array.isArray(payload.missing_fields)
    && payload.missing_fields.every((item) => typeof item === 'string')
    && (payload.fallback_from === undefined || typeof payload.fallback_from === 'string')
    && validDeliveryVisibilityMetadata(payload)
  if (!arraysAreValid || !metadataIsValid || !Array.isArray(payload.expiries) || !payload.expiries.every((item) => typeof item === 'string')) {
    throw new BrowserApiError('期权链响应格式无效。', 502)
  }
  return payload
}

export async function fetchOptionCandles(contractCode: string, timeframe: string) {
  const params = new URLSearchParams({ contract_code: contractCode, timeframe })
  const payload = await request<OptionCandlePayload>(`/api/rewrite/v1/options/candles?${params}`)
  const valid = Array.isArray(payload.items) && payload.items.every((item) => (
    (typeof item.time === 'string' || typeof item.time === 'number')
    && [item.open, item.high, item.low, item.close, item.volume].every(Number.isFinite)
  ))
  const metadataIsValid = typeof payload.source === 'string'
    && typeof payload.is_realtime === 'boolean'
    && typeof payload.actionable_quote === 'boolean'
    && typeof payload.freshness === 'string'
    && typeof payload.verification === 'string'
    && typeof payload.configuration_allows_realtime === 'boolean'
    && typeof payload.qot_right === 'string'
    && Array.isArray(payload.missing_fields)
    && payload.missing_fields.every((item) => typeof item === 'string')
    && (payload.fallback_from === undefined || typeof payload.fallback_from === 'string')
    && validDeliveryVisibilityMetadata(payload)
  if (!valid || !metadataIsValid) throw new BrowserApiError('期权 K 线响应格式无效。', 502)
  return payload
}

export async function resumeOpeningPause(password: string, confirmation: string) {
  return request<{ execution_control: ExecutionControl; resumed: boolean }>(
    '/api/rewrite/v1/settings/opening-pause',
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paused: false, confirmation, password }),
    },
  )
}

export function getChartDrawings(market: 'US' | 'CN', symbol: string, timeframe: string, crossTimeframe: boolean) {
  const params = new URLSearchParams({ market, symbol, timeframe, cross_timeframe: String(crossTimeframe) })
  return request<{
    items: ChartDrawingPayload[]
    truncated: boolean
    tombstones: ChartDrawingTombstonePayload[]
    tombstones_truncated: boolean
  }>(`/api/rewrite/v1/chart-drawings?${params}`)
}

export function syncChartDrawings(scope: { market: 'US' | 'CN'; symbol: string }, operations: ChartDrawingOperation[]) {
  return request<{ items: Array<{ drawing_id: string; origin_timeframe: string; cross_timeframe: boolean; revision: number; deleted: boolean }> }>('/api/rewrite/v1/chart-drawings/batch', {
    method: 'POST',
    body: JSON.stringify({ market: scope.market, symbol: scope.symbol, operations }),
  })
}

export function searchMarket(query: string, market: '美股' | 'A股' | '全部') {
  return request<{ items: MarketSearchItem[] }>(`/api/rewrite/v1/market/search?q=${encodeURIComponent(query)}&market=${encodeURIComponent(market)}`)
}

export interface WatchlistPayload {
  watchlists: { us: string[]; a_share: string[] }
  pins: { us: string[]; a_share: string[] }
}

export function updateWatchlist(market: 'US' | 'CN', symbol: string, remove = false) {
  return request<WatchlistPayload>('/api/rewrite/v1/watchlist', {
    method: remove ? 'DELETE' : 'POST',
    body: JSON.stringify({ market, symbol }),
  })
}

export function updateWatchlistPin(market: 'US' | 'CN', symbol: string, pinned: boolean) {
  return request<WatchlistPayload>('/api/rewrite/v1/watchlist', {
    method: 'PATCH',
    body: JSON.stringify({ market, symbol, pinned }),
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

export interface PriceAlertOptions {
  market?: 'US' | 'CN'
  triggerMode?: 'at_or_above' | 'at_or_below' | 'crosses_above' | 'crosses_below'
  repeatMode?: 'once' | 'repeat'
  expiresAt?: string | null
  channels?: Array<'website' | 'telegram'>
}

export function createPriceAlert(symbol: string, value: number, options: PriceAlertOptions = {}) {
  return request<{ items: PriceAlert[] }>('/api/rewrite/v1/alerts', {
    method: 'POST',
    body: JSON.stringify({
      symbol,
      ...(options.market ? { market: options.market } : {}),
      conditions: [{ type: 'price', operator: options.triggerMode === 'at_or_below' || options.triggerMode === 'crosses_below' ? '<=' : '>=', value }],
      logic: 'AND',
      ...(options.triggerMode ? { trigger_mode: options.triggerMode } : {}),
      ...(options.repeatMode ? { repeat_mode: options.repeatMode } : {}),
      ...(options.expiresAt !== undefined ? { expires_at: options.expiresAt } : {}),
      ...(options.channels ? { channels: options.channels } : {}),
      notify_only: true,
    }),
  })
}

export function createAlert(payload: {
  symbol: string
  conditions?: Array<Record<string, unknown>>
  logic?: 'AND' | 'OR'
  trigger_mode?: string
  repeat_mode?: 'once' | 'repeat'
  expires_at?: string | null
  channels?: Array<'website' | 'telegram'>
}) {
  return request<{ items: PriceAlert[] }>('/api/rewrite/v1/alerts', {
    method: 'POST',
    body: JSON.stringify({ ...payload, notify_only: true }),
  })
}

export function deactivatePriceAlert(alertId: number) {
  return request<{ items: PriceAlert[] }>(`/api/rewrite/v1/alerts/${encodeURIComponent(alertId)}`, {
    method: 'DELETE',
  })
}

export function createMembershipOrder(
  payload: { plan: MembershipPlanKey; cycle: MembershipBillingCycle; method: string; terms_accepted: boolean },
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
