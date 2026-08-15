import { AlertTriangle, CheckCircle2, CircleDashed, Clock3, XCircle } from 'lucide-react'
import type { ReactNode } from 'react'
import { WORKFLOW_STATUS_COPY, type WorkflowPublicStatus } from './workflowStatus'

const terminalStatuses = new Set<WorkflowPublicStatus>(['succeeded', 'failed', 'cancelled', 'blocked'])

export function WorkflowStatusPill({ status }: { status: WorkflowPublicStatus }) {
  const tone = status === 'succeeded'
    ? 'success'
    : status === 'failed' || status === 'blocked'
      ? 'error'
      : status === 'cancelled' || status === 'partial'
        ? 'warning'
        : 'info'
  return <span className={`intelligence-status is-${tone}`}><i />{WORKFLOW_STATUS_COPY[status]}</span>
}

export function WorkflowStatusRail({ status }: { status: WorkflowPublicStatus }) {
  const statuses: WorkflowPublicStatus[] = ['queued', 'running', 'partial', 'succeeded', 'failed', 'cancelled']
  return <div className="workflow-state-rail" aria-label={`当前任务状态：${WORKFLOW_STATUS_COPY[status]}`}>
    {statuses.map((item) => {
      const current = item === status
      return <div className={current ? 'is-current' : ''} key={item} aria-current={current ? 'step' : undefined}>
        <span>{current ? terminalStatuses.has(item) ? <CheckCircle2 /> : <CircleDashed /> : <i />}</span>
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
  tone: 'support' | 'counter'
}

export function EvidenceStrength({ label, value, tone }: EvidenceStrengthProps) {
  const available = typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100
  return <section className={`evidence-strength is-${tone}`} aria-label={label}>
    <header><span>{label}</span><strong>{available ? value : '—'}</strong></header>
    <div
      className="evidence-strength-track"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={available ? value : undefined}
      aria-valuetext={available ? `${value} / 100` : 'missing'}
    >
      <i style={{ width: available ? `${value}%` : '0%' }} />
    </div>
    <footer><span>{available ? 'ready' : 'missing'}</span><small>独立 0–100，不要求合计 100</small></footer>
  </section>
}
