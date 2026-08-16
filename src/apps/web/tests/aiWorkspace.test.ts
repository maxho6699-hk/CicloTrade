import assert from 'node:assert/strict'
import test from 'node:test'
import {
  AI_TASK_STATUSES,
  AiWorkspaceApiError,
  createAiWorkspaceApi,
  decodeAiWorkspaceTaskResult,
  readAiWorkspaceStructuredMessage,
  type AiTaskStatus,
} from '../src/api/aiWorkspace.ts'

const sessionId = 'ais_1234567890abcdef'
const taskId = 'ait_1234567890abcdef'
const now = '2026-08-16T01:00:00Z'

function task(status: AiTaskStatus) {
  return {
    public_id: taskId,
    session_public_id: sessionId,
    status,
    blocked_reason: status === 'blocked' ? 'provider_unavailable' : null,
    error_code: status === 'failed' ? 'provider_rejected' : null,
    provider_version: status === 'succeeded' ? 'provider-v1' : null,
    contract_version: status === 'succeeded' ? 'contract-v1' : null,
    created_at: now,
    updated_at: now,
  }
}

function session() {
  return { public_id: sessionId, title: '股票研究', status: 'active', context_snapshot_public_id: null, created_at: now, messages: [] }
}
function summary() {
  return { public_id: sessionId, title: '股票研究', status: 'active', context_snapshot_public_id: null, created_at: now }
}

test('AI workspace API covers readiness, session lifecycle, task detail, events and cancellation routes', async () => {
  const calls: Array<{ path: string; method: string; body?: string }> = []
  const api = createAiWorkspaceApi(async (path, init) => {
    calls.push({ path, method: init?.method ?? 'GET', body: typeof init?.body === 'string' ? init.body : undefined })
    if (path.endsWith('/readiness')) return { ready: true, status: 'ready', missing: [], provider_version: 'provider-v1', contract_version: 'contract-v1', model: 'ciclo-test' }
    if (path === '/api/rewrite/v1/ai/workspace/sessions' && init?.method === 'GET') return { items: [summary()] }
    if (path === '/api/rewrite/v1/ai/workspace/sessions') return session()
    if (path.endsWith('/archive')) return { ...session(), status: 'archived' }
    if (path.endsWith('/messages')) return { task: task('queued'), assistant: null, blocked: false }
    if (path.endsWith('/events')) return { items: [{ seq: 1, status: 'queued', payload: {}, created_at: now }] }
    if (path.endsWith('/cancel')) return task('cancelled')
    if (path.includes('/tasks/')) return task('running')
    return session()
  })

  assert.equal((await api.readiness()).ready, true)
  assert.equal((await api.listSessions())[0].public_id, sessionId)
  assert.equal((await api.createSession({ title: '股票研究' }, 'session-key-1234')).title, '股票研究')
  assert.equal((await api.getSession(sessionId)).public_id, sessionId)
  assert.equal((await api.archiveSession(sessionId, 'archive-key-1234')).status, 'archived')
  const result = await api.sendMessage(sessionId, '请分析股票风险', 'message-key-1234')
  assert.equal(result.task.status, 'queued')
  assert.equal((await api.getTask(taskId)).status, 'running')
  assert.equal((await api.listTaskEvents(taskId))[0].status, 'queued')
  assert.equal((await api.cancelTask(taskId, 'cancel-key-1234')).status, 'cancelled')
  assert.deepEqual(calls.map(({ path, method }) => [path, method]), [
    ['/api/rewrite/v1/ai/workspace/readiness', 'GET'],
    ['/api/rewrite/v1/ai/workspace/sessions', 'GET'],
    ['/api/rewrite/v1/ai/workspace/sessions', 'POST'],
    [`/api/rewrite/v1/ai/workspace/sessions/${sessionId}`, 'GET'],
    [`/api/rewrite/v1/ai/workspace/sessions/${sessionId}/archive`, 'POST'],
    [`/api/rewrite/v1/ai/workspace/sessions/${sessionId}/messages`, 'POST'],
    [`/api/rewrite/v1/ai/workspace/tasks/${taskId}`, 'GET'],
    [`/api/rewrite/v1/ai/workspace/tasks/${taskId}/events`, 'GET'],
    [`/api/rewrite/v1/ai/workspace/tasks/${taskId}/cancel`, 'POST'],
  ])
})

test('blocked 503-style task receipt stays public and never gains an assistant answer', () => {
  const receipt = decodeAiWorkspaceTaskResult({ task: task('blocked'), assistant: null, blocked: true })
  assert.equal(receipt.task.status, 'blocked')
  assert.equal(receipt.assistant, null)
  assert.equal(receipt.blocked, true)
})

test('historical assistant messages expose only the server structured answer contract', () => {
  const structured = {
    conclusion: { text: '需要继续核验', citation_ids: ['cit_12345678'] },
    citations: ['cit_12345678'],
    support: { text: ['有可追溯行情'], citation_ids: ['cit_12345678'] },
    counter: { text: '反向资料仍不足', citation_ids: ['cit_12345678'] },
    risks: { text: '数据可能延迟', citation_ids: ['cit_12345678'] },
    next_steps: { text: '打开股票研究页', citation_ids: ['cit_12345678'] },
  }
  assert.deepEqual(readAiWorkspaceStructuredMessage({ structured, provider_version: 'provider-v1', contract_version: 'contract-v1' }), structured)
  assert.equal(readAiWorkspaceStructuredMessage({ structured: { ...structured, chain_of_thought: 'hidden' } }), null)
  assert.equal(readAiWorkspaceStructuredMessage({ text: '自然语言回答' }), null)
})

test('all public task states are recognized and malformed receipts fail closed', () => {
  assert.deepEqual(AI_TASK_STATUSES, ['queued', 'running', 'partial', 'succeeded', 'failed', 'cancelled', 'blocked', 'timed_out'])
  for (const status of AI_TASK_STATUSES) {
    assert.equal(decodeAiWorkspaceTaskResult({ task: task(status), assistant: null, blocked: status === 'blocked' }).task.status, status)
  }
  assert.throws(() => decodeAiWorkspaceTaskResult({ task: task('succeeded'), assistant: null, blocked: true }), AiWorkspaceApiError)
  assert.throws(() => decodeAiWorkspaceTaskResult({ task: { ...task('queued'), internal_trace: 'nope' }, assistant: null, blocked: false }), AiWorkspaceApiError)
})

test('write requests require idempotency and prevent unsafe path ids', async () => {
  const api = createAiWorkspaceApi(async () => ({ items: [] }))
  await assert.rejects(() => api.createSession({}, 'short'), AiWorkspaceApiError)
  await assert.rejects(() => api.getSession('../escape'), AiWorkspaceApiError)
  await assert.rejects(() => api.sendMessage(sessionId, '   ', 'message-key-1234'), AiWorkspaceApiError)
})
