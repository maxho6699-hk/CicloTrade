import { ArrowRight, Bot, Database, FileSearch, LockKeyhole, MessageSquareText, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import { CicloCore } from '../components/paper/CicloCore'
import { TruthState } from '../components/intelligence/IntelligencePrimitives'
import '../styles/intelligence.css'

const AI_PROVIDER_AVAILABLE = false

const supportedCapabilities = [
  { icon: FileSearch, title: '研究解释', detail: '整理真实行情、引用、支持与反向证据。' },
  { icon: Database, title: '安全草稿', detail: '只生成研究、提醒或模拟草稿，不提交订单。' },
  { icon: ShieldCheck, title: '风险边界', detail: '显示来源、时间、数据状态、风险和失效条件。' },
] as const

const responseSections = ['结论', '引用与时间', '支持证据', '反向证据', '风险与失效', '下一步'] as const

export function AIWorkspacePage() {
  return <div className="intelligence-page ai-workspace-page">
    <header className="intelligence-page-header">
      <div><span>GLOBAL AI / BOUNDED CONTEXT</span><h1>Ciclo AI 工作台</h1><p>AI 负责解释、比较与生成安全草稿；用户负责审阅证据并作出最终决定。</p></div>
      <span className="intelligence-status is-warning"><i />{AI_PROVIDER_AVAILABLE ? 'available' : 'unavailable'}</span>
    </header>

    <div className="ai-workspace-layout">
      <section className="intelligence-panel ai-core-stage" aria-labelledby="ai-service-title">
        <div className="ai-core-visual"><CicloCore label="Ciclo AI 服务暂不可用" state="locked" /></div>
        <div className="ai-core-copy">
          <span className="ai-core-kicker"><Bot /> CICLO RESEARCH CORE</span>
          <h2 id="ai-service-title">AI 服务暂不可用</h2>
          <p>当前部署没有返回可用的服务提供方（provider）与版本证明，因此不会生成占位回答，不会伪造回答，也不会展示虚假的执行轨迹。</p>
        </div>
        <TruthState
          tone="warning"
          title="输入已锁定"
          detail="服务端返回 provider、模型版本、数据授权和工具能力后，才会开放真实会话。"
          action={<Link className="intelligence-inline-action" to="/research">先查看股票研究 <ArrowRight /></Link>}
        />
        <div className="ai-composer-disabled">
          <label htmlFor="ai-message">向 Ciclo AI 提问</label>
          <div><textarea id="ai-message" disabled aria-describedby="ai-unavailable-reason" placeholder="AI 服务可用后在这里输入问题" /><button type="button" disabled><MessageSquareText />发送问题</button></div>
          <p id="ai-unavailable-reason"><LockKeyhole />当前没有可验证的 AI provider，输入与发送保持禁用。</p>
        </div>
      </section>

      <aside className="ai-workspace-inspector" aria-label="AI 工作台说明">
        <section className="intelligence-panel">
          <header className="intelligence-section-heading"><div><span>RESPONSE CONTRACT</span><h2>结构化回答顺序</h2></div></header>
          <ol className="response-contract-list">{responseSections.map((item, index) => <li key={item}><span>{String(index + 1).padStart(2, '0')}</span><strong>{item}</strong></li>)}</ol>
        </section>
        <section className="intelligence-panel">
          <header className="intelligence-section-heading"><div><span>CAPABILITY BOUNDARY</span><h2>允许的协助范围</h2></div></header>
          <div className="ai-capability-list">{supportedCapabilities.map(({ icon: Icon, title, detail }) => <article key={title}><Icon /><div><strong>{title}</strong><p>{detail}</p></div></article>)}</div>
          <p className="intelligence-boundary-note"><ShieldCheck />自然语言 AI 永久没有订单提交、付款审批、权益审批或自动实盘启用权限。</p>
        </section>
      </aside>
    </div>
  </div>
}
