import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { createAutoLiveApi, validAutoLiveActionMandate, validAutoLiveMandate, validAutoLivePauseResult, validAutoLiveSafeReceipt, validAutoLiveSnapshot, validAutoLiveTarget } from '../src/api/autoLive.ts'
import { deriveAutoLiveKillSwitchView } from '../src/domain/autoLiveSafety.ts'
import { createSessionIdempotencyRegistry } from '../src/domain/sessionIdempotency.ts'

const now = '2026-08-16T00:00:00+00:00'
const mandate = {
  public_id: 'mandate_12345678', broker_account_public_id: 'broker_12345678', strategy_version: 'strategy-v1', risk_version: 'risk-v1',
  capital_limit_minor: 100000, frequency_limit: 10, valid_from: now, valid_until: '2026-09-16T00:00:00+00:00', state: 'active', can_reduce_exposure: true,
  snapshot_sha256: 'a'.repeat(64), confirmed_at: now, created_at: now, updated_at: now,
}
const actionMandate = Object.fromEntries(Object.entries(mandate).filter(([key]) => key !== 'broker_account_public_id'))
const snapshot = { snapshot_at: now, broker_accounts: [{ public_id: 'broker_12345678', provider: 'tiger', status: 'authorized' }], mandates: [mandate], runtime_projections: [{ mandate_public_id: mandate.public_id, state: 'starting', can_reduce_exposure: 1, last_error_code: null, observed_at: now }], heartbeat_projections: [{ mandate_public_id: mandate.public_id, heartbeat_state: 'fresh', heartbeat_at: now, observed_at: now }], pause_receipts: [{ public_id: 'pause_12345678', status: 'partial', receipt: { public_id: 'pause_12345678', status: 'partial', confirmed: 0, total: 1, unconfirmed: [mandate.public_id], scope: 'aggregate', can_reduce_exposure: true, actor: 'owner', created_at: now, idempotency_key_sha256: 'f'.repeat(64), request_fingerprint: 'a'.repeat(64), target_details: [{ target_type: 'mandate', target_public_id: mandate.public_id, confirmed: false, status: 'failed', fencing_epoch: 1, detail: 'runtime_missing', created_at: now }] }, receipt_sha256: 'b'.repeat(64), created_at: now }], start_receipts: [{ public_id: 'start_12345678', mandate_public_id: mandate.public_id, status: 'starting', state: 'active', runtime_state: 'starting', fencing_epoch: 1, created_at: now, idempotency_key_sha256: 'b'.repeat(64), request_fingerprint: 'c'.repeat(64), all_ok: true, gates: [], actor: 'owner', receipt_sha256: 'd'.repeat(64) }], order_receipts: [{ public_id: 'order_12345678', mandate_public_id: mandate.public_id, client_order_id: 'client-1', submission_state: 'submission_unknown', observed_at: now, receipt_sha256: 'e'.repeat(64) }] }
const tradeSource = readFileSync(new URL('../src/pages/TradePage.tsx', import.meta.url), 'utf8')
const shellSource = readFileSync(new URL('../src/components/AppShell.tsx', import.meta.url), 'utf8')

test('auto-live snapshot and mandate validators enforce exact keys', () => {
  assert.equal(validAutoLiveMandate(mandate), true)
  assert.equal(validAutoLiveActionMandate(actionMandate), true)
  assert.equal(validAutoLiveActionMandate({ ...actionMandate, broker_account_public_id: mandate.broker_account_public_id }), false)
  assert.equal(validAutoLiveMandate({ ...mandate, extra: true }), false)
  assert.equal(validAutoLiveSafeReceipt(snapshot.pause_receipts[0].receipt), true, 'pause receipt')
  assert.equal(validAutoLiveTarget(snapshot.pause_receipts[0].receipt.target_details[0]), true, 'pause target')
  assert.equal(validAutoLiveSnapshot(snapshot), true)
  assert.equal(validAutoLiveSnapshot({ ...snapshot, extra: true }), false)
  assert.equal(validAutoLiveSnapshot({ ...snapshot, mandates: [{ ...mandate, snapshot_sha256: 'bad' }] }), false)
})

