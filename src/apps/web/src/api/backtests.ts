import {
  authenticatedJsonRequest,
  authenticatedStreamRequest,
  BrowserApiError,
} from './client.ts'

const BASE = '/api/rewrite/v1/backtests'
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/
const SAFE_ARTIFACT = /^(?!\.)(?!.*\.\.)(?!.*[\\/])[A-Za-z0-9._-]{1,128}$/
const SHA256 = /^[0-9a-f]{64}$/
const DATE = /^\d{4}-\d{2}-\d{2}$/

type JsonScalar = string | number | boolean | null

export type BacktestStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'blocked'
export type BacktestProgressStage = 'queued' | 'loading' | 'executing' | 'finalizing'

export interface BacktestInput {
  artifact_key: string
  sha256: string
  dataset_end: string
  bytes?: number
  rows?: number
}

export interface BacktestManifest {
  schema_version: 1
  evaluation_date: string
  dataset_end: string
  code_bundle_sha256: string
  inputs: BacktestInput[]
  experiment_budget: Partial<Record<'runs' | 'candidates' | 'folds', number>>
  parameters?: Record<string, Exclude<JsonScalar, null>>
}

export interface BacktestCreateRequest {
  type: 'backtest.run.v1' | 'backtest.optimize.v1'
  manifest: BacktestManifest
}

export interface BacktestArtifact {
  artifactKey: string
  sha256: string
  verified: true
}

export interface BacktestEvidence {
  kind: 'research' | 'shadow'
  metrics: Record<string, JsonScalar>
  limitations: string[]
}

export interface BacktestJob {
  id: string
  jobType: BacktestCreateRequest['type']
  status: BacktestStatus
  progress: number | null
  progressStage: BacktestProgressStage
  cancelRequested: boolean
  attemptCount: number
  maxAttempts: number
  createdAt: string
  updatedAt: string
  completedAt: string | null
  manifest: BacktestManifest
  manifestSha256: string | null
  evidence: BacktestEvidence | null
  artifacts: BacktestArtifact[]
}

export class BacktestApiError extends Error {
  status: number

  constructor(message: string, status = 0) {
    super(message)
    this.status = status
  }
}

export type BacktestErrorKind =
  | 'locked'
  | 'unauthorized'
  | 'forbidden'
  | 'missing'
  | 'conflict'
  | 'limited'
  | 'error'

export function classifyBacktestError(status: number, operation: 'list' | 'item' | 'write' | 'artifact'): BacktestErrorKind {
  if (status === 401) return 'unauthorized'
  if (status === 403) return 'forbidden'
  if (status === 404) return operation === 'list' ? 'locked' : 'missing'
  if (status === 409) return 'conflict'
  if (status === 429) return 'limited'
  return 'error'
}

export type BacktestTransport = (path: string, init?: RequestInit) => Promise<unknown>
export type BacktestBinaryTransport = (path: string, init?: RequestInit) => Promise<Response>

export interface BacktestApi {
  listJobs: (signal?: AbortSignal) => Promise<BacktestJob[]>
  getJob: (jobId: string, signal?: AbortSignal) => Promise<BacktestJob>
  createJob: (request: BacktestCreateRequest, idempotencyKey: string, signal?: AbortSignal) => Promise<{ created: boolean; job: BacktestJob }>
  cancelJob: (jobId: string, signal?: AbortSignal) => Promise<BacktestJob>
  downloadArtifact: (jobId: string, artifact: BacktestArtifact, signal?: AbortSignal) => Promise<Blob>
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]) {
  const keys = Object.keys(value)
  return keys.length === expected.length && keys.every((key) => expected.includes(key))
}

function safeInteger(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): value is number {
  return Number.isSafeInteger(value) && Number(value) >= minimum && Number(value) <= maximum
}

function safeNumber(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= minimum && value <= maximum
}

function timestamp(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 64 && Number.isFinite(Date.parse(value))
}

function safePath(value: string, label: string) {
  if (!SAFE_ID.test(value)) throw new BacktestApiError(`${label} 无效。`, 400)
  return encodeURIComponent(value)
}

function safeArtifact(value: string) {
  if (!SAFE_ARTIFACT.test(value)) throw new BacktestApiError('回测制品名称无效。', 400)
  return encodeURIComponent(value)
}

