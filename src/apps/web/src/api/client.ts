import type { DrawingTime } from '../data/chartDrawings'
import { decodeFeatureCatalog, type FeatureCatalogPayload } from '../domain/featureCatalog.ts'
import {
  decodeStockScreenerPayload,
  decodeStockScreenerPreset,
  decodeStockScreenerRequest,
  type StockScreenerPayload,
  type StockScreenerPreset,
  type StockScreenerRequest,
} from '../domain/stockScreener.ts'

export interface SessionUser {
  id: number
  display_name: string
  plan: string
  plan_display_name: string
  subscription_expire: string | null
  /** Omitted or null for every account that is not explicitly a super admin. */
  admin_role: 'super_admin' | null
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
  lifecycle: 'active_public'
}

export interface LegacyMembershipPlan {
  key: Extract<MembershipPlanKey, '专业版' | '定制版'>
  display_name: string
  lifecycle: 'retired_legacy'
  summary: string
  can_purchase: false
  can_renew: false
}

export interface MembershipQuote {
  plan: MembershipPlanKey
  cycle: MembershipBillingCycle
  currency: 'HKD'
  list_price_minor: number
  coupon_discount_minor: number
  referral_discount_minor: number
  final_amount_minor: number
  coupon_code: string | null
  referral_eligible: boolean
  discount_order: ['coupon', 'referral']
  server_reprices_on_order: true
}

export type BrokerCatalogStatus =
  | 'market_data_only'
  | 'limited_backend_capability'
  | 'integration_in_progress'

export interface BrokerCatalogEntry {
  key: 'futu_moomoo' | 'tiger' | 'ibkr' | 'webull' | 'longbridge'
  display_name: string
  status: BrokerCatalogStatus
  status_label: string
  availability_detail: string
  capabilities: Array<'market_data' | 'us_stock_limit_orders'>
  connection_available: false
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
  available_at?: string
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
    legacy_plans: LegacyMembershipPlan[]
    policy: { key: string | null; version: number | null; sha256: string | null }
    orders: MembershipOrder[]
    payment_methods: Record<'fps' | 'alipay' | 'wechat', { available: boolean; has_text: boolean; has_qr: boolean }>
    brokerage: {
      auto_control_account_limit: number
      accounts_used: number
      accounts: BrokerAccountSummary[]
      capability_catalog: BrokerCatalogEntry[]
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
  recommendations: {
    items: RecommendationItem[]
    source: string
    fresh_marks: false
    delivery: { stock: number; option: number }
  }
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

export type PersonalPaperSide = 'BUY' | 'SELL' | 'SHORT' | 'COVER'
export type PersonalPaperOrderType = 'MARKET' | 'LIMIT' | 'STOP' | 'STOP_LIMIT'
export type PersonalPaperQuoteState = 'fresh' | 'delayed' | 'stale' | 'missing'
export type PersonalPaperRiskDecision = 'allow' | 'review' | 'reject'
export type PersonalPaperRiskLevel = 'low' | 'moderate' | 'high' | 'blocked'
export type PersonalPaperRiskDataState = 'fresh' | 'partial' | 'stale' | 'missing'
export type PersonalPaperRiskCheckStatus = 'pass' | 'warn' | 'fail' | 'unknown'
export type PersonalPaperRiskCheckCode =
  | 'buying_power'
  | 'max_loss'
  | 'position_concentration'
  | 'sector_concentration'
  | 'drawdown'
  | 'event_gap'
  | 'liquidity'

export interface PersonalPaperSeason {
  id: string
  state: 'active' | 'closed'
  currency: 'USD'
  initial_cash: 10000
  started_at: string
  closed_at: string | null
  version: number
}

export interface PersonalPaperPosition {
  market: 'US'
  symbol: string
  quantity: number
}

export interface PersonalPaperAccount {
  season: PersonalPaperSeason
  cash: number
  reserved_cash: number
  buying_power: number
  market_value: number
  realized_pnl: number
  unrealized_pnl: number
  total_equity: number
  as_of: string
  quote_state: PersonalPaperQuoteState
  account_version: number
  positions: PersonalPaperPosition[]
  open_orders: PersonalPaperOrder[]
  recent_orders: PersonalPaperOrder[]
}

export interface PersonalPaperQuoteProof {
  quote_id: string
  market: 'US'
  symbol: string
  bid_minor: number
  ask_minor: number
  last_minor: number
  quote_at: string
  observed_at: string
  available_at: string
  expires_at: string
  session: string | null
  freshness: 'fresh'
  source: string
}

export interface PersonalPaperRiskCheck {
  code: PersonalPaperRiskCheckCode
  status: PersonalPaperRiskCheckStatus
  title: string
  detail: string
  value: string | null
  limit: string | null
  data_state: PersonalPaperRiskDataState
}

export interface PersonalPaperRiskProof {
  id: string
  schema_version: 'r1'
  season_id: string
  quote_id: string
  account_version: number
  draft_sha256: string
  proof_sha256: string
  created_at: string
  computed_at: string
  marks_as_of: string
  expires_at: string
  decision: PersonalPaperRiskDecision
  risk_level: PersonalPaperRiskLevel
  data_state: PersonalPaperRiskDataState
  checks: PersonalPaperRiskCheck[]
  blocking_reasons: string[]
  warnings: string[]
}

export interface PersonalPaperRiskProofRequest {
  season_id: string
  market: 'US'
  symbol: string
  side: PersonalPaperSide
  order_type: PersonalPaperOrderType
  quantity: number
  limit_price: number | null
  stop_price: number | null
  time_in_force: 'DAY'
  quote_id: string
  account_version: number
  source_context: {
    kind: 'manual' | 'recommendation' | 'chart' | 'screener'
    reference_id: string | null
  }
}

export interface PersonalPaperOrder {
  id: string
  season_id: string
  market: 'US'
  symbol: string
  side: PersonalPaperSide
  order_type: PersonalPaperOrderType
  quantity: number
  status: 'PENDING' | 'FILLED' | 'CANCELLED'
  created_at: string
  quote_id: string
  account_version: number
  cancel_eligible: boolean
  cancel_account_version: number | null
}

export interface PersonalPaperOrderRequest {
  idempotency_key: string
  season_id: string
  market: 'US'
  symbol: string
  side: PersonalPaperSide
  order_type: PersonalPaperOrderType
  quantity: number
  limit_price: number | null
  stop_price: number | null
  time_in_force: 'DAY'
  quote_id: string
  risk_proof_id: string
  account_version: number
  source_context: {
    kind: 'manual' | 'recommendation' | 'chart' | 'screener'
    reference_id: string | null
  }
}

export interface PersonalPaperOrderResult {
  order: PersonalPaperOrder
  account: PersonalPaperAccount
  replayed: boolean
}

export const FEATURE_CATALOG_UPDATED_EVENT = 'ciclotrade:feature-catalog-updated'

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
  missing_fields: string[]
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
  missing_fields: string[]
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

/**
 * Opens an authenticated, same-origin response without consuming its body.
 * Streaming callers own the response body and must close it when unmounted.
 */
export async function authenticatedStreamRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'text/event-stream')
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { error?: string }
    throw new BrowserApiError(payload.error ?? '服务暂时不可用。', response.status)
  }
  return response
}

export interface AdminOverview {
  users?: number
  active_users?: number
  subscribers?: number
  pending_orders?: number
  paid_amount?: number
  critical_risk?: number
  [key: string]: unknown
}

export interface AdminUser {
  id: number
  email: string
  display_name: string
  plan_type?: string
  subscription_expire?: string | null
  last_login?: string | null
  is_active?: boolean
  admin_role?: string | null
  active_sessions?: number
  [key: string]: unknown
}

export interface AdminManualClaim {
  id: number
  order_no: string
  status: 'submitted' | 'approved' | 'rejected' | string
  user_email?: string
  amount?: number
  currency?: string
  pay_method?: string
  created_at?: string
  settlement_reference_masked?: string | null
  [key: string]: unknown
}

export type AdminReferralWithdrawalStatus = 'submitted' | 'approved' | 'rejected' | 'paid' | 'system_cancelled'

export interface AdminReferralWithdrawalReceipt {
  withdrawal_id: string
  amount_minor: number
  currency: 'HKD'
  status: AdminReferralWithdrawalStatus
  submitted_at: string
  reviewed_at: string | null
  approved_at: string | null
  paid_at: string | null
  rejection_reason: string | null
}

