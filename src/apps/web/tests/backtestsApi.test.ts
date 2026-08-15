import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import test from 'node:test'

import {
  BacktestApiError,
  classifyBacktestError,
  createBacktestApi,
  decodeBacktestList,
  type BacktestCreateRequest,
} from '../src/api/backtests.ts'

const inputHash = 'a'.repeat(64)
const codeHash = 'b'.repeat(64)
const manifestHash = 'c'.repeat(64)

const manifest = {
  schema_version: 1,
  evaluation_date: '2026-08-15',
  dataset_end: '2026-08-14',
  code_bundle_sha256: codeHash,
  inputs: [{ artifact_key: 'prices.csv', sha256: inputHash, dataset_end: '2026-08-14' }],
  experiment_budget: { runs: 1, folds: 1 },
  parameters: { symbol: 'AAPL', lookback_years: 1 },
} as const

function job(status = 'completed', extra: Record<string, unknown> = {}) {
  const base = {
    id: '1234567890abcdef1234567890abcdef',
    job_type: 'backtest.run.v1',
    status,
    manifest,
    attempt_count: status === 'queued' ? 0 : 1,
    max_attempts: 3,
    progress: status === 'completed' ? 1 : 0,
    progress_stage: status === 'completed' ? 'finalizing' : 'queued',
    cancel_requested: false,
    created_at: '2026-08-15T08:00:00Z',
    updated_at: '2026-08-15T08:01:00Z',
    completed_at: status === 'completed' ? '2026-08-15T08:01:00Z' : null,
  }
  return { ...base, ...extra }
}

function completedJob(outputHash = 'd'.repeat(64)) {
  return job('completed', {
    result: {
      job_id: '1234567890abcdef1234567890abcdef',
      manifest_sha256: manifestHash,
      fencing_epoch: 7,
      input_hashes: { 'prices.csv': inputHash },
      output_hashes: { 'report.json': outputHash },
      evidence: {
        kind: 'research',
        metrics: { total_return_pct: 4.2, max_drawdown_pct: -2.1 },
        limitations: ['只供研究，不构成交易指令。'],
      },
      code_bundle_sha256: codeHash,
    },
  })
}

test('strict decoder maps backend lifecycle and exposes only verified public artifacts', () => {
  const [decoded] = decodeBacktestList({ items: [completedJob()] })
  assert.equal(decoded.status, 'succeeded')
  assert.equal(decoded.manifestSha256, manifestHash)
  assert.deepEqual(decoded.artifacts, [{ artifactKey: 'report.json', sha256: 'd'.repeat(64), verified: true }])
  assert.deepEqual(decoded.evidence?.metrics, { total_return_pct: 4.2, max_drawdown_pct: -2.1 })
  assert.equal('fencing_epoch' in decoded, false)

  assert.equal(decodeBacktestList({ items: [job('preparing')] })[0].status, 'queued')
  assert.equal(decodeBacktestList({ items: [job('superseded')] })[0].status, 'blocked')
})

test('decoder fails closed on internal fields, unsafe statuses, or unverified result bindings', () => {
  assert.throws(() => decodeBacktestList({ items: [{ ...job('queued'), worker_id: 'internal-worker' }] }), BacktestApiError)
  assert.throws(() => decodeBacktestList({ items: [job('leased')] }), BacktestApiError)
  assert.throws(() => decodeBacktestList({ items: [completedJob('not-a-hash')] }), BacktestApiError)
  assert.throws(() => decodeBacktestList({ items: [job('completed')] }), BacktestApiError)
})

test('client uses exact list, detail, create, and cancel contracts', async () => {
  const calls: Array<{ path: string; init?: RequestInit }> = []
  const api = createBacktestApi(async (path, init) => {
    calls.push({ path, init })
    if (init?.method === 'POST' && path.endsWith('/cancel')) return job('running', { progress: 0.4, progress_stage: 'executing', cancel_requested: true })
    if (init?.method === 'POST') return { created: true, job: job('queued') }
    if (path === '/api/rewrite/v1/backtests') return { items: [job('queued')] }
    return job('queued')
  })
  const request: BacktestCreateRequest = { type: 'backtest.run.v1', manifest: structuredClone(manifest) }
  await api.listJobs()
  await api.getJob('1234567890abcdef1234567890abcdef')
  await api.createJob(request, 'stable-key-1234')
  const cancelled = await api.cancelJob('1234567890abcdef1234567890abcdef')
  assert.equal(cancelled.cancelRequested, true)
  assert.deepEqual(calls.map((call) => [call.path, call.init?.method]), [
    ['/api/rewrite/v1/backtests', 'GET'],
    ['/api/rewrite/v1/backtests/1234567890abcdef1234567890abcdef', 'GET'],
    ['/api/rewrite/v1/backtests', 'POST'],
    ['/api/rewrite/v1/backtests/1234567890abcdef1234567890abcdef/cancel', 'POST'],
  ])
  assert.equal(new Headers(calls[2].init?.headers).get('Idempotency-Key'), 'stable-key-1234')
  assert.deepEqual(JSON.parse(String(calls[2].init?.body)), request)
})

test('artifact download verifies response headers and bytes against the result hash', async () => {
  const body = new TextEncoder().encode('{"verified":true}')
  const hash = createHash('sha256').update(body).digest('hex')
  const artifact = { artifactKey: 'report.json', sha256: hash, verified: true as const }
  const api = createBacktestApi(async () => ({ items: [] }), async () => new Response(body, {
    headers: {
      'Content-Type': 'application/octet-stream',
      'Content-Disposition': 'attachment; filename="report.json"',
      ETag: hash,
    },
  }))
  const blob = await api.downloadArtifact('1234567890abcdef1234567890abcdef', artifact)
  assert.equal(blob.size, body.byteLength)

  const unsafe = createBacktestApi(async () => ({ items: [] }), async () => new Response(body, {
    headers: {
      'Content-Type': 'application/octet-stream',
      'Content-Disposition': 'attachment; filename="report.json"',
      ETag: '0'.repeat(64),
    },
  }))
  await assert.rejects(() => unsafe.downloadArtifact('1234567890abcdef1234567890abcdef', artifact), BacktestApiError)
})

test('HTTP status classification distinguishes disabled list from missing owned resources', () => {
  assert.equal(classifyBacktestError(404, 'list'), 'locked')
  assert.equal(classifyBacktestError(404, 'item'), 'missing')
  assert.equal(classifyBacktestError(401, 'list'), 'unauthorized')
  assert.equal(classifyBacktestError(403, 'write'), 'forbidden')
  assert.equal(classifyBacktestError(409, 'write'), 'conflict')
  assert.equal(classifyBacktestError(429, 'write'), 'limited')
})

test('browser transport preserves the feature-disabled 404 for the locked UI state', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify({
    code: 'backtest_request_failed',
    error: '回测服务暂时无法处理请求。',
    correlation_id: 'safe-correlation-id',
  }), { status: 404, headers: { 'Content-Type': 'application/json' } })
  try {
    await assert.rejects(
      () => createBacktestApi().listJobs(),
      (error: unknown) => error instanceof BacktestApiError
        && error.status === 404
        && classifyBacktestError(error.status, 'list') === 'locked',
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})