test('auto-live pause result rejects partial receipts with malformed fields', () => {
  const result = { public_id: 'pause_12345678', scope: 'aggregate', status: 'partial', confirmed: 1, total: 2, unconfirmed: ['mandate_12345678'], can_reduce_exposure: true, receipt_sha256: 'b'.repeat(64), created_at: now, updated_at: now }
  assert.equal(validAutoLivePauseResult(result), true)
  assert.equal(validAutoLivePauseResult({ ...result, total: 0 }), false)
  assert.equal(validAutoLivePauseResult({ ...result, extra: true }), false)
})

test('auto-live controls stay on trade and global shell only exposes emergency aggregate pause', () => {
  assert.match(tradeSource, /AutoLiveControlPanel/)
  assert.match(tradeSource, /broker_account_public_id/)
  assert.match(tradeSource, /partial 不代表全部暂停/)
  assert.match(shellSource, /GlobalAutoLiveKillSwitch/)
  assert.match(shellSource, /scope: 'aggregate'/)
  assert.doesNotMatch(shellSource, /autoLiveApi\.start|autoLiveApi\.resume|autoLiveApi\.confirm/)
})

test('receipt responses clear persisted keys and runtime states suppress duplicate starts', () => {
  assert.match(tradeSource, /const result = await autoLiveApi\.start[\s\S]+idempotency\.current\.clear\(scope, fingerprint\)/)
  assert.match(tradeSource, /const result = await autoLiveApi\.pause[\s\S]+idempotency\.current\.clear\(scope, fingerprint\)/)
  assert.match(tradeSource, /\['starting', 'running'\]\.includes\(runtime\?\.state/)
  assert.match(shellSource, /setPauseUnknown\(false\)\s+idempotency\.current\.clear\('pause-aggregate', fingerprint\)/)
  assert.match(tradeSource, /createMandate\([\s\S]+idempotency\.current\.key\(scope, fingerprint\)[\s\S]+idempotency\.current\.clear\(scope, fingerprint\)/)
})

test('auto-live idempotency survives remounts until a known response clears it', () => {
  const values = new Map<string, string>()
  const store = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
    removeItem: (key: string) => { values.delete(key) },
  }
  const namespace = 'auto-live-test'
  const first = createSessionIdempotencyRegistry(namespace, store)
  const fingerprint = JSON.stringify({ mandate_public_id: mandate.public_id, expected_fencing_epoch: 2 })
  const pendingKey = first.key(`start-${mandate.public_id}`, fingerprint)
  const afterRemount = createSessionIdempotencyRegistry(namespace, store)
  assert.equal(afterRemount.key(`start-${mandate.public_id}`, fingerprint), pendingKey)
  afterRemount.clear(`start-${mandate.public_id}`, 'different-request')
  assert.equal(afterRemount.key(`start-${mandate.public_id}`, fingerprint), pendingKey)
  afterRemount.clear(`start-${mandate.public_id}`, fingerprint)
  assert.notEqual(afterRemount.key(`start-${mandate.public_id}`, fingerprint), pendingKey)
})

test('auto-live client sends exact confirmation and idempotent pause requests', async () => {
  const calls: Array<{ path: string; init?: RequestInit }> = []
  const transport = async <T>(path: string, init?: RequestInit) => {
    calls.push({ path, init })
    if (path.endsWith('/mandates')) return mandate as T
    if (path.endsWith('/confirmation')) return { ...actionMandate, confirmation_phrase: `ACTIVATE ${mandate.public_id}`, confirmation_snapshot_sha256: mandate.snapshot_sha256 } as T
    if (path.endsWith('/confirm') || path.endsWith('/resume')) return { ...actionMandate, all_ok: false, gates: [{ name: 'telegram', ok: false, reason: 'telegram_unverified' }] } as T
    if (path.endsWith('/revoke')) return actionMandate as T
    return { public_id: 'pause_12345678', scope: 'aggregate', status: 'paused', confirmed: 0, total: 0, unconfirmed: [], can_reduce_exposure: true, receipt_sha256: 'b'.repeat(64), created_at: now, updated_at: now } as T
  }
  const api = createAutoLiveApi(transport)
  const confirmation = await api.requestConfirmation(mandate.public_id)
  assert.equal(confirmation.confirmation_phrase, `ACTIVATE ${mandate.public_id}`)
  await api.createMandate({ broker_account_public_id: mandate.broker_account_public_id, strategy_version: mandate.strategy_version, risk_version: mandate.risk_version, capital_limit_minor: mandate.capital_limit_minor, frequency_limit: mandate.frequency_limit, valid_from: mandate.valid_from, valid_until: mandate.valid_until }, 'create-key-12345678')
  await api.pause({ scope: 'aggregate' }, 'pause-key-12345678')
  assert.equal(calls[0].path, `/api/rewrite/v1/auto-live/mandates/${mandate.public_id}/confirmation`)
  assert.equal(calls[1].path, '/api/rewrite/v1/auto-live/mandates')
  assert.equal(new Headers(calls[1].init?.headers).get('Idempotency-Key'), 'create-key-12345678')
  assert.equal(calls[2].path, '/api/rewrite/v1/auto-live/pause')
  assert.equal(new Headers(calls[2].init?.headers).get('Idempotency-Key'), 'pause-key-12345678')
  const gated = await api.confirm(mandate.public_id, { mandate_public_id: mandate.public_id, snapshot_sha256: mandate.snapshot_sha256, confirmation_phrase: `ACTIVATE ${mandate.public_id}` })
  assert.equal(gated.all_ok, false)
  await api.resume(mandate.public_id)
  await api.revoke(mandate.public_id, '用户撤销')
  assert.throws(() => api.pause({ scope: 'aggregate', extra: true } as never, 'pause-key-12345678'), /暂停范围字段无效/)
})

test('global auto-live pause remains available across stale snapshots and unknown responses', () => {
  assert.deepEqual(deriveAutoLiveKillSwitchView(snapshot, 'stale', null, false), {
    visible: true,
    tone: 'stale',
    label: '状态刷新失败 · 仍可暂停',
  })
  assert.deepEqual(deriveAutoLiveKillSwitchView(null, 'stale', null, false), {
    visible: true,
    tone: 'unknown',
    label: '状态未知 · 尝试暂停所有',
  })
  assert.deepEqual(deriveAutoLiveKillSwitchView(null, 'stale', null, true), {
    visible: true,
    tone: 'unknown',
    label: '暂停结果未知 · 重试同一请求',
  })
  assert.deepEqual(deriveAutoLiveKillSwitchView(null, 'loading', null, false), {
    visible: true,
    tone: 'unknown',
    label: '状态读取中 · 可尝试暂停所有',
  })
  assert.match(shellSource, /catch\(\(\) => \{\s+if \(active\) setAutoLiveSnapshotState\('stale'\)/)
  assert.doesNotMatch(shellSource, /catch\(\(\) => \{ if \(active\) setAutoLiveSnapshot\(null\)/)
  assert.match(shellSource, /catch \{ setPauseUnknown\(true\) \}/)
  assert.match(shellSource, /setPauseUnknown\(false\)\s+idempotency\.current\.clear\('pause-aggregate', fingerprint\)/)
})

test('trade locks risk-increasing actions on stale snapshots while safety exits remain', () => {
  assert.equal((tradeSource.match(/snapshotState !== 'fresh'/g) ?? []).length, 4)
  assert.match(tradeSource, /创建、确认、恢复与启动已锁定，暂停和撤销仍保留/)
  assert.match(tradeSource, /状态已过期/)
  assert.match(tradeSource, /暂停 mandate<\/button><button[\s\S]+disabled=\{busy !== ''\}>撤销/)
})