export interface AdminReferralWithdrawal extends AdminReferralWithdrawalReceipt {
  user_reference: string
  user_masked: string
}

export type AdminReferralBonusTier = { qualified_count: number; cumulative_amount_minor: number }
export type AdminReferralPolicyValue = {
  commission_rate_bps: number
  referral_discount_bps: number
  minimum_final_amount_minor: number
  commission_cap_minor: number
  hold_days: number
  withdrawal_min_minor: number
  withdrawal_max_minor: number
  withdrawal_daily_limit: number
  withdrawal_monthly_limit: number
  withdrawal_open_limit: number
  withdrawal_cooldown_days: number
  automatic_payout_review_threshold_minor: number
  withdrawal_paused: boolean
  bonus_enabled: boolean
  bonus_tiers: AdminReferralBonusTier[]
}

export interface AdminReferralPolicy { version: number; policy: AdminReferralPolicyValue }

export interface AdminReferralCoupon {
  coupon_id: string
  code: string
  campaign_name: string
  discount_type: 'percent' | 'fixed_hkd'
  discount_value: number
  max_discount_minor: number | null
  min_spend_minor: number
  total_use_limit: number
  per_user_limit: number
  applicable_plans: Array<'标准版' | '高级版'>
  applicable_cycles: Array<'monthly' | 'quarterly' | 'yearly'>
  starts_at: string
  expires_at: string
  enabled: boolean
  version: number
  created_at: string
  updated_at: string
}

export type AdminReferralPromotionType = 'coupon_only' | 'referral_only' | 'stacked' | 'none'
export interface AdminReferralAnalyticsItem {
  coupon_code: string | null
  campaign: string | null
  customer: string
  order_id: string
  status: 'pending' | 'paid' | 'refunded' | 'cancelled' | 'failed'
  list_price_minor: number
  coupon_discount_minor: number
  referral_discount_minor: number
  paid_revenue_minor: number
  refund_or_chargeback_minor: number
  net_revenue_minor: number
  discount_cost_minor: number
  created_at: string
  paid_at: string | null
  refunded_at: string | null
  promotion_type: AdminReferralPromotionType
  commission_cost_minor: number
  bonus_cost_minor: number
  promotion_cost_minor: number
}

export interface AdminReferralAnalytics {
  items: AdminReferralAnalyticsItem[]
  summary: {
    orders: number; list_price_minor: number; coupon_cost_minor: number; referral_cost_minor: number
    paid_revenue_minor: number; refund_or_chargeback_minor: number; net_revenue_minor: number
    customers: number; coupon_only_orders: number; referral_only_orders: number; stacked_orders: number
    unattributed_orders: number; commission_cost_minor: number; bonus_cost_minor: number
    promotion_cost_minor: number
  }
}

export interface AdminBrokerAccount {
  id?: string | number
  broker?: string
  account_masked?: string
  status?: string
  updated_at?: string
  [key: string]: unknown
}

export interface AdminAuditEntry {
  id?: string | number
  action_type?: string
  created_at?: string
  actor_id?: number | null
  actor_display?: string
  details?: string | Record<string, unknown>
  [key: string]: unknown
}

export interface AdminComputeEvidenceStatus {
  available: boolean
  publication_ceiling: 'shadow'
  research_only: true
  actionable: false
  user_visible: false
  counts: { quarantine: number; shadow: number }
  last_received_at: string | null
}

export interface AdminComputeEvidenceItem {
  publication_state: 'quarantine' | 'shadow'
  received_at: string
  completed_at: string
  candidate_id: string
  candidate_version: string
  market: string
  instrument_family: 'equity'
  symbols: string[]
  candidate_status: string
  manifest_sha256: string
  result_sha256: string
  package_sha256: string
  artifact_count: number
  research_only: true
  actionable: false
  user_visible: false
}

export interface AdminComputeEvidenceLatest {
  available: boolean
  publication_ceiling: 'shadow'
  research_only: true
  actionable: false
  user_visible: false
  evidence: AdminComputeEvidenceItem | null
}

export interface AdminComputeEvidenceHistory {
  available: boolean
  publication_ceiling: 'shadow'
  research_only: true
  actionable: false
  user_visible: false
  limit: number
  items: AdminComputeEvidenceItem[]
}

function adminItems<T>(payload: unknown): T[] {
  return Array.isArray(payload) ? payload as T[] : Array.isArray((payload as { items?: unknown })?.items) ? (payload as { items: T[] }).items : []
}

export function validAdminReferralWithdrawalReceipt(value: unknown): value is AdminReferralWithdrawalReceipt {
  return exactKeys(value, [
    'withdrawal_id', 'amount_minor', 'currency', 'status', 'submitted_at',
    'reviewed_at', 'approved_at', 'paid_at', 'rejection_reason',
  ])
    && typeof value.withdrawal_id === 'string'
    && /^WDR[A-Z0-9]{20,40}$/.test(value.withdrawal_id)
    && finiteNonNegativeInteger(value.amount_minor)
    && value.currency === 'HKD'
    && ['submitted', 'approved', 'rejected', 'paid', 'system_cancelled'].includes(String(value.status))
    && validIsoTimestamp(value.submitted_at)
    && nullableTimestamp(value.reviewed_at)
    && nullableTimestamp(value.approved_at)
    && nullableTimestamp(value.paid_at)
    && (value.rejection_reason === null || typeof value.rejection_reason === 'string')
}

export function validAdminReferralWithdrawal(value: unknown): value is AdminReferralWithdrawal {
  if (!exactKeys(value, [
    'withdrawal_id', 'user_reference', 'user_masked', 'amount_minor', 'currency',
    'status', 'submitted_at', 'reviewed_at', 'approved_at', 'paid_at', 'rejection_reason',
  ])) return false
  const receipt = {
    withdrawal_id: value.withdrawal_id,
    amount_minor: value.amount_minor,
    currency: value.currency,
    status: value.status,
    submitted_at: value.submitted_at,
    reviewed_at: value.reviewed_at,
    approved_at: value.approved_at,
    paid_at: value.paid_at,
    rejection_reason: value.rejection_reason,
  }
  return validAdminReferralWithdrawalReceipt(receipt)
    && typeof value.user_reference === 'string'
    && /^USR[A-Z0-9]{20,40}$/.test(value.user_reference)
    && typeof value.user_masked === 'string'
    && value.user_masked.length >= 1
    && value.user_masked.length <= 254
}

function validAdminReferralBonusTier(value: unknown): value is AdminReferralBonusTier {
  return exactKeys(value, ['qualified_count', 'cumulative_amount_minor'])
    && finiteNonNegativeInteger(value.qualified_count)
    && finiteNonNegativeInteger(value.cumulative_amount_minor)
    && value.qualified_count > 0
    && value.cumulative_amount_minor > 0
}

export function validAdminReferralPolicy(value: unknown): value is AdminReferralPolicy {
  if (!exactKeys(value, ['version', 'policy']) || !finiteNonNegativeInteger(value.version) || value.version < 1 || !exactKeys(value.policy, [
    'commission_rate_bps', 'referral_discount_bps', 'minimum_final_amount_minor', 'commission_cap_minor',
    'hold_days', 'withdrawal_min_minor', 'withdrawal_max_minor', 'withdrawal_daily_limit',
    'withdrawal_monthly_limit', 'withdrawal_open_limit', 'withdrawal_cooldown_days',
    'automatic_payout_review_threshold_minor', 'withdrawal_paused', 'bonus_enabled', 'bonus_tiers',
  ])) return false
  const policy = value.policy as AdminReferralPolicyValue
  const numeric = [
    policy.commission_rate_bps, policy.referral_discount_bps, policy.minimum_final_amount_minor,
    policy.commission_cap_minor, policy.hold_days, policy.withdrawal_min_minor, policy.withdrawal_max_minor,
    policy.withdrawal_daily_limit, policy.withdrawal_monthly_limit, policy.withdrawal_open_limit,
    policy.withdrawal_cooldown_days, policy.automatic_payout_review_threshold_minor,
  ]
  return numeric.every(finiteNonNegativeInteger)
    && policy.commission_rate_bps === 1_000
    && policy.referral_discount_bps === 500
    && [policy.minimum_final_amount_minor, policy.commission_cap_minor, policy.withdrawal_min_minor,
      policy.withdrawal_max_minor, policy.automatic_payout_review_threshold_minor]
      .every((amount) => amount >= 1 && amount <= 100_000_000)
    && policy.hold_days <= 365
    && [policy.withdrawal_daily_limit, policy.withdrawal_monthly_limit, policy.withdrawal_open_limit]
      .every((limit) => limit >= 1 && limit <= 100_000)
    && policy.withdrawal_cooldown_days <= 3_650
    && policy.withdrawal_max_minor >= policy.withdrawal_min_minor
    && typeof policy.withdrawal_paused === 'boolean'
    && typeof policy.bonus_enabled === 'boolean'
    && Array.isArray(policy.bonus_tiers)
    && policy.bonus_tiers.length >= 1
    && policy.bonus_tiers.length <= 10
    && policy.bonus_tiers.every(validAdminReferralBonusTier)
    && policy.bonus_tiers.every((tier, index, tiers) => index === 0 || (
      tier.qualified_count > tiers[index - 1].qualified_count
      && tier.cumulative_amount_minor > tiers[index - 1].cumulative_amount_minor
    ))
}

