export type EarningsMarket = 'US' | 'CN'
export type EarningsTiming = 'BMO' | 'AMC' | 'DURING' | 'UNKNOWN'
export type EarningsEventStatus = 'CONFIRMED' | 'RESCHEDULED' | 'CANCELLED'
export type EarningsDirection = 'up' | 'down' | 'flat'
export type EarningsTab = 'future' | 'history' | 'statistics'

export type SimulatedEarningsAction =
  | 'OBSERVE'
  | 'PAPER_OPEN'
  | 'PAPER_ADD'
  | 'PAPER_REDUCE'
  | 'PAPER_CLOSE'
  | 'RESEARCH_LONG_CALL'
  | 'RESEARCH_LONG_PUT'
  | 'RESEARCH_LONG_STRADDLE'
  | 'RESEARCH_LONG_STRANGLE'

export interface EarningsLockedOverview {
  state: 'locked'
  feature: 'earnings_forecast'
  required_capability: 'earnings_forecast'
  window_days: number
  confirmed_event_count: number
  reason_code: 'legacy_entitlement_required'
  description: string
  upgrade_path: '/membership' | null
}

export interface EarningsNarrative {
  summary: string
  changed_since_previous: string[]
  supporting_evidence: string[]
  counter_evidence: string[]
}

export interface EarningsCausalClaim {
  kind: 'mechanism_hypothesis'
  claim: string
  confidence: number
  evidence_count: number
  confounders: string[]
}

export interface EarningsActionContract {
  structure: SimulatedEarningsAction
  entry: { limit_price: null; quantity: null }
  stop: null
  targets: []
  max_loss: number
  max_account_pct: null
  breakeven: null
  invalidation: string
  exit: string
  roll: string
  quote_at: string
  model_artifact_sha256: string
  evidence_manifest_sha256: string
  execution_eligible: false
  automatic_ordering: false
}

export interface EarningsOptionReferenceItem {
  option_id: string
  structure_type: 'LONG_CALL' | 'LONG_PUT' | 'LONG_STRADDLE' | 'LONG_STRANGLE'
}

export interface EarningsOptionLocked {
  state: 'locked'
  feature: 'earnings_option_research'
  required_capability: 'earnings_option_defined_risk'
  reason_code: 'legacy_entitlement_required'
  upgrade_path: '/membership' | null
}

export type EarningsOptionReference = EarningsOptionLocked | {
  state: 'no_data'
  items: []
} | {
  state: 'available'
  items: EarningsOptionReferenceItem[]
}

export interface EarningsForecastSnapshot {
  countdown_day: number
  decision_at: string
  available_cutoff_at: string
  p_up: number
  p_down: number
  p_flat: number
  flat_band_pct: number
  confidence: number
  calibration_sample_size: number
  reference_price: number
  currency: 'USD' | 'CNY'
  price_p10: number
  price_p50: number
  price_p90: number
  estimated_mfe_pct: number
  estimated_mae_pct: number
  simulated_action: SimulatedEarningsAction
  narrative: EarningsNarrative
  causal_graph: { claims: EarningsCausalClaim[] }
  risk: {
    defined_risk: true
    max_loss_amount: number
    currency: 'USD' | 'CNY'
    invalidation_condition: string
  }
  evidence_count: number
  evidence_sha256: string[]
  model_artifact_sha256: string
  evidence_manifest_sha256: string
  research_only: true
  execution_eligible: false
  automatic_ordering: false
  action_contract: EarningsActionContract
  option_research: EarningsOptionReference
}

export interface EarningsEventSummary {
  event_id: string
  market: EarningsMarket
  symbol: string
  fiscal_period: string
  scheduled_at: string
  exchange_timezone: string
  timing: EarningsTiming
  status: EarningsEventStatus
}

export interface EarningsForecastItem extends EarningsEventSummary {
  latest_forecast: EarningsForecastSnapshot | null
  forecast_state: 'sealed' | 'pending'
}

export interface EarningsResearchOverview {
  state: 'research'
  data_state: 'ready' | 'no_data'
  window_days: number
  research_only: true
  execution_eligible: false
  automatic_ordering: false
  items: EarningsForecastItem[]
}

export type EarningsOverview = EarningsLockedOverview | EarningsResearchOverview

export interface EarningsOutcome {
  checkpoint: 'AFTER_HOURS' | 'NEXT_CLOSE' | 'D3_CLOSE' | 'D5_CLOSE'
  baseline_price: number
  observed_price: number
  return_pct: number
  mfe_pct: number
  mae_pct: number
  observed_at: string
  available_at: string
}

export interface EarningsPostmortem {
  stage: 'PRELIMINARY' | 'FINAL' | 'CORRECTION'
  completed_at: string
  direction_correct: boolean
  interval_covered: boolean
  paper_performance: {
    state: 'unavailable'
    pnl_net: null
    max_drawdown: null
    ledger_snapshot_sha256: null
  }
  analysis: Record<'correct' | 'incorrect' | 'error_categories' | 'lessons' | 'candidate_hypotheses', string[]>
}

export interface EarningsResearchDetail extends EarningsEventSummary {
  state: 'research'
  research_only: true
  execution_eligible: false
  automatic_ordering: false
  timeline: EarningsForecastSnapshot[]
  outcomes: EarningsOutcome[]
  postmortems: EarningsPostmortem[]
}

export type EarningsDetail = EarningsLockedOverview | EarningsResearchDetail
export type EarningsHistory = EarningsLockedOverview | {
  state: 'research'
  items: EarningsResearchDetail[]
  next_cursor: string | null
}

