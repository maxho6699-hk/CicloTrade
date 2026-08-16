import { authenticatedJsonRequest, authenticatedStreamRequest, BrowserApiError } from './client.ts'

const BASE = '/api/rewrite/v1/signal-imports'
const MAX_BYTES = 256 * 1024
const SHA256 = /^[0-9a-f]{64}$/

export class LabCsvImportApiError extends Error {
  status: number

  constructor(message: string, status = 400) {
    super(message)
    this.name = 'LabCsvImportApiError'
    this.status = status
  }
}

export interface LabCsvImportQuota {
  used: number
  limit: number | null
  remaining: number | null
}

export interface LabCsvImportReadiness {
  capability: 'csv_import'
  allowed: true
  quota: LabCsvImportQuota
  limits: { max_bytes: number; max_rows: number }
  safety_boundary: {
    scope: 'research_history_only'
    creates_orders: false
    triggers_telegram: false
    touches_official_or_live: false
  }
}

export interface LabCsvImportJob {
  public_id: string
  import_type: 'csv'
  filename: string | null
  status: string
  row_count: number
  error_message: string | null
  created_at: string | null
  completed_at: string | null
  source_sha256: string | null
  request_sha256: string | null
  provenance_sha256: string | null
  replayed: boolean
  safety_boundary: LabCsvImportReadiness['safety_boundary']
  signal_count?: number
}

export interface LabCsvImportSignal {
  signal_id: string
  symbol: string
  action: string
  quantity: number | string | null
  price: number | string | null
  timestamp: string
  strategy: string | null
  confidence: number | string | null
  disclaimer: string | null
}

type JsonTransport = (path: string, init?: RequestInit) => Promise<unknown>
type BinaryTransport = (path: string, init?: RequestInit) => Promise<Response>

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]) {
  const actual = Object.keys(value)
  return actual.length === keys.length && actual.every((key) => keys.includes(key))
}

function safeText(value: unknown, max = 512): value is string {
  return typeof value === 'string' && value.length <= max
}

function safeCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

function decodeBoundary(value: unknown): LabCsvImportReadiness['safety_boundary'] {
  if (!record(value) || !exactKeys(value, ['scope', 'creates_orders', 'triggers_telegram', 'touches_official_or_live'])
    || value.scope !== 'research_history_only' || value.creates_orders !== false || value.triggers_telegram !== false || value.touches_official_or_live !== false) {
    throw new LabCsvImportApiError('CSV 股票记录导入边界响应格式无效。', 502)
  }
  return value as LabCsvImportReadiness['safety_boundary']
}

function decodeQuota(value: unknown): LabCsvImportQuota {
  if (!record(value) || !exactKeys(value, ['used', 'limit', 'remaining']) || !safeCount(value.used)
    || !(value.limit === null || safeCount(value.limit)) || !(value.remaining === null || safeCount(value.remaining))) {
    throw new LabCsvImportApiError('CSV 导入额度响应格式无效。', 502)
  }
  return value as unknown as LabCsvImportQuota
}

export function decodeLabCsvImportReadiness(value: unknown): LabCsvImportReadiness {
  if (!record(value) || !exactKeys(value, ['capability', 'allowed', 'quota', 'limits', 'safety_boundary'])
    || value.capability !== 'csv_import' || value.allowed !== true || !record(value.limits)
    || !exactKeys(value.limits, ['max_bytes', 'max_rows']) || value.limits.max_bytes !== MAX_BYTES || !safeCount(value.limits.max_rows) || value.limits.max_rows < 1) {
    throw new LabCsvImportApiError('CSV 导入准备状态响应格式无效。', 502)
  }
  return {
    capability: 'csv_import',
    allowed: true,
    quota: decodeQuota(value.quota),
    limits: value.limits as LabCsvImportReadiness['limits'],
    safety_boundary: decodeBoundary(value.safety_boundary),
  }
}

function decodeJob(value: unknown): LabCsvImportJob {
  const hasSignalCount = record(value) && Object.prototype.hasOwnProperty.call(value, 'signal_count')
  const keys = ['public_id', 'import_type', 'filename', 'status', 'row_count', 'error_message', 'created_at', 'completed_at', 'source_sha256', 'request_sha256', 'provenance_sha256', 'replayed', 'safety_boundary']
  if (!record(value) || (!exactKeys(value, keys) && !(hasSignalCount && exactKeys(value, [...keys, 'signal_count'])))
    || typeof value.public_id !== 'string' || !/^sigjob_[A-Za-z0-9_-]{8,80}$/.test(value.public_id)
    || value.import_type !== 'csv' || !(value.filename === null || safeText(value.filename, 160)) || !safeText(value.status, 64)
    || !safeCount(value.row_count) || !(value.error_message === null || safeText(value.error_message))
    || !(value.created_at === null || safeText(value.created_at, 64)) || !(value.completed_at === null || safeText(value.completed_at, 64))
    || !(value.source_sha256 === null || (typeof value.source_sha256 === 'string' && SHA256.test(value.source_sha256)))
    || !(value.request_sha256 === null || (typeof value.request_sha256 === 'string' && SHA256.test(value.request_sha256)))
    || !(value.provenance_sha256 === null || (typeof value.provenance_sha256 === 'string' && SHA256.test(value.provenance_sha256)))
    || typeof value.replayed !== 'boolean' || (hasSignalCount && !safeCount(value.signal_count))) {
    throw new LabCsvImportApiError('CSV 导入记录响应格式无效。', 502)
  }
  return { ...value, safety_boundary: decodeBoundary(value.safety_boundary) } as LabCsvImportJob
}

