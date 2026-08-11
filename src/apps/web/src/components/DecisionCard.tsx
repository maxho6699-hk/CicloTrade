import { ArrowRight, CandlestickChart, CircleAlert, Clock3, FlaskConical, ShieldCheck } from 'lucide-react'
import type { Decision } from '../types'
import { useNavigate } from 'react-router-dom'

interface DecisionCardProps {
  decision: Decision
  compact?: boolean
  demo?: boolean
}

export function DecisionCard({ decision, compact = false, demo = false }: DecisionCardProps) {
  const navigate = useNavigate()
  const positive = decision.actionable && (decision.action === 'buy' || decision.action === 'hold' || decision.action === 'cover')
  const tone = !decision.actionable || decision.action === 'wait' ? 'neutral' : positive ? 'positive' : 'negative'
  const isOfficial = decision.state === 'official' && !demo

  return (
    <article className={`decision-card ${compact ? 'compact' : ''}`}>
      <header className="decision-card-header">
        <div>
          <span className={`status-chip ${isOfficial ? 'official' : 'research'}`}>
            {isOfficial ? <ShieldCheck size={14} /> : <FlaskConical size={14} />}
            {demo ? '界面演示 · 不是真实建议' : isOfficial ? '官方记录 · 可核对证据' : '研究候选 · 尚未批准'}
          </span>
          <h2>{decision.instrument.symbol} · {decision.title}</h2>
          <p>{decision.instrument.name} · {decision.instrument.market === 'US' ? '美股' : 'A股'}</p>
        </div>
        <div className={`action-value ${tone}`}>
          <small>现在怎么做</small>
          <strong>{demo ? '界面演示，不可交易' : decision.currentInstruction}</strong>
        </div>
      </header>

      {!compact && <p className="decision-summary">{decision.summary}</p>}

      {!compact && <section className="decision-plain-language" aria-label="简单说明">
        <div className="plain-action"><span>一句话先看</span><strong>{demo ? '这是界面演示，不能据此交易' : decision.currentInstruction}</strong></div>
        {decision.plainLanguage ? <div className="plain-reason-grid"><p><b>为什么</b>{decision.plainLanguage.reason}</p><p><b>从什么时候</b>{decision.plainLanguage.setup}</p>{decision.plainLanguage.rebound && <p><b>有没有反弹</b>{decision.plainLanguage.rebound}</p>}{decision.action !== 'reduce' && decision.action !== 'exit' && decision.action !== 'cover' && decision.plainLanguage.takeProfit && <p><b>可能到哪里止盈</b>{decision.plainLanguage.takeProfit}</p>}<p><b>新手数量</b>{decision.plainLanguage.quantityHint}</p></div> : <p><strong>{decision.action === 'buy' ? '不要一次买满。' : decision.action === 'short' ? '美股可以做空，但必须先确认券商保证金与可借券。' : decision.action === 'cover' ? '只用于已有空头仓位，不会建立新的多头。' : decision.action === 'wait' ? '等待条件出现，不代表错过。' : decision.action === 'reduce' || decision.action === 'exit' ? '只针对已有仓位，不代表要做空。' : '先按原计划观察，不要因为涨跌临时追单。'}</strong></p>}
        <small>信号强度不是成功率；内容只供研究和官方验证，不保证收益。</small>
      </section>}
      <dl className="decision-metrics">
        <div><dt>当前报价</dt><dd>{demo ? '演示数据' : decision.currentPrice}</dd></div>
        <div><dt>数量</dt><dd>{demo ? '0（不可交易）' : decision.quantityHint}</dd></div>
        {!compact && <div><dt>报价时间</dt><dd>{demo ? '没有真实报价时间' : decision.quoteUpdatedAt}</dd></div>}
        <div><dt>{compact ? '事件参考' : '关注 / 触发条件'}</dt><dd>{decision.entry}</dd></div>
        <div><dt>{compact ? '风险线' : '失效 / 止损'}</dt><dd>{decision.stop}</dd></div>
        {!compact && <div><dt>{decision.action === 'reduce' || decision.action === 'exit' || decision.action === 'cover' ? '风险复评' : '目标情景'}</dt><dd>{decision.action === 'reduce' || decision.action === 'exit' || decision.action === 'cover' ? '等待重评' : decision.target}</dd></div>}
        {!compact && <div><dt>最多可能亏损（估算）</dt><dd>{decision.maxLoss}</dd></div>}
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
          <button className="icon-button" type="button" title="打开 K 线" aria-label="打开 K 线" onClick={() => navigate(`/markets?market=${decision.instrument.market}&symbol=${encodeURIComponent(decision.instrument.symbol)}&event_id=${decision.officialEventId ?? ''}`)}>
            <CandlestickChart size={17} />
          </button>
          <button className="button secondary" type="button" onClick={() => navigate(`/markets?market=${decision.instrument.market}&symbol=${encodeURIComponent(decision.instrument.symbol)}&event_id=${decision.officialEventId ?? ''}&tab=信号时间线`)}>查看证据</button>
          <button className="button primary" type="button" disabled={!decision.officialEventId} onClick={() => navigate(`/portfolio?symbol=${decision.instrument.symbol}&event_id=${decision.officialEventId ?? ''}`)}>
            查看模拟验证结果 <ArrowRight size={16} />
          </button>
        </div>
      </footer>
    </article>
  )
}
