import { ArrowDownRight, ArrowUpRight, ChevronDown, Clock3, History, ListChecks, ShieldCheck, WalletCards } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import { PageHeader } from '../components/PageHeader'
import { WorkspaceState } from '../components/WorkspaceState'
import { apiPositionToPosition, formatTime } from '../data/adapters'
import { getFormatLocale } from '../i18n/runtime'
import { useLocale } from '../i18n/useLocale'
import type { Market, Position } from '../types'

const actionLabels = { buy: '买入', hold: '持有', reduce: '减仓', exit: '退出', short: '空头', cover: '回补', wait: '等待' }
const marketLabels: Record<Market | 'HK', string> = { US: '美股', CN: 'A股', HK: '港股' }
type AccountMarket = Market | 'HK'

type ClosedOrder = {
  id: string
  symbol: string
  market: AccountMarket
  currency: 'USD' | 'CNY' | 'HKD'
  instrumentType: 'stock' | 'option'
  direction: 'LONG' | 'SHORT'
  openedAt: string
  closedAt: string
  quantity: number
  entry: number
  exit: number
  pnl: number
}

type TimelineItem = {
  id: string
  symbol: string
  market: Market
  instrumentType: 'stock' | 'option'
  action: string
  quantity: number
  price: number
  createdAt: string
  status: 'verified' | 'pending' | 'rejected'
  side: 'BUY' | 'SELL'
}

function formatAmount(value: number | null | undefined, currency: string, signed = false) {
  if (value == null) return '未记录'
  const sign = signed && value > 0 ? '+' : ''
  return `${sign}${new Intl.NumberFormat(getFormatLocale(), { maximumFractionDigits: 2 }).format(value)} ${currency}`
}

function PositionRow({ position, onResearch }: { position: Position; onResearch: (position: Position) => void }) {
  const [expanded, setExpanded] = useState(false)
  const unit = position.instrumentType === 'option' ? '张' : '股'
  return <article className={`account-position-row ${expanded ? 'is-expanded' : ''}`}>
    <div className="account-symbol"><button className="account-symbol-link" type="button" onClick={() => onResearch(position)}><strong>{position.symbol}</strong><small>{position.name} · {marketLabels[position.market]} · 打开股票研究</small></button></div>
    <div><span>数量</span><strong>{position.quantity} {unit}</strong></div>
    <div><span>成本 / 记录价</span><strong>{position.averagePrice.toFixed(2)} / {position.lastPrice.toFixed(2)}</strong></div>
    <div><span>未实现盈亏</span><strong className={position.unrealizedPnl >= 0 ? 'positive-text' : 'negative-text'}>{position.unrealizedPnl >= 0 ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}{position.unrealizedPnl.toFixed(2)} <small>{position.unrealizedPnlPct.toFixed(2)}%</small></strong></div>
    <span className={`action-pill ${position.action}`}>{actionLabels[position.action]}</span>
    <button className="account-position-expand icon-button" type="button" aria-expanded={expanded} aria-label={`${expanded ? '收起' : '展开'} ${position.symbol} 持仓详情`} title={expanded ? '收起持仓详情' : '展开持仓详情'} onClick={() => setExpanded((current) => !current)}><ChevronDown size={16} /></button>
  </article>
}

