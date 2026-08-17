import {
  Activity,
  ArrowRight,
  BellRing,
  CalendarDays,
  Cpu,
  Filter,
  ListFilter,
  Newspaper,
  Radar,
  RotateCcw,
  ShieldAlert,
  Signal,
  Sparkles,
  Star,
  TrendingDown,
  TrendingUp,
  WalletCards,
  X,
} from 'lucide-react'
import { useEffect, useId, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { fetchMarketCandles } from '../api/client'
import type { BootstrapPayload, MarketCandlePayload, RecommendationItem } from '../api/client'
import { useWorkspace } from '../api/workspace-context'
import { useCicloTier } from '../api/use-ciclo-tier'
import { deliberationBindingFromRecommendation } from '../api/deliberation'
import { WatchlistToggle } from '../components/WatchlistToggle'
import { CicloCore, type CicloCoreTier } from '../components/paper/CicloCore'
import {
  formatMoney,
  formatTime,
  InspectorToggle,
  RemoteMiniCandles,
  safeNumber,
  SearchField,
  StockTaskBadge,
  V2Card,
  V2PageContext,
  V2PrimaryButton,
  V2SecondaryButton,
  V2StatePanel,
  V2StatusPill,
} from '../components/v2/V2Primitives'
import '../styles/today-discover-v2.css'

type DiscoverView = '候选股票' | '事件发现' | '研究覆盖'
type MarketFilter = '全部' | '美股' | 'A股'
type ActionFilter = '全部' | '研究候选' | '风险复核' | '访问受限'
type CoverageFilter = '全部' | '资料完整' | '资料缺口'
type WatchlistEntry = {
  market: '美股' | 'A股'
  symbol: string
  pinned: boolean
  item: RecommendationItem | null
}
type SparklineState = { status: 'loading' | 'success' | 'empty' | 'error'; candles: MarketCandlePayload['items'] }

const DISCOVER_ANCHORS = [
  { id: 'discover-action', label: '今日行动' },
  { id: 'discover-beginner', label: '新手推荐' },
  { id: 'discover-watchlist', label: '自选覆盖' },
  { id: 'discover-candidates', label: '候选矩阵' },
  { id: 'discover-timeline', label: '时间线' },
] as const

const discoverSparklineRequests = new Map<string, Promise<MarketCandlePayload['items']>>()

function loadDiscoverSparkline(symbol: string) {
  const key = symbol.trim().toUpperCase()
  const cached = discoverSparklineRequests.get(key)
  if (cached) return cached
  const request = fetchMarketCandles(key, '日线').then((payload) => payload.items.slice(-30))
  discoverSparklineRequests.set(key, request)
  void request.catch(() => discoverSparklineRequests.delete(key))
  return request
}

function sparklinePoints(values: number[], width: number, height: number, padding: number) {
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = Math.max(max - min, 0.0001)
  return values.map((value, index) => {
    const x = padding + (index / Math.max(values.length - 1, 1)) * (width - padding * 2)
    const y = padding + ((max - value) / range) * (height - padding * 2)
    return `${x.toFixed(2)},${y.toFixed(2)}`
  }).join(' ')
}

function formatSignedPercent(value: number | null) {
  if (value == null || !Number.isFinite(value)) return '变动未提供'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function currencyCode(currency?: string): 'USD' | 'CNY' | 'HKD' | null {
  return currency === 'USD' || currency === 'CNY' || currency === 'HKD' ? currency : null
}

function metricMoney(value: number, currency?: string) {
  const code = currencyCode(currency)
  if (code) return formatMoney(value, code)
  return `${currency || ''} ${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`.trim()
}

function itemMoney(value: number | null | undefined, currency?: string) {
  const code = currencyCode(currency)
  return code ? formatMoney(value, code) : '价格或币种未提供'
}

function marketName(market?: string) {
  if (market === 'CN' || market === 'A股') return 'A股'
  if (market === 'HK' || market === '港股') return '港股'
  if (market === 'US' || market === '美股') return '美股'
  return '市场未提供'
}

function watchlistMarket(market?: string): 'US' | 'CN' | null {
  if (market === 'CN' || market === 'A股') return 'CN'
  if (market === 'US' || market === '美股') return 'US'
  return null
}

function quoteLabel(item: RecommendationItem) {
  const value = safeNumber(item.current_price ?? item.reference_price)
  return value == null ? '暂无数据' : itemMoney(value, item.currency)
}

function actionLabel(item: RecommendationItem) {
  if (item.state === 'locked') return '访问受限'
  if (item.action === 'REDUCE' || item.action === 'EXIT' || item.action === 'SHORT') return '风险复核'
  return item.action || '研究候选'
}

function actionGroup(item: RecommendationItem): ActionFilter {
  if (item.state === 'locked') return '访问受限'
  if (item.action === 'REDUCE' || item.action === 'EXIT' || item.action === 'SHORT') return '风险复核'
  return '研究候选'
}

function evidenceCounts(item: RecommendationItem) {
  const supplied = [item.rationale, item.stop_price, item.target_price, item.max_loss, item.quote_at]
    .filter((value) => value !== null && value !== undefined && value !== '').length
  return { supplied, missing: item.missing_fields?.length ?? 0 }
}

function DiscoverSparkline({ symbol, authenticated }: { symbol?: string; authenticated: boolean }) {
  const gradientId = `discover-spark-${useId().replace(/:/g, '')}`
  const [state, setState] = useState<SparklineState>({ status: 'empty', candles: [] })

  useEffect(() => {
    if (!authenticated || !symbol?.trim()) {
      setState({ status: 'empty', candles: [] })
      return
    }
    let active = true
    setState({ status: 'loading', candles: [] })
    void loadDiscoverSparkline(symbol).then((candles) => {
      if (active) setState({ status: candles.length ? 'success' : 'empty', candles })
    }).catch(() => {
      if (active) setState({ status: 'error', candles: [] })
    })
    return () => { active = false }
  }, [authenticated, symbol])

  if (state.status !== 'success') {
    const label = state.status === 'loading' ? '读取 K 线' : state.status === 'error' ? 'K 线读取失败' : '暂无 K 线'
    return <div className={`discover-sparkline-state is-${state.status}`} role="status">{label}</div>
  }

  const closes = state.candles.map((item) => item.close)
  const first = closes[0]
  const last = closes[closes.length - 1]
  const change = first === 0 ? null : ((last - first) / first) * 100
  const rising = change == null || change >= 0
  const points = sparklinePoints(closes, 126, 42, 3)
  const TrendIcon = rising ? TrendingUp : TrendingDown

  return (
    <div className={`discover-sparkline ${rising ? 'is-up' : 'is-down'}`} role="img" aria-label={`${symbol || '股票'} ${closes.length} 个真实收盘价点，变动 ${formatSignedPercent(change)}`}>
      <svg viewBox="0 0 126 42" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="currentColor" stopOpacity=".38" />
            <stop offset="1" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={`3,41 ${points} 123,41`} fill={`url(#${gradientId})`} />
        <polyline points={points} />
      </svg>
      <span><TrendIcon size={10} aria-hidden="true" />{formatSignedPercent(change)}</span>
    </div>
  )
}

function DiscoverAIBanner({
  selected,
  filteredCount,
  completeCount,
  tier,
  source,
  freshness,
  onResearch,
}: {
  selected: RecommendationItem | null
  filteredCount: number
  completeCount: number
  tier: CicloCoreTier
  source?: string
  freshness?: string
  onResearch: () => void
}) {
  const riskCount = Math.max(filteredCount - completeCount, 0)
  return (
    <V2Card className="discover-ai-banner">
      <div className="discover-banner-copy">
        <span className="v2-eyebrow"><Sparkles size={13} aria-hidden="true" /> AI DISCOVERY NETWORK</span>
        <h1>发现值得研究的股票</h1>
        <p>把真实行情、策略信号、研究缺口和风险上下文汇入同一工作台。AI 只协助整理研究，不自动下单。</p>
        <div className="discover-banner-metrics" aria-label="发现页概览">
          <div><span>当前候选</span><strong>{filteredCount}</strong><small>真实股票记录</small></div>
          <div><span>研究完整</span><strong>{completeCount}</strong><small>合同字段齐备</small></div>
          <div><span>当前股票</span><strong className={selected ? undefined : 'is-empty-title'}>{selected?.symbol || '未选择'}</strong><small>{selected ? actionLabel(selected) : '从候选表选择'}</small></div>
        </div>
        <div className="discover-banner-actions">
          <V2PrimaryButton onClick={onResearch} disabled={!selected}>进入股票研究 <ArrowRight size={14} /></V2PrimaryButton>
          <span><i className="is-online" /> {source || '候选来源未提供'} · {freshness || '行情状态未提供'}</span>
        </div>
      </div>

      <div className="discover-banner-visual" role="img" aria-label="Ciclo AI 机器人连接 MARKET、NEWS、SIGNAL 与 RISK 能力节点">
        <svg className="discover-banner-links" viewBox="0 0 620 250" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <filter id="discover-banner-glow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="4" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
            <linearGradient id="discover-banner-line" x1="0" x2="1">
              <stop offset="0" stopColor="#3B63FF" stopOpacity=".32" />
              <stop offset=".52" stopColor="#8B5CFF" />
              <stop offset="1" stopColor="#FF4FA3" stopOpacity=".68" />
            </linearGradient>
          </defs>
          <path d="M300 119 C225 70 153 56 78 54" />
          <path d="M300 137 C220 175 151 194 75 200" />
          <path d="M336 116 C408 67 474 52 548 52" />
          <path d="M338 139 C417 179 480 197 551 201" />
        </svg>
        <div className="discover-banner-node is-market"><span><Radar size={14} /> MARKET</span><strong>{filteredCount} 候选</strong><i className="is-online" /></div>
        <div className="discover-banner-node is-news"><span><Newspaper size={14} /> NEWS</span><strong>{selected ? '上下文就绪' : '等待股票'}</strong><i className={selected ? 'is-online' : 'is-waiting'} /></div>
        <div className="discover-banner-node is-signal"><span><Signal size={14} /> SIGNAL</span><strong>{completeCount} 完整</strong><i className="is-online" /></div>
        <div className="discover-banner-node is-risk"><span><ShieldAlert size={14} /> RISK</span><strong>{riskCount} 待核</strong><i className={riskCount ? 'is-risk' : 'is-online'} /></div>
        <div className="discover-banner-halo" aria-hidden="true"><span /><span /><span /></div>
        <div className="discover-banner-robot"><CicloCore label="Ciclo AI 发现机器人" size="compact" tier={tier} /></div>
        <div className="discover-banner-platform" aria-hidden="true"><span /><span /><span /></div>
        <div className="discover-banner-particles" aria-hidden="true"><i /><i /><i /><i /><i /><i /></div>
      </div>
    </V2Card>
  )
}

function priorityLabel(item: RecommendationItem, index: number) {
  if (item.state === 'locked') return { label: 'P3 · 权限', tone: 'is-locked' }
  if ((item.missing_fields?.length ?? 0) > 0) return { label: 'P1 · 补资料', tone: 'is-high' }
  if (item.action === 'REDUCE' || item.action === 'EXIT' || item.action === 'SHORT') return { label: 'P1 · 风险', tone: 'is-high' }
  return index === 0 ? { label: 'P1 · 先研究', tone: 'is-high' } : { label: 'P2 · 跟进', tone: 'is-normal' }
}

function TodayActionMatrix({ items, authenticated, onSelect, onResearch }: {
  items: RecommendationItem[]
  authenticated: boolean
  onSelect: (item: RecommendationItem) => void
  onResearch: (item: RecommendationItem) => void
}) {
  const actions = items.slice(0, 4)
  return (
    <V2Card className="discover-action-matrix">
      <header className="discover-card-heading">
        <div><span className="v2-eyebrow">TODAY ACTION MATRIX</span><h2>今日行动矩阵</h2></div>
        <span className="discover-heading-note"><Activity size={14} />优先级 · 状态 · 下一步</span>
      </header>
      {actions.length ? <div className="discover-action-grid">
        {actions.map((item, index) => {
          const evidence = evidenceCounts(item)
          const priority = priorityLabel(item, index)
          return <article className="discover-action-card" key={item.event_id}>
            <header><StockTaskBadge symbol={item.symbol} market={marketName(item.market)} /><span className={`discover-priority ${priority.tone}`}>{priority.label}</span></header>
            <button className="discover-action-main" type="button" onClick={() => onSelect(item)}>
              <span><small>当前记录价</small><strong>{quoteLabel(item)}</strong></span>
              <DiscoverSparkline symbol={item.symbol} authenticated={authenticated} />
            </button>
            <div className="discover-action-aux">
              <span><small>研究状态</small><strong>{actionLabel(item)}</strong></span>
              <span><small>证据完整度</small><strong>{evidence.supplied} / {evidence.supplied + evidence.missing || 1}</strong></span>
              <span><small>资料缺口</small><strong>{evidence.missing}</strong></span>
              <span><small>最大风险</small><strong>{item.max_loss == null ? '暂无数据' : itemMoney(item.max_loss, item.currency)}</strong></span>
            </div>
            <div className="discover-action-cta"><button className="v2-button v2-button-primary" type="button" onClick={() => onResearch(item)}>进入研究 <ArrowRight size={14} /></button></div>
            <footer><span><i className={evidence.missing ? 'is-waiting' : 'is-online'} />{evidence.missing ? '资料待补齐' : '研究资料可继续'}</span><small>{item.available_at ? formatTime(item.available_at) : '新鲜度未提供'}</small></footer>
          </article>
        })}
      </div> : <V2StatePanel state={authenticated ? 'empty' : 'locked'} title={authenticated ? '今日暂无候选行动' : '登录后查看行动矩阵'} detail="这里只整理真实候选的研究优先级，不会替你下单。" />}
    </V2Card>
  )
}

function BeginnerRecommendations({ items, authenticated, onResearch }: {
  items: RecommendationItem[]
  authenticated: boolean
  onResearch: (item: RecommendationItem) => void
}) {
  const recommended = [...items]
    .sort((left, right) => Number(right.contract_status === 'complete') - Number(left.contract_status === 'complete') || evidenceCounts(right).supplied - evidenceCounts(left).supplied)
    .slice(0, 3)
  return (
    <V2Card className="discover-beginner-recommendations">
      <header className="discover-card-heading"><div><span className="v2-eyebrow">BEGINNER STARTER CARDS</span><h2>0 基础研究起点</h2></div><V2StatusPill state="info">先理解，再决策</V2StatusPill></header>
      {recommended.length ? <div className="discover-beginner-grid">{recommended.map((item, index) => {
        const evidence = evidenceCounts(item)
        const reason = item.contract_status === 'complete'
          ? '资料字段较完整，适合先学习如何阅读研究证据。'
          : evidence.supplied > 0 ? `已有 ${evidence.supplied} 项可核对依据，同时保留 ${evidence.missing} 项资料缺口。` : '当前资料有限，适合学习如何识别未知项。'
        return <article key={item.event_id}>
          <header><span>研判卡 {index + 1}</span><V2StatusPill state={item.contract_status === 'complete' ? 'success' : 'warning'}>{item.contract_status === 'complete' ? '资料较完整' : '带缺口'}</V2StatusPill></header>
          <div className="discover-beginner-symbol"><CandidateLogo item={item} /><span><strong>{item.symbol || '股票代码未提供'}</strong><small>{marketName(item.market)} · AI 研判方向：{item.action === 'BUY' ? '做多' : item.action === 'SHORT' ? '做空' : '观察'}</small></span></div>
          <p><strong>数据支撑分析：</strong>{reason}</p>
          <div className="discover-beginner-meter"><span style={{ width: `${Math.min(100, Math.round(evidence.supplied / Math.max(evidence.supplied + evidence.missing, 1) * 100))}%` }} /></div>
          <button className="v2-button v2-button-secondary" type="button" onClick={() => onResearch(item)}>先看研究证据 <ArrowRight size={14} /></button>
          <footer><i className={evidence.missing ? 'is-waiting' : 'is-online'} />仅供研究参考，不构成投资建议</footer>
        </article>
      })}</div> : <V2StatePanel state={authenticated ? 'empty' : 'locked'} title="暂无可推荐的研究起点" detail="有真实候选资料后再显示，不使用虚构热门股票填充。" />}
    </V2Card>
  )
}

type CandidateLogoFields = RecommendationItem & {
  logo?: unknown
  logoUrl?: unknown
  logo_url?: unknown
}

function candidateLogoUrl(item: RecommendationItem) {
  const candidate = item as CandidateLogoFields
  const value = [candidate.logo_url, candidate.logoUrl, candidate.logo]
    .find((entry): entry is string => typeof entry === 'string' && /^(https?:\/\/|data:image\/)/i.test(entry.trim()))
  return value?.trim() ?? ''
}

function CandidateLogo({ item }: { item: RecommendationItem }) {
  const src = candidateLogoUrl(item)
  const [imageFailed, setImageFailed] = useState(false)
  const symbol = item.symbol?.trim().toUpperCase() || '股票'
  const tone = [...symbol].reduce((total, character) => total + character.charCodeAt(0), 0) % 5

  useEffect(() => setImageFailed(false), [src])

  if (src && !imageFailed) {
    return <span className="discover-company-logo"><img src={src} alt={`${symbol} 公司 Logo`} loading="lazy" referrerPolicy="no-referrer" onError={() => setImageFailed(true)} /></span>
  }

  return <span className={`discover-company-logo is-fallback is-tone-${tone}`} aria-hidden="true">{symbol.charAt(0)}</span>
}

function CandidateRow({
  item,
  authenticated,
  selected,
  saved,
  watchBusy,
  onSelect,
  onResearch,
  onWatchlist,
  onAlert,
}: {
  item: RecommendationItem
  authenticated: boolean
  selected: boolean
  saved: boolean
  watchBusy: boolean
  onSelect: () => void
  onResearch: () => void
  onWatchlist: (remove: boolean) => void | Promise<void>
  onAlert: () => void
}) {
  const evidence = evidenceCounts(item)
  const supportedMarket = watchlistMarket(item.market) != null
  return (
    <tr className={`v2-candidate-row ${selected ? 'is-selected' : ''}`} aria-selected={selected}>
      <td data-label="股票 / 策略">
        <button className="v2-symbol-button" type="button" onClick={onSelect}>
          <span className="discover-symbol-main">
            <CandidateLogo item={item} />
            <StockTaskBadge symbol={item.symbol} name={item.symbol ? undefined : '股票名称未提供'} market={marketName(item.market)} />
          </span>
        </button>
        <small className="discover-strategy-version">{item.strategy_name || '策略未提供'} · {item.strategy_version || '版本未提供'}</small>
      </td>
      <td data-label="价格 / Mini K线">
        <div className="discover-price-chart-cell">
          <div className="v2-price-cell">
            <strong>{quoteLabel(item)}</strong>
            <small>{item.quote_at ? formatTime(item.quote_at) : '报价时间：暂无数据'}</small>
          </div>
          {item.symbol && item.market
            ? <DiscoverSparkline symbol={item.symbol} authenticated={authenticated} />
            : <V2StatePanel state="empty" title="趋势不可绘制" detail="记录缺少股票或市场字段。" />}
        </div>
      </td>
      <td data-label="研究状态">
        <div className="discover-status-cell">
          <V2StatusPill state={item.state === 'locked' ? 'locked' : item.contract_status === 'incomplete' ? 'warning' : 'success'}>{actionLabel(item)}</V2StatusPill>
          <small>{item.max_loss == null ? '最大风险：暂无数据' : `最大风险 ${itemMoney(item.max_loss, item.currency)}`}</small>
        </div>
      </td>
      <td data-label="证据">
        <div className="discover-evidence-cell">
          <span><i className="is-online" />依据 <strong>{evidence.supplied}</strong></span>
          <span><i className={evidence.missing ? 'is-risk' : 'is-online'} />缺口 <strong>{evidence.missing}</strong></span>
        </div>
      </td>
      <td data-label="更新时间"><div className="discover-time-cell"><strong>{item.available_at ? formatTime(item.available_at) : '可见时间未提供'}</strong><small>{item.recorded_at ? `记录 ${formatTime(item.recorded_at)}` : '记录时间未提供'}</small></div></td>
      <td data-label="操作">
        <div className="discover-row-actions">
          <button className="discover-row-icon is-research" type="button" onClick={onResearch} aria-label={`研究 ${item.symbol || '股票'}`} title="进入研究"><ArrowRight size={14} /></button>
          {supportedMarket
            ? <WatchlistToggle symbol={item.symbol || '股票'} saved={saved} busy={watchBusy} onToggle={onWatchlist} className="discover-row-watch" />
            : <button className="discover-row-icon" type="button" disabled aria-label="当前市场暂不支持自选"><Star size={14} /></button>}
          <button className="discover-row-icon is-alert" type="button" onClick={onAlert} aria-label={`为 ${item.symbol || '股票'} 建立预警草稿`} title="建立预警草稿"><BellRing size={14} /></button>
        </div>
      </td>
    </tr>
  )
}

function WatchlistPanel({
  entries,
  selectedId,
  busyKey,
  onSelect,
  onToggle,
}: {
  entries: WatchlistEntry[]
  selectedId: number | null
  busyKey: string
  onSelect: (item: RecommendationItem) => void
  onToggle: (entry: WatchlistEntry, remove: boolean) => void | Promise<void>
}) {
  return (
    <V2Card className="discover-watchlist-card">
      <header className="discover-card-heading">
        <div><span className="v2-eyebrow">WATCHLIST</span><h2>自选列表</h2></div>
        <V2StatusPill state="info">{entries.length} 只</V2StatusPill>
      </header>
      {entries.length ? (
        <div className="discover-watchlist-list">
          {entries.map((entry) => {
            const key = `${entry.market}-${entry.symbol}`
            return (
              <div className={entry.item && selectedId === entry.item.event_id ? 'is-selected' : ''} key={key}>
                {entry.item ? (
                  <button className="discover-watchlist-main" type="button" onClick={() => onSelect(entry.item!)}>
                    <StockTaskBadge symbol={entry.symbol} market={entry.market} />
                    <span className="discover-watchlist-quote"><strong>{quoteLabel(entry.item)}</strong><small>{actionLabel(entry.item)}</small></span>
                  </button>
                ) : (
                  <div className="discover-watchlist-main is-unmatched">
                    <StockTaskBadge symbol={entry.symbol} market={entry.market} />
                    <span className="discover-watchlist-quote"><strong>候选池暂无报价</strong><small>仍保留真实自选记录</small></span>
                  </div>
                )}
                {entry.pinned && <span className="discover-watchlist-pin is-pinned" title="置顶自选">置顶</span>}
                <WatchlistToggle symbol={entry.symbol} saved busy={busyKey === key} onToggle={(remove) => onToggle(entry, remove)} className="discover-watchlist-toggle" />
              </div>
            )
          })}
        </div>
      ) : <V2StatePanel state="empty" title="暂无自选股票" detail="从中栏候选行点击星标，即可加入真实账户自选。" />}
      <footer className="discover-watchlist-footer"><span><i className="is-online" />真实账户自选</span><span>星标可加入或移除</span></footer>
    </V2Card>
  )
}

function CoveragePanel({ records, authenticated, source, delivery }: { records: RecommendationItem[]; authenticated: boolean; source?: string; delivery?: number }) {
  if (!authenticated) return (
    <V2Card className="discover-coverage-card">
      <header className="discover-card-heading"><div><span className="v2-eyebrow">RESEARCH COVERAGE</span><h2>研究覆盖统计</h2></div><ListFilter size={16} /></header>
      <V2StatePanel state="locked" title="覆盖统计已锁定" detail="登录后显示真实研究记录与资料状态。" />
    </V2Card>
  )
  if (!records.length) return (
    <V2Card className="discover-coverage-card">
      <header className="discover-card-heading"><div><span className="v2-eyebrow">RESEARCH COVERAGE</span><h2>研究覆盖统计</h2></div><ListFilter size={16} /></header>
      <V2StatePanel state="empty" title="暂无研究覆盖" detail="当前 Workspace 没有可验证的股票候选记录。" />
    </V2Card>
  )

  const complete = records.filter((item) => item.contract_status === 'complete').length
  const missing = records.filter((item) => item.contract_status === 'incomplete').length
  const locked = records.filter((item) => item.state === 'locked').length
  return (
    <V2Card className="discover-coverage-card">
      <header className="discover-card-heading"><div><span className="v2-eyebrow">RESEARCH COVERAGE</span><h2>研究覆盖统计</h2></div><ListFilter size={16} /></header>
      <div className="discover-coverage-list">
        <div><span><i className="is-online" />候选记录</span><strong>{records.length}</strong><small>当前股票范围</small></div>
        <div><span><i className="is-online" />资料完整</span><strong>{complete}</strong><small>可继续深入研究</small></div>
        <div><span><i className={missing ? 'is-waiting' : 'is-online'} />资料缺口</span><strong>{missing}</strong><small>需补齐字段</small></div>
        <div><span><i className={locked ? 'is-risk' : 'is-online'} />访问受限</span><strong>{locked}</strong><small>遵守账户权限</small></div>
      </div>
      <footer className="discover-coverage-source"><span>来源</span><strong>{source || '来源未提供'}</strong><span>投递延迟</span><strong>{delivery == null ? '延迟未提供' : `${delivery} 分钟`}</strong></footer>
    </V2Card>
  )
}

function FilterResultChart({ items }: { items: RecommendationItem[] }) {
  const values = items.map((item) => safeNumber(item.current_price ?? item.reference_price)).filter((value): value is number => value != null).slice(0, 12)
  if (values.length < 2) return <V2StatePanel state="empty" title="筛选分布暂不可绘制" detail="至少需要两条真实价格记录。" />
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = Math.max(max - min, 0.0001)
  const points = values.map((value, index) => `${(index / (values.length - 1)) * 100},${88 - ((value - min) / range) * 70}`).join(' ')
  return (
    <div className="discover-filter-chart" role="img" aria-label={`筛选结果价格分布，共 ${values.length} 个真实价格点`}>
      <div className="discover-chart-meta"><span>筛选价格分布</span><strong>{values.length}</strong></div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id="discover-area-fill" x1="0" x2="1" y1="0" y2="1"><stop offset="0" stopColor="var(--discover-blue)" stopOpacity=".42" /><stop offset="1" stopColor="var(--discover-pink)" stopOpacity=".04" /></linearGradient>
          <linearGradient id="discover-line-stroke" x1="0" x2="1"><stop offset="0" stopColor="var(--discover-blue)" /><stop offset=".55" stopColor="var(--discover-violet)" /><stop offset="1" stopColor="var(--discover-pink)" /></linearGradient>
        </defs>
        <path className="discover-chart-grid" d="M0 18H100 M0 53H100 M0 88H100" />
        <polygon points={`0,94 ${points} 100,94`} fill="url(#discover-area-fill)" />
        <polyline points={points} fill="none" stroke="url(#discover-line-stroke)" />
      </svg>
      <div className="discover-chart-legend"><span><i />真实报价</span><small>当前筛选结果</small></div>
    </div>
  )
}

function RecentTimeline({ items, selectedId, onSelect, onResearch }: { items: RecommendationItem[]; selectedId: number | null; onSelect: (item: RecommendationItem) => void; onResearch: () => void }) {
  const events = items.slice(0, 6)
  return (
    <V2Card className="discover-timeline-card">
      <header className="discover-card-heading"><div><span className="v2-eyebrow">EVENT TIMELINE</span><h2>近期事件时间轴</h2></div><span className="discover-heading-note"><CalendarDays size={14} />真实候选事件</span></header>
      {events.length ? (
        <ol className="discover-timeline">
          {events.map((item) => {
            const evidence = evidenceCounts(item)
            return (
              <li className={selectedId === item.event_id ? 'is-selected' : ''} key={item.event_id}>
                <button type="button" onClick={() => onSelect(item)}>
                  <time dateTime={item.occurred_at}>{formatTime(item.occurred_at)}</time>
                  <i aria-hidden="true" />
                  <strong>{item.symbol || '股票代码未提供'}</strong>
                  <span>{actionLabel(item)} · 依据 {evidence.supplied}</span>
                  <small>候选记录 #{item.event_id} · 缺口 {evidence.missing}</small>
                </button>
              </li>
            )
          })}
        </ol>
      ) : <V2StatePanel state="empty" title="暂无近期事件" detail="当前筛选结果没有可展示的真实候选事件。" />}
      <footer className="discover-timeline-footer"><V2SecondaryButton onClick={onResearch} disabled={selectedId == null}>进入已选股票研究</V2SecondaryButton></footer>
    </V2Card>
  )
}

function AIResearchPanel({
  selected,
  authenticated,
  symbol,
  message,
  onSymbolChange,
  onSubmit,
  onResearch,
  onPaper,
  onAlert,
}: {
  selected: RecommendationItem | null
  authenticated: boolean
  symbol: string
  message: string
  onSymbolChange: (value: string) => void
  onSubmit: () => void
  onResearch: () => void
  onPaper: () => void
  onAlert: () => void
}) {
  const evidence = selected ? evidenceCounts(selected) : null
  return (
    <V2Card className="discover-ai-entry-card">
      <header className="discover-card-heading discover-ai-entry-heading">
        <div><span className="v2-eyebrow">AI RESEARCH ENTRY</span><h2>AI 股票研究入口</h2></div>
        <V2StatusPill state={authenticated ? 'success' : 'info'}>{authenticated ? '在线' : '只读'}</V2StatusPill>
      </header>
      <form className="discover-ai-form" onSubmit={(event) => { event.preventDefault(); onSubmit() }}>
        <label><span>股票代码</span><input value={symbol} onChange={(event) => onSymbolChange(event.target.value)} placeholder="输入候选池股票代码" autoComplete="off" /></label>
        <button className="v2-button v2-button-primary" type="submit" disabled={!authenticated || !symbol.trim()}><Sparkles size={14} />进入股票研究</button>
      </form>
      {message && <div className="discover-inline-feedback" role="status">{message}</div>}
      {selected ? (
        <div className="discover-ai-selection">
          <div className="discover-ai-selection-head"><StockTaskBadge symbol={selected.symbol} market={marketName(selected.market)} /><span><strong>{quoteLabel(selected)}</strong><small>{actionLabel(selected)}</small></span></div>
          <RemoteMiniCandles symbol={selected.symbol} authenticated={authenticated} label={`${selected.symbol || '股票'} AI 研究入口 Mini K线`} />
          <div className="discover-ai-evidence"><span>依据 <strong>{evidence?.supplied}</strong></span><span>缺口 <strong>{evidence?.missing}</strong></span><span>事件 <strong>#{selected.event_id}</strong></span></div>
        </div>
      ) : <V2StatePanel state="empty" title="请选择股票开始研究" detail="可输入股票代码，或从候选矩阵与近期事件中选择一只股票。" />}
      <div className="discover-ai-quick-actions" aria-label="AI 研究快捷动作">
        <button type="button" onClick={onResearch} disabled={!selected}><Sparkles size={13} />研究草稿</button>
        <button type="button" onClick={onPaper} disabled={!selected}><WalletCards size={13} />模拟预填</button>
        <button type="button" onClick={onAlert} disabled={!selected}><BellRing size={13} />风险预警</button>
      </div>
      <footer className="discover-ai-boundary"><i className={authenticated ? 'is-online' : 'is-waiting'} /><span>AI 只生成研究上下文，不自动下单</span></footer>
    </V2Card>
  )
}

function AccountSnapshotPanel({ data, authenticated }: { data: BootstrapPayload | null; authenticated: boolean }) {
  if (!authenticated) return (
    <V2Card className="discover-account-card">
      <header className="discover-card-heading"><div><span className="v2-eyebrow">ACCOUNT SNAPSHOT</span><h2>账户快照</h2></div><WalletCards size={16} /></header>
      <V2StatePanel state="locked" title="账户快照已锁定" detail="登录后显示真实资产、收益、回撤与持仓。" />
    </V2Card>
  )
  const ordered = [...(data?.performance.items ?? [])].sort((a, b) => a.captured_at.localeCompare(b.captured_at))
  const latest = ordered.at(-1)
  if (!latest || !data) return (
    <V2Card className="discover-account-card">
      <header className="discover-card-heading"><div><span className="v2-eyebrow">ACCOUNT SNAPSHOT</span><h2>账户快照</h2></div><WalletCards size={16} /></header>
      <V2StatePanel state="empty" title="暂无账户快照" detail="Workspace 尚未提供可用的真实绩效记录。" />
    </V2Card>
  )

  const recent = ordered.filter((item) => item.currency === latest.currency).slice(-30)
  const equities = recent.map((item) => item.total_equity)
  let peak = equities[0] ?? latest.total_equity
  let maxDrawdown = 0
  equities.forEach((value) => {
    peak = Math.max(peak, value)
    if (peak > 0) maxDrawdown = Math.max(maxDrawdown, ((peak - value) / peak) * 100)
  })
  const returnPct = latest.initial_cash === 0 ? null : (latest.total_pnl / latest.initial_cash) * 100
  const risk = data.execution_control.effective_opening_paused || maxDrawdown >= 10 ? '高' : maxDrawdown >= 5 ? '中' : '低'
  const riskTone = risk === '高' ? 'is-risk' : risk === '中' ? 'is-waiting' : 'is-online'
  const points = sparklinePoints(equities, 260, 70, 5)
  const positive = latest.total_pnl >= 0
  return (
    <V2Card className="discover-account-card">
      <header className="discover-card-heading"><div><span className="v2-eyebrow">ACCOUNT SNAPSHOT</span><h2>账户快照</h2></div><V2StatusPill state="success">真实数据</V2StatusPill></header>
      <div className="discover-account-primary"><span>账户资产</span><strong>{metricMoney(latest.total_equity, latest.currency)}</strong><small>{formatTime(latest.captured_at)} · {latest.currency}</small></div>
      <div className="discover-account-metrics">
        <div><span>累计收益</span><strong className={positive ? 'is-positive' : 'is-negative'}>{formatSignedPercent(returnPct)}</strong></div>
        <div><span>最大回撤</span><strong className="is-negative">-{maxDrawdown.toFixed(2)}%</strong></div>
        <div><span>持仓数</span><strong>{data.portfolio.positions.length}</strong></div>
        <div><span><i className={riskTone} />风险等级</span><strong>{risk}</strong></div>
      </div>
      <div className={`discover-account-curve ${positive ? 'is-positive' : 'is-negative'}`}>
        <svg viewBox="0 0 260 70" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="discover-account-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="currentColor" stopOpacity=".34" /><stop offset="1" stopColor="currentColor" stopOpacity="0" /></linearGradient></defs><polygon points={`5,68 ${points} 255,68`} fill="url(#discover-account-fill)" /><polyline points={points} /></svg>
        <span>近 {equities.length} 期真实权益</span>
      </div>
      <footer className="discover-account-footer"><span>{data.performance.scope || '系统模型验证域'}</span><strong>与个人模拟账户隔离</strong></footer>
    </V2Card>
  )
}

function DiscoverFooter({ count }: { count: number }) {
  return <div className="v2-search-footer"><span>{count ? `显示 ${count} 条真实股票记录` : '暂无真实股票记录'}</span><span>选择股票只更新研究上下文，不自动跳页</span></div>
}

function DiscoverStatusBar({ data, authenticated }: { data: BootstrapPayload | null; authenticated: boolean }) {
  const marketHealthy = authenticated && data?.market_data.freshness !== '不可用'
  const delay = data?.market_data.delivery_delay_minutes
  const executionPaused = data?.execution_control.effective_opening_paused
  return (
    <footer className="discover-status-bar" aria-label="发现页系统状态">
      <span><i className={marketHealthy ? 'is-online' : 'is-waiting'} /><small>数据健康</small><strong>{marketHealthy ? (data?.market_data.freshness || '已连接') : '待验证'}</strong></span>
      <span><i className="is-online" /><small>研究状态</small><strong>{authenticated ? '记录已核验' : '登录后核验'}</strong></span>
      <span><i className={delay ? 'is-waiting' : 'is-online'} /><small>行情延迟</small><strong>{delay == null ? '延迟未提供' : `${delay} 分钟`}</strong></span>
      <span><i className={executionPaused ? 'is-risk' : 'is-online'} /><small>执行边界</small><strong>{executionPaused ? '新开仓暂停' : 'AI 不下单'}</strong></span>
      <span><Cpu size={13} /><small>来源</small><strong>{data?.market_data.display_source || '来源未提供'}</strong></span>
    </footer>
  )
}

export function DiscoverV2Page() {
  const workspace = useWorkspace()
  const cicloTier = useCicloTier()
  const navigate = useNavigate()
  const [inspectorOpen, setInspectorOpen] = useState(() => typeof window === 'undefined' || window.matchMedia('(min-width: 1481px)').matches)
  const [searchParams, setSearchParams] = useSearchParams()
  const [watchBusy, setWatchBusy] = useState('')
  const [watchMessage, setWatchMessage] = useState('')
  const [aiSymbol, setAiSymbol] = useState('')
  const [aiMessage, setAiMessage] = useState('')
  const [anchorVisible, setAnchorVisible] = useState(false)
  const [activeAnchor, setActiveAnchor] = useState<(typeof DISCOVER_ANCHORS)[number]['id']>('discover-action')
  const query = searchParams.get('q') ?? ''
  const marketParam = searchParams.get('market')
  const market: MarketFilter = marketParam === '美股' || marketParam === 'A股' ? marketParam : '全部'
  const actionParam = searchParams.get('action')
  const actionFilter: ActionFilter = actionParam === '研究候选' || actionParam === '风险复核' || actionParam === '访问受限' ? actionParam : '全部'
  const coverageParam = searchParams.get('coverage')
  const coverageFilter: CoverageFilter = coverageParam === '资料完整' || coverageParam === '资料缺口' ? coverageParam : '全部'
  const viewParam = searchParams.get('view')
  const view: DiscoverView = viewParam === '事件发现' || viewParam === '研究覆盖' ? viewParam : '候选股票'
  const selectedParam = Number(searchParams.get('selected'))
  const selectedId = Number.isSafeInteger(selectedParam) && selectedParam > 0 ? selectedParam : null
  const pageParam = Number(searchParams.get('page'))
  const page = Number.isSafeInteger(pageParam) && pageParam > 0 ? pageParam : 1
  const pageSize = Number.MAX_SAFE_INTEGER

  const updateParams = (updates: Record<string, string | null>) => {
    const next = new URLSearchParams(searchParams)
    Object.entries(updates).forEach(([key, value]) => value === null ? next.delete(key) : next.set(key, value))
    setSearchParams(next)
  }

  const records = useMemo(() => (workspace.data?.recommendations.items ?? []).filter((item) => item.instrument_type === 'stock'), [workspace.data])
  const filtered = useMemo(() => records.filter((item) => {
    const matchesQuery = !query.trim() || item.symbol?.toLowerCase().includes(query.trim().toLowerCase())
    const matchesMarket = market === '全部' || marketName(item.market) === market
    const matchesAction = actionFilter === '全部' || actionGroup(item) === actionFilter
    const matchesCoverage = coverageFilter === '全部' || (coverageFilter === '资料完整' ? item.contract_status === 'complete' : item.contract_status === 'incomplete')
    return matchesQuery && matchesMarket && matchesAction && matchesCoverage
  }), [actionFilter, coverageFilter, market, query, records])
  const watchlists = workspace.data?.settings.watchlists ?? { us: [], a_share: [] }
  const watchlistPins = workspace.data?.settings.watchlist_pins ?? { us: [], a_share: [] }
  const watchlistEntries = useMemo<WatchlistEntry[]>(() => [
    ...watchlists.us.map((symbol) => ({ market: '美股' as const, symbol, pinned: watchlistPins.us.includes(symbol) })),
    ...watchlists.a_share.map((symbol) => ({ market: 'A股' as const, symbol, pinned: watchlistPins.a_share.includes(symbol) })),
  ].map((entry) => ({
    ...entry,
    item: records.find((item) => item.symbol?.toUpperCase() === entry.symbol.toUpperCase() && marketName(item.market) === entry.market) ?? null,
  })).sort((a, b) => Number(b.pinned) - Number(a.pinned)), [records, watchlistPins.a_share, watchlistPins.us, watchlists.a_share, watchlists.us])
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize))
  const currentPage = Math.min(page, pageCount)
  const visibleRecords = filtered.slice(0, currentPage * pageSize)
  const selected = filtered.find((item) => item.event_id === selectedId) ?? null
  const isAuth = workspace.mode === 'authenticated'
  const completeCount = filtered.filter((item) => item.contract_status === 'complete').length

  useEffect(() => {
    if (typeof window === 'undefined') return
    const updateVisibility = () => setAnchorVisible(window.scrollY > 220)
    updateVisibility()
    window.addEventListener('scroll', updateVisibility, { passive: true })
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0]
      if (visible?.target.id) setActiveAnchor(visible.target.id as (typeof DISCOVER_ANCHORS)[number]['id'])
    }, { rootMargin: '-24% 0px -58% 0px', threshold: [0.05, 0.3, 0.65] })
    DISCOVER_ANCHORS.forEach(({ id }) => { const element = document.getElementById(id); if (element) observer.observe(element) })
    return () => { window.removeEventListener('scroll', updateVisibility); observer.disconnect() }
  }, [])

  useEffect(() => {
    if (selected?.symbol) setAiSymbol(selected.symbol)
  }, [selected?.event_id, selected?.symbol])

  useEffect(() => {
    if (!inspectorOpen || typeof window === 'undefined' || !window.matchMedia('(max-width: 1480px)').matches) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previousOverflow }
  }, [inspectorOpen])

  const research = (item: RecommendationItem | null) => {
    if (!item?.symbol?.trim() || !item.market?.trim() || !Number.isSafeInteger(item.event_id) || item.event_id <= 0) return
    navigate(`/research?market=${encodeURIComponent(item.market)}&symbol=${encodeURIComponent(item.symbol)}&event_id=${item.event_id}`)
  }
  const paper = (item: RecommendationItem | null) => {
    if (!item?.symbol?.trim() || !item.market?.trim()) return
    navigate(`/paper?symbol=${encodeURIComponent(item.symbol)}&market=${encodeURIComponent(item.market)}&source=screener`)
  }
  const alertDraft = (item: RecommendationItem | null) => {
    if (!item?.symbol?.trim() || !item.market?.trim()) return
    navigate(`/research?market=${encodeURIComponent(item.market)}&symbol=${encodeURIComponent(item.symbol)}&panel=预警&draft=1`)
  }
  const toggleWatchlist = async (item: RecommendationItem, remove: boolean) => {
    const marketCode = watchlistMarket(item.market)
    if (!marketCode || !item.symbol?.trim()) return
    const key = `${marketName(item.market)}-${item.symbol}`
    setWatchBusy(key)
    setWatchMessage('')
    try {
      await workspace.changeWatchlist(marketCode, item.symbol, remove)
      setWatchMessage(`${item.symbol} 已${remove ? '移出' : '加入'}自选`)
    } catch (caught) {
      setWatchMessage(caught instanceof Error ? caught.message : '自选更新失败，请稍后重试。')
    } finally {
      setWatchBusy('')
    }
  }
  const toggleWatchlistEntry = async (entry: WatchlistEntry, remove: boolean) => {
    const marketCode = entry.market === '美股' ? 'US' : 'CN'
    const key = `${entry.market}-${entry.symbol}`
    setWatchBusy(key)
    setWatchMessage('')
    try {
      await workspace.changeWatchlist(marketCode, entry.symbol, remove)
      setWatchMessage(`${entry.symbol} 已${remove ? '移出' : '加入'}自选`)
    } catch (caught) {
      setWatchMessage(caught instanceof Error ? caught.message : '自选更新失败，请稍后重试。')
    } finally {
      setWatchBusy('')
    }
  }
  const selectCandidate = async (item: RecommendationItem) => {
    if (searchParams.get('returnTo') !== 'deliberation') {
      updateParams({ selected: String(item.event_id) })
      return
    }
    setWatchMessage('正在将股票带入多空观点对照…')
    try {
      const binding = await deliberationBindingFromRecommendation(item)
      const params = new URLSearchParams({
        market: binding.market,
        symbol: binding.symbol,
        timeframe: binding.timeframe,
        question: binding.question,
        source_event_id: binding.source_event_id,
        source_event_version: String(binding.source_event_version),
        source_event_sha256: binding.source_event_sha256,
      })
      navigate(`/deliberation?${params}`)
    } catch (caught) {
      setWatchMessage(caught instanceof Error ? caught.message : '当前股票暂时无法进入多空观点对照，请选择其他候选。')
    }
  }
  const scrollToAnchor = (id: (typeof DISCOVER_ANCHORS)[number]['id']) => {
    const element = document.getElementById(id)
    if (!element) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    element.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' })
  }
  const openAIResearch = () => {
    const normalized = aiSymbol.trim().toUpperCase()
    const target = records.find((item) => item.symbol?.toUpperCase() === normalized) ?? (selected?.symbol?.toUpperCase() === normalized ? selected : null)
    if (!target) {
      setAiMessage('当前真实候选池中未找到该股票，请先调整筛选或选择候选。')
      return
    }
    setAiMessage('')
    research(target)
  }
  const applyFilters = () => updateParams({ page: '1', selected: null })
  const resetFilters = () => updateParams({ q: null, market: null, action: null, coverage: null, page: null, selected: null })
  const isSaved = (item: RecommendationItem) => {
    if (!item.symbol) return false
    const code = watchlistMarket(item.market)
    return code === 'US' ? watchlists.us.includes(item.symbol) : code === 'CN' ? watchlists.a_share.includes(item.symbol) : false
  }
  const loadMoreCandidates = (container: HTMLDivElement) => {
    if (currentPage >= pageCount || container.scrollHeight - container.scrollTop - container.clientHeight > 80) return
    updateParams({ page: String(currentPage + 1) })
  }

  const tableContent = workspace.mode === 'loading'
    ? <V2StatePanel state="loading" title="正在读取股票候选" detail="候选记录、研究状态与数据时效正在同步。" />
    : !isAuth
      ? <V2StatePanel state={workspace.mode === 'offline' ? 'offline' : 'locked'} title={workspace.mode === 'offline' ? '候选池暂时不可用' : '请登录查看真实候选'} detail={workspace.error || '只读状态不展示虚构股票列表。'} />
      : view !== '候选股票'
        ? <V2StatePanel state="empty" title={`${view}暂无可验证记录`} detail="当前 Workspace 尚未提供该视图的真实数据；候选股票视图仍可继续使用。" />
        : filtered.length
          ? (
            <div className="v2-candidate-table" tabIndex={0} aria-label="候选股票滚动列表" onScroll={(event) => loadMoreCandidates(event.currentTarget)}>
              <table>
                <thead><tr><th>股票 / 策略</th><th>价格 / Mini K线</th><th>研究状态</th><th>证据</th><th>更新时间</th><th>操作</th></tr></thead>
                <tbody>{visibleRecords.map((item) => (
                  <CandidateRow
                    key={item.event_id}
                    item={item}
                    authenticated={isAuth}
                    selected={selected?.event_id === item.event_id}
                    saved={isSaved(item)}
                    watchBusy={watchBusy === `${marketName(item.market)}-${item.symbol}`}
                    onSelect={() => void selectCandidate(item)}
                    onResearch={() => research(item)}
                    onWatchlist={(remove) => toggleWatchlist(item, remove)}
                    onAlert={() => alertDraft(item)}
                  />
                ))}</tbody>
              </table>
              <div className="discover-scroll-status" role="status" aria-live="polite">
                {currentPage < pageCount
                  ? `已显示 ${visibleRecords.length} / ${filtered.length} 条 · 继续向下滚动加载`
                  : `已显示全部 ${filtered.length} 条候选`}
              </div>
            </div>
          )
          : <V2StatePanel state="empty" title="当前没有匹配的股票记录" detail="调整市场、行动状态、覆盖条件或搜索代码。" />

  return (
    <div className="v2-page discover-v2-page">
      <DiscoverAIBanner selected={selected} filteredCount={filtered.length} completeCount={completeCount} tier={cicloTier} source={workspace.data?.recommendations.source} freshness={workspace.data?.market_data.freshness} onResearch={() => research(selected)} />
      <nav className={`discover-mobile-anchors ${anchorVisible ? 'is-visible' : ''}`} aria-label="发现页板块导航">{DISCOVER_ANCHORS.map((anchor) => <button className={activeAnchor === anchor.id ? 'is-active' : ''} type="button" aria-label={`滚动到${anchor.label}`} title={anchor.label} onClick={() => scrollToAnchor(anchor.id)} key={anchor.id}><i /><span>{anchor.label}</span></button>)}</nav>

      <section className="discover-command-strip" aria-label="发现页范围与视图">
        <V2PageContext task="候选股票范围" account="研究域" market={market === '全部' ? '全部市场' : market} freshness={workspace.data?.market_data.freshness} observedAt={workspace.data?.market_data.observed_at} detail={workspace.data?.market_data.detail} />
        <nav className="v2-view-tabs discover-view-tabs" aria-label="发现页视图">
          {(['候选股票', '事件发现', '研究覆盖'] as DiscoverView[]).map((item) => <button key={item} className={view === item ? 'is-active' : ''} role="tab" aria-selected={view === item} type="button" onClick={() => updateParams({ view: item === '候选股票' ? null : item, page: '1', selected: null })}>{item}</button>)}
        </nav>
        <InspectorToggle open={inspectorOpen} onClick={() => setInspectorOpen((value) => !value)} label="打开股票筛选、AI 入口与账户快照" />
      </section>
      {watchMessage && <div className="discover-page-feedback" role="status">{watchMessage}</div>}

      <section className="discover-anchor-section" id="discover-action"><TodayActionMatrix items={view === '候选股票' ? visibleRecords : []} authenticated={isAuth} onSelect={(item) => void selectCandidate(item)} onResearch={research} /></section>
      <section className="discover-anchor-section" id="discover-beginner"><BeginnerRecommendations items={view === '候选股票' ? filtered : []} authenticated={isAuth} onResearch={research} /></section>

      <div className={`discover-dashboard ${inspectorOpen ? 'has-open-inspector' : ''}`}>
        <aside className="discover-left-rail discover-anchor-section" id="discover-watchlist" aria-label="自选与研究覆盖">
          <WatchlistPanel entries={watchlistEntries} selectedId={selectedId} busyKey={watchBusy} onSelect={(item) => void selectCandidate(item)} onToggle={toggleWatchlistEntry} />
          <CoveragePanel records={records} authenticated={isAuth} source={workspace.data?.recommendations.source} delivery={workspace.data?.recommendations.delivery.stock} />
        </aside>

        <section className="discover-center-column discover-anchor-section" id="discover-candidates" aria-label="候选股票与近期事件">
          <V2Card className="discover-market-card">
            <header className="discover-card-heading discover-market-heading">
              <div><span className="v2-eyebrow">CANDIDATE MATRIX</span><h2>候选股票研究矩阵</h2></div>
              <div className="discover-market-controls">
                <div className="discover-periods" aria-label="Mini K线周期"><button className="is-active" type="button" aria-pressed="true">1D</button><button type="button" disabled>1W</button><button type="button" disabled>1M</button></div>
              </div>
            </header>
            {tableContent}
            <DiscoverFooter count={isAuth && view === '候选股票' ? visibleRecords.length : 0} />
          </V2Card>
          <section className="discover-anchor-section" id="discover-timeline"><RecentTimeline items={view === '候选股票' ? visibleRecords : []} selectedId={selectedId} onSelect={(item) => void selectCandidate(item)} onResearch={() => research(selected)} /></section>
        </section>

        {inspectorOpen && <button className="discover-inspector-backdrop" type="button" aria-label="关闭股票筛选面板" onClick={() => setInspectorOpen(false)} />}
        <aside className={`v2-inspector discover-right-rail ${inspectorOpen ? 'is-open' : ''}`} aria-label="股票筛选、AI 入口与账户快照">
          <div className="discover-inspector-heading"><strong>筛选与研究面板</strong><button type="button" aria-label="关闭股票筛选面板" onClick={() => setInspectorOpen(false)}><X size={17} /></button></div>
          <V2Card className="discover-filter-card">
            <header className="discover-card-heading"><div><span className="v2-eyebrow">SMART FILTER</span><h2>智能条件筛选</h2></div><Filter size={16} /></header>
            <div className="discover-filter-grid">
              <div className="discover-filter-search"><span>股票代码</span><SearchField value={query} onChange={(value) => updateParams({ q: value || null, page: '1', selected: null })} placeholder="搜索真实候选" /></div>
              <label><span>市场</span><select value={market} onChange={(event) => updateParams({ market: event.target.value === '全部' ? null : event.target.value, page: '1', selected: null })}><option>全部</option><option>美股</option><option>A股</option></select></label>
              <label><span>行动状态</span><select value={actionFilter} onChange={(event) => updateParams({ action: event.target.value === '全部' ? null : event.target.value, page: '1', selected: null })}><option>全部</option><option>研究候选</option><option>风险复核</option><option>访问受限</option></select></label>
              <label><span>研究覆盖</span><select value={coverageFilter} onChange={(event) => updateParams({ coverage: event.target.value === '全部' ? null : event.target.value, page: '1', selected: null })}><option>全部</option><option>资料完整</option><option>资料缺口</option></select></label>
            </div>
            <div className="discover-filter-actions"><button className="v2-button v2-button-secondary" type="button" onClick={resetFilters}><RotateCcw size={14} />重置</button><button className="v2-button v2-button-primary" type="button" onClick={applyFilters}><Sparkles size={14} />应用筛选</button></div>
            <FilterResultChart items={filtered} />
          </V2Card>

          <AIResearchPanel selected={selected} authenticated={isAuth} symbol={aiSymbol} message={aiMessage} onSymbolChange={setAiSymbol} onSubmit={openAIResearch} onResearch={() => research(selected)} onPaper={() => paper(selected)} onAlert={() => alertDraft(selected)} />
          <AccountSnapshotPanel data={workspace.data} authenticated={isAuth} />
        </aside>
      </div>

      <DiscoverStatusBar data={workspace.data} authenticated={isAuth} />
    </div>
  )
}

export default DiscoverV2Page
