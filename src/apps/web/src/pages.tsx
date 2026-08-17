import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Bell,
  BellOff,
  BellRing,
  Bot,
  CalendarDays,
  CandlestickChart,
  CircleAlert,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Database,
  FlaskConical,
  Layers3,
  LockKeyhole,
  Newspaper,
  PanelBottomClose,
  PanelBottomOpen,
  PencilRuler,
  Eye,
  EyeOff,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Target,
  Trash2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { DecisionCard } from './components/DecisionCard'
import { ChartWorkspace } from './components/ChartWorkspace'
import { PageHeader } from './components/PageHeader'
import { StockLogo } from './components/StockLogo'
import { MarketOverview as MarketOverviewDashboard } from './components/MarketOverview'
import { WorkspaceState } from './components/WorkspaceState'
import { useWorkspace } from './api/workspace-context'
import { createPriceAlert, deactivatePriceAlert, fetchMarketCandles, fetchMarketQuote, type MarketQuotePayload, type PriceAlert } from './api/client'
import { subscribeMarketStream } from './api/marketStream'
import { recommendationToDecision } from './data/adapters'
import type { Candle, Instrument, Market } from './types'
import { getFormatLocale } from './i18n/runtime'
import { useLocale } from './i18n/useLocale'
import { createVisibilityPolling, deliveryAllowsImmediateAction, displayDataSource, displayDeliveryDelay, displayFreshness, safeDataError } from './domain/dataSourcePresentation'

function alertMarket(item: PriceAlert): Market {
  if (item.market === 'CN' || item.market === 'A股') return 'CN'
  if (item.market === 'US' || item.market === '美股') return 'US'
  // Older records have no market field. Keep their conventional US/CN scope
  // instead of showing a same-symbol alert in a different market.
  return /^\d{6}$/.test(item.symbol) ? 'CN' : 'US'
}

function isAlertForInstrument(item: PriceAlert, market: Market, symbol: string) {
  return item.symbol === symbol && alertMarket(item) === market
}

function normalizeRecommendationMarket(market?: string): Market | null {
  if (market === 'US' || market === '美股') return 'US'
  if (market === 'CN' || market === 'A股') return 'CN'
  return null
}

export function TodayPage() {
  const navigate = useNavigate()
  const workspace = useWorkspace()
  const { formatLocale } = useLocale()
  const realDecisions = useMemo(() => workspace.data?.recommendations.items.map((item, index) => recommendationToDecision(item, index, formatLocale)).filter((item): item is NonNullable<typeof item> => item !== null) ?? [], [formatLocale, workspace.data])
  const featured = realDecisions.find((decision) => decision.actionable) ?? realDecisions[0] ?? null
  const queue = realDecisions.length
    ? realDecisions.filter((decision) => decision.eventId !== featured?.eventId && !decision.actionable).slice(0, 3)
    : []
  const exposureByCurrency = useMemo(() => (workspace.data?.portfolio.positions ?? []).reduce((totals, item) => {
    const currency = /^\d{6}$/.test(item.symbol) ? 'CNY' : 'USD'
    totals[currency] += Math.abs(item.market_value)
    return totals
  }, { USD: 0, CNY: 0 }), [workspace.data])
  const usdLimit = workspace.data?.settings.risk.max_total_position ?? 50_000
  const cnyLimit = workspace.data?.settings.risk.max_total_position_cny ?? 500_000
  const riskScore = Math.min(100, Math.round(Math.max(exposureByCurrency.USD / Math.max(usdLimit, 1), exposureByCurrency.CNY / Math.max(cnyLimit, 1)) * 100))
  const riskCurrency = exposureByCurrency.CNY / Math.max(cnyLimit, 1) > exposureByCurrency.USD / Math.max(usdLimit, 1) ? 'CNY' : 'USD'
  return (
    <div className="page today-page">
      <PageHeader kicker="TODAY / NEXT ACTION" title="今日行动机会" description="正式量化事件、持仓风险和等待机会入场的研究候选按优先级排列。" />
      <WorkspaceState empty={workspace.mode === 'authenticated' && realDecisions.length === 0} emptyText="当前账户没有有效的正式量化事件。系统不会用演示建议填充登录账户。" />
      <section className="today-grid">
        {featured ? <DecisionCard decision={featured} demo={false} /> : (
          <section className="today-empty-primary"><CircleAlert size={22} /><div><h2>等待下一条正式行动</h2><p>量化账本当前没有可执行记录。新的行动通过风控和发布审核后会出现在这里。</p></div></section>
        )}
        <aside className="side-rail">
          <section className="rail-section">
            <div className="section-heading"><span>账户风险</span><small>{riskCurrency} 最高占用</small></div>
            <strong className="risk-score">{riskScore}<small>/100</small></strong>
            <p>USD 与 CNY 分开计算；当前较高币种约占对应上限 {riskScore}%。这里不把不同币种直接相加。</p>
            <div className="risk-bar"><i style={{ width: `${riskScore}%` }} /></div>
          </section>
          <section className="rail-section">
            <div className="section-heading"><span>推荐模型</span><small>受控迭代</small></div>
            <dl className="model-status"><div><dt>账本版本</dt><dd>{featured?.modelVersion ?? '暂无正式版本'}</dd></div><div><dt>挑战版本</dt><dd>仅影子运行</dd></div><div><dt>自动发布</dt><dd>禁止</dd></div><div><dt>发布状态</dt><dd className="warning-text">需独立审核</dd></div></dl>
          </section>
          <section className="rail-section tg-status-panel"><div><Bot size={19} /><span><strong>Telegram {workspace.data?.telegram.consented ? '已启用' : '未启用'}</strong><small>{workspace.data?.telegram.updated_at ? `状态更新 ${new Date(workspace.data.telegram.updated_at).toLocaleString(getFormatLocale(), { hour12: false })}` : '暂无真实投递时间'}</small></span></div><button className="icon-button" type="button" aria-label="打开 Telegram 通知" onClick={() => navigate('/notifications')}><ChevronRight size={17} /></button></section>
        </aside>
      </section>
      <section className="section-block"><header className="section-title"><div><span>WAITING FOR ENTRY</span><h2>等待机会入场</h2></div><button className="button tertiary" type="button" onClick={() => navigate('/discover')}>查看全部机会 <ArrowRight size={15} /></button></header>{queue.length > 0 ? <div className="candidate-grid">{queue.map((decision) => <DecisionCard compact decision={decision} demo={false} key={decision.eventId} />)}</div> : <div className="opportunity-inline-empty">当前没有等待入场的正式记录。打开发现页查看其他股票与数据状态。</div>}</section>
    </div>
  )
}

