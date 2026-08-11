import assert from 'node:assert/strict'
import test from 'node:test'
import { validMarketStatusPayload } from '../src/api/client.ts'
import { createVisibilityPolling, deliveryAllowsImmediateAction, displayDeliveryDelay, displayFreshness, type VisibilityPollingHost } from '../src/domain/dataSourcePresentation.ts'

class FakePollingClock implements VisibilityPollingHost {
  private visible = true
  private nextTimerId = 1
  private now = 0
  private readonly timers = new Map<number, { at: number; callback: () => void }>()
  private readonly listeners = new Set<() => void>()

  isVisible = () => this.visible
  addVisibilityListener = (listener: () => void) => { this.listeners.add(listener) }
  removeVisibilityListener = (listener: () => void) => { this.listeners.delete(listener) }
  setTimeout = (callback: () => void, delayMs: number) => {
    const id = this.nextTimerId++
    this.timers.set(id, { at: this.now + delayMs, callback })
    return id as unknown as ReturnType<typeof setTimeout>
  }
  clearTimeout = (timer: ReturnType<typeof setTimeout>) => {
    this.timers.delete(timer as unknown as number)
  }

  setVisible(visible: boolean) {
    this.visible = visible
    for (const listener of this.listeners) listener()
  }

  advance(delayMs: number) {
    const target = this.now + delayMs
    while (true) {
      const due = [...this.timers.entries()]
        .filter(([, timer]) => timer.at <= target)
        .sort((left, right) => left[1].at - right[1].at)[0]
      if (!due) break
      const [id, timer] = due
      this.timers.delete(id)
      this.now = timer.at
      timer.callback()
    }
    this.now = target
  }
}

async function settle() {
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
}

test('visibility polling refreshes on its interval and stops cleanly', async () => {
  const clock = new FakePollingClock()
  let calls = 0
  const stop = createVisibilityPolling(() => { calls += 1 }, 5_000, clock)

  assert.equal(calls, 1, 'the first visible request is immediate')
  await settle()
  clock.advance(4_999)
  assert.equal(calls, 1)
  clock.advance(1)
  assert.equal(calls, 2)

  stop()
  await settle()
  clock.advance(10_000)
  assert.equal(calls, 2, 'cleanup cancels the scheduled request')
})

test('visibility polling pauses hidden pages and refreshes immediately when visible', async () => {
  const clock = new FakePollingClock()
  let calls = 0
  const stop = createVisibilityPolling(() => { calls += 1 }, 5_000, clock)

  await settle()
  clock.setVisible(false)
  clock.advance(20_000)
  assert.equal(calls, 1, 'a hidden page must not poll')
  clock.setVisible(true)
  assert.equal(calls, 2, 'returning to the page refreshes immediately')
  stop()
})

test('visibility polling never overlaps a slow request', async () => {
  const clock = new FakePollingClock()
  let calls = 0
  let complete: (() => void) | undefined
  const stop = createVisibilityPolling(() => new Promise<void>((resolve) => {
    calls += 1
    complete = resolve
  }), 5_000, clock)

  assert.equal(calls, 1)
  clock.advance(30_000)
  assert.equal(calls, 1, 'a pending request cannot be duplicated')
  complete?.()
  await settle()
  clock.advance(5_000)
  assert.equal(calls, 2)
  stop()
})

test('delivery delay is an API visibility boundary, not a provider realtime right', () => {
  assert.equal(displayDeliveryDelay(15), '延迟 15 分钟')
  assert.equal(displayDeliveryDelay(60), '延迟 1 小时')
  assert.equal(displayDeliveryDelay(0), '')
  assert.equal(displayFreshness('实时权限未启用'), '未启用或暂不可用')
  assert.equal(displayFreshness('不可用'), '未启用或暂不可用')
  assert.equal(
    deliveryAllowsImmediateAction({ delivery_delay_minutes: 15, is_realtime: true, actionable_quote: true }),
    false,
  )
  assert.equal(
    deliveryAllowsImmediateAction({ delivery_delay_minutes: 0, is_realtime: true, actionable_quote: true }),
    true,
  )
})

test('market status decoder accepts only the vendor-neutral authenticated contract', () => {
  const valid = {
    status: 'available',
    upstream_connected: true,
    provider_realtime: true,
    configuration_allows_realtime: true,
    equity_realtime_entitled: true,
    option_realtime_entitled: true,
    delivery_delay_minutes: 15,
    is_realtime: false,
    visible_as_of: '2026-08-12T01:00:00+00:00',
    observed_at: '2026-08-12T01:15:00+00:00',
    refresh_after_seconds: 8,
  }
  assert.equal(validMarketStatusPayload(valid), true)
  assert.equal(validMarketStatusPayload({ ...valid, source: 'private-provider' }), false)
  assert.equal(validMarketStatusPayload({ ...valid, delivery_delay_minutes: -1 }), false)
  assert.equal(validMarketStatusPayload({ ...valid, observed_at: 'not-a-time' }), false)
  assert.equal(validMarketStatusPayload({ ...valid, upstream_connected: 'yes' }), false)
})