export function validAdminReferralCoupon(value: unknown): value is AdminReferralCoupon {
  const coupon = value as AdminReferralCoupon
  return exactKeys(value, [
    'coupon_id', 'code', 'campaign_name', 'discount_type', 'discount_value', 'max_discount_minor',
    'min_spend_minor', 'total_use_limit', 'per_user_limit', 'applicable_plans', 'applicable_cycles',
    'starts_at', 'expires_at', 'enabled', 'version', 'created_at', 'updated_at',
  ])
    && typeof value.coupon_id === 'string' && /^CPN[A-Z0-9]{20,40}$/.test(value.coupon_id)
    && typeof value.code === 'string' && /^[A-Z0-9_-]{3,64}$/.test(value.code)
    && typeof value.campaign_name === 'string' && value.campaign_name.length >= 1 && value.campaign_name.length <= 120
    && ['percent', 'fixed_hkd'].includes(String(value.discount_type))
    && finiteNonNegativeInteger(value.discount_value) && value.discount_value > 0
    && (coupon.discount_type === 'percent' ? coupon.discount_value <= 1_500 : coupon.discount_value <= 100_000)
    && (value.max_discount_minor === null || (finiteNonNegativeInteger(value.max_discount_minor) && value.max_discount_minor >= 1 && value.max_discount_minor <= 100_000))
    && [value.min_spend_minor, value.total_use_limit, value.per_user_limit, value.version].every(finiteNonNegativeInteger)
    && coupon.total_use_limit > 0 && coupon.per_user_limit > 0 && coupon.version > 0
    && Array.isArray(value.applicable_plans) && value.applicable_plans.length > 0
    && value.applicable_plans.every((plan) => plan === '标准版' || plan === '高级版')
    && Array.isArray(value.applicable_cycles) && value.applicable_cycles.length > 0
    && value.applicable_cycles.every((cycle) => ['monthly', 'quarterly', 'yearly'].includes(cycle))
    && validIsoTimestamp(value.starts_at) && validIsoTimestamp(value.expires_at)
    && Date.parse(value.expires_at) > Date.parse(value.starts_at)
    && typeof value.enabled === 'boolean'
    && validIsoTimestamp(value.created_at) && validIsoTimestamp(value.updated_at)
}

export function validAdminReferralAnalytics(value: unknown): value is AdminReferralAnalytics {
  if (!exactKeys(value, ['items', 'summary']) || !Array.isArray(value.items) || !exactKeys(value.summary, [
    'orders', 'list_price_minor', 'coupon_cost_minor', 'referral_cost_minor', 'paid_revenue_minor',
    'refund_or_chargeback_minor', 'net_revenue_minor', 'customers', 'coupon_only_orders',
    'referral_only_orders', 'stacked_orders', 'unattributed_orders', 'commission_cost_minor',
    'bonus_cost_minor', 'promotion_cost_minor',
  ])) return false
  const validItem = (item: unknown): item is AdminReferralAnalyticsItem => {
    const row = item as AdminReferralAnalyticsItem
    return exactKeys(item, [
    'coupon_code', 'campaign', 'customer', 'order_id', 'status', 'list_price_minor', 'coupon_discount_minor',
    'referral_discount_minor', 'paid_revenue_minor', 'refund_or_chargeback_minor', 'net_revenue_minor',
    'discount_cost_minor', 'created_at', 'paid_at', 'refunded_at', 'promotion_type', 'commission_cost_minor',
    'bonus_cost_minor', 'promotion_cost_minor',
  ])
    && (item.coupon_code === null || (typeof item.coupon_code === 'string' && /^[A-Z0-9_-]{3,64}$/.test(item.coupon_code)))
    && (item.campaign === null || (typeof item.campaign === 'string' && item.campaign.length >= 1 && item.campaign.length <= 120))
    && typeof item.customer === 'string' && /^USR[A-Z0-9]{20,40}$/.test(item.customer)
    && typeof item.order_id === 'string' && item.order_id.length >= 1 && item.order_id.length <= 128
    && ['pending', 'paid', 'refunded', 'cancelled', 'failed'].includes(String(item.status))
    && [item.list_price_minor, item.coupon_discount_minor, item.referral_discount_minor, item.paid_revenue_minor,
      item.refund_or_chargeback_minor, item.net_revenue_minor, item.discount_cost_minor, item.commission_cost_minor,
      item.bonus_cost_minor, item.promotion_cost_minor].every(finiteNonNegativeInteger)
    && row.discount_cost_minor === row.coupon_discount_minor + row.referral_discount_minor
    && row.net_revenue_minor === row.paid_revenue_minor - row.refund_or_chargeback_minor
    && row.promotion_cost_minor === row.discount_cost_minor + row.commission_cost_minor + row.bonus_cost_minor
    && validIsoTimestamp(item.created_at) && nullableTimestamp(item.paid_at) && nullableTimestamp(item.refunded_at)
    && ['coupon_only', 'referral_only', 'stacked', 'none'].includes(String(item.promotion_type))
  }
  return value.items.length <= 500 && value.items.every(validItem) && Object.values(value.summary).every(finiteNonNegativeInteger)
}

export async function fetchAdminOverview(): Promise<AdminOverview> {
  return request<AdminOverview>('/api/rewrite/v1/admin/overview')
}

export async function fetchAdminUsers(): Promise<AdminUser[]> {
  return adminItems<AdminUser>(await request<unknown>('/api/rewrite/v1/admin/users'))
}

export async function fetchAdminManualClaims(): Promise<AdminManualClaim[]> {
  return adminItems<AdminManualClaim>(await request<unknown>('/api/rewrite/v1/admin/payments/manual-claims'))
}

export async function fetchAdminReferralWithdrawals(
  status: AdminReferralWithdrawalStatus | 'all' = 'all',
): Promise<AdminReferralWithdrawal[]> {
  const payload = await request<unknown>(`/api/rewrite/v1/admin/referrals/withdrawals?status=${encodeURIComponent(status)}`)
  const items = adminItems<unknown>(payload)
  if (!items.every(validAdminReferralWithdrawal)) throw new BrowserApiError('推广提现队列响应格式无效。', 502)
  return items
}

export async function fetchAdminReferralPolicy(): Promise<AdminReferralPolicy> {
  const value = await request<unknown>('/api/rewrite/v1/admin/referrals/policy')
  if (!validAdminReferralPolicy(value)) throw new BrowserApiError('推广政策响应格式无效。', 502)
  return value
}

export async function updateAdminReferralPolicy(
  expectedVersion: number, policy: AdminReferralPolicyValue, password: string, idempotencyKey: string,
): Promise<AdminReferralPolicy> {
  const value = await request<unknown>('/api/rewrite/v1/admin/referrals/policy', {
    method: 'PUT', headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ expected_version: expectedVersion, policy, password }),
  })
  if (!validAdminReferralPolicy(value)) throw new BrowserApiError('推广政策保存回执格式无效。', 502)
  return value
}