const evidenceTabs = ['概览', '技术指标', '新闻与事件', '期权证据', '信号时间线']
const researchTimeframes = ['5分', '15分', '1小时', '日线', '周线', '月线']

function relativeFreshness(value?: string | null) {
  if (!value) return { label: '等待来源时间戳', tone: 'missing' }
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return { label: '时间戳格式异常', tone: 'missing' }
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000))
  if (minutes <= 1) return { label: '刚刚更新', tone: 'fresh' }
  if (minutes <= 5) return { label: `${minutes}分钟前`, tone: 'fresh' }
  if (minutes <= 30) return { label: `${minutes}分钟前`, tone: 'normal' }
  if (minutes <= 120) return { label: `${minutes}分钟前`, tone: 'delayed' }
  return { label: `${Math.max(2, Math.floor(minutes / 60))}小时前`, tone: 'stale' }
}

function sourceCode(source?: string | null) {
  if (!source) return 'SRC'
  const compact = source.replace(/[^a-z0-9]/gi, '').toUpperCase()
  return compact.slice(0, 3) || 'SRC'
}

function marketSessionLabel(market: Market) {
  const timeZone = market === 'US' ? 'America/New_York' : 'Asia/Shanghai'
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date())
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? ''
  const weekday = value('weekday')
  if (weekday === 'Sat' || weekday === 'Sun') return '休市参考'
  const minutes = Number(value('hour')) * 60 + Number(value('minute'))
  if (market === 'CN') return (minutes >= 570 && minutes < 690) || (minutes >= 780 && minutes < 900) ? '盘中参考' : '休市参考'
  if (minutes >= 570 && minutes < 960) return '盘中参考'
  if (minutes >= 240 && minutes < 570) return '盘前参考'
  if (minutes >= 960 && minutes < 1200) return '盘后参考'
  return '休市参考'
}

function ResearchMiniSparkline({ candles, symbol, rising }: { candles: Candle[]; symbol: string; rising: boolean }) {
  const values = candles.slice(-22).map((item) => item.close)
  if (values.length < 2) return <div className="research-mini-empty"><Bot size={16} /><span>等待 K 线</span></div>
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = Math.max(max - min, 0.0001)
  const points = values.map((value, index) => ({
    x: index / (values.length - 1) * 96,
    y: 27 - (value - min) / range * 22,
  }))
  const line = points.reduce((path, point, index) => {
    if (index === 0) return `M ${point.x.toFixed(2)} ${point.y.toFixed(2)}`
    const previous = points[index - 1]
    const midpoint = (previous.x + point.x) / 2
    return `${path} C ${midpoint.toFixed(2)} ${previous.y.toFixed(2)}, ${midpoint.toFixed(2)} ${point.y.toFixed(2)}, ${point.x.toFixed(2)} ${point.y.toFixed(2)}`
  }, '')
  const gradientId = `research-spark-${symbol.replace(/[^a-z0-9]/gi, '')}`
  const color = rising ? '#F87171' : '#2DD4BF'
  const last = points.at(-1)!
  return <svg className="research-mini-sparkline" viewBox="0 0 96 30" role="img" aria-label={`${symbol} 迷你 K 线`}>
    <defs><linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={color} stopOpacity=".34" /><stop offset="1" stopColor={color} stopOpacity="0" /></linearGradient></defs>
    <path className="research-mini-grid" d="M0 9H96 M0 19H96" />
    <path className="research-mini-area" d={`${line} L 96 30 L 0 30 Z`} fill={`url(#${gradientId})`} />
    <path className="research-mini-line" d={line} style={{ stroke: color }} />
    <circle cx={last.x} cy={last.y} r="2.4" fill="var(--workspace)" stroke={color} strokeWidth="1.5" />
  </svg>
}

function ResearchEmptyState({ title, detail, action, onAction, compact = false }: { title: string; detail: string; action?: string; onAction?: () => void; compact?: boolean }) {
  return <div className={`research-empty-state ${compact ? 'is-compact' : ''}`} role="status">
    <span className="research-empty-bot"><Bot size={compact ? 18 : 24} /></span>
    <div><strong>{title}</strong><small>{detail}</small></div>
    {action && onAction && <button className="button tertiary" type="button" onClick={onAction}>{action}<ArrowRight size={14} /></button>}
  </div>
}

function ResearchEnergy({ level, label }: { level: number; label: string }) {
  return <div className="research-energy" aria-label={`${label} ${level}/5`}><span>{label}</span><div>{Array.from({ length: 5 }, (_, index) => <i className={index < level ? 'active' : ''} key={index} />)}</div><strong>{level}/5</strong></div>
}

function QuoteCompletenessRing({ quote }: { quote: MarketQuotePayload | null }) {
  const fields = [quote?.bid, quote?.ask, quote?.open, quote?.high, quote?.low, quote?.volume]
  const available = fields.filter((value) => typeof value === 'number' && Number.isFinite(value)).length
  const percentage = Math.round(available / fields.length * 100)
  return <div className="research-gap-ring" aria-label={`报价字段完整度 ${percentage}%`}>
    <svg viewBox="0 0 64 64" aria-hidden="true"><circle className="ring-track" cx="32" cy="32" r="24" pathLength="100" /><circle className="ring-value" cx="32" cy="32" r="24" pathLength="100" strokeDasharray={`${percentage * .88} 100`} /></svg>
    <span><strong>{percentage}%</strong><small>报价完整</small></span>
  </div>
}