export interface EarningsMetrics {
  sample_size: number
  direction_accuracy: number
  multiclass_brier_score: number
  log_loss: number
  expected_calibration_error: number
  average_confidence_gap: number
  interval_coverage: number
  average_interval_width: number
  overconfidence_rate: number
  high_confidence_sample_size: number
  paper_total_pnl: number | null
  paper_max_drawdown: number | null
}

export type EarningsStatistics = EarningsLockedOverview | { state: 'research'; metrics: EarningsMetrics }

export interface EarningsOptionLeg {
  contract_id: string
  right: 'CALL' | 'PUT'
  strike: number
  expiry: string
  quantity: number
  multiplier: number
  bid: number
  ask: number
  implied_volatility: number
  delta: number
  gamma: number
  theta: number
  vega: number
  volume: number
  open_interest: number
  quote_at: string
  available_at: string
}

export interface EarningsIvCrushScenario {
  relative_iv_change_pct: number
  estimated_structure_value: number
  estimated_pnl_after_costs: number
  method: 'first_order_vega_current_snapshot_estimate'
  spot_held_constant: true
  time_decay_excluded: true
}

export interface EarningsOptionResearch {
  state: 'research'
  structure_type: 'LONG_CALL' | 'LONG_PUT' | 'LONG_STRADDLE' | 'LONG_STRANGLE'
  evidence_mode: 'current_snapshot_research_estimate'
  historical_oos_validated: false
  research_only: true
  execution_eligible: false
  automatic_ordering: false
  legs: EarningsOptionLeg[]
  total_premium: number
  commission_cost: number
  spread_cost: number
  slippage_cost: number
  max_loss: number
  lower_breakeven: number | null
  upper_breakeven: number | null
  required_move_pct: number
  model_expected_move_pct: number
  iv_implied_move_pct: number
  probability_outside_breakeven: number
  expected_value_net_costs: number
  call_zero_coverage: EarningsOneLegCoverage | null
  put_zero_coverage: EarningsOneLegCoverage | null
  terminal_sample_size: number
  iv_crush_scenarios: EarningsIvCrushScenario[]
  decision_at: string
  action_contract: EarningsOptionActionContract
}

export interface EarningsOneLegCoverage {
  covering_leg: 'CALL' | 'PUT'
  required_terminal_price: number
  probability: number
  possible: boolean
}

export interface EarningsOptionActionContract {
  structure: EarningsOptionResearch['structure_type']
  entry: {
    order_type: 'LIMIT_RESEARCH_ONLY'
    legs: Array<Pick<EarningsOptionLeg, 'contract_id' | 'right' | 'strike' | 'expiry' | 'quantity' | 'multiplier'> & { limit_price: number }>
  }
  stop: null
  targets: []
  max_loss: number
  max_account_pct: null
  breakeven: { lower: number | null; upper: number | null }
  invalidation: string
  exit: string
  roll: string
  quote_at: string
  model_artifact_sha256: string
  evidence_manifest_sha256: string
  execution_eligible: false
  automatic_ordering: false
}

export type EarningsOptionDetail = EarningsOptionResearch | EarningsOptionLocked | EarningsLockedOverview

export class EarningsDecodeError extends Error {}

type JsonObject = Record<string, unknown>
const ACTIONS = ['OBSERVE', 'PAPER_OPEN', 'PAPER_ADD', 'PAPER_REDUCE', 'PAPER_CLOSE', 'RESEARCH_LONG_CALL', 'RESEARCH_LONG_PUT', 'RESEARCH_LONG_STRADDLE', 'RESEARCH_LONG_STRANGLE'] as const
const HASH = /^[0-9a-f]{64}$/
const OPAQUE_ID = /^[A-Za-z0-9_-]{20,64}$/
const DATE = /^\d{4}-\d{2}-\d{2}$/
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/

function object(value: unknown, label: string): JsonObject {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new EarningsDecodeError(`${label} 必须是对象。`)
  return value as JsonObject
}

function exact(value: JsonObject, required: readonly string[], label: string) {
  const keys = Object.keys(value)
  if (keys.length !== required.length || required.some((key) => !(key in value))) throw new EarningsDecodeError(`${label} 字段不符合合同。`)
}

function string(value: unknown, label: string, maximum = 2_000, minimum = 1) {
  if (typeof value !== 'string' || value.length < minimum || value.length > maximum || value.includes('\0')) throw new EarningsDecodeError(`${label} 无效。`)
  return value
}

function finite(value: unknown, label: string, minimum = -Number.MAX_VALUE, maximum = Number.MAX_VALUE) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum || value > maximum) throw new EarningsDecodeError(`${label} 必须是有限数值。`)
  return value
}

function integer(value: unknown, label: string, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  const result = finite(value, label, minimum, maximum)
  if (!Number.isInteger(result)) throw new EarningsDecodeError(`${label} 必须是整数。`)
  return result
}

function enumeration<const T extends readonly string[]>(value: unknown, values: T, label: string): T[number] {
  if (typeof value !== 'string' || !values.includes(value as T[number])) throw new EarningsDecodeError(`${label} 枚举无效。`)
  return value as T[number]
}

function boolean(value: unknown, label: string) {
  if (typeof value !== 'boolean') throw new EarningsDecodeError(`${label} 必须是布尔值。`)
  return value
}

function fixedBoolean<T extends boolean>(value: unknown, expected: T, label: string): T {
  if (value !== expected) throw new EarningsDecodeError(`${label} 必须为 ${expected}。`)
  return expected
}

function nullableNumber(value: unknown, label: string) {
  return value === null ? null : finite(value, label)
}

