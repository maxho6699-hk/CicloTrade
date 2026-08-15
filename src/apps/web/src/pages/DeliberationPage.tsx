import { ArrowRight, BookOpenCheck, ChartCandlestick, FileQuestion, Scale, ShieldAlert } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { CicloCore } from '../components/paper/CicloCore'
import { EvidenceStrength, TruthState } from '../components/intelligence/IntelligencePrimitives'
import '../styles/intelligence.css'

const SAFE_TASK_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/

export function DeliberationPage() {
  const [searchParams] = useSearchParams()
  const market = searchParams.get('market')?.toUpperCase() || '—'
  const symbol = searchParams.get('symbol')?.toUpperCase() || '—'
  const requestedTaskId = searchParams.get('taskId') || ''
  const taskId = SAFE_TASK_ID.test(requestedTaskId) ? requestedTaskId : ''

  return <div className="intelligence-page deliberation-page">
    <header className="intelligence-page-header">
      <div><span>MULTI-AGENT EVIDENCE REVIEW</span><h1>牛熊多智能体审议</h1><p>四类研究职责只对真实资料进行整理；牛熊双强度不是胜率、概率或买卖结论。</p></div>
      <div className="deliberation-stock-context"><span>市场</span><strong>{market}</strong><span>股票</span><strong>{symbol}</strong></div>
    </header>

    <div className="deliberation-grid">
      <section className="deliberation-researchers" aria-labelledby="researcher-seats-title">
        <header className="intelligence-section-heading"><div><span>FOUR RESEARCH SEATS</span><h2 id="researcher-seats-title">四大研究员席位</h2></div></header>
        <article className="deliberation-seat" data-state="missing"><BookOpenCheck /><div><span>基本面研究员</span><strong>—</strong><small>missing · 暂无真实任务输出</small></div></article>
        <article className="deliberation-seat" data-state="missing"><ChartCandlestick /><div><span>量价研究员</span><strong>—</strong><small>missing · 暂无真实任务输出</small></div></article>
        <article className="deliberation-seat" data-state="missing"><ShieldAlert /><div><span>风险研究员</span><strong>—</strong><small>missing · 暂无真实任务输出</small></div></article>
        <article className="deliberation-seat" data-state="missing"><Scale /><div><span>反证研究员</span><strong>—</strong><small>missing · 暂无真实任务输出</small></div></article>
      </section>

      <section className="intelligence-panel deliberation-core" aria-labelledby="deliberation-core-title">
        <div className="deliberation-core-orbit" aria-hidden="true"><i /><i /><i /></div>
        <CicloCore label="Ciclo 审议中枢等待真实任务" state="locked" />
        <span>CICLO DELIBERATION CORE</span>
        <h2 id="deliberation-core-title">审议中枢等待资料</h2>
        <p>审议 API 尚未返回研究版本、证据快照、方法版本与真实任务节点。页面不会补写综合结论、分歧或辩论内容。</p>
        <TruthState title="综合结论：—" detail="missing · 缺少可核验的多智能体审议结果。" />
        <Link className="intelligence-inline-action" to={`/research${symbol !== '—' ? `?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}` : ''}`}>返回股票研究 <ArrowRight /></Link>
      </section>

      <aside className="deliberation-evidence" aria-labelledby="directional-evidence-title">
        <header className="intelligence-section-heading"><div><span>DIRECTIONAL EVIDENCE</span><h2 id="directional-evidence-title">牛熊独立证据强度</h2></div></header>
        <div className="directional-emblems" aria-hidden="true"><span className="is-bull">牛</span><span className="is-bear">熊</span></div>
        <EvidenceStrength label="支持证据强度" value={null} status={null} coverage={null} methodVersion={null} observedAt={null} availableAt={null} asOf={null} calculatedAt={null} tone="support" />
        <EvidenceStrength label="反向证据强度" value={null} status={null} coverage={null} methodVersion={null} observedAt={null} availableAt={null} asOf={null} calculatedAt={null} tone="counter" />
        <div className="deliberation-evidence-groups">
          <article><strong>支持证据</strong><span>—</span><small>missing</small></article>
          <article><strong>反向证据</strong><span>—</span><small>missing</small></article>
          <article><strong>分歧 / 风险 / 未知</strong><span>—</span><small>missing</small></article>
        </div>
        <p className="intelligence-boundary-note"><FileQuestion />分数必须由服务端绑定 method_version、证据快照、覆盖率与四个时间字段；前端不补算。</p>
      </aside>

      <section className="intelligence-panel deliberation-timeline" aria-labelledby="deliberation-timeline-title">
        <header className="intelligence-section-heading"><div><span>REAL WORKFLOW TIMELINE</span><h2 id="deliberation-timeline-title">审议任务时间轴</h2></div>{taskId && <Link to={`/workflow/${encodeURIComponent(taskId)}`}>查看真实 Workflow <ArrowRight /></Link>}</header>
        <TruthState title="暂无真实审议节点" detail="审议服务没有返回 queued、running、partial、succeeded、failed、cancelled、blocked 或 timed_out 节点；这里不会使用演示日志填充。" />
      </section>
    </div>
  </div>
}
