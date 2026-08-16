import assert from 'node:assert/strict'
import test from 'node:test'
import { createWorkflowApi } from '../src/api/workflows.ts'

const task = { task_public_id: 'wfl_1', source_kind: 'deliberation', source_public_id: 'dlb_1', attempt: 1, status: 'running', context: { market: 'US', symbol: 'AAPL' }, context_sha256: 'a'.repeat(64), provenance_sha256: 'b'.repeat(64), result: null, result_sha256: null, cancel_requested: false, created_at: '2026-08-16T00:00:00Z', updated_at: '2026-08-16T00:00:01Z', completed_at: null }
const detail = { ...task, events: [{ seq: 1, event_type: 'created', status: 'queued', payload: { attempt: 1 }, created_at: '2026-08-16T00:00:00Z' }], deliberation: null }

test('workflow API reads safe public task projection and events', async () => {
  const calls: string[] = []
  const api = createWorkflowApi(async (path) => { calls.push(path); return path.includes('?limit=') ? { items: [task] } : detail })
  assert.equal((await api.list())[0].context_sha256, 'a'.repeat(64))
  assert.equal((await api.get('wfl_1')).events[0].event_type, 'created')
  assert.deepEqual(calls, ['/api/rewrite/v1/workflows?limit=100', '/api/rewrite/v1/workflows/wfl_1'])
})
test('workflow API fails closed on owner, raw provenance, and arbitrary URLs', async () => {
  const api = createWorkflowApi(async () => ({ ...detail, context: { owner_id: 1 } }))
  await assert.rejects(() => api.get('wfl_1'), /Workflow 任务详情响应格式无效/)
  const unsafe = createWorkflowApi(async () => ({ ...detail, result: { artifact_url: 'https://example.test/file' } }))
  await assert.rejects(() => unsafe.get('wfl_1'), /Workflow 任务详情响应格式无效/)
})
