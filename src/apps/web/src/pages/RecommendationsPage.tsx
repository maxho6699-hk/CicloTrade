import { AlertTriangle, ArrowRight, BookOpenCheck, CheckCircle2, ChevronDown, Clock3, Gauge, ShieldCheck, Sparkles, Target, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
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

function RecommendationCard({ item, onOpenDetail }: { item: RecommendationItem; onOpenDetail: () => void }) {
  const missing = recommendationMissingLabels(item.missing_fields)
  const complete = item.contract_status === 'complete' && missing.length === 0
  const optionPremium = premium(item)
  return <article className={`recommendation-preview-card ${complete ? 'is-complete' : 'is-incomplete'}`}>
    <header>
      <div className="recommendation-preview-identity"><StockLogo symbol={item.symbol || ''} market={normalizedMarket(item)} size="md" /><span><small>{normalizedMarket(item) === 'US' ? '美股' : 'A股'} · {item.instrument_type === 'stock' ? '正股' : item.option_right === 'CALL' ? 'CALL' : item.option_right === 'PUT' ? 'PUT' : '期权'}</small><strong>{item.symbol || '暂无数据'}</strong><em>{item.strategy_name || '暂无数据'}</em></span></div>
      <div className="recommendation-preview-badges"><span className={`recommendation-action is-${(item.action || 'wait').toLowerCase()}`}>AI 研判方向 · {ACTION_LABELS[item.action || ''] || SIGNAL_LABELS[signalFor(item)]}</span><span className={`recommendation-risk is-${riskLevel(item).includes('高') ? 'high' : 'normal'}`}>{riskLevel(item)}</span></div>
    </header>
    <div className="recommendation-metric-rail">
      <div><span>{item.instrument_type === 'option' ? '权利金' : '现价'}</span><strong>{money(item.instrument_type === 'option' ? optionPremium : item.current_price, item.currency)}</strong></div>
      <div><span>目标</span><strong>{money(item.target_price, item.currency)}</strong></div>
      <div><span>{item.instrument_type === 'option' ? '最大风险' : '止损'}</span><strong>{money(item.instrument_type === 'option' ? item.max_loss : item.stop_price, item.currency)}</strong></div>
    </div>
    <section className="recommendation-preview-return"><Target /><span>目标空间</span><strong>{returnSpace(item)}</strong></section>
    <p className="recommendation-preview-reason"><strong>数据支撑分析</strong>{item.rationale || '暂无数据'}</p>
    <button className="recommendation-expand" type="button" aria-haspopup="dialog" onClick={onOpenDetail}>查看完整研判<ChevronDown /></button>
  </article>
}

function RecommendationContextPanel({ item, peers }: { item: RecommendationItem; peers: RecommendationItem[] }) {
  const missing = recommendationMissingLabels(item.missing_fields)
  return <aside className="recommendation-context-panel" aria-label="同类研判对照">
    <header><div><span>SAME FILTER COMPARISON</span><h2>同类研判对照</h2></div><strong>{peers.length + 1} 条真实记录</strong></header>
    <section className="recommendation-context-current">
      <div className="recommendation-preview-identity"><StockLogo symbol={item.symbol || ''} market={normalizedMarket(item)} size="md" /><span><small>当前查看</small><strong>{item.symbol || '暂无数据'}</strong><em>{item.strategy_name || '暂无数据'}</em></span></div>
      <dl><div><dt>目标空间</dt><dd>{returnSpace(item)}</dd></div><div><dt>风险</dt><dd>{riskLevel(item)}</dd></div><div><dt>资料</dt><dd>{item.contract_status === 'complete' && !missing.length ? '字段完整' : missing.length ? `${missing.length} 项缺口` : '待核对'}</dd></div></dl>
    </section>
    <div className="recommendation-peer-list">
      <h3>同市场 · 同产品 · 同方向</h3>
      {peers.length ? peers.map((peer) => {
        const peerMissing = recommendationMissingLabels(peer.missing_fields)
        return <article key={`peer-${peer.event_id}-${peer.symbol}`}><StockLogo symbol={peer.symbol || ''} market={normalizedMarket(peer)} size="sm" /><span><strong>{peer.symbol || '暂无数据'}</strong><small>{peer.strategy_name || '暂无策略名'}</small></span><dl><div><dt>空间</dt><dd>{returnSpace(peer)}</dd></div><div><dt>风险</dt><dd>{riskLevel(peer)}</dd></div><div><dt>资料</dt><dd>{peer.contract_status === 'complete' && !peerMissing.length ? '完整' : `${peerMissing.length || '待核对'}${peerMissing.length ? ' 项' : ''}`}</dd></div></dl></article>
      }) : <p>当前筛选下没有其他真实研判可供对照。</p>}
    </div>
    <footer>仅比较当前服务端记录，不生成胜率、评分或收益承诺。</footer>
  </aside>
}

function RecommendationDetail({ item, source, onResearch, onPractice, onClose }: { item: RecommendationItem; source: string; onResearch: () => void; onPractice: () => void; onClose: () => void }) {
  const missing = recommendationMissingLabels(item.missing_fields)
  const complete = item.contract_status === 'complete' && missing.length === 0
  const observedAt = item.occurred_at || item.available_at
  const drawerRef = useRef<HTMLElement>(null)
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    drawerRef.current?.querySelector<HTMLElement>('button')?.focus()
    return () => previous?.focus()
  }, [])
  const trapFocus = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Tab') return
    const focusable = [...(drawerRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])') ?? [])]
    if (!focusable.length) return
    const first = focusable[0]
    const last = focusable.at(-1) as HTMLElement
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }
  return <aside ref={drawerRef} className="recommendation-detail-drawer" role="dialog" aria-modal="true" aria-labelledby="recommendation-detail-title" onKeyDown={trapFocus}>
    <header className="recommendation-detail-header">
      <div className="recommendation-preview-identity"><StockLogo symbol={item.symbol || ''} market={normalizedMarket(item)} size="md" /><span><small>{normalizedMarket(item) === 'US' ? '美股' : 'A股'} · {item.instrument_type === 'stock' ? '正股' : item.option_right === 'CALL' ? 'CALL' : item.option_right === 'PUT' ? 'PUT' : '期权'}</small><strong id="recommendation-detail-title">{item.symbol || '暂无数据'} 完整研判</strong><em>{item.strategy_name || '暂无数据'}</em></span></div>
      <button type="button" aria-label="关闭研判详情" title="关闭" onClick={onClose}><X /></button>
    </header>
    <div className="recommendation-detail-body">
      <section className="beginner-explanation"><BookOpenCheck /><div><span>新手说明</span><p>{instrumentExplanation(item)}</p></div></section>
      <div className="recommendation-detail-grid">
        <div><span>参考价</span><strong>{money(item.reference_price, item.currency)}</strong></div>
        <div><span>当前价</span><strong>{money(item.current_price, item.currency)}</strong></div>
        <div><span>数量参考</span><strong>{number(item.quantity_hint ?? item.quantity_delta)}</strong></div>
        {item.instrument_type === 'option' && <><div><span>标的</span><strong>{item.symbol || '暂无数据'}</strong></div><div><span>方向</span><strong>{item.option_right === 'CALL' ? 'Call 看涨' : item.option_right === 'PUT' ? 'Put 看跌' : '暂无数据'}</strong></div><div><span>行权价</span><strong>{money(item.option_strike, item.currency)}</strong></div><div><span>到期日</span><strong>{item.option_expiry || '暂无数据'}</strong></div><div><span>买价 / 卖价</span><strong>{money(item.bid, item.currency)} / {money(item.ask, item.currency)}</strong></div><div><span>隐含波动率</span><strong>{percent(item.implied_volatility)}</strong></div><div><span>成交量</span><strong>{number(item.volume)}</strong></div><div><span>未平仓量</span><strong>{number(item.open_interest)}</strong></div></>}
        <div><span>资料来源</span><strong>{source || '暂无数据'}</strong></div>
        <div><span>记录时间</span><strong>{observedAt ? formatTime(observedAt) : '暂无数据'}</strong></div>
      </div>
      <section className={`recommendation-contract-state ${complete ? 'is-complete' : 'is-warning'}`}>{complete ? <CheckCircle2 /> : <AlertTriangle />}<div><strong>{complete ? '关键字段完整' : '资料仍待补全'}</strong><p>{complete ? '字段通过服务端合同检查；行情时效仍需在行动前重新核对。' : missing.length ? `缺少：${missing.join('、')}` : '暂无数据'}</p></div></section>
      <section className="beginner-risk-note"><ShieldCheck /><div><strong>风险边界</strong><p>{item.state === 'locked' ? '当前记录受权限或数据门限制，不应据此行动。' : item.instrument_type === 'option' ? '期权可能归零，并受时间衰减、波动率和流动性影响。' : '正股也会出现回撤、跳空与流动性风险；没有完整风险字段时不要自行补数。'}</p></div></section>
    </div>
    <footer className="recommendation-detail-footer"><span><Clock3 />{item.quote_at ? `报价 ${formatTime(item.quote_at)}` : '报价时间：暂无数据'}</span><div><button className="button secondary" type="button" onClick={onResearch}>查看证据</button><button className="button primary" type="button" disabled={item.state === 'locked'} onClick={onPractice}>{item.instrument_type === 'stock' ? '进入个人模拟' : '打开期权研究'}<ArrowRight /></button></div></footer>
  </aside>
}

