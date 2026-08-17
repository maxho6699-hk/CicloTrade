import { AlertTriangle, ArrowRight, BookOpenCheck, CheckCircle2, ChevronDown, Clock3, Gauge, ShieldCheck, Sparkles, Target } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import type { RecommendationItem } from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { StockLogo } from '../components/StockLogo'
import { formatMoney, formatTime } from '../components/v2/V2Primitives'
import { recommendationMissingLabels } from '../domain/actionContract'
import '../styles/recommendations.css'

type RecommendationView = 'stock' | 'option'
type MarketView = 'US' | 'CN'
type RecommendationSignal = 'long' | 'short' | 'call' | 'put' | 'wait'

const ACTION_LABELS: Record<string, string> = {
  BUY: '做多', REDUCE: '减仓观察', EXIT: '退出观察', SHORT: '做空', COVER: '回补观察',
}

const SIGNAL_LABELS: Record<RecommendationSignal, string> = {
  long: '做多', short: '做空', call: 'Call', put: 'Put', wait: '等机会',
}

function money(value: number | null | undefined, currency?: string) {
  return typeof value === 'number' && Number.isFinite(value) ? formatMoney(value, currency || 'USD') : '暂无数据'
}

function number(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN') : '暂无数据'
}

function percent(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '暂无数据'
}

function normalizedMarket(item: RecommendationItem): MarketView {
  return String(item.market || 'US').toUpperCase().includes('CN') || String(item.market).includes('A股') ? 'CN' : 'US'
}

function signalFor(item: RecommendationItem): RecommendationSignal {
  if (item.instrument_type === 'option') {
    if (!item.action || ['REDUCE', 'EXIT', 'COVER'].includes(item.action)) return 'wait'
    return item.option_right === 'CALL' ? 'call' : item.option_right === 'PUT' ? 'put' : 'wait'
  }
  return item.action === 'BUY' ? 'long' : item.action === 'SHORT' ? 'short' : 'wait'
}

function premium(item: RecommendationItem) {
  const bid = Number(item.bid)
  const ask = Number(item.ask)
  if (Number.isFinite(bid) && Number.isFinite(ask) && bid >= 0 && ask >= 0) return (bid + ask) / 2
  if (Number.isFinite(ask) && ask >= 0) return ask
  if (Number.isFinite(bid) && bid >= 0) return bid
  return null
}

function returnSpace(item: RecommendationItem) {
  const base = item.instrument_type === 'option' ? premium(item) : item.current_price
  const target = item.target_price
  if (base == null || target == null || !Number.isFinite(base) || !Number.isFinite(target) || base <= 0) return '暂无数据'
  const direction = item.action === 'SHORT' || item.option_right === 'PUT' ? -1 : 1
  const ratio = ((target - base) / base) * direction
  return `${ratio >= 0 ? '+' : ''}${(ratio * 100).toFixed(1)}%`
}

function riskLevel(item: RecommendationItem) {
  if (item.state === 'locked') return '资料受限'
  if (item.instrument_type === 'option') return '高风险'
  if (item.current_price == null || item.stop_price == null || item.current_price <= 0) return '暂无数据'
  const distance = Math.abs(item.current_price - item.stop_price) / item.current_price
  return distance >= 0.08 ? '高风险' : distance >= 0.04 ? '中风险' : '较低风险'
}

function instrumentExplanation(item: RecommendationItem) {
  if (item.instrument_type === 'stock') return '正股价格会直接影响持仓价值；请同时核对目标、止损和仓位边界。'
  const right = item.option_right === 'CALL' ? 'Call 看涨期权' : item.option_right === 'PUT' ? 'Put 看跌期权' : '期权合约'
  return `${right}受方向、到期时间、波动率与流动性共同影响，最大损失可能达到全部权利金。`
}