export function MarketsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const workspace = useWorkspace()
  const { updateMarketDataStatus } = workspace
  const { formatLocale } = useLocale()
  const [switcherStatus, setSwitcherStatus] = useState('')
  const [watchBusy, setWatchBusy] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [stockSwitcherOpen, setStockSwitcherOpen] = useState(false)
  const [mobileEvidenceOpen, setMobileEvidenceOpen] = useState(() => window.matchMedia('(max-width: 600px)').matches)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [chartOptions, setChartOptions] = useState({ volume: true, grid: true, upColor: '#F87171', downColor: '#2DD4BF', textColor: '#94A3B8' })
  const [liveCandles, setLiveCandles] = useState<Candle[]>([])
  const [liveCandleIdentity, setLiveCandleIdentity] = useState('')
  const [chartStatus, setChartStatus] = useState('')
  const [marketQuote, setMarketQuote] = useState<MarketQuotePayload | null>(null)
  const candleRequestSequence = useRef(0)
  const quoteRequestSequence = useRef(0)
  const [alertPrice, setAlertPrice] = useState(0)
  const [alertStatus, setAlertStatus] = useState('')
  const [alertBusy, setAlertBusy] = useState(false)
  const [localAlerts, setLocalAlerts] = useState<PriceAlert[] | null>(null)
  const [hiddenAlertIds, setHiddenAlertIds] = useState<number[]>([])
  const requestedSymbol = (searchParams.get('symbol') ?? '').toUpperCase()
  const requestedMarket = searchParams.get('market')
  const timeframe = searchParams.get('timeframe') ?? '日线'
  const activeTab = searchParams.get('tab') ?? '概览'
  const marketFilter = requestedMarket === 'CN' || requestedMarket === 'A股' ? 'CN' : 'US'
  const hasExplicitMarket = requestedMarket === 'US' || requestedMarket === '美股' || requestedMarket === 'CN' || requestedMarket === 'A股'
  const marketDataEnabled = workspace.mode === 'authenticated' && Boolean(workspace.data?.market_data) && workspace.data?.market_data.freshness !== '已停用'
  const watchlists = workspace.data?.settings.watchlists ?? { us: [], a_share: [] }
  const currentSavedSymbols = marketFilter === 'CN' ? watchlists.a_share : watchlists.us
  const savedInstruments = useMemo<Instrument[]>(() => currentSavedSymbols.map((symbol) => {
    return {
      symbol,
      name: symbol,
      market: marketFilter,
      price: 0,
      changePct: 0,
      currency: marketFilter === 'CN' ? 'CNY' : 'USD',
    }
  }), [currentSavedSymbols, marketFilter])
  const allInstruments = savedInstruments
  const selectedBase = useMemo<Instrument>(() => allInstruments.find((item) => item.symbol === requestedSymbol) ?? ({
    symbol: requestedSymbol,
    name: requestedSymbol,
    market: marketFilter,
    price: 0,
    changePct: 0,
    currency: marketFilter === 'CN' ? 'CNY' : 'USD',
  }), [allInstruments, marketFilter, requestedSymbol])
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams)
    next.set(key, value)
    setSearchParams(next)
  }
  const setMarket = (market: 'US' | 'CN') => {
    const next = new URLSearchParams(searchParams)
    next.set('market', market)
    const saved = market === 'CN' ? watchlists.a_share : watchlists.us
    if (saved[0]) next.set('symbol', saved[0])
    else next.delete('symbol')
    next.delete('event_id')
    setSearchParams(next)
    setStockSwitcherOpen(false)
  }
  const visibleInstruments = savedInstruments.map((instrument) => instrument.symbol === selectedBase.symbol
      && marketQuote?.symbol === instrument.symbol
      && typeof marketQuote.last === 'number'
      && Number.isFinite(marketQuote.last)
      ? { ...instrument, price: marketQuote.last, changePct: typeof marketQuote.prev_close === 'number' && marketQuote.prev_close ? (marketQuote.last - marketQuote.prev_close) / marketQuote.prev_close * 100 : 0 }
      : instrument)

  useEffect(() => {
    const currentQuote = marketQuote?.symbol === selectedBase.symbol ? marketQuote : null
    const currentPrice = typeof currentQuote?.last === 'number' && Number.isFinite(currentQuote.last) && currentQuote.last > 0
      ? currentQuote.last
      : 0
    setAlertPrice(currentPrice)
  }, [marketQuote, selectedBase.symbol])

  useEffect(() => {
    setLocalAlerts(workspace.data?.alerts.items ?? [])
  }, [workspace.data?.alerts.items])

  useEffect(() => {
    let active = true
    setLiveCandles([])
    if (!marketDataEnabled || !selectedBase.symbol) { setChartStatus(''); return () => { active = false } }
    setChartStatus('正在读取受控行情…')
    const stopPolling = createVisibilityPolling(async () => {
      const sequence = ++candleRequestSequence.current
      try {
        const payload = await fetchMarketCandles(selectedBase.symbol, timeframe)
        if (!active || candleRequestSequence.current !== sequence) return
        setLiveCandleIdentity(`${selectedBase.symbol}:${timeframe}`)
        setLiveCandles(Array.isArray(payload.items) ? payload.items : [])
        const delivery = displayDeliveryDelay(payload.status.delivery_delay_minutes)
        setChartStatus(`${displayDataSource(payload.status.display_source)} · ${delivery || displayFreshness(payload.status.freshness)}`)
      } catch {
        if (active && candleRequestSequence.current === sequence) setChartStatus(safeDataError())
      }
    }, 15_000)
    return () => { active = false; stopPolling() }
  }, [marketDataEnabled, selectedBase.symbol, timeframe])

  useEffect(() => {
    let active = true
    setMarketQuote(null)
    if (!marketDataEnabled || !selectedBase.symbol) {
      return () => { active = false }
    }
    const stopPolling = createVisibilityPolling(async () => {
      const sequence = ++quoteRequestSequence.current
      try {
        const payload = await fetchMarketQuote(selectedBase.symbol)
        if (!active || quoteRequestSequence.current !== sequence) return
        setMarketQuote(payload)
        updateMarketDataStatus({
          display_source: '真实数据来源',
          is_realtime: payload.is_realtime,
          freshness: payload.delivery_delay_minutes === undefined
            ? displayFreshness(payload.freshness)
            : payload.delivery_delay_minutes > 0
              ? '延迟行情'
              : payload.is_realtime ? '实时' : '仅供研究',
          detail: '当前股票报价状态已核对',
          ...(payload.delivery_delay_minutes === undefined ? {} : {
            delivery_delay_minutes: payload.delivery_delay_minutes,
            visible_as_of: payload.visible_as_of,
            observed_at: payload.observed_at,
          }),
        })
      } catch {
        if (active && quoteRequestSequence.current === sequence) {
          setMarketQuote(null)
        }
      }
    }, 5_000)
    return () => { active = false; stopPolling() }
  }, [marketDataEnabled, selectedBase.symbol, updateMarketDataStatus])

  const openInstrument = (instrument: Instrument) => {
    const next = new URLSearchParams(searchParams)
    next.set('market', instrument.market)
    next.set('symbol', instrument.symbol)
    next.delete('event_id')
    setSearchParams(next)
    setStockSwitcherOpen(false)
  }

  const changeWatchlist = async (instrument: Instrument, remove: boolean) => {
    setWatchBusy(instrument.symbol)
    setSwitcherStatus('')
    try {
      await workspace.changeWatchlist(instrument.market, instrument.symbol, remove)
      setSwitcherStatus(remove ? `${instrument.symbol} 已从自选移除` : `${instrument.symbol} 已加入自选`)
    } catch (caught) {
      setSwitcherStatus(caught instanceof Error ? caught.message : '自选更新失败。')
    } finally {
      setWatchBusy('')
    }
  }

  const changeChartWatchlist = async (market: Instrument['market'], symbol: string, remove: boolean) => {
    const instrument = {
      symbol, name: symbol, market, price: 0, changePct: 0, currency: market === 'CN' ? 'CNY' as const : 'USD' as const,
    }
    await changeWatchlist(instrument, remove)
  }

  const currentCandleIdentity = `${selectedBase.symbol}:${timeframe}`
  const currentLiveCandles = useMemo(
    () => liveCandleIdentity === currentCandleIdentity && Array.isArray(liveCandles) ? liveCandles : [],
    [currentCandleIdentity, liveCandleIdentity, liveCandles],
  )
  const selected = useMemo(() => {
    const currentQuote = marketQuote?.symbol === selectedBase.symbol ? marketQuote : null
    if (currentQuote && typeof currentQuote.last === 'number') {
      const changePct = typeof currentQuote.prev_close === 'number' && currentQuote.prev_close
        ? (currentQuote.last - currentQuote.prev_close) / currentQuote.prev_close * 100
        : 0
      return { ...selectedBase, price: currentQuote.last, changePct }
    }
    return { ...selectedBase, price: 0, changePct: 0 }
  }, [marketQuote, selectedBase])
  const chartData = useMemo(() => {
    if (currentLiveCandles.length) return currentLiveCandles
    return []
  }, [currentLiveCandles])
  const officialDecision = useMemo(() => {
    const eventParam = searchParams.get('event_id')
    const eventId = Number(eventParam)
    const hasEvent = Boolean(eventParam)
    const validEvent = Number.isSafeInteger(eventId) && eventId > 0
    if (hasEvent && !validEvent) return null
    const item = workspace.data?.recommendations.items.find((candidate) => {
      const candidateSymbol = candidate.symbol?.trim().toUpperCase()
      const candidateMarket = normalizeRecommendationMarket(candidate.market)
      if (!hasExplicitMarket || !candidateSymbol || !candidateMarket || !selected.symbol || !selected.market) return false
      if (candidateSymbol !== selected.symbol.toUpperCase() || candidateMarket !== selected.market) return false
      return !hasEvent || candidate.event_id === eventId
    })
    if (!item) return null
    const currentQuote = marketQuote?.symbol === selected.symbol ? marketQuote : null
    const quoteOverride = currentQuote && deliveryAllowsImmediateAction(currentQuote)
      && typeof currentQuote.last === 'number' && currentQuote.quote_at
      ? { price: currentQuote.last, quoteAt: currentQuote.quote_at }
      : undefined
    return recommendationToDecision(item, 0, formatLocale, quoteOverride)
  }, [formatLocale, hasExplicitMarket, marketQuote, searchParams, selected.market, selected.symbol, workspace.data])
  const alerts = useMemo(() => localAlerts ?? workspace.data?.alerts.items ?? [], [localAlerts, workspace.data?.alerts.items])
  const alertPricesForInstrument = useCallback((market: Market, symbol: string) => alerts
    .filter((item) => isAlertForInstrument(item, market, symbol) && item.is_active !== false && (item.id === undefined || !hiddenAlertIds.includes(item.id)))
    .flatMap((item) => {
      if (typeof item.target_price === 'number' && Number.isFinite(item.target_price)) return [item.target_price]
      const priceCondition = Array.isArray(item.conditions) ? item.conditions.find((condition) => typeof condition === 'object' && condition !== null && (condition as { type?: string }).type === 'price') as { value?: unknown } | undefined : undefined
      return typeof priceCondition?.value === 'number' && Number.isFinite(priceCondition.value) ? [priceCondition.value] : []
    }), [alerts, hiddenAlertIds])
  const loadWorkspaceCandles = useCallback(async (symbol: string, nextTimeframe: string) => {
    if (!marketDataEnabled) throw new Error('行情连接未启用。')
    const payload = await fetchMarketCandles(symbol, nextTimeframe)
    return payload.items
  }, [marketDataEnabled])

  const loadWorkspaceQuote = useCallback(async (symbol: string) => {
    if (!marketDataEnabled) throw new Error('行情连接未启用。')
    return fetchMarketQuote(symbol)
  }, [marketDataEnabled])

  const openTechnicalIndicators = () => {
    setParam('tab', '技术指标')
    setMobileEvidenceOpen(true)
  }

  useEffect(() => {
    const desktopInspector = window.matchMedia('(min-width: 1071px)')
    const closeOverlayInspector = (event: MediaQueryListEvent) => {
      if (!event.matches) setInspectorOpen(false)
    }
    desktopInspector.addEventListener('change', closeOverlayInspector)
    return () => desktopInspector.removeEventListener('change', closeOverlayInspector)
  }, [])

  const saveAlertInChart = async () => {
    if (!Number.isFinite(alertPrice) || alertPrice <= 0 || !selected.symbol) return
    setAlertBusy(true)
    setAlertStatus('正在保存预警…')
    try {
      const payload = await createPriceAlert(selected.symbol, alertPrice, { market: selected.market })
      setLocalAlerts(payload.items)
      setAlertStatus(`已在 ${alertPrice.toFixed(2)} 设定价格预警，图上已显示水平线。`)
    } catch (caught) {
      setAlertStatus(caught instanceof Error ? caught.message : '预警保存失败，请稍后再试。')
    } finally {
      setAlertBusy(false)
    }
  }

  const disableAlertInChart = async (id: number) => {
    setAlertBusy(true)
    try {
      const payload = await deactivatePriceAlert(id)
      setLocalAlerts(payload.items)
      setAlertStatus('预警已关闭，图上的价位线也会移除。')
    } catch (caught) {
      setAlertStatus(caught instanceof Error ? caught.message : '预警关闭失败，请稍后再试。')
    } finally {
      setAlertBusy(false)
    }
  }
  const toggleAlertMarker = (id: number) => {
    setHiddenAlertIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])
  }
  const capabilities = workspace.data?.membership.capabilities ?? []
  const plan = workspace.user?.plan ?? '免费版'
  const aiAvailable = capabilities.includes('ai_workspace') || ['标准版', '高级版', '专业版', '定制版'].includes(plan)
  const optionCapabilities = ['option_chain', 'option_quote_chart', 'option_greeks', 'option_iv', 'option_strategy', 'option_strategy_multi_leg']
  const optionUnlocked = optionCapabilities.every((capability) => capabilities.includes(capability)) || ['专业版', '定制版'].includes(plan)
  const compareUnlocked = aiAvailable
  const quoteTimestamp = marketQuote?.visible_as_of ?? marketQuote?.quote_at ?? marketQuote?.observed_at
  const freshness = relativeFreshness(quoteTimestamp)
  const activeSource = marketQuote?.source ? displayDataSource(marketQuote.source) : currentLiveCandles.length ? chartStatus : '等待可验证来源'
  const openAiResearch = () => {
    if (!aiAvailable) { navigate('/membership'); return }
    const query = new URLSearchParams({ market: selected.market, symbol: selected.symbol, scope: 'single-stock-research' })
    navigate(`/ai?${query.toString()}`)
  }
  const activateDrawingTool = (label: string) => {
    document.querySelector<HTMLButtonElement>(`.research-market-workspace .drawing-tool-dock button[aria-label="${label}"]`)?.click()
  }
  const researchToolPanel = <div className="research-tool-panel-inner">
    <header><span>TOOL NAV</span><strong>研究工具</strong><small>{selected.symbol} · {timeframe}</small></header>
    <section><h2><Clock3 size={14} />周期切换</h2><div className="research-tool-grid">{researchTimeframes.map((item) => <button className={timeframe === item ? 'active' : ''} type="button" onClick={() => setParam('timeframe', item)} key={item}>{item}</button>)}</div></section>
    <section><h2><Activity size={14} />指标与事件</h2><div className="research-tool-stack"><button className={activeTab === '技术指标' ? 'active' : ''} type="button" onClick={openTechnicalIndicators}><Activity size={14} />技术指标</button><button type="button" onClick={() => { setParam('tab', '概览'); setMobileEvidenceOpen(true) }}><ShieldCheck size={14} />证据概览</button><button type="button" onClick={() => { setParam('tab', '信号时间线'); setMobileEvidenceOpen(true) }}><Target size={14} />事件标记</button></div></section>
    <section><h2><PencilRuler size={14} />画线工具</h2><div className="research-tool-stack"><button type="button" onClick={() => activateDrawingTool('线段')}>趋势线</button><button type="button" onClick={() => activateDrawingTool('水平直线')}>水平线</button><button type="button" onClick={() => activateDrawingTool('全部画线工具')}>全部工具</button></div><small className="research-tool-note">画线同步与撤销沿用图表原有逻辑。</small></section>
    <section><h2><Sparkles size={14} />研究入口</h2><div className="research-tool-stack"><button type="button" onClick={openAiResearch}><Sparkles size={14} />{aiAvailable ? 'AI 研究' : '升级解锁 AI'}</button><button type="button" onClick={() => { setParam('tab', '新闻与事件'); setMobileEvidenceOpen(true) }}><Newspaper size={14} />新闻与事件</button><button type="button" onClick={() => setInspectorOpen(true)}><Layers3 size={14} />盘口与资料</button></div></section>
    <section className="research-tool-alerts"><h2><BellRing size={14} />图内预警</h2><p className="alert-safety-note"><ShieldCheck size={14} />只提醒，不会自动买卖。</p><div className="inline-alert-form"><label><span>提醒价格</span><input aria-label="提醒价格" inputMode="decimal" min="0.01" step="0.01" type="number" value={alertPrice || ''} onChange={(event) => setAlertPrice(Number(event.target.value))} /></label><button className="button primary" type="button" disabled={alertBusy || !alertPrice} onClick={() => void saveAlertInChart()}><BellRing size={15} />保存</button></div>{alertStatus && <p className="form-status" role="status" aria-live="polite">{alertStatus}</p>}<div className="alert-list">{alerts.filter((item) => isAlertForInstrument(item, selected.market, selected.symbol)).map((item) => { const isActive = item.is_active === undefined || item.is_active === true; const markerVisible = item.id === undefined || !hiddenAlertIds.includes(item.id); return <div key={item.id ?? `${item.symbol}-${item.target_price}`}><span><strong>{item.target_price ?? '条件预警'}</strong><small>{isActive ? <><Bell size={13} aria-hidden="true" />已开启</> : <><BellOff size={13} aria-hidden="true" />已关闭</>}</small></span>{item.id && isActive && <span className="alert-list-actions"><button className="icon-button" type="button" aria-label={`${markerVisible ? '隐藏' : '显示'} ${item.symbol} ${item.target_price ?? ''} 预警线`} title={markerVisible ? '隐藏图上预警线' : '显示图上预警线'} onClick={() => toggleAlertMarker(item.id!)}>{markerVisible ? <Eye size={15} /> : <EyeOff size={15} />}</button><button className="icon-button danger" type="button" aria-label={`关闭 ${item.symbol} ${item.target_price ?? ''} 预警`} title="关闭预警并移除价格线" disabled={alertBusy} onClick={() => void disableAlertInChart(item.id!)}><Trash2 size={15} /></button></span>}</div> })}</div></section>
    <section className="research-quote-card"><h2><CandlestickChart size={14} />报价与市场</h2><QuoteCompletenessRing quote={marketQuote} /><div className="research-quote-grid"><div><span>Bid</span><strong>{marketQuote?.bid?.toFixed(2) ?? '等待买价'}</strong></div><div><span>Ask</span><strong>{marketQuote?.ask?.toFixed(2) ?? '等待卖价'}</strong></div><div><span>最高</span><strong>{marketQuote?.high?.toFixed(2) ?? '等待区间'}</strong></div><div><span>最低</span><strong>{marketQuote?.low?.toFixed(2) ?? '等待区间'}</strong></div></div><footer><span className={`research-freshness-dot ${freshness.tone}`} />{activeSource}</footer></section>
  </div>

  if (searchParams.has('symbol') && !/^(?:[A-Z][A-Z0-9.=-]{0,14}|\d{6})$/.test(requestedSymbol)) {
    return <div className="page research-stock-empty">
      <PageHeader kicker="MARKET / RESEARCH" title="无法打开这只股票" description="链接中的股票代码为空或格式不受支持，研究页不会猜测或补入演示标的。" />
      <ResearchEmptyState title="缺少有效股票代码" detail="请从发现页、行情总览或全局搜索选择一只股票。" action="前往发现股票" onAction={() => navigate('/discover')} />
    </div>
  }

  if (!searchParams.has('symbol')) {
    return <MarketOverviewDashboard
      market={marketFilter}
      watchlist={currentSavedSymbols}
      marketDataEnabled={marketDataEnabled}
      authenticated={workspace.mode === 'authenticated'}
      busySymbol={watchBusy}
      onMarketChange={setMarket}
      onOpen={openInstrument}
      onWatchlist={changeWatchlist}
    />
  }

  return (
    <div className={`market-workspace research-market-workspace ${stockSwitcherOpen ? 'stock-switcher-open' : ''} ${mobileEvidenceOpen ? 'mobile-evidence-open' : ''}`}>
      <header className="instrument-header research-instrument-header">
        <div className="research-instrument-identity"><StockLogo symbol={selected.symbol} market={selected.market} size="lg" /><span><strong>{selected.name}</strong><small>{selected.symbol} · {selected.market === 'US' ? '美股' : 'A股'} · {selected.currency}</small></span></div>
        <div className="instrument-price"><strong>{selected.price ? selected.price.toFixed(2) : '等待报价'}</strong>{selected.price > 0 && <span className={selected.changePct >= 0 ? 'positive-text' : 'negative-text'}>{selected.changePct >= 0 ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />}{selected.changePct >= 0 ? '+' : ''}{selected.changePct.toFixed(2)}%</span>}</div>
        <ResearchMiniSparkline candles={chartData} symbol={selected.symbol} rising={selected.changePct >= 0} />
        <div className="research-market-status"><span className={`research-status-pill ${marketQuote?.is_realtime ? 'is-live' : marketQuote ? 'is-delayed' : 'is-missing'}`}><i />{marketQuote?.is_realtime ? '实时行情' : marketQuote ? '延迟/研究行情' : '等待行情'}</span><span className="research-status-pill is-session"><Clock3 size={12} />{marketSessionLabel(selected.market)}</span><small><span className={`research-freshness-dot ${freshness.tone}`} />{freshness.label} · <b className="research-source-badge">{sourceCode(marketQuote?.source)}</b></small></div>
        <div className="instrument-actions"><button className="button secondary" type="button" aria-controls="research-stock-switcher" aria-expanded={stockSwitcherOpen} onClick={() => setStockSwitcherOpen((current) => !current)}><Search size={16} /> 切换股票</button><button className="button tertiary" type="button" onClick={() => navigate(`/portfolio?market=${selected.market}&symbol=${encodeURIComponent(selected.symbol)}&event_id=${officialDecision?.officialEventId ?? ''}`)}><CircleDollarSign size={16} /> 官方验证复盘</button></div>
        <div className="mobile-market-controls"><button type="button" aria-controls="research-stock-switcher" aria-expanded={stockSwitcherOpen} onClick={() => setStockSwitcherOpen((current) => !current)}><Search size={16} /> 股票</button><button type="button" aria-controls="mobile-evidence-panel" aria-expanded={mobileEvidenceOpen} onClick={() => setMobileEvidenceOpen((current) => !current)}>{mobileEvidenceOpen ? <PanelBottomClose size={16} /> : <PanelBottomOpen size={16} />} 分析</button></div>
      </header>
      {stockSwitcherOpen && <section className="research-stock-switcher" id="research-stock-switcher" aria-label="当前股票切换"><header><strong>切换当前股票</strong><span>不复制全局搜索或发现筛选</span></header><div className="market-tabs"><button className={marketFilter === 'US' ? 'active' : ''} type="button" onClick={() => setMarket('US')}>美股</button><button className={marketFilter === 'CN' ? 'active' : ''} type="button" onClick={() => setMarket('CN')}>A股</button></div><div className="research-switcher-list">{visibleInstruments.slice(0, 8).map((instrument) => <button className={instrument.symbol === selected.symbol ? 'active' : ''} type="button" onClick={() => openInstrument(instrument)} key={instrument.symbol}><StockLogo symbol={instrument.symbol} market={instrument.market} size="sm" /><span><strong>{instrument.symbol}</strong><small>{instrument.name}</small></span><span>{instrument.price ? instrument.price.toFixed(2) : '等待报价'}</span></button>)}{!visibleInstruments.length && <ResearchEmptyState compact title="当前市场没有自选股票" detail="前往发现页建立候选后会显示在这里。" />}</div>{switcherStatus && <p className="form-status" role="status" aria-live="polite">{switcherStatus}</p>}<button className="button tertiary wide" type="button" onClick={() => navigate('/discover')}>前往发现股票</button></section>}

      <div className="research-workbench-grid">
        <main className="research-center-column">
          <section className="research-panel research-chart-panel">
            <header className="research-panel-header"><div><span>K-LINE RESEARCH</span><strong>K 线研究工作区</strong></div><div><span className="research-status-pill is-live"><i />成交量</span><span className="research-status-pill is-event"><Target size={12} />事件标记</span><span className="research-status-pill is-gap"><Database size={12} />原序列保留缺口</span></div></header>
            <div className="chart-frame"><ChartWorkspace userId={workspace.data?.me.id} initialSymbol={selected.symbol} initialMarket={selected.market} initialTimeframe={timeframe} inspectorOpen={inspectorOpen} onInspectorOpenChange={setInspectorOpen} toolPanel={researchToolPanel} toolPanelLabel="研究工具" toolbarActions={<><button type="button" aria-label="查看盘口与资料" title="盘口与资料" onClick={() => setInspectorOpen(true)}><Layers3 size={15} /><span>盘口</span></button><button type="button" aria-label="查看技术指标" title="技术指标" onClick={openTechnicalIndicators}><Activity size={15} /><span>指标</span></button><button className={settingsOpen ? 'active' : ''} type="button" aria-label="图表设置" title="图表设置" aria-expanded={settingsOpen} onClick={() => setSettingsOpen(!settingsOpen)}><Settings2 size={15} /><span>设置</span></button></>} candles={chartData} showGrid={chartOptions.grid} showVolume={chartOptions.volume} upColor={chartOptions.upColor} downColor={chartOptions.downColor} textColor={chartOptions.textColor} dataStatus={currentLiveCandles.length ? chartStatus : chartStatus || '暂无真实行情'} initialQuote={marketQuote?.symbol === selected.symbol ? marketQuote : null} loadQuote={marketDataEnabled ? loadWorkspaceQuote : undefined} subscribeMarketStream={marketDataEnabled ? subscribeMarketStream : undefined} officialActivity={workspace.data?.portfolio.activity ?? null} alertPrices={alertPricesForInstrument} watchlistSymbols={{ US: watchlists.us, CN: watchlists.a_share }} isWatchlisted={(market, symbol) => (market === 'CN' ? watchlists.a_share : watchlists.us).includes(symbol)} onWatchlistToggle={changeChartWatchlist} watchBusy={watchBusy} loadCandles={marketDataEnabled ? loadWorkspaceCandles : undefined} onSymbolChange={(symbol, market) => { const next = new URLSearchParams(searchParams); next.set('market', market); next.set('symbol', symbol); next.delete('event_id'); setSearchParams(next) }} onTimeframeChange={(nextTimeframe) => setParam('timeframe', nextTimeframe)} />{!currentLiveCandles.length && <div className="chart-empty-state"><span className="research-empty-bot"><Bot size={24} /></span><strong>{marketDataEnabled ? '暂时无法读取 K 线' : '行情连接未启用'}</strong><span>{chartStatus || '登录并确认行情服务后可加载真实 K 线；当前不显示演示数据。'}</span></div>}</div>
            {settingsOpen && <div className="chart-settings" aria-label="图表设置"><label><input type="checkbox" checked={chartOptions.volume} onChange={(event) => setChartOptions({ ...chartOptions, volume: event.target.checked })} />成交量</label><label><input type="checkbox" checked={chartOptions.grid} onChange={(event) => setChartOptions({ ...chartOptions, grid: event.target.checked })} />网格</label><span className="chart-color-setting positive-text">上涨颜色 · 系统语义色（红）</span><span className="chart-color-setting negative-text">下跌颜色 · 系统语义色（绿）</span><label className="chart-color-setting">文字颜色<input type="color" value={chartOptions.textColor} onChange={(event) => setChartOptions({ ...chartOptions, textColor: event.target.value })} /></label><button className="button tertiary" type="button" onClick={() => setChartOptions({ volume: true, grid: true, upColor: '#F87171', downColor: '#2DD4BF', textColor: '#94A3B8' })}>恢复默认</button></div>}
            <footer className="research-panel-footer"><span><span className={`research-freshness-dot ${freshness.tone}`} />{freshness.label}</span><span className="research-source-badge">{sourceCode(marketQuote?.source)}</span><span>{quoteTimestamp ? new Date(quoteTimestamp).toLocaleString(formatLocale, { hour12: false }) : '等待来源时间戳'}</span></footer>
          </section>

          <section className="research-panel research-evidence-panel mobile-evidence-panel" id="mobile-evidence-panel">
            <header className="research-panel-header"><div><span>EVIDENCE REVIEW</span><strong>证据审议面板</strong></div><ResearchEnergy level={Math.min(5, officialDecision?.evidence.filter(Boolean).length ?? 0)} label="证据强度" /></header>
            <nav className="workspace-tabs">{evidenceTabs.map((tab) => <button className={activeTab === tab ? 'active' : ''} type="button" onClick={() => setParam('tab', tab)} key={tab}>{tab}</button>)}{activeTab === '期权证据' && <button className="button secondary options-tab-cta" type="button" onClick={() => navigate(optionUnlocked ? '/lab' : '/membership')}>{optionUnlocked ? '打开期权研究入口' : '升级解锁期权研究'}</button>}</nav>
            <EvidencePanel tab={activeTab} candles={chartData} decision={officialDecision} source={currentLiveCandles.length ? chartStatus : '暂无可验证行情'} optionUnlocked={optionUnlocked} />
          </section>
        </main>
      </div>

      <section className="research-bottom-deck" aria-label="研究后续工作">
        <article className="research-bottom-card"><header><span><Database size={16} />研究库</span><small className="research-status-pill is-planned">PLANNED</small></header><ResearchEmptyState compact title="尚无已保存研究" detail="保存宿主已预留；不会用演示记录填充研究库。" /><div className="research-card-actions"><button className="button tertiary" type="button" onClick={() => setParam('view', 'saved')}>查看研究库</button></div><footer><span className="research-freshness-dot missing" />当前股票 {selected.symbol}</footer></article>
        <article className={`research-bottom-card research-compare-card ${compareUnlocked ? '' : 'is-light-locked'}`}><header><span><Layers3 size={16} />股票比较</span><small className="research-status-pill">{compareUnlocked ? '可进入' : <><LockKeyhole size={12} />会员专属</>}</small></header><div className="research-compare-slots"><strong>{selected.symbol}</strong><span>VS</span><em>{compareUnlocked ? '选择第二只股票' : '升级后选择比较对象'}</em></div><div className="research-card-actions"><button className="button secondary" type="button" onClick={() => compareUnlocked ? navigate(`/ai?mode=compare&market=${selected.market}&symbol=${encodeURIComponent(selected.symbol)}`) : navigate('/membership')}>{compareUnlocked ? '开始比较' : '升级解锁'}</button></div><footer><span className={`research-freshness-dot ${compareUnlocked ? 'normal' : 'missing'}`} />结构化比较，不生成交易指令</footer></article>
        <article className="research-bottom-card research-handoff-card"><header><span><ArrowRight size={16} />行动交接</span><small className="research-status-pill is-live">RESEARCH ONLY</small></header><div className="research-handoff-grid"><button type="button" onClick={() => navigate(`/paper?market=${selected.market}&symbol=${encodeURIComponent(selected.symbol)}`)}><CircleDollarSign size={16} />进入模拟</button><button type="button" onClick={() => setInspectorOpen(true)}><Layers3 size={16} />盘口资料</button><button type="button" onClick={() => navigate(`/earnings?market=${selected.market}&symbol=${encodeURIComponent(selected.symbol)}`)}><CalendarDays size={16} />财报事件</button><button type="button" onClick={() => navigate('/lab')}><FlaskConical size={16} />实验室</button></div><footer><ShieldCheck size={13} />AI 不在研究页自动下单</footer></article>
      </section>
    </div>
  )
}

