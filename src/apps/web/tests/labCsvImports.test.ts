import assert from 'node:assert/strict'
import test from 'node:test'
import {
  decodeLabCsvImportReadiness,
  fetchLabCsvImport,
  fetchLabCsvImportSignals,
  uploadLabCsvImport,
} from '../src/api/labCsvImports.ts'

const boundary = { scope: 'research_history_only', creates_orders: false, triggers_telegram: false, touches_official_or_live: false } as const
const job = {
  public_id: 'sigjob_Abcdefghijk', import_type: 'csv', filename: 'signals.csv', status: 'validated', row_count: 1,
  error_message: null, created_at: '2026-08-16T01:00:00+00:00', completed_at: '2026-08-16T01:00:00+00:00',
  source_sha256: 'a'.repeat(64), request_sha256: 'b'.repeat(64), provenance_sha256: 'c'.repeat(64), replayed: false, safety_boundary: boundary,
}

test('CSV readiness decoder keeps the 256 KB and research-history boundary', () => {
  const readiness = decodeLabCsvImportReadiness({ capability: 'csv_import', allowed: true, quota: { used: 1, limit: 3, remaining: 2 }, limits: { max_bytes: 256 * 1024, max_rows: 500 }, safety_boundary: boundary })
  assert.equal(readiness.limits.max_bytes, 256 * 1024)
  assert.equal(readiness.safety_boundary.creates_orders, false)
  assert.throws(() => decodeLabCsvImportReadiness({ capability: 'csv_import', allowed: true, quota: { used: 1, limit: 3, remaining: 2 }, limits: { max_bytes: 1024, max_rows: 500 }, safety_boundary: boundary }))
})

test('CSV upload sends multipart file and idempotency key; detail accepts signal_count', async () => {
  let received: { path: string; init?: RequestInit } | undefined
  const file = new File(['股票代码,操作\nAAPL,買入\n'], 'signals.csv', { type: 'text/csv' })
  const result = await uploadLabCsvImport(file, 'csv-test-0001', async (path, init) => {
    received = { path, init }
    return job
  })
  assert.equal(result.public_id, job.public_id)
  assert.ok(received)
  assert.equal(received.path, '/api/rewrite/v1/signal-imports')
  assert.equal(received.init?.headers && new Headers(received.init.headers).get('Idempotency-Key'), 'csv-test-0001')
  assert.equal(received.init?.body instanceof FormData, true)
  const detail = await fetchLabCsvImport(job.public_id, async () => ({ ...job, signal_count: 1 }))
  assert.equal(detail.signal_count, 1)
})

test('CSV signals decoder reads the owner-scoped detail endpoint', async () => {
  const signals = await fetchLabCsvImportSignals(job.public_id, async (path) => {
    assert.match(path, /\/signals$/)
    return { items: [{ signal_id: 'signal_1', symbol: 'AAPL', action: '買入', quantity: 2, price: 100, timestamp: '2026-08-15T01:00:00+00:00', strategy: '趋势研究', confidence: null, disclaimer: '仅供研究' }] }
  })
  assert.equal(signals[0]?.symbol, 'AAPL')
})