function decodeSignal(value: unknown): LabCsvImportSignal {
  if (!record(value) || !exactKeys(value, ['signal_id', 'symbol', 'action', 'quantity', 'price', 'timestamp', 'strategy', 'confidence', 'disclaimer'])
    || !safeText(value.signal_id, 160) || !safeText(value.symbol, 32) || !safeText(value.action, 64) || !(typeof value.quantity === 'number' || typeof value.quantity === 'string' || value.quantity === null)
    || !(typeof value.price === 'number' || typeof value.price === 'string' || value.price === null) || !safeText(value.timestamp, 64)
    || !(value.strategy === null || safeText(value.strategy, 160)) || !(typeof value.confidence === 'number' || typeof value.confidence === 'string' || value.confidence === null)
    || !(value.disclaimer === null || safeText(value.disclaimer, 512))) {
    throw new LabCsvImportApiError('CSV 股票记录响应格式无效。', 502)
  }
  return value as unknown as LabCsvImportSignal
}

export function decodeLabCsvImportJobs(value: unknown): LabCsvImportJob[] {
  if (!record(value) || !exactKeys(value, ['items']) || !Array.isArray(value.items) || value.items.length > 100) throw new LabCsvImportApiError('CSV 导入历史响应格式无效。', 502)
  return value.items.map(decodeJob)
}

export function decodeLabCsvImportSignals(value: unknown): LabCsvImportSignal[] {
  if (!record(value) || !exactKeys(value, ['items']) || !Array.isArray(value.items) || value.items.length > 500) throw new LabCsvImportApiError('CSV 股票记录详情响应格式无效。', 502)
  return value.items.map(decodeSignal)
}

async function jsonTransport(path: string, init?: RequestInit) {
  try {
    return await authenticatedJsonRequest<unknown>(path, init)
  } catch (error) {
    if (error instanceof BrowserApiError) throw new LabCsvImportApiError(error.message.slice(0, 300), error.status)
    throw error
  }
}

async function binaryTransport(path: string, init?: RequestInit) {
  try {
    return await authenticatedStreamRequest(path, init)
  } catch (error) {
    if (error instanceof BrowserApiError) throw new LabCsvImportApiError(error.message.slice(0, 300), error.status)
    throw error
  }
}

function idempotencyKey() {
  return typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `csv-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export async function fetchLabCsvImportReadiness(transport: JsonTransport = jsonTransport) {
  return decodeLabCsvImportReadiness(await transport(`${BASE}/readiness`, { method: 'GET', cache: 'no-store' }))
}

export async function listLabCsvImports(transport: JsonTransport = jsonTransport) {
  return decodeLabCsvImportJobs(await transport(BASE, { method: 'GET', cache: 'no-store' }))
}

export async function uploadLabCsvImport(file: File, key = idempotencyKey(), transport: JsonTransport = jsonTransport) {
  if (typeof File === 'undefined' || !(file instanceof File) || file.size < 1 || file.size > MAX_BYTES) throw new LabCsvImportApiError('CSV 文件必须介于 1 byte 与 256 KB。', 413)
  if (!file.name || file.name.length > 160 || !/\.csv$/i.test(file.name)) throw new LabCsvImportApiError('请选择 CSV 文件。', 400)
  if (!/^[A-Za-z0-9._:-]{8,128}$/.test(key)) throw new LabCsvImportApiError('CSV 导入幂等请求字段无效。', 400)
  const body = new FormData()
  body.append('file', file, file.name)
  return decodeJob(await transport(BASE, { method: 'POST', headers: { 'Idempotency-Key': key }, body }))
}

export async function fetchLabCsvImport(publicId: string, transport: JsonTransport = jsonTransport) {
  if (!/^sigjob_[A-Za-z0-9_-]{8,80}$/.test(publicId)) throw new LabCsvImportApiError('CSV 导入记录编号无效。', 400)
  return decodeJob(await transport(`${BASE}/${encodeURIComponent(publicId)}`, { method: 'GET', cache: 'no-store' }))
}

export async function fetchLabCsvImportSignals(publicId: string, transport: JsonTransport = jsonTransport) {
  if (!/^sigjob_[A-Za-z0-9_-]{8,80}$/.test(publicId)) throw new LabCsvImportApiError('CSV 导入记录编号无效。', 400)
  return decodeLabCsvImportSignals(await transport(`${BASE}/${encodeURIComponent(publicId)}/signals`, { method: 'GET', cache: 'no-store' }))
}

export async function downloadLabCsvImportCsv(publicId?: string, transport: BinaryTransport = binaryTransport) {
  if (publicId !== undefined && !/^sigjob_[A-Za-z0-9_-]{8,80}$/.test(publicId)) throw new LabCsvImportApiError('CSV 导入记录编号无效。', 400)
  const path = publicId ? `${BASE}/${encodeURIComponent(publicId)}/export.csv` : `${BASE}/export.csv`
  const response = await transport(path, { method: 'GET', cache: 'no-store' })
  const contentType = response.headers.get('Content-Type')?.split(';', 1)[0]
  if (contentType !== 'text/csv') throw new LabCsvImportApiError('CSV 导出响应格式无效。', 502)
  return response.blob()
}

export const labCsvImportApi = {
  readiness: fetchLabCsvImportReadiness,
  list: listLabCsvImports,
  upload: uploadLabCsvImport,
  detail: fetchLabCsvImport,
  signals: fetchLabCsvImportSignals,
  exportCsv: downloadLabCsvImportCsv,
}

export const fetchCsvImportReadiness = fetchLabCsvImportReadiness
export const listCsvImports = listLabCsvImports
export const uploadCsvImport = uploadLabCsvImport
export const getCsvImport = fetchLabCsvImport
export const listCsvImportSignals = fetchLabCsvImportSignals
export const exportCsvImport = downloadLabCsvImportCsv

export { MAX_BYTES as LAB_CSV_IMPORT_MAX_BYTES }
