import { authenticatedJsonRequest } from './client.ts'

export interface LabStressScenario {
  key: string
  label: string
  price_shock_pct: number
  volatility_shock_pct: number
  gap_risk: string
}

export interface LabStressCatalog {
  method_version: string
  catalog_sha256: string
  fee_bps: number
  slippage_bps: number
  scenarios: LabStressScenario[]
}

export interface LabStressPosition {
  symbol: string
  instrument_type?: 'stock'
  currency: 'USD' | 'HKD' | 'CNY'
  quantity: number
  last_trade_price: number
}

export interface LabStressSnapshot {
  account_mode: 'official' | 'personal_paper'
  currency: LabStressPosition['currency']
  as_of: string
  data_status: 'fresh' | 'recorded'
  positions: LabStressPosition[]
}

export interface LabStressResult {
  method_version: string
  scenario: { key: string; label: string; price_shock_pct: number; volatility_shock_pct: number; fee_bps: number; slippage_bps: number }
  account_mode: string
  currency: string
  as_of: string
  evaluated_at: string
  data_status: string
  input_sha256: string
  result_sha256: string
  baseline_value: number
  stressed_value: number
  pnl_change: number
  positions: Array<{ symbol: string; stressed_value: number; cost: number }>
  is_prediction: false
  execution_eligible: false
}

type LabStressTransport = (path: string, init?: RequestInit) => Promise<unknown>
function record(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === 'object' && !Array.isArray(value) }
function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean { const actual = Object.keys(value); return actual.length === keys.length && actual.every((key) => keys.includes(key)) }
function safeInteger(value: unknown): value is number { return typeof value === 'number' && Number.isSafeInteger(value) }
function sha256(value: string): Promise<string> {
  return crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)).then((digest) => Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join(''))
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (record(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
  return JSON.stringify(value)
}

export function decodeLabStressCatalog(value: unknown): LabStressCatalog {
  if (!record(value) || !exactKeys(value, ['method_version', 'catalog_sha256', 'fee_bps', 'slippage_bps', 'scenarios']) || typeof value.method_version !== 'string' || value.method_version.length < 1 || value.method_version.length > 64 || typeof value.catalog_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(value.catalog_sha256) || !safeInteger(value.fee_bps) || value.fee_bps < 0 || value.fee_bps > 10_000 || !safeInteger(value.slippage_bps) || value.slippage_bps < 0 || value.slippage_bps > 10_000 || !Array.isArray(value.scenarios) || !value.scenarios.length) throw new Error('压力场景目录响应格式无效。')
  if (!value.scenarios.every((item) => record(item) && exactKeys(item, ['key', 'label', 'price_shock_pct', 'volatility_shock_pct', 'gap_risk']) && typeof item.key === 'string' && /^[a-z][a-z0-9_]{2,63}$/.test(item.key) && typeof item.label === 'string' && item.label.length > 0 && item.label.length <= 128 && safeInteger(item.price_shock_pct) && item.price_shock_pct >= -100 && item.price_shock_pct <= 100 && safeInteger(item.volatility_shock_pct) && item.volatility_shock_pct >= 0 && item.volatility_shock_pct <= 1_000 && typeof item.gap_risk === 'string' && item.gap_risk.length > 0 && item.gap_risk.length <= 32)) throw new Error('压力场景字段无效。')
  const scenarios = value.scenarios as LabStressScenario[]
  const keys = scenarios.map((item) => item.key)
  if (new Set(keys).size !== keys.length) throw new Error('压力场景目录包含重复 key。')
  return value as unknown as LabStressCatalog
}

export async function fetchLabStressCatalog(transport: LabStressTransport = authenticatedJsonRequest): Promise<LabStressCatalog> {
  const catalog = decodeLabStressCatalog(await transport('/api/rewrite/v1/lab/stress/catalog', { cache: 'no-store' }))
  const { catalog_sha256: expected, ...content } = catalog
  if (await sha256(canonical(content)) !== expected) throw new Error('压力场景目录校验失败。')
  return catalog
}

export function runLabStress(scenarioKey: string, transport: LabStressTransport = authenticatedJsonRequest): Promise<LabStressResult> {
  return transport('/api/rewrite/v1/lab/stress', {
    method: 'POST',
    body: JSON.stringify({ scenario_key: scenarioKey }),
  }) as Promise<LabStressResult>
}
