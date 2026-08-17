import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'

const moduleUrl = new URL('../src/api/watchlistMutationCoordinator.ts', import.meta.url)
const workspaceSource = readFileSync(new URL('../src/api/WorkspaceContext.tsx', import.meta.url), 'utf8')

test('watchlist mutations run serially even when an earlier operation fails', async () => {
  assert.equal(existsSync(moduleUrl), true, 'watchlist mutation coordinator is missing')
  const module = await import('../src/api/watchlistMutationCoordinator.ts')
  const enqueue = module.createSerialMutationQueue()
  const order: string[] = []
  let releaseFirst!: () => void
  const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve })
  const first = enqueue(async () => {
    order.push('first:start')
    await firstGate
    order.push('first:fail')
    throw new Error('expected failure')
  })
  void first.catch(() => undefined)
  const second = enqueue(async () => {
    order.push('second:start')
    return 'ok'
  })
  await Promise.resolve()
  assert.deepEqual(order, ['first:start'])
  releaseFirst()
  await assert.rejects(first, /expected failure/)
  assert.equal(await second, 'ok')
  assert.deepEqual(order, ['first:start', 'first:fail', 'second:start'])
})

test('bootstrap responses started before a committed watchlist update become stale', async () => {
  assert.equal(existsSync(moduleUrl), true, 'watchlist mutation coordinator is missing')
  const module = await import('../src/api/watchlistMutationCoordinator.ts')
  const revisions = module.createStateRevisionGuard()
  const beforeMutation = revisions.snapshot()
  assert.equal(revisions.isCurrent(beforeMutation), true)
  revisions.advance()
  assert.equal(revisions.isCurrent(beforeMutation), false)
  assert.equal(revisions.isCurrent(revisions.snapshot()), true)
  assert.match(workspaceSource, /watchlistMutationQueueRef/)
  assert.match(workspaceSource, /watchlistStateRevisionRef/)
})
