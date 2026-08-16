import assert from 'node:assert/strict'
import test from 'node:test'
import { validAccountOverview, validAppearancePayload, validDataAuthorization, validMemoryList } from '../src/api/accountCenter.ts'

const overview = {
  account: { public_id: 'usr_123456789012345678901234', display_name: 'CicloTrade 用户' },
  membership: { state: 'available', plan: '免费版', subscription_expire: null },
  telegram: { state: 'not_configured', policy_state: 'not_configured' },
  brokers: { state: 'not_configured', items: [] },
  agent_levels: Object.fromEntries(['L0', 'L1', 'L2', 'L3', 'L4'].map((level) => [level, { level: null, policy_state: 'not_configured' }])),
  runtime: { auto_live: 'not_ready' },
}

test('account DTO validators reject extra keys and preserve locked levels', () => {
  assert.equal(validAccountOverview(overview), true)
  assert.equal(validAccountOverview({ ...overview, extra: true }), false)
  assert.equal(validAccountOverview({ ...overview, agent_levels: { ...overview.agent_levels, L0: { level: 5, policy_state: 'configured' } } }), false)
})

test('appearance and memory validators require exact server fields', () => {
  const appearance = { current: { public_id: null, skin_id: null, asset_version: null, manifest_sha256: null, source: 'unavailable' }, items: [] }
  assert.equal(validAppearancePayload(appearance), true)
  assert.equal(validAppearancePayload({ ...appearance, items: [{ public_id: 'man_1', skin_id: 'free', asset_version: 'v1', manifest_sha256: '0'.repeat(64), assets: {}, entitled: false, rank: 0, created_at: '2026-08-16T00:00:00+00:00', extra: 'reject' }] }), false)
  const memory = { public_id: 'mem_123456789012345678901234', memory_key: 'risk', memory_json: '{"enabled":true}', expires_at: null, created_at: '2026-08-16T00:00:00+00:00' }
  assert.equal(validMemoryList({ items: [memory] }), true)
  assert.equal(validMemoryList({ items: [{ ...memory, created_at: 'not-a-date' }] }), false)
})

test('authorization validator distinguishes configured and unconfigured exact payloads', () => {
  assert.equal(validDataAuthorization({ data_kind: 'quotes', authorized: false, policy_state: 'not_configured' }), true)
  assert.equal(validDataAuthorization({ data_kind: 'quotes', authorized: true, policy_state: 'configured', receipt_public_id: 'auth_123' }), true)
  assert.equal(validDataAuthorization({ data_kind: 'quotes', authorized: true, policy_state: 'configured', receipt_public_id: 'auth_123', extra: true }), false)
})
