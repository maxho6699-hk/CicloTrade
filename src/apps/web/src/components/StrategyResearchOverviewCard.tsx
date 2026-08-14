import { ArrowUpRight, FlaskConical, ShieldCheck } from 'lucide-react'
import type { StrategyResearch97State } from '../api/strategyResearch97'
import '../styles/strategy-research-97.css'

export interface StrategyResearchOverview {
  stableCount: 13
  stableState: 'running' | 'waiting' | 'unavailable'
  expandedCount: 97
  expandedCoverage: number | null
  expandedNoData: number | null
  expandedState: StrategyResearch97State | 'unavailable'
  expandedUpdatedAt: string | null
}

export interface StrategyResearchOverviewCopy {
  title: string
  coverage: string
  updated: string
  stableLabel: string
  expandedLabel: string
  running: string
  waiting: string
  unavailable: string
  stale: string
  degraded: string
  link: string
}

export type StrategyResearchOverviewLocale = 'zh-Hans' | 'zh-Hant'

export interface StrategyResearchOverviewCardProps {
  overview: StrategyResearchOverview
  locale?: StrategyResearchOverviewLocale
  copy?: StrategyResearchOverviewCopy
  formatTimestamp?: (value: string | null) => string
  href?: string
}

const SIMPLIFIED: StrategyResearchOverviewCopy = { title: '策略研究覆盖', coverage: '扩容覆盖', updated: '最后更新', stableLabel: '13 股稳定 shadow', expandedLabel: '97 标的扩容 research', running: '运行中', waiting: '等待中', unavailable: '暂不可用', stale: '已过期', degraded: '降级', link: '查看研究证据' }
const TRADITIONAL: StrategyResearchOverviewCopy = { title: '策略研究覆蓋', coverage: '擴容覆蓋', updated: '最後更新', stableLabel: '13 股穩定 shadow', expandedLabel: '97 標的擴容 research', running: '運行中', waiting: '等待中', unavailable: '暫不可用', stale: '已過期', degraded: '降級', link: '檢視研究證據' }

function stateLabel(state: StrategyResearchOverview['expandedState'], copy: StrategyResearchOverviewCopy): string {
  return state === 'unavailable' ? copy.unavailable : state === 'healthy' ? copy.running : state === 'waiting' ? copy.waiting : state === 'stale' ? copy.stale : copy.degraded
}

export function StrategyResearchOverviewCard({ overview, locale = 'zh-Hans', copy, formatTimestamp = (value) => value ?? '—', href = '/reports?view=影子策略研究&research_scope=expanded' }: StrategyResearchOverviewCardProps) {
  const text = copy ?? (locale === 'zh-Hant' ? TRADITIONAL : SIMPLIFIED)
  const stableStatus = overview.stableState === 'running' ? text.running : overview.stableState === 'waiting' ? text.waiting : text.unavailable
  const expandedStatus = stateLabel(overview.expandedState, text)
  return <article className="strategy-research-overview-card data-panel">
    <header className="strategy-research-overview-card-header"><div><span>RESEARCH COVERAGE</span><h2>{text.title}</h2></div><FlaskConical aria-hidden="true" size={18} /></header>
    <div className="strategy-research-overview-card-grid"><div><ShieldCheck aria-hidden="true" size={15} /><span><strong>{text.stableLabel}</strong><small>{stableStatus}</small></span></div><div><FlaskConical aria-hidden="true" size={15} /><span><strong>{text.expandedLabel}</strong><small>{expandedStatus}</small></span></div></div>
    <dl className="strategy-research-overview-card-meta"><div><dt>{text.coverage}</dt><dd>{overview.expandedCoverage === null ? '—' : `${overview.expandedCoverage}/${overview.expandedCount}`}</dd></div><div><dt>{text.updated}</dt><dd>{formatTimestamp(overview.expandedUpdatedAt)}</dd></div></dl>
    <a className="strategy-research-overview-card-link" href={href}>{text.link} <ArrowUpRight aria-hidden="true" size={14} /></a>
  </article>
}

export type { StrategyResearch97State }
