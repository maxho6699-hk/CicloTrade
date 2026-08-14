import { ArrowUpRight, FlaskConical, ShieldCheck } from 'lucide-react'
import type { StrategyResearch97State } from '../api/strategyResearch97'
import '../styles/strategy-research-97.css'

export interface StrategyResearchOverview {
  stableCount: 13
  stableLabel?: string
  stableState: 'running' | 'waiting' | 'unavailable'
  expandedCount: 97
  expandedCoverage: number | null
  expandedNoData: number | null
  expandedState: StrategyResearch97State | 'unavailable'
  expandedUpdatedAt: string | null
}

export interface StrategyResearchOverviewCardProps {
  overview: StrategyResearchOverview
  formatTimestamp?: (value: string | null) => string
  title?: string
  href?: string
}

export function StrategyResearchOverviewCard({
  overview,
  formatTimestamp = (value) => value ?? '—',
  title = '策略研究覆盖',
  href = '/reports?view=影子策略研究',
}: StrategyResearchOverviewCardProps) {
  const stableLabel = overview.stableLabel ?? '13 股稳定 shadow'
  const expandedLabel = '97 标的扩容 research'
  const expandedStatus = overview.expandedState === 'unavailable' ? '暂不可用' : overview.expandedState === 'healthy' ? '运行中' : overview.expandedState === 'waiting' ? '等待中' : overview.expandedState === 'stale' ? '已过期' : '降级'
  const stableStatus = overview.stableState === 'running' ? '运行中' : overview.stableState === 'waiting' ? '等待中' : '暂不可用'

  return <article className="strategy-research-overview-card data-panel">
    <header className="strategy-research-overview-card-header"><div><span>RESEARCH COVERAGE</span><h2>{title}</h2></div><FlaskConical size={18} /></header>
    <div className="strategy-research-overview-card-grid">
      <div><ShieldCheck size={15} /><span><strong>{stableLabel}</strong><small>{stableStatus}</small></span></div>
      <div><FlaskConical size={15} /><span><strong>{expandedLabel}</strong><small>{expandedStatus}</small></span></div>
    </div>
    <dl className="strategy-research-overview-card-meta">
      <div><dt>扩容覆盖</dt><dd>{overview.expandedCoverage === null ? '—' : `${overview.expandedCoverage}/${overview.expandedCount}`}</dd></div>
      <div><dt>最后更新</dt><dd>{formatTimestamp(overview.expandedUpdatedAt)}</dd></div>
    </dl>
    <a className="strategy-research-overview-card-link" href={href}>查看研究证据 <ArrowUpRight size={14} /></a>
  </article>
}

export type { StrategyResearch97State }