function EvidencePanel({ tab, candles: series, decision, source, optionUnlocked }: { tab: string; candles: Candle[]; decision: ReturnType<typeof recommendationToDecision>; source: string; optionUnlocked: boolean }) {
  const navigate = useNavigate()
  const latest = series.at(-1)
  const recent = series.slice(-14)
  const start = recent[0]?.close ?? 0
  const trend = latest && start ? latest.close > start ? '近期向上' : latest.close < start ? '近期向下' : '近期横盘' : '数据不足'
  const volume = latest ? latest.volume >= 1_000_000 ? `${(latest.volume / 1_000_000).toFixed(1)}M` : latest.volume.toLocaleString(getFormatLocale()) : '无记录'
  if (tab === '概览') {
    const evidence = decision?.evidence.filter(Boolean) ?? []
    const counterEvidence = decision?.counterEvidence.filter(Boolean) ?? []
    return <div className="evidence-review-layout">
      <div className="evidence-columns">
        <section className="evidence-card is-support"><header><span><ShieldCheck size={15} />支持证据</span><small>{evidence.length} 项</small></header>{evidence.length ? <ul>{evidence.map((item) => <li key={item}>{item}</li>)}</ul> : <ResearchEmptyState compact title="尚无支持证据" detail="等待正式量化事件写入可核对证据。" />}</section>
        <section className="evidence-card is-counter"><header><span><CircleAlert size={15} />反向证据</span><small>{counterEvidence.length} 项</small></header>{counterEvidence.length ? <ul>{counterEvidence.map((item) => <li key={item}>{item}</li>)}</ul> : <ResearchEmptyState compact title="尚无反向证据" detail="没有可验证反向证据时不作乐观补全。" />}</section>
      </div>
      <div className="evidence-summary-grid"><div><span>分歧</span><strong>{evidence.length && counterEvidence.length ? '双向证据并存' : '等待双向资料'}</strong></div><div><span>风险</span><strong>{decision?.actionBlockReason ?? decision?.maxLoss ?? '等待正式风险字段'}</strong></div><div><span>未知项</span><strong>{decision ? '流动性与即时盘口仍需核对' : '事件、模型与报价尚未形成合同'}</strong></div><div><span>数据来源</span><strong>{source}</strong></div></div>
    </div>
  }
  if (tab === '新闻与事件') return <ResearchEmptyState title="暂无可验证新闻与基本面事件" detail="新闻接口尚未返回带来源和时间戳的真实条目；系统不会用占位新闻填充。" action="打开财报日历" onAction={() => navigate('/earnings')} />
  if (tab === '期权证据') return <div className={`research-option-module ${optionUnlocked ? '' : 'is-heavy-locked'}`}>
    <div><FlaskConical size={22} /><span><strong>有限风险期权研究</strong><small>期权链、Greeks、IV 与多腿策略独立于正股建议。</small></span></div>
    <div className="research-option-grid"><span>期权链</span><span>Greeks</span><span>IV 曲面</span><span>有限风险结构</span></div>
    <button className="button primary" type="button" onClick={() => navigate(optionUnlocked ? '/lab' : '/membership')}>{optionUnlocked ? '打开期权研究' : '升级至专业版解锁'}</button>
    {!optionUnlocked && <div className="research-lock-overlay"><LockKeyhole size={24} /><strong>专业版会员专属</strong><small>升级后解锁期权链、Greeks 与有限风险策略研究。</small></div>}
  </div>
  const content = {
    技术指标: [['近期趋势', trend], ['样本数量', `${recent.length} 根`], ['最新收盘', latest ? latest.close.toFixed(2) : '无记录'], ['量能', volume]],
    信号时间线: [['正式事件', decision?.eventId ?? '无当前正式事件'], ['模型版本', decision?.modelVersion ?? '无正式版本'], ['记录时间', decision?.updatedAt ?? '无记录'], ['状态', decision ? '有效' : '等待']],
  }[tab] ?? []
  return <div className="market-facts research-fact-grid">{content.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
}
