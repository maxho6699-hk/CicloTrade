import assert from 'node:assert/strict'
import test from 'node:test'
import { candidateDecisions } from '../src/data/demo.ts'

test('reduce candidates do not inherit a bullish profit target story', () => {
  const tesla = candidateDecisions.find((decision) => decision.instrument.symbol === 'TSLA')

  assert.ok(tesla)
  assert.equal(tesla.action, 'reduce')
  assert.equal(tesla.target, '等待重评')
  assert.doesNotMatch(JSON.stringify(tesla.plainLanguage), /228|235|锁定部分利润/)
})