function RecommendationCard({ item, source, onResearch, onPractice }: { item: RecommendationItem; source: string; onResearch: () => void; onPractice: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const missing = recommendationMissingLabels(item.missing_fields)
  const complete = item.contract_status === 'complete' && missing.length === 0
  const observedAt = item.occurred_at || item.available_at
  const optionPremium = premium(item)
  const cardId = `recommendation-${item.event_id}`
  return <article className={`recommendation-preview-card ${complete ? 'is-complete' : 'is-incomplete'} ${expanded ? 'is-expanded' : ''}`}>
    <header>
      <div className="recommendation-preview-identity"><StockLogo symbol={item.symbol || ''} size="md" /><span><small>{normalizedMarket(item) === 'US' ? '美股' : 'A股'} · {item.instrument_type === 'stock' ? '正股' : item.option_right === 'CALL' ? 'CALL' : item.option_right === 'PUT' ? 'PUT' : '期权'}</small><strong>{item.symbol || '暂无数据'}</strong><em>{item.strategy_name || '暂无数据'}</em></span></div>
      <div className="recommendation-preview-badges"><span className={`recommendation-action is-${(item.action || 'wait').toLowerCase()}`}>{ACTION_LABELS[item.action || ''] || SIGNAL_LABELS[signalFor(item)]}</span><span className={`recommendation-risk is-${riskLevel(item).includes('高') ? 'high' : 'normal'}`}>{riskLevel(item)}</span></div>
    </header>
    <div className="recommendation-metric-rail">
      <div><span>{item.instrument_type === 'option' ? '权利金' : '现价'}</span><strong>{money(item.instrument_type === 'option' ? optionPremium : item.current_price, item.currency)}</strong></div>
      <div><span>目标</span><strong>{money(item.target_price, item.currency)}</strong></div>
      <div><span>{item.instrument_type === 'option' ? '最大风险' : '止损'}</span><strong>{money(item.instrument_type === 'option' ? item.max_loss : item.stop_price, item.currency)}</strong></div>
    </div>
    <section className="recommendation-preview-return"><Target /><span>目标空间</span><strong>{returnSpace(item)}</strong></section>
    <p className="recommendation-preview-reason">{item.rationale || '暂无数据'}</p>
    <button className="recommendation-expand" type="button" aria-expanded={expanded} aria-controls={cardId} onClick={() => setExpanded((current) => !current)}>{expanded ? '收起详细资料' : '展开详细资料'}<ChevronDown /></button>
    <div className="recommendation-detail" id={cardId} hidden={!expanded}>
      <section className="beginner-explanation"><BookOpenCheck /><div><span>新手说明</span><p>{instrumentExplanation(item)}</p></div></section>
      <div className="recommendation-detail-grid">
        <div><span>参考价</span><strong>{money(item.reference_price, item.currency)}</strong></div>
        <div><span>当前价</span><strong>{money(item.current_price, item.currency)}</strong></div>
        <div><span>建议数量</span><strong>{number(item.quantity_hint ?? item.quantity_delta)}</strong></div>
        {item.instrument_type === 'option' && <><div><span>标的</span><strong>{item.symbol || '暂无数据'}</strong></div><div><span>方向</span><strong>{item.option_right === 'CALL' ? 'Call 看涨' : item.option_right === 'PUT' ? 'Put 看跌' : '暂无数据'}</strong></div><div><span>行权价</span><strong>{money(item.option_strike, item.currency)}</strong></div><div><span>到期日</span><strong>{item.option_expiry || '暂无数据'}</strong></div><div><span>买价 / 卖价</span><strong>{money(item.bid, item.currency)} / {money(item.ask, item.currency)}</strong></div><div><span>隐含波动率</span><strong>{percent(item.implied_volatility)}</strong></div><div><span>成交量</span><strong>{number(item.volume)}</strong></div><div><span>未平仓量</span><strong>{number(item.open_interest)}</strong></div></>}
        <div><span>资料来源</span><strong>{source || '暂无数据'}</strong></div>
        <div><span>记录时间</span><strong>{observedAt ? formatTime(observedAt) : '暂无数据'}</strong></div>
      </div>
      <section className={`recommendation-contract-state ${complete ? 'is-complete' : 'is-warning'}`}>{complete ? <CheckCircle2 /> : <AlertTriangle />}<div><strong>{complete ? '关键字段完整' : '资料仍待补全'}</strong><p>{complete ? '字段通过服务端合同检查；行情时效仍需在行动前重新核对。' : missing.length ? `缺少：${missing.join('、')}` : '暂无数据'}</p></div></section>
      <section className="beginner-risk-note"><ShieldCheck /><div><strong>风险边界</strong><p>{item.state === 'locked' ? '当前记录受权限或数据门限制，不应据此行动。' : item.instrument_type === 'option' ? '期权可能归零，并受时间衰减、波动率和流动性影响。' : '正股也会出现回撤、跳空与流动性风险；没有完整风险字段时不要自行补数。'}</p></div></section>
      <footer><span><Clock3 />{item.quote_at ? `报价 ${formatTime(item.quote_at)}` : '报价时间：暂无数据'}</span><div><button className="button secondary" type="button" onClick={onResearch}>查看证据</button><button className="button primary" type="button" disabled={item.state === 'locked'} onClick={onPractice}>{item.instrument_type === 'stock' ? '进入个人模拟' : '打开期权研究'}<ArrowRight /></button></div></footer>
    </div>
  </article>
}

export function RecommendationsPage() {
  const workspace = useWorkspace()
  const navigate = useNavigate()
  const [market, setMarket] = useState<MarketView>('US')
  const [view, setView] = useState<RecommendationView>('stock')
  const [signal, setSignal] = useState<RecommendationSignal>('long')
  const allItems = workspace.data?.recommendations.items ?? []
  const availableSignals: RecommendationSignal[] = view === 'stock' ? ['long', 'short', 'wait'] : ['call', 'put', 'wait']
  const items = useMemo(() => [...allItems]
    .filter((item) => normalizedMarket(item) === market && item.instrument_type === view && signalFor(item) === signal)
    .sort((a, b) => (b.occurred_at || b.available_at || '').localeCompare(a.occurred_at || a.available_at || '')), [allItems, market, signal, view])
  const setRecommendationView = (next: RecommendationView) => {
    setView(next)
    setSignal(next === 'stock' ? 'long' : 'call')
  }
  const openResearch = (item: RecommendationItem) => navigate(`/research?market=${encodeURIComponent(item.market || 'US')}&symbol=${encodeURIComponent(item.symbol || '')}${item.event_id ? `&event_id=${item.event_id}` : ''}${item.instrument_type === 'option' ? '&tab=期权证据' : ''}`)
  const openPractice = (item: RecommendationItem) => item.instrument_type === 'stock'
    ? navigate(`/paper?market=${encodeURIComponent(item.market || 'US')}&symbol=${encodeURIComponent(item.symbol || '')}&source=recommendation&reference=${item.event_id}`)
    : navigate(`/research?market=${encodeURIComponent(item.market || 'US')}&symbol=${encodeURIComponent(item.symbol || '')}&tab=期权证据`)

  return <div className="page recommendations-page">
    <PageHeader kicker="BEGINNER RECOMMENDATIONS" title="正股与期权推荐" description="先用预览卡快速比较方向、目标空间与风险，再按需展开完整字段。推荐只用于研究与个人模拟，不会自动下单。" />
    <section className="recommendation-guide"><Sparkles /><div><strong>推荐不是订单</strong><p>所有卡片来自服务端正式记录；缺失字段统一标为“暂无数据”，不会用估算或虚构内容补齐。</p></div><span><Gauge />{items.length} 条结果</span></section>
    <nav className="recommendation-market-tabs" aria-label="推荐市场"><button className={market === 'US' ? 'active' : ''} type="button" onClick={() => setMarket('US')}>美股 <span>{allItems.filter((item) => normalizedMarket(item) === 'US').length}</span></button><button className={market === 'CN' ? 'active' : ''} type="button" onClick={() => setMarket('CN')}>A股 <span>{allItems.filter((item) => normalizedMarket(item) === 'CN').length}</span></button></nav>
    <div className="recommendation-filter-deck">
      <div className="recommendation-tabs" role="tablist" aria-label="推荐类型"><button className={view === 'stock' ? 'active' : ''} type="button" role="tab" aria-selected={view === 'stock'} onClick={() => setRecommendationView('stock')}>正股</button><button className={view === 'option' ? 'active' : ''} type="button" role="tab" aria-selected={view === 'option'} onClick={() => setRecommendationView('option')}>期权</button></div>
      <div className="recommendation-signal-tabs" role="tablist" aria-label="推荐方向">{availableSignals.map((item) => <button className={signal === item ? 'active' : ''} type="button" role="tab" aria-selected={signal === item} onClick={() => setSignal(item)} key={item}>{SIGNAL_LABELS[item]} <span>{allItems.filter((candidate) => normalizedMarket(candidate) === market && candidate.instrument_type === view && signalFor(candidate) === item).length}</span></button>)}</div>
    </div>
    {items.length ? <section className="recommendation-preview-grid" aria-label={`${market === 'US' ? '美股' : 'A股'}${view === 'stock' ? '正股' : '期权'}推荐列表`}>{items.map((item) => <RecommendationCard key={`${item.event_id}-${item.symbol}`} item={item} source={workspace.data?.recommendations.source || ''} onResearch={() => openResearch(item)} onPractice={() => openPractice(item)} />)}</section> : <section className="recommendation-empty" role="status"><BookOpenCheck /><div><h2>当前分类暂无数据</h2><p>新的正式研究记录通过服务端发布后会显示在这里。可以切换市场、产品或方向查看其他记录。</p></div><button className="button secondary" type="button" onClick={() => navigate('/discover')}>前往发现股票</button></section>}
  </div>
}

export default RecommendationsPage
