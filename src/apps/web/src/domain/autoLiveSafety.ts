import type { AutoLivePauseResult, AutoLiveSnapshot } from '../api/autoLive.ts'

export type AutoLiveSnapshotState = 'idle' | 'loading' | 'fresh' | 'stale'
export type AutoLiveKillSwitchTone = 'danger' | 'stale' | 'partial' | 'failed' | 'unknown'

export interface AutoLiveKillSwitchView {
  visible: boolean
  tone: AutoLiveKillSwitchTone
  label: string
}

export function deriveAutoLiveKillSwitchView(
  snapshot: AutoLiveSnapshot | null,
  snapshotState: AutoLiveSnapshotState,
  result: AutoLivePauseResult | null,
  pauseUnknown: boolean,
): AutoLiveKillSwitchView {
  const runtimeActive = snapshot?.runtime_projections.some((item) => ['running', 'starting', 'pausing'].includes(item.state)) ?? false
  const receiptActive = snapshot?.pause_receipts.some((item) => ['pausing', 'partial'].includes(item.status)) ?? false
  const mandateActive = snapshot?.mandates.some((item) => item.state === 'active') ?? false
  const lastKnownActive = runtimeActive || receiptActive || mandateActive

  if (pauseUnknown) return { visible: true, tone: 'unknown', label: '暂停结果未知 · 重试同一请求' }
  if (result?.status === 'failed') return { visible: true, tone: 'failed', label: '暂停失败 · 请重试' }
  if (result?.status === 'partial') return { visible: true, tone: 'partial', label: `暂停部分确认 · ${result.confirmed}/${result.total}` }
  if (result?.status === 'pausing') return { visible: true, tone: 'partial', label: `正在等待暂停确认 · ${result.confirmed}/${result.total}` }
  if (lastKnownActive && snapshotState === 'stale') return { visible: true, tone: 'stale', label: '状态刷新失败 · 仍可暂停' }
  if (!snapshot && snapshotState === 'stale') return { visible: true, tone: 'unknown', label: '状态未知 · 尝试暂停所有' }
  if (!snapshot && snapshotState === 'loading') return { visible: true, tone: 'unknown', label: '状态读取中 · 可尝试暂停所有' }
  return { visible: lastKnownActive, tone: 'danger', label: '立即暂停所有自动实盘' }
}