function list<T>(value: unknown, label: string, maximum: number, decode: (item: unknown, index: number) => T): T[] {
  if (!Array.isArray(value) || value.length > maximum) throw new EarningsDecodeError(`${label} 数组无效。`)
  return value.map(decode)
}

function timestamp(value: unknown, label: string) {
  const result = string(value, label, 80)
  if (!TIMESTAMP.test(result) || !Number.isFinite(Date.parse(result))) throw new EarningsDecodeError(`${label} 时间无效。`)
  return result
}

function hash(value: unknown, label: string) {
  const result = string(value, label, 64, 64)
  if (!HASH.test(result)) throw new EarningsDecodeError(`${label} 摘要无效。`)
  return result
}

function opaqueId(value: unknown, label: string) {
  const result = string(value, label, 64, 20)
  if (!OPAQUE_ID.test(result)) throw new EarningsDecodeError(`${label} 无效。`)
  return result
}

function decodeOptionReference(value: unknown): EarningsOptionReference {
  const item = object(value, 'option_research')
  if (item.state === 'locked') {
    exact(item, ['state', 'feature', 'required_capability', 'reason_code', 'upgrade_path'], 'option_research')
    return {
      state: enumeration(item.state, ['locked'] as const, 'option_research.state'),
      feature: enumeration(item.feature, ['earnings_option_research'] as const, 'option_research.feature'),
      required_capability: enumeration(item.required_capability, ['earnings_option_defined_risk'] as const, 'option_research.required_capability'),
      reason_code: enumeration(item.reason_code, ['legacy_entitlement_required'] as const, 'option_research.reason_code'),
      upgrade_path: item.upgrade_path === null ? null : enumeration(item.upgrade_path, ['/membership'] as const, 'option_research.upgrade_path'),
    }
  }
  exact(item, ['state', 'items'], 'option_research')
  const state = enumeration(item.state, ['available', 'no_data'] as const, 'option_research.state')
  const items = list(item.items, 'option_research.items', 8, (raw, index) => {
    const reference = object(raw, `option_research.items[${index}]`)
    exact(reference, ['option_id', 'structure_type'], `option_research.items[${index}]`)
    return {
      option_id: opaqueId(reference.option_id, `option_research.items[${index}].option_id`),
      structure_type: enumeration(reference.structure_type, ['LONG_CALL', 'LONG_PUT', 'LONG_STRADDLE', 'LONG_STRANGLE'] as const, `option_research.items[${index}].structure_type`),
    }
  })
  if ((state === 'no_data') !== (items.length === 0)) throw new EarningsDecodeError('option_research 状态与项目不一致。')
  if (new Set(items.map((entry) => entry.option_id)).size !== items.length) throw new EarningsDecodeError('option_research 编号重复。')
  return state === 'no_data' ? { state, items: [] } : { state, items }
}

function stringList(value: unknown, label: string, maximum = 100) {
  return list(value, label, maximum, (item, index) => string(item, `${label}[${index}]`, 2_000))
}

function decodeLocked(value: JsonObject): EarningsLockedOverview {
  exact(value, ['state', 'feature', 'required_capability', 'window_days', 'confirmed_event_count', 'reason_code', 'description', 'upgrade_path'], '锁定响应')
  return {
    state: enumeration(value.state, ['locked'] as const, 'state'),
    feature: enumeration(value.feature, ['earnings_forecast'] as const, 'feature'),
    required_capability: enumeration(value.required_capability, ['earnings_forecast'] as const, 'required_capability'),
    window_days: integer(value.window_days, 'window_days', 1, 30),
    confirmed_event_count: integer(value.confirmed_event_count, 'confirmed_event_count', 0, 1_000_000),
    reason_code: enumeration(value.reason_code, ['legacy_entitlement_required'] as const, 'reason_code'),
    description: string(value.description, 'description', 500),
    upgrade_path: value.upgrade_path === null ? null : enumeration(value.upgrade_path, ['/membership'] as const, 'upgrade_path'),
  }
}

function decodeNarrative(value: unknown): EarningsNarrative {
  const item = object(value, 'narrative')
  exact(item, ['summary', 'changed_since_previous', 'supporting_evidence', 'counter_evidence'], 'narrative')
  return { summary: string(item.summary, 'summary', 4_000), changed_since_previous: stringList(item.changed_since_previous, 'changed_since_previous'), supporting_evidence: stringList(item.supporting_evidence, 'supporting_evidence'), counter_evidence: stringList(item.counter_evidence, 'counter_evidence') }
}

function decodeForecastAction(value: unknown): EarningsActionContract {
  const item = object(value, 'action_contract')
  exact(item, ['structure', 'entry', 'stop', 'targets', 'max_loss', 'max_account_pct', 'breakeven', 'invalidation', 'exit', 'roll', 'quote_at', 'model_artifact_sha256', 'evidence_manifest_sha256', 'execution_eligible', 'automatic_ordering'], 'action_contract')
  const entry = object(item.entry, 'action_contract.entry')
  exact(entry, ['limit_price', 'quantity'], 'action_contract.entry')
  if (entry.limit_price !== null || entry.quantity !== null || item.stop !== null || item.max_account_pct !== null || item.breakeven !== null) throw new EarningsDecodeError('研究行动合同包含执行字段。')
  if (!Array.isArray(item.targets) || item.targets.length !== 0) throw new EarningsDecodeError('研究目标必须为空。')
  return { structure: enumeration(item.structure, ACTIONS, 'structure'), entry: { limit_price: null, quantity: null }, stop: null, targets: [], max_loss: finite(item.max_loss, 'max_loss', 0), max_account_pct: null, breakeven: null, invalidation: string(item.invalidation, 'invalidation'), exit: string(item.exit, 'exit'), roll: string(item.roll, 'roll'), quote_at: timestamp(item.quote_at, 'quote_at'), model_artifact_sha256: hash(item.model_artifact_sha256, 'model_artifact_sha256'), evidence_manifest_sha256: hash(item.evidence_manifest_sha256, 'evidence_manifest_sha256'), execution_eligible: fixedBoolean(item.execution_eligible, false, 'execution_eligible'), automatic_ordering: fixedBoolean(item.automatic_ordering, false, 'automatic_ordering') }
}