export async function fetchAdminReferralCoupons(): Promise<AdminReferralCoupon[]> {
  const items = adminItems<unknown>(await request<unknown>('/api/rewrite/v1/admin/referrals/coupons'))
  if (!items.every(validAdminReferralCoupon)) throw new BrowserApiError('优惠码列表响应格式无效。', 502)
  return items
}

export async function createAdminReferralCoupon(
  coupon: Omit<AdminReferralCoupon, 'coupon_id' | 'version' | 'created_at' | 'updated_at'>, password: string, idempotencyKey: string,
): Promise<AdminReferralCoupon> {
  const value = await request<unknown>('/api/rewrite/v1/admin/referrals/coupons', {
    method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify({ coupon, password }),
  })
  if (!validAdminReferralCoupon(value)) throw new BrowserApiError('优惠码创建回执格式无效。', 502)
  return value
}

export async function pauseAdminReferralCoupon(
  couponId: string, expectedVersion: number, password: string, idempotencyKey: string,
): Promise<AdminReferralCoupon> {
  const value = await request<unknown>(`/api/rewrite/v1/admin/referrals/coupons/${encodeURIComponent(couponId)}/pause`, {
    method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify({ expected_version: expectedVersion, password }),
  })
  if (!validAdminReferralCoupon(value)) throw new BrowserApiError('优惠码暂停回执格式无效。', 502)
  return value
}

export type AdminReferralAnalyticsFilter = Partial<{
  coupon_code: string; campaign: string; status: AdminReferralAnalyticsItem['status']
  started_at: string; ended_at: string; promotion_type: AdminReferralPromotionType | 'all'
}>

export async function fetchAdminReferralAnalytics(filter: AdminReferralAnalyticsFilter = {}): Promise<AdminReferralAnalytics> {
  const query = new URLSearchParams({ promotion_type: filter.promotion_type ?? 'all' })
  for (const [key, value] of Object.entries(filter)) if (value && key !== 'promotion_type') query.set(key, value)
  const value = await request<unknown>(`/api/rewrite/v1/admin/referrals/analytics?${query.toString()}`)
  if (!validAdminReferralAnalytics(value)) throw new BrowserApiError('推广归因仪表盘响应格式无效。', 502)
  return value
}

export async function fetchAdminBrokers(): Promise<AdminBrokerAccount[]> {
  return adminItems<AdminBrokerAccount>(await request<unknown>('/api/rewrite/v1/admin/brokers'))
}

export async function fetchAdminAudit(): Promise<AdminAuditEntry[]> {
  return adminItems<AdminAuditEntry>(await request<unknown>('/api/rewrite/v1/admin/audit'))
}

export async function fetchAdminComputeEvidenceStatus(): Promise<AdminComputeEvidenceStatus> {
  const payload = await request<unknown>('/api/rewrite/v1/admin/compute-evidence/status')
  if (!validAdminComputeEvidenceStatus(payload)) throw new BrowserApiError('策略研究收据状态响应格式无效。', 502)
  return payload
}

export async function fetchAdminComputeEvidenceLatest(): Promise<AdminComputeEvidenceLatest> {
  const payload = await request<unknown>('/api/rewrite/v1/admin/compute-evidence/latest')
  if (!validAdminComputeEvidenceLatest(payload)) throw new BrowserApiError('策略研究最新收据响应格式无效。', 502)
  return payload
}

export async function fetchAdminComputeEvidenceHistory(limit = 20): Promise<AdminComputeEvidenceHistory> {
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new BrowserApiError('研究收据数量必须介于 1 与 100。', 400)
  const payload = await request<unknown>(`/api/rewrite/v1/admin/compute-evidence/history?limit=${limit}`)
  if (!validAdminComputeEvidenceHistory(payload, limit)) throw new BrowserApiError('策略研究收据历史响应格式无效。', 502)
  return payload
}

export async function reviewAdminManualClaim(id: number, payload: {
  decision: 'approve' | 'reject'
  password: string
  settlement_reference?: string
  rejection_reason?: string
}): Promise<AdminManualClaim> {
  return request<AdminManualClaim>(`/api/rewrite/v1/admin/payments/manual-claims/${id}/review`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function reviewAdminReferralWithdrawal(
  withdrawalId: string,
  payload: { decision: 'approve' | 'reject'; password: string; reason?: string },
  idempotencyKey: string,
): Promise<AdminReferralWithdrawalReceipt> {
  const value = await request<unknown>(
    `/api/rewrite/v1/admin/referrals/withdrawals/${encodeURIComponent(withdrawalId)}/review`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    },
  )
  if (!validAdminReferralWithdrawalReceipt(value)) throw new BrowserApiError('推广提现审核回执格式无效。', 502)
  return value
}

export async function confirmAdminReferralWithdrawalPaid(
  withdrawalId: string,
  payload: { password: string; payout_method: 'fps' | 'bank' | 'other'; payout_reference: string },
  idempotencyKey: string,
): Promise<AdminReferralWithdrawalReceipt> {
  const value = await request<unknown>(
    `/api/rewrite/v1/admin/referrals/withdrawals/${encodeURIComponent(withdrawalId)}/paid`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    },
  )
  if (!validAdminReferralWithdrawalReceipt(value)) throw new BrowserApiError('推广提现付款回执格式无效。', 502)
  return value
}

