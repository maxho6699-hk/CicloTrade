import assert from 'node:assert/strict'
import test from 'node:test'
import { positionReturnPct } from '../src/domain/portfolioMath.ts'

test('option position return uses the contract multiplier in its cost basis', () => {
  assert.equal(positionReturnPct(1, 5, 100, 100), 20)
})