function decodeForecast(value: unknown): EarningsForecastSnapshot {
  const item = object(value, 'forecast')
  exact(item, ['countdown_day', 'decision_at', 'available_cutoff_at', 'p_up', 'p_down', 'p_flat', 'flat_band_pct', 'confidence', 'calibration_sample_size', 'reference_price', 'currency', 'price_p10', 'price_p50', 'price_p90', 'estimated_mfe_pct', 'estimated_mae_pct', 'simulated_action', 'narrative', 'causal_graph', 'risk', 'evidence_count', 'evidence_sha256', 'model_artifact_sha256', 'evidence_manifest_sha256', 'research_only', 'execution_eligible', 'automatic_ordering', 'action_contract', 'option_research'], 'forecast')
  const pUp = finite(item.p_up, 'p_up', 0, 1), pDown = finite(item.p_down, 'p_down', 0, 1), pFlat = finite(item.p_flat, 'p_flat', 0, 1)
  if (Math.abs(pUp + pDown + pFlat - 1) > 1e-9) throw new EarningsDecodeError('方向概率总和必须为 1。')
  const p10 = finite(item.price_p10, 'price_p10', 0.000001), p50 = finite(item.price_p50, 'price_p50', 0.000001), p90 = finite(item.price_p90, 'price_p90', 0.000001)
  if (!(p10 <= p50 && p50 <= p90)) throw new EarningsDecodeError('P10/P50/P90 顺序无效。')
  const currency = enumeration(item.currency, ['USD', 'CNY'] as const, 'currency')
  const risk = object(item.risk, 'risk'); exact(risk, ['defined_risk', 'max_loss_amount', 'currency', 'invalidation_condition'], 'risk')
  const causal = object(item.causal_graph, 'causal_graph'); exact(causal, ['claims'], 'causal_graph')
  const claims = list(causal.claims, 'claims', 100, (raw, index) => { const claim = object(raw, `claim[${index}]`); exact(claim, ['kind', 'claim', 'confidence', 'evidence_count', 'confounders'], `claim[${index}]`); return { kind: enumeration(claim.kind, ['mechanism_hypothesis'] as const, 'kind'), claim: string(claim.claim, 'claim', 4_000), confidence: finite(claim.confidence, 'claim.confidence', 0, 1), evidence_count: integer(claim.evidence_count, 'claim.evidence_count', 0, 1_000_000), confounders: stringList(claim.confounders, 'confounders') } })
  const decisionAt = timestamp(item.decision_at, 'decision_at'), cutoffAt = timestamp(item.available_cutoff_at, 'available_cutoff_at')
  if (Date.parse(cutoffAt) > Date.parse(decisionAt)) throw new EarningsDecodeError('可用证据截止时间晚于决策时间。')
  const simulatedAction = enumeration(item.simulated_action, ACTIONS, 'simulated_action'), riskMaxLoss = finite(risk.max_loss_amount, 'max_loss_amount', 0), invalidation = string(risk.invalidation_condition, 'invalidation_condition')
  const evidenceHashes = list(item.evidence_sha256, 'evidence_sha256', 200, (entry, index) => hash(entry, `evidence_sha256[${index}]`)), evidenceCount = integer(item.evidence_count, 'evidence_count', 0, 1_000_000)
  if (evidenceCount !== evidenceHashes.length) throw new EarningsDecodeError('evidence_count 与证据摘要数量不一致。')
  const modelHash = hash(item.model_artifact_sha256, 'model_artifact_sha256'), manifestHash = hash(item.evidence_manifest_sha256, 'evidence_manifest_sha256'), action = decodeForecastAction(item.action_contract)
  if (action.structure !== simulatedAction || Math.abs(action.max_loss - riskMaxLoss) > 1e-9 || action.quote_at !== decisionAt || action.model_artifact_sha256 !== modelHash || action.evidence_manifest_sha256 !== manifestHash || action.invalidation !== invalidation) throw new EarningsDecodeError('行动合同与预测快照不一致。')
  return { countdown_day: integer(item.countdown_day, 'countdown_day', 1, 7), decision_at: decisionAt, available_cutoff_at: cutoffAt, p_up: pUp, p_down: pDown, p_flat: pFlat, flat_band_pct: finite(item.flat_band_pct, 'flat_band_pct', 0), confidence: finite(item.confidence, 'confidence', 0, 1), calibration_sample_size: integer(item.calibration_sample_size, 'calibration_sample_size', 0, 100_000_000), reference_price: finite(item.reference_price, 'reference_price', 0.000001), currency, price_p10: p10, price_p50: p50, price_p90: p90, estimated_mfe_pct: finite(item.estimated_mfe_pct, 'estimated_mfe_pct', 0), estimated_mae_pct: finite(item.estimated_mae_pct, 'estimated_mae_pct', -Number.MAX_VALUE, 0), simulated_action: simulatedAction, narrative: decodeNarrative(item.narrative), causal_graph: { claims }, risk: { defined_risk: fixedBoolean(risk.defined_risk, true, 'defined_risk'), max_loss_amount: riskMaxLoss, currency: enumeration(risk.currency, [currency] as const, 'risk.currency'), invalidation_condition: invalidation }, evidence_count: evidenceCount, evidence_sha256: evidenceHashes, model_artifact_sha256: modelHash, evidence_manifest_sha256: manifestHash, research_only: fixedBoolean(item.research_only, true, 'research_only'), execution_eligible: fixedBoolean(item.execution_eligible, false, 'execution_eligible'), automatic_ordering: fixedBoolean(item.automatic_ordering, false, 'automatic_ordering'), action_contract: action, option_research: decodeOptionReference(item.option_research) }
}