function decodeManifest(value: unknown): BacktestManifest {
  if (!isRecord(value)) throw new BacktestApiError('回测 manifest 响应格式无效。', 502)
  const allowed = [
    'schema_version', 'evaluation_date', 'dataset_end', 'code_bundle_sha256', 'inputs',
    'experiment_budget', 'parameters', 'candidate_id', 'candidate_version', 'provenance',
    'hypothesis', 'parent_version', 'parent_job_id', 'parent_manifest_sha256',
    'parent_result_sha256', 'template_key', 'asset_universe', 'search_space',
    'evidence_hashes', 'promotion_proposal', 'authority', 'risk_contract', 'validation_plan',
  ]
  if (Object.keys(value).some((key) => !allowed.includes(key))) {
    throw new BacktestApiError('回测 manifest 包含未知字段。', 502)
  }
  if (value.schema_version !== 1 || typeof value.evaluation_date !== 'string' || !DATE.test(value.evaluation_date)
    || typeof value.dataset_end !== 'string' || !DATE.test(value.dataset_end)
    || value.dataset_end > value.evaluation_date || typeof value.code_bundle_sha256 !== 'string'
    || !SHA256.test(value.code_bundle_sha256) || !Array.isArray(value.inputs)
    || value.inputs.length < 1 || value.inputs.length > 64 || !isRecord(value.experiment_budget)) {
    throw new BacktestApiError('回测 manifest 核心字段无效。', 502)
  }
  const datasetEnd = value.dataset_end
  const inputs = value.inputs.map((input) => {
    if (!isRecord(input) || Object.keys(input).some((key) => !['artifact_key', 'sha256', 'dataset_end', 'bytes', 'rows'].includes(key))
      || typeof input.artifact_key !== 'string' || !SAFE_ARTIFACT.test(input.artifact_key)
      || typeof input.sha256 !== 'string' || !SHA256.test(input.sha256)
      || typeof input.dataset_end !== 'string' || !DATE.test(input.dataset_end)
      || input.dataset_end > datasetEnd
      || (input.bytes !== undefined && !safeInteger(input.bytes))
      || (input.rows !== undefined && !safeInteger(input.rows))) {
      throw new BacktestApiError('回测冻结输入字段无效。', 502)
    }
    return input as unknown as BacktestInput
  })
  if (new Set(inputs.map((item) => item.artifact_key)).size !== inputs.length) {
    throw new BacktestApiError('回测冻结输入重复。', 502)
  }
  const budgetKeys = Object.keys(value.experiment_budget)
  if (budgetKeys.length < 1 || budgetKeys.some((key) => !['runs', 'candidates', 'folds'].includes(key))) {
    throw new BacktestApiError('回测预算字段无效。', 502)
  }
  const maximum = { runs: 64, candidates: 64, folds: 20 }
  for (const key of budgetKeys as Array<keyof typeof maximum>) {
    if (!safeInteger(value.experiment_budget[key], 1, maximum[key])) {
      throw new BacktestApiError('回测预算数值无效。', 502)
    }
  }
  let parameters: BacktestManifest['parameters']
  if (value.parameters !== undefined) {
    if (!isRecord(value.parameters) || Object.keys(value.parameters).length > 64) {
      throw new BacktestApiError('回测参数字段无效。', 502)
    }
    parameters = {}
    for (const [key, item] of Object.entries(value.parameters)) {
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(key)
        || /(?:code|script|command|module|import|exec|eval|url|path|file|shell)/i.test(key)
        || !['string', 'number', 'boolean'].includes(typeof item)
        || (typeof item === 'number' && !Number.isFinite(item))
        || (typeof item === 'string' && (item.length < 1 || item.length > 128))) {
        throw new BacktestApiError('回测参数内容无效。', 502)
      }
      parameters[key] = item as Exclude<JsonScalar, null>
    }
  }
  return {
    schema_version: 1,
    evaluation_date: value.evaluation_date,
    dataset_end: value.dataset_end,
    code_bundle_sha256: value.code_bundle_sha256,
    inputs,
    experiment_budget: value.experiment_budget as BacktestManifest['experiment_budget'],
    ...(parameters ? { parameters } : {}),
  }
}

