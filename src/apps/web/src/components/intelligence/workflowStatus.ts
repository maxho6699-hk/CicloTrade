import type { BacktestStatus } from '../../api/backtests'

export type WorkflowPublicStatus = BacktestStatus | 'partial' | 'timed_out'

export const WORKFLOW_STATUS_COPY: Record<WorkflowPublicStatus, string> = {
  queued: '排队中',
  running: '执行中',
  partial: '部分完成',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
  blocked: '已阻断',
  timed_out: '已超时',
}