function decodeEvent(value: JsonObject): EarningsEventSummary {
  return { event_id: string(value.event_id, 'event_id', 64, 20), market: enumeration(value.market, ['US', 'CN'] as const, 'market'), symbol: string(value.symbol, 'symbol', 16), fiscal_period: string(value.fiscal_period, 'fiscal_period', 40), scheduled_at: timestamp(value.scheduled_at, 'scheduled_at'), exchange_timezone: string(value.exchange_timezone, 'exchange_timezone', 64), timing: enumeration(value.timing, ['BMO', 'AMC', 'DURING', 'UNKNOWN'] as const, 'timing'), status: enumeration(value.status, ['CONFIRMED', 'RESCHEDULED', 'CANCELLED'] as const, 'status') }
}

export function decodeEarningsOverview(value: unknown): EarningsOverview {
  const root = object(value, 'overview')
  if (root.state === 'locked') return decodeLocked(root)
  exact(root, ['state', 'data_state', 'window_days', 'research_only', 'execution_eligible', 'automatic_ordering', 'items'], 'overview')
  const items = list(root.items, 'items', 200, (raw, index) => { const item = object(raw, `items[${index}]`); exact(item, ['event_id', 'market', 'symbol', 'fiscal_period', 'scheduled_at', 'exchange_timezone', 'timing', 'status', 'latest_forecast', 'forecast_state'], `items[${index}]`); const latest = item.latest_forecast === null ? null : decodeForecast(item.latest_forecast), forecastState = enumeration(item.forecast_state, ['sealed', 'pending'] as const, 'forecast_state'); if ((forecastState === 'sealed') !== (latest !== null)) throw new EarningsDecodeError('forecast_state 与 latest_forecast 不一致。'); return { ...decodeEvent(item), latest_forecast: latest, forecast_state: forecastState } })
  const dataState = enumeration(root.data_state, ['ready', 'no_data'] as const, 'data_state')
  if ((dataState === 'no_data') !== (items.length === 0)) throw new EarningsDecodeError('data_state 与 items 不一致。')
  return { state: enumeration(root.state, ['research'] as const, 'state'), data_state: dataState, window_days: integer(root.window_days, 'window_days', 1, 30), research_only: fixedBoolean(root.research_only, true, 'research_only'), execution_eligible: fixedBoolean(root.execution_eligible, false, 'execution_eligible'), automatic_ordering: fixedBoolean(root.automatic_ordering, false, 'automatic_ordering'), items }
}

function decodeOutcome(value: unknown, index: number): EarningsOutcome {
  const item = object(value, `outcomes[${index}]`); exact(item, ['checkpoint', 'baseline_price', 'observed_price', 'return_pct', 'mfe_pct', 'mae_pct', 'observed_at', 'available_at'], `outcomes[${index}]`)
  const baseline = finite(item.baseline_price, 'baseline_price', 0.000001), observed = finite(item.observed_price, 'observed_price', 0.000001), returnPct = finite(item.return_pct, 'return_pct'), mfe = finite(item.mfe_pct, 'mfe_pct', 0), mae = finite(item.mae_pct, 'mae_pct', -Number.MAX_VALUE, 0), observedAt = timestamp(item.observed_at, 'observed_at'), availableAt = timestamp(item.available_at, 'available_at')
  if (Math.abs(returnPct - (observed / baseline - 1) * 100) > 1e-6 || mae > returnPct || returnPct > mfe || Date.parse(observedAt) > Date.parse(availableAt)) throw new EarningsDecodeError('结果合同不一致。')
  return { checkpoint: enumeration(item.checkpoint, ['AFTER_HOURS', 'NEXT_CLOSE', 'D3_CLOSE', 'D5_CLOSE'] as const, 'checkpoint'), baseline_price: baseline, observed_price: observed, return_pct: returnPct, mfe_pct: mfe, mae_pct: mae, observed_at: observedAt, available_at: availableAt }
}

function decodePostmortem(value: unknown, index: number): EarningsPostmortem {
  const item = object(value, `postmortems[${index}]`); exact(item, ['stage', 'completed_at', 'direction_correct', 'interval_covered', 'paper_performance', 'analysis'], `postmortems[${index}]`)
  const paper = object(item.paper_performance, 'paper_performance'); exact(paper, ['state', 'pnl_net', 'max_drawdown', 'ledger_snapshot_sha256'], 'paper_performance')
  if (paper.state !== 'unavailable' || paper.pnl_net !== null || paper.max_drawdown !== null || paper.ledger_snapshot_sha256 !== null) throw new EarningsDecodeError('纸上表现缺少封存账本，必须保持不可用。')
  const analysis = object(item.analysis, 'analysis'); const keys = ['correct', 'incorrect', 'error_categories', 'lessons', 'candidate_hypotheses'] as const; exact(analysis, keys, 'analysis')
  return { stage: enumeration(item.stage, ['PRELIMINARY', 'FINAL', 'CORRECTION'] as const, 'stage'), completed_at: timestamp(item.completed_at, 'completed_at'), direction_correct: boolean(item.direction_correct, 'direction_correct'), interval_covered: boolean(item.interval_covered, 'interval_covered'), paper_performance: { state: 'unavailable', pnl_net: null, max_drawdown: null, ledger_snapshot_sha256: null }, analysis: { correct: stringList(analysis.correct, 'correct'), incorrect: stringList(analysis.incorrect, 'incorrect'), error_categories: stringList(analysis.error_categories, 'error_categories'), lessons: stringList(analysis.lessons, 'lessons'), candidate_hypotheses: stringList(analysis.candidate_hypotheses, 'candidate_hypotheses') } }
}

