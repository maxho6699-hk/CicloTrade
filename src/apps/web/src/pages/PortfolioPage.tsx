import { ArrowDownRight, ArrowUpRight, Clock3, Plus, ShieldCheck, WalletCards } from 'lucide-react'
import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import { PageHeader } from '../components/PageHeader'
import { WorkspaceState } from '../components/WorkspaceState'
import { apiOrderToOrder, apiPositionToPosition } from '../data/adapters'
import { orders, portfolioSummary, positions } from '../data/workspace'
import { getFormatLocale } from '../i18n/runtime'
import { useLocale } from '../i18n/useLocale'

const actionLabels = { buy: '买入', hold: '持有', reduce: '减仓', exit: '退出', wait: '等待' }

export function PortfolioPage() {
  const workspace = useWorkspace()
  const { formatLocale } = useLocale()
  const navigate = useNavigate()
  const usingReal = workspace.mode === 'authenticated'
  const shownPositions = useMemo(() => usingReal ? (workspace.data?.portfolio.positions ?? []).map(apiPositionToPosition) : positions, [usingReal, workspace.data])
  const shownOrders = useMemo(() => usingReal ? (workspace.data?.portfolio.orders ?? []).map((item) => apiOrderToOrder(item, formatLocale)) : orders, [formatLocale, usingReal, workspace.data])
  const marketValue = shownPositions.reduce((total, item) => total + item.marketValue, 0)
  const unrealized = shownPositions.reduce((total, item) => total + item.unrealizedPnl, 0)
  const realized = usingReal ? workspace.data?.portfolio.realized_pnl ?? 0 : portfolioSummary.totalPnl
  return (
    <div className="page operations-page">
      <PageHeader kicker="PORTFOLIO / RISK" title="组合与仓位" description="先看账户风险，再处理需要行动的仓位。模拟账户与实盘账户始终明确分开。" />
      <WorkspaceState empty={usingReal && shownPositions.length === 0} emptyText="当前账户还没有模拟成交仓位；可前往交易页建立第一笔模拟订单。" />
      <div className="account-source-bar">
        <span><WalletCards size={18} /> 模拟账户</span>
        <strong><i className="positive-dot" /> {usingReal ? '历史成交已同步' : '演示数据'}</strong>
        <small><Clock3 size={14} /> {usingReal ? '估值来自最后记录成交，不是实时行情' : portfolioSummary.freshness}</small>
      </div>

      <section className="metric-grid portfolio-metrics" aria-label="账户摘要">
        <article><span>记录持仓市值</span><strong>USD {marketValue.toLocaleString(getFormatLocale(), { maximumFractionDigits: 2 })}</strong><small>按最后成交价估算</small></article>
        <article><span>未实现盈亏</span><strong className={unrealized >= 0 ? 'positive-text' : 'negative-text'}>{unrealized >= 0 ? '+' : ''}{unrealized.toLocaleString(getFormatLocale(), { maximumFractionDigits: 2 })}</strong><small>不是实时盯市</small></article>
        <article><span>已实现盈亏</span><strong className={realized >= 0 ? 'positive-text' : 'negative-text'}>{realized >= 0 ? '+' : ''}{realized.toLocaleString(getFormatLocale(), { maximumFractionDigits: 2 })}</strong><small>来自模拟成交记录</small></article>
        <article><span>模拟订单</span><strong>{shownOrders.length}</strong><small>{shownPositions.length} 个当前仓位</small></article>
      </section>

      <section className="data-panel">
        <header className="panel-heading"><div><span>POSITIONS</span><h2>当前仓位与建议</h2></div><span className="status-chip official"><ShieldCheck size={14} /> 风险闸门正常</span></header>
        <div className="responsive-table">
          <table>
            <thead><tr><th>标的</th><th>数量</th><th>成本</th><th>现价</th><th>市值</th><th>未实现盈亏</th><th>当前建议</th></tr></thead>
            <tbody>{shownPositions.map((position) => <tr key={position.symbol}>
              <td><strong>{position.symbol}</strong><small>{position.name} · {position.market === 'US' ? '美股' : 'A股'}</small></td>
              <td>{position.quantity}</td><td>{position.averagePrice.toFixed(2)}</td><td>{position.lastPrice.toFixed(2)}</td><td>{position.marketValue.toLocaleString(getFormatLocale())}</td>
              <td className={position.unrealizedPnl >= 0 ? 'positive-text' : 'negative-text'}>{position.unrealizedPnl >= 0 ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}{position.unrealizedPnl.toFixed(2)} <small>{position.unrealizedPnlPct.toFixed(2)}%</small></td>
              <td><span className={`action-pill ${position.action}`}>{actionLabels[position.action]}</span></td>
            </tr>)}</tbody>
          </table>
        </div>
      </section>

      <section className="data-panel">
        <header className="panel-heading"><div><span>ORDER ACTIVITY</span><h2>最近模拟订单</h2></div><button className="button tertiary" type="button" onClick={() => navigate('/trade')}><Plus size={15} /> 新建模拟订单</button></header>
        <div className="compact-list">{shownOrders.map((order) => <article key={order.id}>
          <span className={`side-mark ${order.side.toLowerCase()}`}>{order.side === 'BUY' ? '买' : '卖'}</span>
          <div><strong>{order.symbol} · {order.quantity} 股</strong><small>{order.id} · {order.createdAt}</small></div>
          <div className="list-value"><strong>{order.price.toFixed(2)}</strong><small className={order.status === 'FILLED' ? 'positive-text' : order.status === 'REJECTED' ? 'negative-text' : 'warning-text'}>{order.status === 'FILLED' ? '已成交' : order.status === 'REJECTED' ? '已拒绝' : '处理中'}</small></div>
        </article>)}</div>
      </section>
    </div>
  )
}