function decodeEvidence(value: unknown): BacktestEvidence {
  if (!isRecord(value) || !['research', 'shadow'].includes(String(value.kind))) {
    throw new BacktestApiError('回测证据格式无效。', 502)
  }
  const metrics: Record<string, JsonScalar> = {}
  if (isRecord(value.metrics)) {
    for (const [key, item] of Object.entries(value.metrics)) {
      if (/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(key)
        && (item === null || ['string', 'number', 'boolean'].includes(typeof item))
        && !(typeof item === 'number' && !Number.isFinite(item))) {
        metrics[key] = item as JsonScalar
      }
    }
  }
  const limitations = Array.isArray(value.limitations)
    ? value.limitations.filter((item): item is string => typeof item === 'string' && item.length <= 500).slice(0, 50)
    : []
  return { kind: value.kind as BacktestEvidence['kind'], metrics, limitations }
}

function decodeJob(value: unknown): BacktestJob {
  if (!isRecord(value)) throw new BacktestApiError('回测任务响应格式无效。', 502)
  const baseKeys = [
    'id', 'job_type', 'status', 'manifest', 'attempt_count', 'max_attempts', 'progress',
    'progress_stage', 'cancel_requested', 'created_at', 'updated_at', 'completed_at',
  ]
  const expected = value.result === undefined ? baseKeys : [...baseKeys, 'result']
  if (!exactKeys(value, expected) || typeof value.id !== 'string' || !SAFE_ID.test(value.id)
    || !['backtest.run.v1', 'backtest.optimize.v1'].includes(String(value.job_type))
    || !safeInteger(value.attempt_count, 0, 1_000) || !safeInteger(value.max_attempts, 1, 1_000)
    || Number(value.attempt_count) > Number(value.max_attempts)
    || !safeNumber(value.progress, 0, 1)
    || !['queued', 'loading', 'executing', 'finalizing'].includes(String(value.progress_stage))
    || typeof value.cancel_requested !== 'boolean' || !timestamp(value.created_at)
    || !timestamp(value.updated_at) || (value.completed_at !== null && !timestamp(value.completed_at))) {
    throw new BacktestApiError('回测任务字段无效。', 502)
  }
  const statusMap: Record<string, BacktestStatus> = {
    queued: 'queued', preparing: 'queued', running: 'running', completed: 'succeeded',
    failed: 'failed', cancelled: 'cancelled', superseded: 'blocked',
  }
  const status = statusMap[String(value.status)]
  if (!status) throw new BacktestApiError('回测任务状态无效。', 502)
  const manifest = decodeManifest(value.manifest)
  let manifestSha256: string | null = null
  let evidence: BacktestEvidence | null = null
  let artifacts: BacktestArtifact[] = []
  if (value.result !== undefined) {
    if (!isRecord(value.result) || !exactKeys(value.result, [
      'job_id', 'manifest_sha256', 'fencing_epoch', 'input_hashes', 'output_hashes',
      'evidence', 'code_bundle_sha256',
    ]) || value.result.job_id !== value.id || typeof value.result.manifest_sha256 !== 'string'
      || !SHA256.test(value.result.manifest_sha256) || !safeInteger(value.result.fencing_epoch, 1)
      || !isRecord(value.result.input_hashes) || !isRecord(value.result.output_hashes)
      || value.result.code_bundle_sha256 !== manifest.code_bundle_sha256) {
      throw new BacktestApiError('回测结果绑定字段无效。', 502)
    }
    const result = value.result as {
      job_id: string
      manifest_sha256: string
      fencing_epoch: number
      input_hashes: Record<string, unknown>
      output_hashes: Record<string, unknown>
      evidence: unknown
      code_bundle_sha256: string
    }
    const expectedInputs = Object.fromEntries(manifest.inputs.map((item) => [item.artifact_key, item.sha256]))
    const inputKeys = Object.keys(result.input_hashes)
    if (inputKeys.length !== manifest.inputs.length
      || inputKeys.some((key) => result.input_hashes[key] !== expectedInputs[key])) {
      throw new BacktestApiError('回测结果输入哈希不匹配。', 502)
    }
    artifacts = Object.entries(result.output_hashes).map(([artifactKey, digest]) => {
      if (!SAFE_ARTIFACT.test(artifactKey) || typeof digest !== 'string' || !SHA256.test(digest)) {
        throw new BacktestApiError('回测制品哈希无效。', 502)
      }
      return { artifactKey, sha256: digest, verified: true as const }
    })
    if (status !== 'succeeded') throw new BacktestApiError('未完成任务不得包含成功制品。', 502)
    manifestSha256 = result.manifest_sha256
    evidence = decodeEvidence(result.evidence)
  } else if (status === 'succeeded') {
    throw new BacktestApiError('已完成任务缺少结果绑定。', 502)
  }
  return {
    id: value.id,
    jobType: value.job_type as BacktestJob['jobType'],
    status,
    progress: status === 'running' ? value.progress : null,
    progressStage: value.progress_stage as BacktestProgressStage,
    cancelRequested: value.cancel_requested,
    attemptCount: value.attempt_count,
    maxAttempts: value.max_attempts,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
    completedAt: value.completed_at,
    manifest,
    manifestSha256,
    evidence,
    artifacts,
  }
}