export function decodeEarningsDetail(value: unknown): EarningsDetail {
  const root = object(value, 'detail'); if (root.state === 'locked') return decodeLocked(root)
  exact(root, ['state', 'event_id', 'market', 'symbol', 'fiscal_period', 'scheduled_at', 'exchange_timezone', 'timing', 'status', 'research_only', 'execution_eligible', 'automatic_ordering', 'timeline', 'outcomes', 'postmortems'], 'detail')
  const timeline = list(root.timeline, 'timeline', 7, (item) => decodeForecast(item)); const days = new Set(timeline.map((item) => item.countdown_day)); if (days.size !== timeline.length) throw new EarningsDecodeError('timeline 日期重复。')
  return { state: enumeration(root.state, ['research'] as const, 'state'), ...decodeEvent(root), research_only: fixedBoolean(root.research_only, true, 'research_only'), execution_eligible: fixedBoolean(root.execution_eligible, false, 'execution_eligible'), automatic_ordering: fixedBoolean(root.automatic_ordering, false, 'automatic_ordering'), timeline, outcomes: list(root.outcomes, 'outcomes', 16, decodeOutcome), postmortems: list(root.postmortems, 'postmortems', 100, decodePostmortem) }
}

export function decodeEarningsHistory(value: unknown): EarningsHistory {
  const root = object(value, 'history'); if (root.state === 'locked') return decodeLocked(root)
  exact(root, ['state', 'items', 'next_cursor'], 'history')
  return { state: enumeration(root.state, ['research'] as const, 'state'), items: list(root.items, 'history.items', 200, (item) => { const decoded = decodeEarningsDetail(item); if (decoded.state !== 'research') throw new EarningsDecodeError('历史项目不能为锁定状态。'); return decoded }), next_cursor: root.next_cursor === null ? null : string(root.next_cursor, 'next_cursor', 64, 20) }
}

export function decodeEarningsStatistics(value: unknown): EarningsStatistics {
  const root = object(value, 'statistics'); if (root.state === 'locked') return decodeLocked(root)
  exact(root, ['state', 'metrics'], 'statistics'); const metrics = object(root.metrics, 'metrics'); exact(metrics, ['sample_size', 'direction_accuracy', 'multiclass_brier_score', 'log_loss', 'expected_calibration_error', 'average_confidence_gap', 'interval_coverage', 'average_interval_width', 'overconfidence_rate', 'high_confidence_sample_size', 'paper_total_pnl', 'paper_max_drawdown'], 'metrics')
  return { state: enumeration(root.state, ['research'] as const, 'state'), metrics: { sample_size: integer(metrics.sample_size, 'sample_size', 0), direction_accuracy: finite(metrics.direction_accuracy, 'direction_accuracy', 0, 1), multiclass_brier_score: finite(metrics.multiclass_brier_score, 'multiclass_brier_score', 0), log_loss: finite(metrics.log_loss, 'log_loss', 0), expected_calibration_error: finite(metrics.expected_calibration_error, 'expected_calibration_error', 0, 1), average_confidence_gap: finite(metrics.average_confidence_gap, 'average_confidence_gap', -1, 1), interval_coverage: finite(metrics.interval_coverage, 'interval_coverage', 0, 1), average_interval_width: finite(metrics.average_interval_width, 'average_interval_width', 0), overconfidence_rate: finite(metrics.overconfidence_rate, 'overconfidence_rate', 0, 1), high_confidence_sample_size: integer(metrics.high_confidence_sample_size, 'high_confidence_sample_size', 0), paper_total_pnl: nullableNumber(metrics.paper_total_pnl, 'paper_total_pnl'), paper_max_drawdown: metrics.paper_max_drawdown === null ? null : finite(metrics.paper_max_drawdown, 'paper_max_drawdown', 0, 1) } }
}

function decodeCoverage(value: unknown, label: string): EarningsOneLegCoverage | null {
  if (value === null) return null
  const item = object(value, label); exact(item, ['covering_leg', 'required_terminal_price', 'probability', 'possible'], label)
  return { covering_leg: enumeration(item.covering_leg, ['CALL', 'PUT'] as const, 'covering_leg'), required_terminal_price: finite(item.required_terminal_price, 'required_terminal_price', 0), probability: finite(item.probability, 'probability', 0, 1), possible: boolean(item.possible, 'possible') }
}