export async function updateAdminUserAutoTrading(payload: {
  enabled: boolean
  confirmation: '暂停用户实盘服务' | '恢复用户实盘服务'
  password: string
}): Promise<{ enabled?: boolean; affected_users?: number }> {
  return request<{ enabled?: boolean; affected_users?: number }>('/api/rewrite/v1/admin/user-auto-trading', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export type FeedbackCategory = 'bug' | 'suggestion' | 'data' | 'experience' | 'other'

export interface FeedbackReceipt {
  id: string
  category: FeedbackCategory
  status: string
  created_at: string
  summary: string
}

function validFeedbackReceipt(value: unknown): value is FeedbackReceipt {
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return typeof item.id === 'string' && typeof item.category === 'string' && typeof item.status === 'string'
    && typeof item.created_at === 'string' && typeof item.summary === 'string'
}

export async function fetchFeedback(): Promise<FeedbackReceipt[]> {
  const payload = await request<unknown>('/api/rewrite/v1/feedback')
  const items = Array.isArray(payload) ? payload : (payload as { items?: unknown })?.items
  if (!Array.isArray(items) || !items.every(validFeedbackReceipt)) throw new BrowserApiError('反馈回执响应格式无效。', 502)
  return items
}

export async function submitFeedback(payload: {
  category: FeedbackCategory
  context_path: string
  message: string
  contact_preference: 'none' | 'telegram' | 'email'
}, idempotencyKey: string): Promise<FeedbackReceipt> {
  const response = await request<unknown>('/api/rewrite/v1/feedback', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(payload),
  })
  const item = (response as { item?: unknown }).item ?? response
  if (!validFeedbackReceipt(item)) throw new BrowserApiError('反馈回执响应格式无效。', 502)
  return item
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

function decodeFeatureCatalogResponse(value: unknown): FeatureCatalogPayload {
  try {
    return decodeFeatureCatalog(value)
  } catch {
    throw new BrowserApiError('功能目录响应格式无效。', 502)
  }
}

export async function fetchFeatureCatalog(): Promise<FeatureCatalogPayload> {
  return decodeFeatureCatalogResponse(await request<unknown>('/api/rewrite/v1/features/catalog'))
}

export async function saveFeatureCatalogPreferences(payload: {
  expected_version: number
  pinned: string[]
  recent: string[]
}): Promise<FeatureCatalogPayload> {
  const decoded = decodeFeatureCatalogResponse(await request<unknown>('/api/rewrite/v1/features/preferences', {
    method: 'PUT',
    body: JSON.stringify(payload),
  }))
  window.dispatchEvent(new CustomEvent(FEATURE_CATALOG_UPDATED_EVENT, { detail: decoded }))
  return decoded
}

export async function recordRecentFeature(key: string, expectedVersion: number): Promise<FeatureCatalogPayload> {
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(key) || !finiteNonNegativeInteger(expectedVersion)) {
    throw new BrowserApiError('功能目录请求无效。', 400)
  }
  const decoded = decodeFeatureCatalogResponse(await request<unknown>('/api/rewrite/v1/features/recent', {
    method: 'PUT',
    body: JSON.stringify({ key, expected_version: expectedVersion }),
  }))
  window.dispatchEvent(new CustomEvent(FEATURE_CATALOG_UPDATED_EVENT, { detail: decoded }))
  return decoded
}

export async function fetchStockScreener(payload: StockScreenerRequest): Promise<StockScreenerPayload> {
  const normalized = decodeStockScreenerRequest(payload)
  if (!normalized) throw new BrowserApiError('选股请求无效。', 400)
  const decoded = decodeStockScreenerPayload(await request<unknown>('/api/rewrite/v1/stock-screener/query', {
    method: 'POST', body: JSON.stringify(normalized),
  }))
  if (!decoded) throw new BrowserApiError('选股响应格式无效。', 502)
  return decoded
}

export async function fetchStockScreenerPreset(): Promise<StockScreenerPreset | null> {
  const payload = await request<unknown>('/api/rewrite/v1/stock-screener/preset')
  if (payload === null) return null
  const decoded = decodeStockScreenerPreset(payload)
  if (!decoded) throw new BrowserApiError('选股预设响应格式无效。', 502)
  return decoded
}

export async function saveStockScreenerPreset(payload: StockScreenerPreset): Promise<StockScreenerPreset> {
  const normalized = decodeStockScreenerPreset(payload)
  if (!normalized) throw new BrowserApiError('选股预设无效。', 400)
  const decoded = decodeStockScreenerPreset(await request<unknown>('/api/rewrite/v1/stock-screener/preset', {
    method: 'PUT', body: JSON.stringify(normalized),
  }))
  if (!decoded) throw new BrowserApiError('选股预设响应格式无效。', 502)
  return decoded
}

export async function createPersonalPaperSeason(): Promise<PersonalPaperSeason> {
  const payload = await request<unknown>('/api/rewrite/v1/personal-paper/seasons', { method: 'POST' })
  if (!exactKeys(payload, ['season']) || !validPersonalPaperSeason(payload.season)) {
    throw new BrowserApiError('个人模拟赛季响应格式无效。', 502)
  }
  return payload.season
}

export async function fetchPersonalPaperAccount(seasonId: string): Promise<PersonalPaperAccount> {
  if (!validOpaqueId(seasonId)) throw new BrowserApiError('个人模拟赛季无效。', 400)
  const payload = await request<unknown>(`/api/rewrite/v1/personal-paper/seasons/${encodeURIComponent(seasonId)}`)
  if (!exactKeys(payload, ['account']) || !validPersonalPaperAccount(payload.account)) {
    throw new BrowserApiError('个人模拟账户响应格式无效。', 502)
  }
  return payload.account
}

export async function issuePersonalPaperQuote(symbol: string): Promise<PersonalPaperQuoteProof> {
  const normalized = symbol.trim().toUpperCase()
  if (!/^[A-Z][A-Z0-9.-]{0,15}$/.test(normalized)) throw new BrowserApiError('请输入有效的美股代码。', 400)
  const payload = await request<unknown>('/api/rewrite/v1/personal-paper/quotes', {
    method: 'POST',
    body: JSON.stringify({ market: 'US', symbol: normalized }),
  })
  if (!validPersonalPaperQuoteProof(payload) || payload.symbol !== normalized) {
    throw new BrowserApiError('个人模拟报价响应格式无效。', 502)
  }
  return payload
}

export async function issuePersonalPaperRiskProof(requestPayload: PersonalPaperRiskProofRequest): Promise<PersonalPaperRiskProof> {
  if (!validPersonalPaperRiskProofRequest(requestPayload)) throw new BrowserApiError('个人模拟风险证明请求无效。', 400)
  const payload = await request<unknown>('/api/rewrite/v1/personal-paper/risk-proofs', {
    method: 'POST',
    body: JSON.stringify(requestPayload),
  })
  if (!exactKeys(payload, ['risk_proof'])
    || !validPersonalPaperRiskProof(payload.risk_proof)
    || payload.risk_proof.season_id !== requestPayload.season_id
    || payload.risk_proof.quote_id !== requestPayload.quote_id
    || payload.risk_proof.account_version !== requestPayload.account_version) {
    throw new BrowserApiError('个人模拟风险证明响应格式无效。', 502)
  }
  return payload.risk_proof
}

export async function submitPersonalPaperStockOrder(payload: PersonalPaperOrderRequest): Promise<PersonalPaperOrderResult> {
  const result = await request<unknown>('/api/rewrite/v1/personal-paper/orders', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!validPersonalPaperOrderResult(result)) throw new BrowserApiError('个人模拟订单响应格式无效。', 502)
  return result
}

export async function cancelPersonalPaperStockOrder(payload: {
  season_id: string
  order_id: string
  account_version: number
}): Promise<PersonalPaperOrderResult> {
  const result = await request<unknown>('/api/rewrite/v1/personal-paper/orders/cancel', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!validPersonalPaperOrderResult(result)) throw new BrowserApiError('个人模拟撤单响应格式无效。', 502)
  return result
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

function nullableTimestamp(value: unknown): value is string | null {
  return value === null || validIsoTimestamp(value)
}

function validFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function validOpaqueId(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)
}

export function validPersonalPaperSeason(value: unknown): value is PersonalPaperSeason {
  return exactKeys(value, ['id', 'state', 'currency', 'initial_cash', 'started_at', 'closed_at', 'version'])
    && validOpaqueId(value.id)
    && (value.state === 'active' || value.state === 'closed')
    && value.currency === 'USD'
    && value.initial_cash === 10000
    && validIsoTimestamp(value.started_at)
    && nullableTimestamp(value.closed_at)
    && finiteNonNegativeInteger(value.version)
}

export function validPersonalPaperAccount(value: unknown): value is PersonalPaperAccount {
  if (!exactKeys(value, [
    'season', 'cash', 'reserved_cash', 'buying_power', 'market_value', 'realized_pnl',
    'unrealized_pnl', 'total_equity', 'as_of', 'quote_state', 'account_version', 'positions',
    'open_orders', 'recent_orders',
  ])
    || !['fresh', 'delayed', 'stale', 'missing'].includes(value.quote_state as string)
    || !validIsoTimestamp(value.as_of)
    || !finiteNonNegativeInteger(value.account_version)
    || !Array.isArray(value.positions)
    || !Array.isArray(value.open_orders)
    || !Array.isArray(value.recent_orders)
    || value.recent_orders.length > 50) return false
  const season = value.season
  if (!validPersonalPaperSeason(season)) return false
  if (![value.cash, value.reserved_cash, value.buying_power, value.market_value, value.realized_pnl, value.unrealized_pnl, value.total_equity].every(validFiniteNumber)) return false
  const validPosition = (position: unknown) => exactKeys(position, ['market', 'symbol', 'quantity'])
    && position.market === 'US'
    && typeof position.symbol === 'string'
    && /^[A-Z][A-Z0-9.-]{0,15}$/.test(position.symbol)
    && Number.isInteger(position.quantity)
    && position.quantity !== 0
  return value.positions.every(validPosition)
    && value.open_orders.every((order) => validPersonalPaperOrder(order)
      && order.season_id === season.id && order.status === 'PENDING' && order.cancel_eligible
      && order.cancel_account_version === value.account_version)
    && value.recent_orders.every((order) => validPersonalPaperOrder(order) && order.season_id === season.id)
}

export function validPersonalPaperQuoteProof(value: unknown): value is PersonalPaperQuoteProof {
  return exactKeys(value, [
    'quote_id', 'market', 'symbol', 'bid_minor', 'ask_minor', 'last_minor', 'quote_at',
    'observed_at', 'available_at', 'expires_at', 'session', 'freshness', 'source',
  ])
    && validOpaqueId(value.quote_id)
    && value.market === 'US'
    && typeof value.symbol === 'string'
    && /^[A-Z][A-Z0-9.-]{0,15}$/.test(value.symbol)
    && finiteNonNegativeInteger(value.bid_minor) && value.bid_minor > 0
    && finiteNonNegativeInteger(value.ask_minor) && value.ask_minor > 0
    && finiteNonNegativeInteger(value.last_minor) && value.last_minor > 0
    && value.ask_minor >= value.bid_minor
    && validIsoTimestamp(value.quote_at)
    && validIsoTimestamp(value.observed_at)
    && validIsoTimestamp(value.available_at)
    && validIsoTimestamp(value.expires_at)
    && Date.parse(value.observed_at) <= Date.parse(value.available_at)
    && Date.parse(value.quote_at) <= Date.parse(value.expires_at)
    && Date.parse(value.available_at) <= Date.parse(value.expires_at)
    && (value.session === null || (typeof value.session === 'string' && value.session.length > 0 && value.session.length <= 64))
    && value.freshness === 'fresh'
    && typeof value.source === 'string'
    && value.source.length > 0
    && value.source.length <= 128
}

const PERSONAL_PAPER_RISK_CHECK_CODES: readonly PersonalPaperRiskCheckCode[] = [
  'buying_power', 'max_loss', 'position_concentration', 'sector_concentration',
  'drawdown', 'event_gap', 'liquidity',
]

type RiskNumber = number
type ParsedRiskCheck =
  | { code: 'buying_power'; value: { required: RiskNumber; available: RiskNumber }; limit: { required_max: RiskNumber } }
  | { code: 'max_loss'; value: { usd: RiskNumber | null; pct: RiskNumber | null; unbounded: boolean }; limit: { usd: RiskNumber | null; pct: RiskNumber | null } }
  | { code: 'position_concentration'; value: { usd: RiskNumber; pct: RiskNumber }; limit: { pct: RiskNumber } }
  | { code: 'sector_concentration'; value: { industry: string; usd: RiskNumber; pct: RiskNumber }; limit: { pct: RiskNumber } }
  | { code: 'drawdown'; value: { pct: RiskNumber; peak_usd: RiskNumber; current_usd: RiskNumber }; limit: { pct: RiskNumber } }
  | { code: 'event_gap'; value: { scheduled_at: string; revision_id: number; payload_sha256: string }; limit: { scheduled_at: 'must_be_known' } | null }
  | { code: 'liquidity'; value: { spread_pct: RiskNumber }; limit: { spread_pct: RiskNumber } }

const RISK_FORMAT_ERROR = '数据格式异常/需重新获取'
const RISK_DATA_MISSING = '暂无数据（需重新获取）'

function strictRiskObject(raw: string | null, keys: readonly string[]): Record<string, unknown> | null {
  if (typeof raw !== 'string' || raw.trim() === '') return null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    const object = parsed as Record<string, unknown>
    return Object.keys(object).length === keys.length && keys.every((key) => Object.prototype.hasOwnProperty.call(object, key)) ? object : null
  } catch {
    return null
  }
}

function riskFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function riskNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function riskIsoTimestamp(value: unknown): value is string {
  return validIsoTimestamp(value)
}

function riskSha256(value: unknown): value is string {
  return typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value)
}

function riskPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) > 0
}

export function parsePersonalPaperRiskCheck(check: PersonalPaperRiskCheck): ParsedRiskCheck | null {
  const value = check.value === null ? null : strictRiskObject(check.value, check.code === 'buying_power' ? ['required', 'available'] : check.code === 'max_loss' ? ['usd', 'pct', 'unbounded'] : check.code === 'position_concentration' ? ['usd', 'pct'] : check.code === 'sector_concentration' ? ['industry', 'usd', 'pct'] : check.code === 'drawdown' ? ['pct', 'peak_usd', 'current_usd'] : check.code === 'event_gap' ? ['scheduled_at', 'revision_id', 'payload_sha256'] : ['spread_pct'])
  const limit = check.limit === null ? null : strictRiskObject(check.limit, check.code === 'buying_power' ? ['required_max'] : check.code === 'max_loss' ? ['usd', 'pct'] : check.code === 'position_concentration' || check.code === 'sector_concentration' || check.code === 'drawdown' ? ['pct'] : check.code === 'event_gap' ? ['scheduled_at'] : ['spread_pct'])
  if (!value || !limit) return null
  if (check.code === 'buying_power' && riskFiniteNumber(value.required) && riskFiniteNumber(value.available) && riskFiniteNumber(limit.required_max)) return { code: check.code, value: { required: value.required, available: value.available }, limit: { required_max: limit.required_max } }
  if (check.code === 'max_loss' && (value.usd === null || riskFiniteNumber(value.usd)) && (value.pct === null || riskFiniteNumber(value.pct)) && typeof value.unbounded === 'boolean' && (limit.usd === null || riskFiniteNumber(limit.usd)) && (limit.pct === null || riskFiniteNumber(limit.pct))) return { code: check.code, value: { usd: value.usd, pct: value.pct, unbounded: value.unbounded }, limit: { usd: limit.usd, pct: limit.pct }, }
  if (check.code === 'position_concentration' && riskFiniteNumber(value.usd) && riskFiniteNumber(value.pct) && riskFiniteNumber(limit.pct)) return { code: check.code, value: { usd: value.usd, pct: value.pct }, limit: { pct: limit.pct } }
  if (check.code === 'sector_concentration' && riskNonEmptyString(value.industry) && riskFiniteNumber(value.usd) && riskFiniteNumber(value.pct) && riskFiniteNumber(limit.pct)) return { code: check.code, value: { industry: value.industry, usd: value.usd, pct: value.pct }, limit: { pct: limit.pct } }
  if (check.code === 'drawdown' && riskFiniteNumber(value.pct) && riskFiniteNumber(value.peak_usd) && riskFiniteNumber(value.current_usd) && riskFiniteNumber(limit.pct)) return { code: check.code, value: { pct: value.pct, peak_usd: value.peak_usd, current_usd: value.current_usd }, limit: { pct: limit.pct } }
  if (check.code === 'event_gap' && riskIsoTimestamp(value.scheduled_at) && riskPositiveInteger(value.revision_id) && riskSha256(value.payload_sha256) && (limit === null || limit.scheduled_at === 'must_be_known')) return { code: check.code, value: { scheduled_at: value.scheduled_at, revision_id: value.revision_id, payload_sha256: value.payload_sha256 }, limit: limit as { scheduled_at: 'must_be_known' } | null }
  if (check.code === 'liquidity' && riskFiniteNumber(value.spread_pct) && riskFiniteNumber(limit.spread_pct)) return { code: check.code, value: { spread_pct: value.spread_pct }, limit: { spread_pct: limit.spread_pct } }
  return null
}

function formatRiskMoney(value: number | null, locale: 'zh-Hans' | 'zh-Hant'): string {
  if (value === null) return locale === 'zh-Hant' ? '未提供' : '未提供'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(value)
}

function formatRiskPercent(value: number | null): string {
  return value === null ? '未提供' : `${value.toFixed(2)}%`
}

export function formatPersonalPaperRiskCheck(check: PersonalPaperRiskCheck, locale: 'zh-Hans' | 'zh-Hant' = 'zh-Hans'): { value: string; limit: string | null } {
  if (check.value === null && check.limit === null) return { value: RISK_DATA_MISSING, limit: null }
  if (check.code === 'sector_concentration' && check.value === null) {
    const limit = strictRiskObject(check.limit, ['pct'])
    if (check.data_state === 'missing' && limit && riskFiniteNumber(limit.pct)) return { value: RISK_DATA_MISSING, limit: `≤ ${formatRiskPercent(limit.pct)}` }
  }
  const parsed = parsePersonalPaperRiskCheck(check)
  if (!parsed) return { value: RISK_FORMAT_ERROR, limit: RISK_FORMAT_ERROR }
  const dateLocale = locale === 'zh-Hant' ? 'zh-TW' : 'zh-CN'
  switch (parsed.code) {
    case 'buying_power': return { value: `需要 ${formatRiskMoney(parsed.value.required, locale)} · 可用 ${formatRiskMoney(parsed.value.available, locale)}`, limit: `上限 ${formatRiskMoney(parsed.limit.required_max, locale)}` }
    case 'max_loss': return { value: parsed.value.unbounded ? (locale === 'zh-Hant' ? '理論無上限' : '理论无上限') : `${formatRiskMoney(parsed.value.usd, locale)} · ${formatRiskPercent(parsed.value.pct)}`, limit: `${formatRiskMoney(parsed.limit.usd, locale)} · ${formatRiskPercent(parsed.limit.pct)}` }
    case 'position_concentration': return { value: `${formatRiskMoney(parsed.value.usd, locale)} · ${formatRiskPercent(parsed.value.pct)}`, limit: `≤ ${formatRiskPercent(parsed.limit.pct)}` }
    case 'sector_concentration': return { value: `${parsed.value.industry} · ${formatRiskMoney(parsed.value.usd, locale)} · ${formatRiskPercent(parsed.value.pct)}`, limit: `≤ ${formatRiskPercent(parsed.limit.pct)}` }
    case 'drawdown': return { value: `${formatRiskPercent(parsed.value.pct)} · 峰值 ${formatRiskMoney(parsed.value.peak_usd, locale)} · 当前 ${formatRiskMoney(parsed.value.current_usd, locale)}`, limit: `≤ ${formatRiskPercent(parsed.limit.pct)}` }
    case 'event_gap': return { value: `事件时间 ${new Date(parsed.value.scheduled_at).toLocaleString(dateLocale)} · 修订 ${parsed.value.revision_id}`, limit: parsed.limit ? '必须有已知时间' : null }
    case 'liquidity': return { value: `价差 ${formatRiskPercent(parsed.value.spread_pct)}`, limit: `≤ ${formatRiskPercent(parsed.limit.spread_pct)}` }
  }
}