export function PortfolioPage() {
  const workspace = useWorkspace()
  const { formatLocale } = useLocale()
  const navigate = useNavigate()
  const [market, setMarket] = useState<AccountMarket>('US')
  const authenticated = workspace.mode === 'authenticated'
  // This page is intentionally fail-closed: personal paper orders and local
  // demo fixtures never represent CicloTrade's immutable validation ledger.
  const portfolio = authenticated ? workspace.data?.portfolio : undefined
  const shownPositions = useMemo(
    () => (portfolio?.positions ?? []).map(apiPositionToPosition),
    [portfolio],
  )
  const marketPositions = shownPositions.filter((position) => position.market === market)
  const activity = portfolio?.activity
  const intervalDirection = new Map((activity?.intervals ?? []).map((item) => [item.interval_id, item.direction]))
  const officialTimeline = (activity?.execution_previews_by_market?.[market]
    ?? (activity?.executions ?? []).filter((item) => item.market === market))
    .map<TimelineItem>((item) => {
      const direction = intervalDirection.get(item.interval_id)
      const action = direction === 'SHORT'
        ? item.side === 'BUY' ? '买入平空' : '卖出做空'
        : item.side === 'BUY' ? '买入 / 增持' : '卖出 / 平多'
      return {
        id: item.execution_id,
        symbol: item.symbol,
        market: item.market,
        instrumentType: item.instrument_type,
        action,
        quantity: item.quantity,
        price: item.price,
        createdAt: formatTime(item.executed_at, formatLocale),
        status: 'verified',
        side: item.side,
      }
    })
  const timeline = officialTimeline.slice(0, 8)
  const closed = (activity?.intervals ?? [])
    .filter((item) => item.market === market && item.status === 'CLOSED')
    .map<ClosedOrder>((item) => ({
      id: item.interval_id,
      symbol: item.symbol,
      market: item.market,
      currency: item.currency,
      instrumentType: item.instrument_type,
      direction: item.direction,
      openedAt: formatTime(item.opened_at, formatLocale),
      closedAt: formatTime(item.closed_at, formatLocale),
      quantity: item.opened_quantity,
      entry: item.average_entry_price,
      exit: item.average_exit_price ?? 0,
      pnl: item.realized_pnl ?? 0,
    }))
  const currency = market === 'CN' ? 'CNY' : market === 'HK' ? 'HKD' : 'USD'
  const account = portfolio?.accounts?.[market]
  const accountAvailable = account?.status === 'recorded' || account?.status === 'not_recorded'
  const accountUnavailableLabel = account ? '尚未接入' : '账户不可用'
  const accountEmptyText = !account
    ? `${marketLabels[market]}官方验证账户合同不可用。`
    : account.status === 'not_connected'
      ? `${marketLabels[market]}官方验证账户尚未接入。`
      : `${marketLabels[market]}尚无官方权益快照。`
  const balance = account?.total_equity
  const marketValue = account?.market_value
  const unrealized = account?.unrealized_pnl
  const realized = account?.realized_pnl
  const capturedAt = account?.captured_at ? formatTime(account.captured_at, formatLocale) : '未记录快照时间'
  const returnedExecutionLimit = activity?.returned_execution_limit ?? 0
  const activityTruncated = activity?.truncated ?? false
  const officialRecordCount = (() => {
    const reported = activity?.execution_counts_by_market?.[market]
    return typeof reported === 'number' && Number.isFinite(reported) && reported >= 0
      ? reported
      : officialTimeline.length
  })()
  const marketHasOfficialRecords = Boolean(
    officialRecordCount
    || marketPositions.length
    || (activity?.intervals ?? []).some((item) => item.market === market),
  )
  const hasOfficialRecords = Boolean(
    portfolio && (
      portfolio.orders.length
      || portfolio.positions.length
      || activity?.executions.length
      || activity?.intervals.length
    ),
  )
  const timelineEmptyText = account?.status === 'not_recorded'
    ? accountEmptyText
    : `${marketLabels[market]}暂无时间线记录。`
  const positionsEmptyText = account?.status === 'not_recorded'
    ? accountEmptyText
    : `${marketLabels[market]}目前没有持仓。`
  const closedEmptyText = account?.status === 'not_recorded'
    ? accountEmptyText
    : `${marketLabels[market]}暂无已平仓记录。`

  return <div className="page operations-page">
    <PageHeader kicker="PORTFOLIO / OFFICIAL VALIDATION" title="官方验证模拟组合与复盘" description="只读取 CicloTrade 官方验证模拟账户；它不是你的个人模拟或券商实盘账户，也不会自动下单。USD、CNY 和 HKD 始终分开查看。" />
    <WorkspaceState empty={!hasOfficialRecords} emptyText="当前没有 CicloTrade 官方验证记录；系统不会用个人练习订单或演示订单填充。" />
    <div className="account-source-bar">
      <span><WalletCards size={18} /> CicloTrade 官方验证模拟</span>
      <strong><i className={marketHasOfficialRecords ? 'positive-dot' : 'neutral-dot'} /> {account?.status === 'not_connected' ? `${marketLabels[market]}尚未接入` : marketHasOfficialRecords ? `${marketLabels[market]}不可变日志已同步` : `${marketLabels[market]}暂无官方记录`}</strong>
      <small><Clock3 size={14} /> {authenticated ? `快照 ${capturedAt} · ${portfolio?.mark_source ?? '来源未记录'} · 记录价非实时可成交报价` : '登录并同步官方验证记录后显示。'}</small>
    </div>

    <div className="account-market-tabs" role="tablist" aria-label="官方验证账户市场">
      {(['US', 'HK', 'CN'] as AccountMarket[]).map((value) => <button className={market === value ? 'active' : ''} type="button" role="tab" aria-selected={market === value} onClick={() => setMarket(value)} key={value}>{marketLabels[value]}</button>)}
    </div>

    <section className="metric-grid portfolio-metrics" aria-label={`${marketLabels[market]}账户摘要`}>
      <article><span>{marketLabels[market]}模拟余额</span><strong>{accountAvailable ? formatAmount(balance, currency) : accountUnavailableLabel}</strong><small>{account?.status === 'not_recorded' ? '尚无官方权益快照' : accountAvailable ? `${currency} 独立显示` : accountEmptyText}</small></article>
      <article><span>记录持仓市值</span><strong>{accountAvailable ? formatAmount(marketValue, currency) : accountUnavailableLabel}</strong><small>按最后记录价估算</small></article>
      <article><span>已实现 / 未实现</span><strong className={accountAvailable && (realized ?? 0) + (unrealized ?? 0) >= 0 ? 'positive-text' : accountAvailable ? 'negative-text' : ''}>{accountAvailable ? `${formatAmount(realized, currency, true)} / ${formatAmount(unrealized, currency, true)}` : accountUnavailableLabel}</strong><small>不会跨币种相加</small></article>
      <article><span>官方验证记录</span><strong>{accountAvailable ? officialRecordCount : accountUnavailableLabel}</strong><small>{accountAvailable ? `${marketPositions.length} 个当前持仓` : accountEmptyText}</small></article>
    </section>

    <section className="account-layout account-simulator-grid">
      <article className="data-panel account-timeline">
        <header className="panel-heading"><div><span>OFFICIAL ACTION TIMELINE</span><h2>交易时间线</h2></div><ListChecks size={20} /></header>
        {!accountAvailable ? <div className="inline-empty">{accountEmptyText}</div> : timeline.length ? <div className="timeline-list">{timeline.map((item) => <div className="timeline-item" key={item.id}><span className={`timeline-dot ${item.side === 'BUY' ? 'buy' : 'sell'}`} /><div><strong>{item.action} {item.symbol} · {item.quantity} {item.instrumentType === 'option' ? '张' : '股'}</strong><small>{item.createdAt} · {item.status === 'verified' ? '官方日志记录' : item.status === 'rejected' ? '风险闸门拒绝' : '等待处理'}</small></div><b>{item.price.toFixed(2)}</b></div>)}</div> : <div className="inline-empty">{timelineEmptyText}</div>}
        <div className={`portfolio-projection-note ${activityTruncated ? 'warning' : ''}`} role="note"><strong>{activityTruncated ? '执行记录已截断' : '执行记录范围'}</strong><span>当前首屏显示 {timeline.length} 条；服务端返回上限 {returnedExecutionLimit || '未提供'}。{activityTruncated ? '请打开验证报告查看完整范围与导出限制。' : '当前响应未声明截断。'}</span></div>
        <button className="button tertiary account-timeline-link" type="button" onClick={() => navigate('/reports')}><History size={15} /> 打开验证报告</button>
      </article>
      <article className="data-panel account-current">
        <header className="panel-heading"><div><span>{market} / OPEN POSITIONS</span><h2>当前持仓及建议</h2></div><span className="status-chip official"><ShieldCheck size={14} /> 官方验证</span></header>
        {!accountAvailable ? <div className="inline-empty">{accountEmptyText}</div> : marketPositions.length ? <div className="account-position-list">{marketPositions.map((position) => <PositionRow position={position} onResearch={(item) => navigate(`/research?market=${item.market}&symbol=${encodeURIComponent(item.symbol)}`)} key={`${position.symbol}-${position.name}`} />)}</div> : <div className="inline-empty">{positionsEmptyText}</div>}
        <div className="portfolio-capability-lock" role="note"><ShieldCheck size={17} /><div><strong>风险与计划偏差能力尚未接入</strong><span>当前只展示真实快照、持仓和执行日志，不生成伪风险分数或偏差结论。请从验证报告核对现有证据。</span></div></div>
      </article>
      <article className="data-panel account-closed">
        <header className="panel-heading"><div><span>CLOSED ORDERS / REALIZED P&amp;L</span><h2>已平仓订单及盈亏</h2></div><span className="status-chip research">逐组核对</span></header>
        {!accountAvailable ? <div className="inline-empty">{accountEmptyText}</div> : closed.length ? <div className="closed-order-list">{closed.map((item) => <div className="closed-order-row" key={item.id}><div><strong>{item.symbol} · {item.quantity} {item.instrumentType === 'option' ? '张' : '股'}</strong><small>{item.openedAt} 开仓 → {item.closedAt} 平仓</small></div><div><span>{item.direction === 'SHORT' ? '卖出开仓' : '买入开仓'} {item.entry.toFixed(2)} · {item.direction === 'SHORT' ? '买入平空' : '卖出平多'} {item.exit.toFixed(2)}</span><strong className={item.pnl >= 0 ? 'positive-text' : 'negative-text'}>{formatAmount(item.pnl, item.currency, true)}</strong></div></div>)}</div> : <div className="inline-empty">{closedEmptyText}</div>}
      </article>
    </section>
  </div>
}