function decodeOptionLeg(value: unknown, index: number): EarningsOptionLeg {
  const item = object(value, `legs[${index}]`); exact(item, ['contract_id', 'right', 'strike', 'expiry', 'quantity', 'multiplier', 'bid', 'ask', 'implied_volatility', 'delta', 'gamma', 'theta', 'vega', 'volume', 'open_interest', 'quote_at', 'available_at'], `legs[${index}]`)
  const expiry = string(item.expiry, 'expiry', 10, 10); if (!DATE.test(expiry) || !Number.isFinite(Date.parse(`${expiry}T00:00:00Z`))) throw new EarningsDecodeError('expiry 无效。')
  const bid = finite(item.bid, 'bid', 0), ask = finite(item.ask, 'ask', 0.000001), quoteAt = timestamp(item.quote_at, 'quote_at'), availableAt = timestamp(item.available_at, 'available_at')
  if (ask < bid || Date.parse(quoteAt) > Date.parse(availableAt)) throw new EarningsDecodeError('期权报价合同不一致。')
  return { contract_id: string(item.contract_id, 'contract_id', 128), right: enumeration(item.right, ['CALL', 'PUT'] as const, 'right'), strike: finite(item.strike, 'strike', 0.000001), expiry, quantity: integer(item.quantity, 'quantity', 1, 100), multiplier: integer(item.multiplier, 'multiplier', 1, 100_000), bid, ask, implied_volatility: finite(item.implied_volatility, 'implied_volatility', 0.000001), delta: finite(item.delta, 'delta'), gamma: finite(item.gamma, 'gamma'), theta: finite(item.theta, 'theta'), vega: finite(item.vega, 'vega'), volume: integer(item.volume, 'volume', 1), open_interest: integer(item.open_interest, 'open_interest', 1), quote_at: quoteAt, available_at: availableAt }
}

function decodeOptionAction(value: unknown): EarningsOptionActionContract {
  const item = object(value, 'option.action_contract'); exact(item, ['structure', 'entry', 'stop', 'targets', 'max_loss', 'max_account_pct', 'breakeven', 'invalidation', 'exit', 'roll', 'quote_at', 'model_artifact_sha256', 'evidence_manifest_sha256', 'execution_eligible', 'automatic_ordering'], 'option.action_contract')
  const entry = object(item.entry, 'entry'); exact(entry, ['order_type', 'legs'], 'entry'); const breakeven = object(item.breakeven, 'breakeven'); exact(breakeven, ['lower', 'upper'], 'breakeven')
  if (item.stop !== null || item.max_account_pct !== null || !Array.isArray(item.targets) || item.targets.length !== 0) throw new EarningsDecodeError('期权研究行动合同包含执行字段。')
  const structures = ['LONG_CALL', 'LONG_PUT', 'LONG_STRADDLE', 'LONG_STRANGLE'] as const
  const actionLegs = list(entry.legs, 'entry.legs', 2, (raw, index) => { const leg = object(raw, `entry.legs[${index}]`); exact(leg, ['contract_id', 'right', 'strike', 'expiry', 'quantity', 'multiplier', 'limit_price'], `entry.legs[${index}]`); return { contract_id: string(leg.contract_id, 'contract_id', 128), right: enumeration(leg.right, ['CALL', 'PUT'] as const, 'right'), strike: finite(leg.strike, 'strike', 0.000001), expiry: string(leg.expiry, 'expiry', 10, 10), quantity: integer(leg.quantity, 'quantity', 1, 100), multiplier: integer(leg.multiplier, 'multiplier', 1), limit_price: finite(leg.limit_price, 'limit_price', 0.000001) } })
  return { structure: enumeration(item.structure, structures, 'structure'), entry: { order_type: enumeration(entry.order_type, ['LIMIT_RESEARCH_ONLY'] as const, 'order_type'), legs: actionLegs }, stop: null, targets: [], max_loss: finite(item.max_loss, 'max_loss', 0.000001), max_account_pct: null, breakeven: { lower: nullableNumber(breakeven.lower, 'breakeven.lower'), upper: nullableNumber(breakeven.upper, 'breakeven.upper') }, invalidation: string(item.invalidation, 'invalidation'), exit: string(item.exit, 'exit'), roll: string(item.roll, 'roll'), quote_at: timestamp(item.quote_at, 'quote_at'), model_artifact_sha256: hash(item.model_artifact_sha256, 'model_artifact_sha256'), evidence_manifest_sha256: hash(item.evidence_manifest_sha256, 'evidence_manifest_sha256'), execution_eligible: fixedBoolean(item.execution_eligible, false, 'execution_eligible'), automatic_ordering: fixedBoolean(item.automatic_ordering, false, 'automatic_ordering') }
}