function validPersonalPaperSourceContext(value: unknown): value is PersonalPaperRiskProofRequest['source_context'] {
  return exactKeys(value, ['kind', 'reference_id'])
    && ['manual', 'recommendation', 'chart', 'screener'].includes(value.kind as string)
    && (value.reference_id === null || validOpaqueId(value.reference_id))
}

export function validPersonalPaperRiskProofRequest(value: unknown): value is PersonalPaperRiskProofRequest {
  if (!(exactKeys(value, [
    'season_id', 'market', 'symbol', 'side', 'order_type', 'quantity', 'limit_price',
    'stop_price', 'time_in_force', 'quote_id', 'account_version', 'source_context',
  ])
    && validOpaqueId(value.season_id)
    && value.market === 'US'
    && typeof value.symbol === 'string'
    && /^[A-Z][A-Z0-9.-]{0,15}$/.test(value.symbol)
    && ['BUY', 'SELL', 'SHORT', 'COVER'].includes(value.side as string)
    && ['MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT'].includes(value.order_type as string)
    && Number.isSafeInteger(value.quantity)
    && Number(value.quantity) > 0
    && (value.limit_price === null || (validFiniteNumber(value.limit_price) && value.limit_price > 0))
    && (value.stop_price === null || (validFiniteNumber(value.stop_price) && value.stop_price > 0))
    && value.time_in_force === 'DAY'
    && validOpaqueId(value.quote_id)
    && finiteNonNegativeInteger(value.account_version)
    && validPersonalPaperSourceContext(value.source_context))) return false
  const hasLimit = validFiniteNumber(value.limit_price) && value.limit_price > 0
  const hasStop = validFiniteNumber(value.stop_price) && value.stop_price > 0
  if (value.order_type === 'MARKET') return value.limit_price === null && value.stop_price === null
  if (value.order_type === 'LIMIT') return hasLimit && value.stop_price === null
  if (value.order_type === 'STOP') return value.limit_price === null && hasStop
  return hasLimit && hasStop
}

function validPersonalPaperRiskCheck(value: unknown): value is PersonalPaperRiskCheck {
  if (!(exactKeys(value, ['code', 'status', 'title', 'detail', 'value', 'limit', 'data_state'])
    && PERSONAL_PAPER_RISK_CHECK_CODES.includes(value.code as PersonalPaperRiskCheckCode)
    && ['pass', 'warn', 'fail', 'unknown'].includes(value.status as string)
    && typeof value.title === 'string'
    && value.title.trim().length > 0
    && typeof value.detail === 'string'
    && value.detail.trim().length > 0
    && (value.value === null || typeof value.value === 'string')
    && (value.limit === null || typeof value.limit === 'string')
    && ['fresh', 'partial', 'stale', 'missing'].includes(value.data_state as string))) return false
  if (value.value === null) {
    if (value.status !== 'unknown' || value.data_state !== 'missing') return false
    if (value.code === 'event_gap') return value.limit === null
    if (value.code !== 'sector_concentration') return false
    const limit = strictRiskObject(value.limit, ['pct'])
    return Boolean(limit && riskFiniteNumber(limit.pct))
  }
  if (value.data_state === 'missing') return false
  return parsePersonalPaperRiskCheck(value as unknown as PersonalPaperRiskCheck) !== null
}

export function validPersonalPaperRiskProof(value: unknown): value is PersonalPaperRiskProof {
  if (!exactKeys(value, [
    'id', 'schema_version', 'season_id', 'quote_id', 'account_version', 'draft_sha256',
    'proof_sha256', 'created_at', 'computed_at', 'marks_as_of', 'expires_at', 'decision', 'risk_level', 'data_state', 'checks',
    'blocking_reasons', 'warnings',
  ])
    || !validOpaqueId(value.id)
    || value.schema_version !== 'r1'
    || !validOpaqueId(value.season_id)
    || !validOpaqueId(value.quote_id)
    || !finiteNonNegativeInteger(value.account_version)
    || !validSha256(value.draft_sha256)
    || !validSha256(value.proof_sha256)
    || !validIsoTimestamp(value.created_at)
    || !validIsoTimestamp(value.computed_at)
    || !validIsoTimestamp(value.marks_as_of)
    || !validIsoTimestamp(value.expires_at)
    || Date.parse(value.expires_at) <= Date.parse(value.created_at)
    || !['allow', 'review', 'reject'].includes(value.decision as string)
    || !['low', 'moderate', 'high', 'blocked'].includes(value.risk_level as string)
    || !['fresh', 'partial', 'stale', 'missing'].includes(value.data_state as string)
    || !Array.isArray(value.checks)
    || value.checks.length !== PERSONAL_PAPER_RISK_CHECK_CODES.length
    || !value.checks.every(validPersonalPaperRiskCheck)
    || !Array.isArray(value.blocking_reasons)
    || !value.blocking_reasons.every((reason) => typeof reason === 'string' && reason.trim().length > 0)
    || !Array.isArray(value.warnings)
    || !value.warnings.every((warning) => typeof warning === 'string' && warning.trim().length > 0)) return false
  const proof = value as unknown as PersonalPaperRiskProof
  const codes = proof.checks.map((check) => check.code)
  const failedDetails = proof.checks.filter((check) => check.status === 'fail').map((check) => check.detail)
  const warningDetails = proof.checks.filter((check) => check.status === 'warn' || check.status === 'unknown').map((check) => check.detail)
  const expectedDataState = proof.checks.some((check) => check.data_state === 'missing')
    ? 'missing'
    : proof.checks.some((check) => check.data_state === 'partial')
      ? 'partial'
      : 'fresh'
  const expectedDecision = proof.blocking_reasons.length > 0 ? 'reject' : warningDetails.length > 0 ? 'review' : 'allow'
  const expectedLevel = expectedDecision === 'reject' ? 'blocked' : expectedDecision === 'review' ? 'moderate' : 'low'
  const decisionIsConsistent = proof.decision === expectedDecision
    && proof.risk_level === expectedLevel
    && proof.data_state === expectedDataState
    && JSON.stringify(proof.warnings) === JSON.stringify(warningDetails)
    && failedDetails.every((detail) => proof.blocking_reasons.includes(detail))
    && (proof.decision !== 'reject' || proof.blocking_reasons.length > 0)
  return decisionIsConsistent
    && new Set(codes).size === PERSONAL_PAPER_RISK_CHECK_CODES.length
    && PERSONAL_PAPER_RISK_CHECK_CODES.every((code) => codes.includes(code))
}

export function validPersonalPaperOrder(value: unknown): value is PersonalPaperOrder {
  return exactKeys(value, [
    'id', 'season_id', 'market', 'symbol', 'side', 'order_type', 'quantity', 'status', 'created_at', 'quote_id',
    'account_version', 'cancel_eligible', 'cancel_account_version',
  ])
    && validOpaqueId(value.id)
    && validOpaqueId(value.season_id)
    && value.market === 'US'
    && typeof value.symbol === 'string'
    && /^[A-Z][A-Z0-9.-]{0,15}$/.test(value.symbol)
    && ['BUY', 'SELL', 'SHORT', 'COVER'].includes(value.side as string)
    && ['MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT'].includes(value.order_type as string)
    && Number.isInteger(value.quantity)
    && Number(value.quantity) > 0
    && ['PENDING', 'FILLED', 'CANCELLED'].includes(value.status as string)
    && validIsoTimestamp(value.created_at)
    && validOpaqueId(value.quote_id)
    && finiteNonNegativeInteger(value.account_version)
    && typeof value.cancel_eligible === 'boolean'
    && (value.cancel_account_version === null || finiteNonNegativeInteger(value.cancel_account_version))
    && value.cancel_eligible === (value.status === 'PENDING')
    && (value.status === 'PENDING' ? value.cancel_account_version !== null : value.cancel_account_version === null)
}