export function RecommendationsPage() {
  const workspace = useWorkspace()
  const navigate = useNavigate()
  const [market, setMarket] = useState<MarketView>('US')
  const [view, setView] = useState<RecommendationView>('stock')
  const [signal, setSignal] = useState<RecommendationSignal>('long')
  const [selectedDetail, setSelectedDetail] = useState<RecommendationItem | null>(null)
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

  useEffect(() => {
    if (!selectedDetail) return
    const previous = document.body.style.overflow
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setSelectedDetail(null) }
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.body.style.overflow = previous
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [selectedDetail])

  return <div className="page recommendations-page">
    <PageHeader kicker="AI RESEARCH DIRECTIONS" title="正股与期权研判" description="先用预览卡比较 AI 研判方向、目标空间与风险，再按需展开完整字段。内容仅用于研究与个人模拟，不会自动下单。" />
    <section className="recommendation-guide"><Sparkles /><div><strong>AI 研判仅供研究</strong><p>所有卡片来自服务端正式记录；缺失字段统一标为“暂无数据”，不会用估算或虚构内容补齐。</p></div><span><Gauge />{items.length} 条结果</span></section>
    <nav className="recommendation-market-tabs" aria-label="研判市场"><button className={market === 'US' ? 'active' : ''} type="button" onClick={() => setMarket('US')}>美股 <span>{allItems.filter((item) => normalizedMarket(item) === 'US').length}</span></button><button className={market === 'CN' ? 'active' : ''} type="button" onClick={() => setMarket('CN')}>A股 <span>{allItems.filter((item) => normalizedMarket(item) === 'CN').length}</span></button></nav>
    <div className="recommendation-filter-deck">
      <div className="recommendation-tabs" role="tablist" aria-label="研判类型"><button className={view === 'stock' ? 'active' : ''} type="button" role="tab" aria-selected={view === 'stock'} onClick={() => setRecommendationView('stock')}>正股</button><button className={view === 'option' ? 'active' : ''} type="button" role="tab" aria-selected={view === 'option'} onClick={() => setRecommendationView('option')}>期权</button></div>
      <div className="recommendation-signal-tabs" role="tablist" aria-label="AI 研判方向">{availableSignals.map((item) => <button className={signal === item ? 'active' : ''} type="button" role="tab" aria-selected={signal === item} onClick={() => setSignal(item)} key={item}>{SIGNAL_LABELS[item]} <span>{allItems.filter((candidate) => normalizedMarket(candidate) === market && candidate.instrument_type === view && signalFor(candidate) === item).length}</span></button>)}</div>
    </div>
    {items.length ? <section className="recommendation-preview-scroll" aria-label={`${market === 'US' ? '美股' : 'A股'}${view === 'stock' ? '正股' : '期权'}研判滚动列表`}><div className="recommendation-preview-grid">{items.map((item) => <RecommendationCard key={`${item.event_id}-${item.symbol}`} item={item} onOpenDetail={() => setSelectedDetail(item)} />)}</div></section> : <section className="recommendation-empty" role="status"><BookOpenCheck /><div><h2>当前分类暂无数据</h2><p>新的正式研究记录通过服务端发布后会显示在这里。可以切换市场、产品或方向查看其他记录。</p></div><button className="button secondary" type="button" onClick={() => navigate('/discover')}>前往发现股票</button></section>}
    {selectedDetail && <><button className="recommendation-detail-backdrop" type="button" aria-label="关闭研判详情" onClick={() => setSelectedDetail(null)} /><RecommendationContextPanel item={selectedDetail} peers={items.filter((item) => item.event_id !== selectedDetail.event_id).slice(0, 4)} /><RecommendationDetail item={selectedDetail} source={workspace.data?.recommendations.source || ''} onResearch={() => openResearch(selectedDetail)} onPractice={() => openPractice(selectedDetail)} onClose={() => setSelectedDetail(null)} /></>}
  </div>
}

export default RecommendationsPage