export function decodeEarningsOptionDetail(value: unknown): EarningsOptionDetail {
  const root = object(value, 'option')
  if (root.state === 'locked') {
    if (root.feature === 'earnings_forecast') return decodeLocked(root)
    exact(root, ['state', 'feature', 'required_capability', 'reason_code', 'upgrade_path'], 'option.locked')
    return { state: 'locked', feature: enumeration(root.feature, ['earnings_option_research'] as const, 'feature'), required_capability: enumeration(root.required_capability, ['earnings_option_defined_risk'] as const, 'required_capability'), reason_code: enumeration(root.reason_code, ['legacy_entitlement_required'] as const, 'reason_code'), upgrade_path: root.upgrade_path === null ? null : enumeration(root.upgrade_path, ['/membership'] as const, 'upgrade_path') }
  }
  exact(root, ['state', 'structure_type', 'evidence_mode', 'historical_oos_validated', 'research_only', 'execution_eligible', 'automatic_ordering', 'legs', 'total_premium', 'commission_cost', 'spread_cost', 'slippage_cost', 'max_loss', 'lower_breakeven', 'upper_breakeven', 'required_move_pct', 'model_expected_move_pct', 'iv_implied_move_pct', 'probability_outside_breakeven', 'expected_value_net_costs', 'call_zero_coverage', 'put_zero_coverage', 'terminal_sample_size', 'iv_crush_scenarios', 'decision_at', 'action_contract'], 'option')
  const structures = ['LONG_CALL', 'LONG_PUT', 'LONG_STRADDLE', 'LONG_STRANGLE'] as const
  const structure = enumeration(root.structure_type, structures, 'structure_type'), legs = list(root.legs, 'legs', 2, decodeOptionLeg), maxLoss = finite(root.max_loss, 'max_loss', 0.000001), lower = nullableNumber(root.lower_breakeven, 'lower_breakeven'), upper = nullableNumber(root.upper_breakeven, 'upper_breakeven'), decisionAt = timestamp(root.decision_at, 'decision_at'), action = decodeOptionAction(root.action_contract)
  const scenarios = list(root.iv_crush_scenarios, 'iv_crush_scenarios', 20, (raw, index) => { const item = object(raw, `iv_crush[${index}]`); exact(item, ['relative_iv_change_pct', 'estimated_structure_value', 'estimated_pnl_after_costs', 'method', 'spot_held_constant', 'time_decay_excluded'], `iv_crush[${index}]`); return { relative_iv_change_pct: finite(item.relative_iv_change_pct, 'relative_iv_change_pct', -100, 0), estimated_structure_value: finite(item.estimated_structure_value, 'estimated_structure_value', 0), estimated_pnl_after_costs: finite(item.estimated_pnl_after_costs, 'estimated_pnl_after_costs'), method: enumeration(item.method, ['first_order_vega_current_snapshot_estimate'] as const, 'method'), spot_held_constant: fixedBoolean(item.spot_held_constant, true, 'spot_held_constant'), time_decay_excluded: fixedBoolean(item.time_decay_excluded, true, 'time_decay_excluded') } })
  const calls = legs.filter((leg) => leg.right === 'CALL'), puts = legs.filter((leg) => leg.right === 'PUT'), expectedLegs = structure === 'LONG_CALL' || structure === 'LONG_PUT' ? 1 : 2
  if (legs.length !== expectedLegs || (structure === 'LONG_CALL' && calls.length !== 1) || (structure === 'LONG_PUT' && puts.length !== 1) || ((structure === 'LONG_STRADDLE' || structure === 'LONG_STRANGLE') && (calls.length !== 1 || puts.length !== 1)) || (structure === 'LONG_STRADDLE' && calls[0]?.strike !== puts[0]?.strike) || (structure === 'LONG_STRANGLE' && !(puts[0]!.strike < calls[0]!.strike)) || legs.some((leg) => Date.parse(`${leg.expiry}T00:00:00Z`) <= Date.parse(decisionAt) || Date.parse(leg.available_at) > Date.parse(decisionAt)) || !scenarios.length) throw new EarningsDecodeError('期权结构合同不一致。')
  if (action.structure !== structure || Math.abs(action.max_loss - maxLoss) > 1e-9 || action.breakeven.lower !== lower || action.breakeven.upper !== upper || action.entry.legs.length !== legs.length || action.entry.legs.some((entry, index) => { const leg = legs[index]; return !leg || entry.contract_id !== leg.contract_id || entry.right !== leg.right || entry.strike !== leg.strike || entry.expiry !== leg.expiry || entry.quantity !== leg.quantity || entry.multiplier !== leg.multiplier || entry.limit_price !== leg.ask })) throw new EarningsDecodeError('期权行动合同与结构不一致。')
  return { state: enumeration(root.state, ['research'] as const, 'state'), structure_type: structure, evidence_mode: enumeration(root.evidence_mode, ['current_snapshot_research_estimate'] as const, 'evidence_mode'), historical_oos_validated: fixedBoolean(root.historical_oos_validated, false, 'historical_oos_validated'), research_only: fixedBoolean(root.research_only, true, 'research_only'), execution_eligible: fixedBoolean(root.execution_eligible, false, 'execution_eligible'), automatic_ordering: fixedBoolean(root.automatic_ordering, false, 'automatic_ordering'), legs, total_premium: finite(root.total_premium, 'total_premium', 0), commission_cost: finite(root.commission_cost, 'commission_cost', 0), spread_cost: finite(root.spread_cost, 'spread_cost', 0), slippage_cost: finite(root.slippage_cost, 'slippage_cost', 0), max_loss: maxLoss, lower_breakeven: lower, upper_breakeven: upper, required_move_pct: finite(root.required_move_pct, 'required_move_pct', 0), model_expected_move_pct: finite(root.model_expected_move_pct, 'model_expected_move_pct', 0), iv_implied_move_pct: finite(root.iv_implied_move_pct, 'iv_implied_move_pct', 0), probability_outside_breakeven: finite(root.probability_outside_breakeven, 'probability_outside_breakeven', 0, 1), expected_value_net_costs: finite(root.expected_value_net_costs, 'expected_value_net_costs'), call_zero_coverage: decodeCoverage(root.call_zero_coverage, 'call_zero_coverage'), put_zero_coverage: decodeCoverage(root.put_zero_coverage, 'put_zero_coverage'), terminal_sample_size: integer(root.terminal_sample_size, 'terminal_sample_size', 100), iv_crush_scenarios: scenarios, decision_at: decisionAt, action_contract: action }
}

export function dominantEarningsDirection(forecast: Pick<EarningsForecastSnapshot, 'p_up' | 'p_down' | 'p_flat'>): EarningsDirection {
  const maximum = Math.max(forecast.p_up, forecast.p_down, forecast.p_flat)
  if (forecast.p_flat === maximum) return 'flat'
  return forecast.p_up === maximum ? 'up' : 'down'
}
