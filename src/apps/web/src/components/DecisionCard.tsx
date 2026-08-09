import { ArrowRight, CandlestickChart, CircleAlert, Clock3, FlaskConical, ShieldCheck } from 'lucide-react'
import type { Decision } from '../types'
import { useNavigate } from 'react-router-dom'

interface DecisionCardProps {
  decision: Decision
  compact?: boolean
  demo?: boolean
}

const actionLabels = {
  buy: '买入 / 增持',
  hold: '继续持有',
  reduce: '减仓 / 回避',
  exit: '全部退出',
  wait: '等待确认',
}

export function DecisionCard({ decision, compact = false, demo = false }: DecisionCardProps) {
  const navigate = useNavigate()
  const positive = decision.action === 'buy' || decision.action === 'hold'
  const tone = positive ? 'positive' : decision.action === 'wait' ? 'neutral' : 'negative'
  const isOfficial = decision.state === 'official' && !demo

  return (
    <article className={`decision-card ${compact ? 'compact' : ''}`}>
      <header className="decision-card-header">
        <div>
          <span className={`status-chip ${isOfficial ? 'official' : 'research'}`}>
            {isOfficial ? <ShieldCheck size={14} /> : <FlaskConical size={14} />}
            {demo ? '界面演示行动' : isOfficial ? '正式量化事件' : '影子研究候选'}
          </span>
          <h2>{decision.instrument.symbol} · {decision.title}</h2>
          <p>{decision.instrument.name} · {decision.instrument.market === 'US' ? '美股' : 'A股'}</p>
        </div>
        <div className={`action-value ${tone}`}>
          <small>当前动作</small>
          <strong>{actionLabels[decision.action]}</strong>
        </div>
      </header>

      {!compact && <p className="decision-summary">{decision.summary}</p>}

      <dl className="decision-metrics">
        <div><dt>关注区间</dt><dd>{decision.entry}</dd></div>
        <div><dt>失效 / 止损</dt><dd>{decision.stop}</dd></div>
        <div><dt>目标情景</dt><dd>{decision.target}</dd></div>
        {!compact && <div><dt>最大建模风险</dt><dd>{decision.maxLoss}</dd></div>}
      </dl>

      {!compact && (
        <div className="decision-evidence">
          <section>
            <h3><ShieldCheck size={15} /> 支持证据</h3>
            <ul>{decision.evidence.map((item) => <li key={item}>{item}</li>)}</ul>
          </section>
          <section className="counter-evidence">
            <h3><CircleAlert size={15} /> 反向证据</h3>
            <ul>{decision.counterEvidence.map((item) => <li key={item}>{item}</li>)}</ul>
          </section>
        </div>
      )}

      <footer className="decision-footer">
        <div className="provenance">
          <span><Clock3 size={13} /> {decision.updatedAt}</span>
          <span>{decision.modelVersion}</span>
          <span>{decision.eventId}</span>
        </div>
        <div className="decision-actions">
          <button className="icon-button" type="button" title="打开 K 线" aria-label="打开 K 线" onClick={() => navigate(`/markets?symbol=${decision.instrument.symbol}`)}>
            <CandlestickChart size={17} />
          </button>
          <button className="button secondary" type="button" onClick={() => navigate(`/markets?symbol=${decision.instrument.symbol}&tab=信号时间线`)}>查看证据</button>
          <button className="button primary" type="button" onClick={() => navigate(`/trade?symbol=${decision.instrument.symbol}`)}>
            模拟验证 <ArrowRight size={16} />
          </button>
        </div>
      </footer>
    </article>
  )
}
