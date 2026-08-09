import { AlertTriangle, Calculator, CheckCircle2, LockKeyhole, ShieldCheck } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import { BrowserApiError, createPaperOrder } from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { getFormatLocale } from '../i18n/runtime'

export function TradePage() {
  const [searchParams] = useSearchParams()
  const workspace = useWorkspace()
  const symbol = searchParams.get('symbol') ?? 'AAPL'
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY')
  const [quantity, setQuantity] = useState(10)
  const [price, setPrice] = useState(213.45)
  const [submitted, setSubmitted] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const estimated = useMemo(() => Math.max(0, quantity) * Math.max(0, price), [quantity, price])
  const positionLimit = workspace.data?.settings.risk.max_position_per_symbol ?? 5_000
  const totalLimit = workspace.data?.settings.risk.max_total_position ?? 50_000
  const positionUsage = positionLimit ? estimated / positionLimit * 100 : 0

  return (
    <div className="page operations-page">
      <PageHeader kicker="TRADE / CONTROLLED EXECUTION" title="受控交易" description="模拟盘默认开启。每笔订单先计算金额并经过仓位、亏损与冷却期检查。" />
      <div className="mode-banner"><div><span>当前账户</span><strong>模拟交易 PAPER</strong></div><span className="status-chip official"><ShieldCheck size={14} /> 不会发送到真实券商</span><button className="button secondary" type="button" disabled><LockKeyhole size={15} /> 实盘需独立开通</button></div>

      <div className="trade-layout">
        <section className="trade-ticket data-panel">
          <header className="panel-heading"><div><span>ORDER TICKET</span><h2>{symbol} · 模拟订单</h2></div><strong className="quote-value">{price.toFixed(2)} <small className="positive-text">参考价</small></strong></header>
          <div className="segmented-control" aria-label="订单方向"><button className={side === 'BUY' ? 'active buy' : ''} type="button" onClick={() => setSide('BUY')}>买入</button><button className={side === 'SELL' ? 'active sell' : ''} type="button" onClick={() => setSide('SELL')}>卖出</button></div>
          <div className="ticket-fields">
            <label><span>订单类型</span><div className="inline-options"><button className="active" type="button">限价</button><button type="button" disabled title="等待实时行情源接入">市价</button><button type="button" disabled title="等待条件单服务接入">止损限价</button></div></label>
            <label><span>数量（股）</span><div className="stepper"><button type="button" aria-label="减少数量" onClick={() => setQuantity((value) => Math.max(1, value - 1))}>−</button><input aria-label="数量" min="1" type="number" value={quantity} onChange={(event) => setQuantity(Number(event.target.value))} /><button type="button" aria-label="增加数量" onClick={() => setQuantity((value) => value + 1)}>+</button></div></label>
            <label><span>限价（USD）</span><input min="0.01" step="0.01" type="number" value={price} onChange={(event) => setPrice(Number(event.target.value))} /></label>
          </div>
          <dl className="order-estimate"><div><dt>预计金额</dt><dd>USD {estimated.toLocaleString(getFormatLocale(), { maximumFractionDigits: 2 })}</dd></div><div><dt>单标的限额占用</dt><dd>{positionUsage.toFixed(2)}%</dd></div><div><dt>订单有效期</dt><dd>当日有效</dd></div></dl>
          {!confirmOpen && <button className={`button wide ${side === 'BUY' ? 'primary' : 'danger'}`} type="button" disabled={submitting || quantity < 1 || price <= 0} onClick={() => {
            setError('')
            if (workspace.mode !== 'authenticated') {
              setError('请先登录，模拟订单才会写入你的账户。')
              return
            }
            setConfirmOpen(true)
          }}>{side === 'BUY' ? '复核模拟买入' : '复核模拟卖出'}</button>}
          {confirmOpen && <div className="order-confirmation" role="group" aria-label="模拟订单最终确认"><span><strong>最终确认</strong><small>{side === 'BUY' ? '买入' : '卖出'} {symbol} · {quantity} 股 · 限价 {price.toFixed(2)} · 预计 USD {estimated.toLocaleString(getFormatLocale())}</small></span><div><button className="button secondary" type="button" disabled={submitting} onClick={() => setConfirmOpen(false)}>返回修改</button><button className={`button ${side === 'BUY' ? 'primary' : 'danger'}`} type="button" disabled={submitting} onClick={async () => {
            setSubmitting(true)
            try {
              const order = await createPaperOrder({ symbol, side, quantity, price })
              setSubmitted(order.order_id)
              setConfirmOpen(false)
              try {
                await workspace.refresh()
              } catch {
                setError('模拟订单已提交，但账户列表刷新失败；请稍后重新打开组合页。')
              }
            } catch (caught) {
              setError(caught instanceof BrowserApiError ? caught.message : '模拟订单提交失败。')
            } finally {
              setSubmitting(false)
            }
          }}>{submitting ? '风控检查中…' : '确认并提交'}</button></div></div>}
          {error && <p className="form-error trade-error" role="alert">{error}</p>}
          {submitted && <div className="inline-success" role="status"><CheckCircle2 size={18} /><span><strong>模拟订单已成交</strong><small>{submitted} · 已写入模拟持仓，不会发送到真实券商。</small></span><button type="button" onClick={() => setSubmitted('')}>关闭</button></div>}
        </section>

        <aside className="risk-check-panel data-panel">
          <header className="panel-heading"><div><span>PRE-TRADE RISK</span><h2>下单前检查</h2></div><Calculator size={20} /></header>
          <ul className="check-list"><li><CheckCircle2 /><span><strong>单标的仓位</strong><small>本单占当前限额 {positionUsage.toFixed(1)}%，上限 USD {positionLimit.toLocaleString(getFormatLocale())}</small></span></li><li><CheckCircle2 /><span><strong>账户总风险</strong><small>总仓位上限 USD {totalLimit.toLocaleString(getFormatLocale())}，提交时重新核验</small></span></li><li><CheckCircle2 /><span><strong>连续亏损冷却</strong><small>提交时读取账户最新冷却状态</small></span></li><li><AlertTriangle className="warning-text" /><span><strong>行情新鲜度</strong><small>当前价格由用户输入，不是实时行情报价</small></span></li></ul>
          <div className="risk-verdict"><span>风控结论</span><strong><ShieldCheck size={18} /> 允许模拟验证</strong><small>最终结果以提交瞬间的真实仓位和行情为准</small></div>
        </aside>
      </div>
    </div>
  )
}
