import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  CircleSlash2,
  Clock3,
  ShieldX,
  TimerOff,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { WORKFLOW_STATUS_COPY, type WorkflowPublicStatus } from './workflowStatus'

const WORKFLOW_STATUS_ICON: Record<WorkflowPublicStatus, LucideIcon> = {
  queued: Clock3,
  running: CircleDashed,
  partial: AlertTriangle,
  succeeded: CheckCircle2,
  failed: XCircle,
  cancelled: CircleSlash2,
  blocked: ShieldX,
  timed_out: TimerOff,
}

export function WorkflowStatusPill({ status }: { status: WorkflowPublicStatus }) {
  const tone = status === 'succeeded'
    ? 'success'
    : status === 'failed' || status === 'blocked' || status === 'timed_out'
      ? 'error'
      : status === 'cancelled' || status === 'partial'
        ? 'warning'
        : 'info'
  return <span className={`intelligence-status is-${tone}`}><i />{WORKFLOW_STATUS_COPY[status]}</span>
}

export function WorkflowStatusRail({ status }: { status: WorkflowPublicStatus }) {
  const statuses: WorkflowPublicStatus[] = ['queued', 'running', 'partial', 'succeeded', 'failed', 'cancelled', 'blocked', 'timed_out']
  return <div className="workflow-state-rail" aria-label={`当前任务状态：${WORKFLOW_STATUS_COPY[status]}`}>
    {statuses.map((item) => {
      const current = item === status
      const Icon = WORKFLOW_STATUS_ICON[item]
      return <div className={current ? 'is-current' : ''} key={item} aria-current={current ? 'step' : undefined}>
        <span>{current ? <Icon aria-hidden="true" /> : <i />}</span>
        <small>{WORKFLOW_STATUS_COPY[item]}</small>
      </div>
    })}
  </div>
}

interface TruthStateProps {
  title: string
  detail: string
  tone?: 'neutral' | 'warning' | 'error' | 'success'
  action?: ReactNode
}

export function TruthState({ title, detail, tone = 'neutral', action }: TruthStateProps) {
  const Icon = tone === 'error' ? XCircle : tone === 'warning' ? AlertTriangle : tone === 'success' ? CheckCircle2 : Clock3
  return <section className={`intelligence-truth-state is-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
    <Icon aria-hidden="true" />
    <div><strong>{title}</strong><p>{detail}</p>{action}</div>
  </section>
}

interface EvidenceStrengthProps {
  label: string
  value: number | null
  status: 'queued' | 'calculating' | 'ready' | 'partial' | 'failed' | 'stale' | 'invalidated' | null
  coverage: number | null
  methodVersion: string | null
  observedAt: string | null
  availableAt: string | null
  asOf: string | null
  calculatedAt: string | null
  tone: 'support' | 'counter'
}

export function EvidenceStrength({
  label,
  value,
  status,
  coverage,
  methodVersion,
  observedAt,
  availableAt,
  asOf,
  calculatedAt,
  tone,
}: EvidenceStrengthProps) {
  const timestamps = [observedAt, availableAt, asOf, calculatedAt]
  const ready = status === 'ready'
    && typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100
    && coverage !== null && Number.isFinite(coverage) && coverage >= 0 && coverage <= 1
    && methodVersion !== null && Boolean(methodVersion.trim())
    && timestamps.every((timestamp) => timestamp !== null && Number.isFinite(Date.parse(timestamp)))
  return <section className={`evidence-strength is-${tone}`} aria-label={label}>
    <header><span>{label}</span><strong>{ready ? value : '—'}</strong></header>
    <div
      className="evidence-strength-track"
      role={ready ? 'progressbar' : 'status'}
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={ready ? value : undefined}
      aria-valuetext={ready ? `${value} / 100` : 'missing'}
    >
      <i style={{ width: ready ? `${value}%` : '0%' }} />
    </div>
    <footer><span>{ready ? 'ready' : status ?? 'missing'}</span><small>独立 0–100，不要求合计 100</small></footer>
  </section>
}