export function validPersonalPaperOrderResult(value: unknown): value is PersonalPaperOrderResult {
  return exactKeys(value, ['order', 'account', 'replayed'])
    && validPersonalPaperOrder(value.order)
    && validPersonalPaperAccount(value.account)
    && typeof value.replayed === 'boolean'
    && value.order.season_id === value.account.season.id
}

function validSha256(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

function validText(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 256
}

function validAdminComputeEvidenceAuthority(value: Record<string, unknown>) {
  return value.publication_ceiling === 'shadow'
    && value.research_only === true
    && value.actionable === false
    && value.user_visible === false
}

function validAdminComputeEvidenceItem(value: unknown): value is AdminComputeEvidenceItem {
  if (!exactKeys(value, [
    'publication_state', 'received_at', 'completed_at', 'candidate_id', 'candidate_version',
    'market', 'instrument_family', 'symbols', 'candidate_status', 'manifest_sha256',
    'result_sha256', 'package_sha256', 'artifact_count', 'research_only', 'actionable',
    'user_visible',
  ])) return false
  return (value.publication_state === 'quarantine' || value.publication_state === 'shadow')
    && validIsoTimestamp(value.received_at)
    && validIsoTimestamp(value.completed_at)
    && validText(value.candidate_id)
    && validText(value.candidate_version)
    && value.market === 'US'
    && value.instrument_family === 'equity'
    && Array.isArray(value.symbols)
    && value.symbols.length > 0
    && value.symbols.length <= 128
    && value.symbols.every(validText)
    && new Set(value.symbols).size === value.symbols.length
    && value.candidate_status === 'shadow'
    && validSha256(value.manifest_sha256)
    && validSha256(value.result_sha256)
    && validSha256(value.package_sha256)
    && finiteNonNegativeInteger(value.artifact_count)
    && value.research_only === true
    && value.actionable === false
    && value.user_visible === false
}

export function validAdminComputeEvidenceStatus(value: unknown): value is AdminComputeEvidenceStatus {
  if (!exactKeys(value, [
    'available', 'publication_ceiling', 'research_only', 'actionable', 'user_visible',
    'counts', 'last_received_at',
  ]) || typeof value.available !== 'boolean' || !validAdminComputeEvidenceAuthority(value)) return false
  const counts = value.counts
  if (!exactKeys(counts, ['quarantine', 'shadow'])
    || !finiteNonNegativeInteger(counts.quarantine)
    || !finiteNonNegativeInteger(counts.shadow)) return false
  const total = Number(counts.quarantine) + Number(counts.shadow)
  return value.available
    ? total > 0 && validIsoTimestamp(value.last_received_at)
    : total === 0 && value.last_received_at === null
}

export function validAdminComputeEvidenceLatest(value: unknown): value is AdminComputeEvidenceLatest {
  if (!exactKeys(value, [
    'available', 'publication_ceiling', 'research_only', 'actionable', 'user_visible', 'evidence',
  ]) || typeof value.available !== 'boolean' || !validAdminComputeEvidenceAuthority(value)) return false
  return value.available ? validAdminComputeEvidenceItem(value.evidence) : value.evidence === null
}

export function validAdminComputeEvidenceHistory(value: unknown, expectedLimit = 20): value is AdminComputeEvidenceHistory {
  if (!exactKeys(value, [
    'available', 'publication_ceiling', 'research_only', 'actionable', 'user_visible', 'limit', 'items',
  ]) || typeof value.available !== 'boolean' || !validAdminComputeEvidenceAuthority(value)
    || !Number.isSafeInteger(expectedLimit) || expectedLimit < 1 || expectedLimit > 100
    || value.limit !== expectedLimit || !Array.isArray(value.items)
    || value.items.length > expectedLimit || !value.items.every(validAdminComputeEvidenceItem)) return false
  const hashes = value.items.map((item) => item.package_sha256)
  return value.available === (value.items.length > 0) && new Set(hashes).size === hashes.length
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

export async function fetchMarketCandles(symbol: string, timeframe: string, signal?: AbortSignal) {
  const payload = await request<MarketCandlePayload>(`/api/rewrite/v1/market/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`, { signal })
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
    && payload.source === '真实数据来源'
    && typeof payload.is_realtime === 'boolean'
    && typeof payload.actionable_quote === 'boolean'
    && typeof payload.freshness === 'string'
    && validPublicVerification(payload.verification)
    && typeof payload.configuration_allows_realtime === 'boolean'
    && typeof payload.request_succeeded === 'boolean'
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

function validPublicVerification(value: unknown) {
  return value === 'realtime_verified'
    || value === 'realtime_unverified'
    || value === 'delayed_research'
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
  const metadataIsValid = payload.source === '真实数据来源'
    && typeof payload.is_realtime === 'boolean'
    && typeof payload.actionable_quote === 'boolean'
    && typeof payload.freshness === 'string'
    && validPublicVerification(payload.verification)
    && typeof payload.configuration_allows_realtime === 'boolean'
    && Array.isArray(payload.missing_fields)
    && payload.missing_fields.every((item) => typeof item === 'string')
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
  const metadataIsValid = payload.source === '真实数据来源'
    && typeof payload.is_realtime === 'boolean'
    && typeof payload.actionable_quote === 'boolean'
    && typeof payload.freshness === 'string'
    && validPublicVerification(payload.verification)
    && typeof payload.configuration_allows_realtime === 'boolean'
    && Array.isArray(payload.missing_fields)
    && payload.missing_fields.every((item) => typeof item === 'string')
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
  payload: { plan: MembershipPlanKey; cycle: MembershipBillingCycle; method: string; terms_accepted: boolean; coupon_code?: string },
  idempotencyKey: string,
) {
  return request<{ order_no: string; status: string; amount: number; currency: string; payment_instructions: string; payment_qr_available: boolean; list_price_minor: number; coupon_discount_minor: number; referral_discount_minor: number; final_amount_minor: number; coupon_code_snapshot: string | null }>(
    '/api/rewrite/v1/membership/orders',
    {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    },
  )
}

function validMembershipQuote(value: unknown): value is MembershipQuote {
  return exactKeys(value, [
    'plan', 'cycle', 'currency', 'list_price_minor', 'coupon_discount_minor',
    'referral_discount_minor', 'final_amount_minor', 'coupon_code',
    'referral_eligible', 'discount_order', 'server_reprices_on_order',
  ])
    && ['标准版', '高级版'].includes(String(value.plan))
    && ['monthly', 'quarterly', 'yearly', 'project'].includes(String(value.cycle))
    && value.currency === 'HKD'
    && finiteNonNegativeInteger(value.list_price_minor)
    && finiteNonNegativeInteger(value.coupon_discount_minor)
    && finiteNonNegativeInteger(value.referral_discount_minor)
    && finiteNonNegativeInteger(value.final_amount_minor)
    && value.list_price_minor >= value.final_amount_minor
    && value.coupon_discount_minor + value.referral_discount_minor === value.list_price_minor - value.final_amount_minor
    && (value.coupon_code === null || (typeof value.coupon_code === 'string' && /^[A-Z0-9_-]{3,64}$/.test(value.coupon_code)))
    && typeof value.referral_eligible === 'boolean'
    && Array.isArray(value.discount_order)
    && value.discount_order.length === 2
    && value.discount_order[0] === 'coupon'
    && value.discount_order[1] === 'referral'
    && value.server_reprices_on_order === true
}

export async function quoteMembershipOrder(payload: {
  plan: MembershipPlanKey
  cycle: MembershipBillingCycle
  coupon_code?: string
}): Promise<MembershipQuote> {
  const value = await request<unknown>('/api/rewrite/v1/membership/quote', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!validMembershipQuote(value)) throw new BrowserApiError('会员报价响应格式无效。', 502)
  return value
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