export function decodeBacktestList(value: unknown): BacktestJob[] {
  if (!isRecord(value) || !exactKeys(value, ['items']) || !Array.isArray(value.items) || value.items.length > 100) {
    throw new BacktestApiError('回测任务列表响应格式无效。', 502)
  }
  return value.items.map(decodeJob)
}

async function browserTransport(path: string, init?: RequestInit) {
  try {
    return await authenticatedJsonRequest<unknown>(path, init)
  } catch (error) {
    if (error instanceof BrowserApiError) throw new BacktestApiError(error.message.slice(0, 300), error.status)
    throw error
  }
}

async function browserBinaryTransport(path: string, init?: RequestInit) {
  try {
    return await authenticatedStreamRequest(path, init)
  } catch (error) {
    if (error instanceof BrowserApiError) throw new BacktestApiError(error.message.slice(0, 300), error.status)
    throw error
  }
}

async function sha256Blob(blob: Blob) {
  const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export function createBacktestApi(
  transport: BacktestTransport = browserTransport,
  binaryTransport: BacktestBinaryTransport = browserBinaryTransport,
): BacktestApi {
  return {
    async listJobs(signal) {
      return decodeBacktestList(await transport(BASE, { method: 'GET', cache: 'no-store', signal }))
    },
    async getJob(jobId, signal) {
      return decodeJob(await transport(`${BASE}/${safePath(jobId, '回测任务')}`, { method: 'GET', cache: 'no-store', signal }))
    },
    async createJob(request, idempotencyKey, signal) {
      if (!isRecord(request) || !exactKeys(request, ['type', 'manifest'])
        || !['backtest.run.v1', 'backtest.optimize.v1'].includes(request.type)
        || !/^[A-Za-z0-9._:-]{8,128}$/.test(idempotencyKey)) {
        throw new BacktestApiError('回测提交字段无效。', 400)
      }
      decodeManifest(request.manifest)
      const value = await transport(BASE, {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify(request),
        signal,
      })
      if (!isRecord(value) || !exactKeys(value, ['created', 'job']) || typeof value.created !== 'boolean') {
        throw new BacktestApiError('回测提交响应格式无效。', 502)
      }
      return { created: value.created, job: decodeJob(value.job) }
    },
    async cancelJob(jobId, signal) {
      return decodeJob(await transport(`${BASE}/${safePath(jobId, '回测任务')}/cancel`, {
        method: 'POST', signal,
      }))
    },
    async downloadArtifact(jobId, artifact, signal) {
      if (!SHA256.test(artifact.sha256) || artifact.verified !== true) {
        throw new BacktestApiError('回测制品证明无效。', 400)
      }
      const response = await binaryTransport(
        `${BASE}/${safePath(jobId, '回测任务')}/artifacts/${safeArtifact(artifact.artifactKey)}`,
        { method: 'GET', cache: 'no-store', signal },
      )
      const mediaType = response.headers.get('Content-Type')?.split(';', 1)[0]
      const etag = response.headers.get('ETag')?.replace(/^W\//, '').replaceAll('"', '')
      const disposition = response.headers.get('Content-Disposition') ?? ''
      if (mediaType !== 'application/octet-stream' || etag !== artifact.sha256
        || !disposition.includes(`filename="${artifact.artifactKey}"`)) {
        throw new BacktestApiError('回测制品下载证明不匹配。', 502)
      }
      const blob = await response.blob()
      if (await sha256Blob(blob) !== artifact.sha256) {
        throw new BacktestApiError('回测制品内容哈希不匹配。', 502)
      }
      return blob
    },
  }
}

export const backtestApi = createBacktestApi()
