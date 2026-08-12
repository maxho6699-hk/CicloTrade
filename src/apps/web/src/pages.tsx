import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Bell,
  BellOff,
  BellRing,
  Bot,
  CircleAlert,
  ChevronRight,
  CircleDollarSign,
  Layers3,
  Newspaper,
  Pin,
  Eye,
  EyeOff,
  PinOff,
  SlidersHorizontal,
  Search,
  Settings2,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { DecisionCard } from './components/DecisionCard'
import { ChartWorkspace } from './components/ChartWorkspace'
import { MarketOverview } from './components/MarketOverview'
import { PageHeader } from './components/PageHeader'
import { WatchlistToggle } from './components/WatchlistToggle'
import { WorkspaceState } from './components/WorkspaceState'
import { useWorkspace } from './api/workspace-context'
import { createPriceAlert, deactivatePriceAlert, fetchMarketCandles, fetchMarketQuote, searchMarket, type MarketQuotePayload, type PriceAlert } from './api/client'
import { recommendationToDecision } from './data/adapters'
import { candles, candidateDecisions, instruments, primaryDecision } from './data/demo'
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

export function TodayPage() {
  const navigate = useNavigate()
  const workspace = useWorkspace()
  const { formatLocale } = useLocale()
  const realDecisions = useMemo(() => workspace.data?.recommendations.items.map((item, index) => recommendationToDecision(item, index, formatLocale)).filter((item): item is NonNullable<typeof item> => item !== null) ?? [], [formatLocale, workspace.data])
  const demoMode = workspace.mode === 'demo' || workspace.mode === 'offline'
  const featured = realDecisions.find((decision) => decision.actionable) ?? realDecisions[0] ?? (demoMode ? primaryDecision : null)
  const queue = realDecisions.length
    ? realDecisions.filter((decision) => decision.eventId !== featured?.eventId && !decision.actionable).slice(0, 3)
    : demoMode ? candidateDecisions : []
  const exposureByCurrency = useMemo(() => (workspace.data?.portfolio.positions ?? []).reduce((totals, item) => {
    const currency = /^\d{6}$/.test(item.symbol) ? 'CNY' : 'USD'
    totals[currency] += Math.abs(item.market_value)
    return totals
  }, { USD: demoMode ? 10_500 : 0, CNY: 0 }), [demoMode, workspace.data])
  const usdLimit = workspace.data?.settings.risk.max_total_position ?? 50_000
  const cnyLimit = workspace.data?.settings.risk.max_total_position_cny ?? 500_000
  const riskScore = Math.min(100, Math.round(Math.max(exposureByCurrency.USD / Math.max(usdLimit, 1), exposureByCurrency.CNY / Math.max(cnyLimit, 1)) * 100))
  const riskCurrency = exposureByCurrency.CNY / Math.max(cnyLimit, 1) > exposureByCurrency.USD / Math.max(usdLimit, 1) ? 'CNY' : 'USD'
  return (
    <div className="page today-page">
      <PageHeader kicker="TODAY / NEXT ACTION" title="今日行动机会" description="正式量化事件、持仓风险和等待机会入场的研究候选按优先级排列。" />
      <WorkspaceState empty={workspace.mode === 'authenticated' && realDecisions.length === 0} emptyText="当前账户没有有效的正式量化事件。系统不会用演示建议填充登录账户。" />
      <section className="today-grid">
        {featured ? <DecisionCard decision={featured} demo={demoMode} /> : (
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
      <section className="section-block"><header className="section-title"><div><span>WAITING FOR ENTRY</span><h2>等待机会入场</h2></div><button className="button tertiary" type="button" onClick={() => navigate('/opportunities')}>查看全部机会 <ArrowRight size={15} /></button></header>{queue.length > 0 ? <div className="candidate-grid">{queue.map((decision) => <DecisionCard compact decision={decision} demo={demoMode} key={decision.eventId} />)}</div> : <div className="opportunity-inline-empty">当前没有等待入场的正式记录。打开机会中心查看其他资产类型与数据状态。</div>}</section>
    </div>
  )
}

const evidenceTabs = ['概览', '技术指标', '新闻与事件', '期权证据', '信号时间线']
const inspectorTabs = ['建议', '新闻', '盘口', '预警', '资料']

export function MarketsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedInspectorTab = searchParams.get('panel')
  const validRequestedInspectorTab = requestedInspectorTab && inspectorTabs.includes(requestedInspectorTab)
    ? requestedInspectorTab
    : null
  const navigate = useNavigate()
  const workspace = useWorkspace()
  const { updateMarketDataStatus } = workspace
  const { formatLocale } = useLocale()
  const [searchOpen, setSearchOpen] = useState(false)
  const [watchQuery, setWatchQuery] = useState('')
  const [remoteInstruments, setRemoteInstruments] = useState<Instrument[]>([])
  const [searchStatus, setSearchStatus] = useState('')
  const [watchBusy, setWatchBusy] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [inspectorOpen, setInspectorOpen] = useState(() => Boolean(validRequestedInspectorTab) || window.matchMedia('(min-width: 1071px)').matches)
  const previousRequestedInspectorTab = useRef(validRequestedInspectorTab)
  const [chartOptions, setChartOptions] = useState({ volume: true, grid: true, upColor: '#27b487', downColor: '#e4606b', textColor: '#98a2ae' })
  const [liveCandles, setLiveCandles] = useState<Candle[]>([])
  const [liveCandleIdentity, setLiveCandleIdentity] = useState('')
  const [chartStatus, setChartStatus] = useState('')
  const [marketQuote, setMarketQuote] = useState<MarketQuotePayload | null>(null)
  const [quoteStatus, setQuoteStatus] = useState('')
  const candleRequestSequence = useRef(0)
  const quoteRequestSequence = useRef(0)
  const [alertPrice, setAlertPrice] = useState(0)
  const [alertStatus, setAlertStatus] = useState('')
  const [alertBusy, setAlertBusy] = useState(false)
  const [localAlerts, setLocalAlerts] = useState<PriceAlert[] | null>(null)
  const [hiddenAlertIds, setHiddenAlertIds] = useState<number[]>([])
  const requestedSymbol = (searchParams.get('symbol') ?? 'AAPL').toUpperCase()
  const timeframe = searchParams.get('timeframe') ?? '日线'
  const activeTab = searchParams.get('tab') ?? '概览'
  const inspectorTab = validRequestedInspectorTab ?? '建议'
  const marketFilter = searchParams.get('market') === 'CN' ? 'CN' : 'US'
  const demoMode = workspace.mode === 'demo' || workspace.mode === 'offline'
  const marketDataEnabled = workspace.mode === 'authenticated' && Boolean(workspace.data?.market_data) && workspace.data?.market_data.freshness !== '已停用'
  const watchlists = workspace.data?.settings.watchlists ?? { us: [], a_share: [] }
  const watchlistPins = workspace.data?.settings.watchlist_pins ?? { us: [], a_share: [] }
  const currentSavedSymbols = marketFilter === 'CN' ? watchlists.a_share : watchlists.us
  const currentPinnedSymbols = marketFilter === 'CN' ? watchlistPins.a_share : watchlistPins.us
  const catalog = useMemo(() => [...instruments, ...remoteInstruments.filter((item) => !instruments.some((existing) => existing.symbol === item.symbol))], [remoteInstruments])
  const savedInstruments = useMemo<Instrument[]>(() => currentSavedSymbols.map((symbol) => {
    const catalogItem = catalog.find((item) => item.symbol === symbol)
    return {
      symbol,
      name: catalogItem?.name ?? symbol,
      market: catalogItem?.market ?? marketFilter,
      price: demoMode ? catalogItem?.price ?? 0 : 0,
      changePct: demoMode ? catalogItem?.changePct ?? 0 : 0,
      currency: catalogItem?.currency ?? (marketFilter === 'CN' ? 'CNY' : 'USD'),
    }
  }), [catalog, currentSavedSymbols, demoMode, marketFilter])
  const allInstruments = useMemo(() => demoMode ? catalog : [...savedInstruments, ...remoteInstruments], [catalog, demoMode, remoteInstruments, savedInstruments])
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
    next.set('symbol', saved[0] ?? instruments.find((item) => item.market === market)?.symbol ?? 'AAPL')
    next.delete('event_id')
    setSearchParams(next)
    setWatchQuery('')
    setRemoteInstruments([])
  }
  const visibleInstruments = demoMode
    ? instruments.filter((instrument) => instrument.market === marketFilter)
    : savedInstruments.map((instrument) => instrument.symbol === selectedBase.symbol
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
    if (!marketDataEnabled) { setChartStatus(''); return () => { active = false } }
    setChartStatus('正在读取受控行情…')
    const stopPolling = createVisibilityPolling(async () => {
      const sequence = ++candleRequestSequence.current
      try {
        const payload = await fetchMarketCandles(selectedBase.symbol, timeframe)
        if (!active || candleRequestSequence.current !== sequence) return
        setLiveCandleIdentity(`${selectedBase.symbol}:${timeframe}`)
        setLiveCandles(payload.items)
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
    if (!marketDataEnabled) {
      setQuoteStatus('')
      return () => { active = false }
    }
    setQuoteStatus('正在核对报价权限与来源…')
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
        const delivery = displayDeliveryDelay(payload.delivery_delay_minutes)
        const access = deliveryAllowsImmediateAction(payload) ? '可核对即时行动' : '仅供研究，不用于立即交易'
        setQuoteStatus(`${displayDataSource(payload.source)} · ${delivery || displayFreshness(payload.freshness)} · ${access}`)
      } catch {
        if (active && quoteRequestSequence.current === sequence) {
          setMarketQuote(null)
          setQuoteStatus(safeDataError())
        }
      }
    }, 5_000)
    return () => { active = false; stopPolling() }
  }, [marketDataEnabled, selectedBase.symbol, updateMarketDataStatus])

  useEffect(() => {
    let active = true
    if (!marketDataEnabled || !searchOpen || watchQuery.trim().length < 2) { setSearchStatus(''); return () => { active = false } }
    const timer = window.setTimeout(() => {
      void searchMarket(watchQuery, marketFilter === 'CN' ? 'A股' : '美股').then((payload) => {
        if (!active) return
        setRemoteInstruments(payload.items.map((item) => ({ symbol: item.symbol.replace(/\.(SS|SZ)$/, ''), name: item.name, market: item.market, price: 0, changePct: 0, currency: item.market === 'CN' ? 'CNY' : 'USD' })))
        setSearchStatus(payload.items.length ? `找到 ${payload.items.length} 个标的` : '没有在线匹配结果')
      }).catch((caught) => { if (active) setSearchStatus(caught instanceof Error ? caught.message : '在线搜索暂时不可用') })
    }, 350)
    return () => { active = false; window.clearTimeout(timer) }
  }, [marketDataEnabled, marketFilter, searchOpen, watchQuery])

  const openInstrument = (instrument: Instrument) => {
    const next = new URLSearchParams(searchParams)
    next.set('market', instrument.market)
    next.set('symbol', instrument.symbol)
    next.delete('event_id')
    setSearchParams(next)
  }

  const changeWatchlist = async (instrument: Instrument, remove: boolean) => {
    setWatchBusy(instrument.symbol)
    setSearchStatus('')
    try {
      await workspace.changeWatchlist(instrument.market, instrument.symbol, remove)
      setSearchStatus(remove ? `${instrument.symbol} 已从自选移除` : `${instrument.symbol} 已加入自选`)
    } catch (caught) {
      setSearchStatus(caught instanceof Error ? caught.message : '自选更新失败。')
    } finally {
      setWatchBusy('')
    }
  }

  const changeChartWatchlist = async (market: Instrument['market'], symbol: string, remove: boolean) => {
    const instrument = instruments.find((item) => item.market === market && item.symbol === symbol) ?? {
      symbol, name: symbol, market, price: 0, changePct: 0, currency: market === 'CN' ? 'CNY' as const : 'USD' as const,
    }
    await changeWatchlist(instrument, remove)
  }

  const changeWatchlistPin = async (instrument: Instrument, pinned: boolean) => {
    setWatchBusy(instrument.symbol)
    setSearchStatus('')
    try {
      await workspace.changeWatchlistPin(instrument.market, instrument.symbol, pinned)
      setSearchStatus(pinned ? `${instrument.symbol} 已置顶` : `${instrument.symbol} 已取消置顶`)
    } catch (caught) {
      setSearchStatus(caught instanceof Error ? caught.message : '自选置顶更新失败。')
    } finally {
      setWatchBusy('')
    }
  }

  const currentCandleIdentity = `${selectedBase.symbol}:${timeframe}`
  const currentLiveCandles = useMemo(
    () => liveCandleIdentity === currentCandleIdentity ? liveCandles : [],
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
    if (!demoMode) return { ...selectedBase, price: 0, changePct: 0 }
    if (currentLiveCandles.length < 2) return selectedBase
    const latest = currentLiveCandles.at(-1)!
    const previous = currentLiveCandles.at(-2)!
    return { ...selectedBase, price: latest.close, changePct: previous.close ? (latest.close - previous.close) / previous.close * 100 : 0 }
  }, [currentLiveCandles, demoMode, marketQuote, selectedBase])
  const chartData = useMemo(() => {
    if (currentLiveCandles.length) return currentLiveCandles
    if (!demoMode) return []
    const scale = (selected.price || 100) / 213.45
    return candles.map((item) => ({ ...item, open: item.open * scale, high: item.high * scale, low: item.low * scale, close: item.close * scale }))
  }, [currentLiveCandles, demoMode, selected.price])
  const officialDecision = useMemo(() => {
    const eventId = Number(searchParams.get('event_id'))
    const item = workspace.data?.recommendations.items.find((candidate) => Number.isFinite(eventId) && eventId > 0
      ? candidate.event_id === eventId
      : candidate.symbol === selected.symbol && (candidate.market === selected.market || (selected.market === 'CN' && candidate.market === 'A股')))
    if (!item) return null
    const currentQuote = marketQuote?.symbol === selected.symbol ? marketQuote : null
    const quoteOverride = currentQuote && deliveryAllowsImmediateAction(currentQuote)
      && typeof currentQuote.last === 'number' && currentQuote.quote_at
      ? { price: currentQuote.last, quoteAt: currentQuote.quote_at }
      : undefined
    return recommendationToDecision(item, 0, formatLocale, quoteOverride)
  }, [formatLocale, marketQuote, searchParams, selected.market, selected.symbol, workspace.data])
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

  const setInspectorTab = (tab: string) => {
    const next = new URLSearchParams(searchParams)
    next.set('panel', tab)
    setSearchParams(next)
    setInspectorOpen(true)
  }

  useEffect(() => {
    const desktopInspector = window.matchMedia('(min-width: 1071px)')
    const closeOverlayInspector = (event: MediaQueryListEvent) => {
      if (!event.matches) setInspectorOpen(false)
    }
    desktopInspector.addEventListener('change', closeOverlayInspector)
    return () => desktopInspector.removeEventListener('change', closeOverlayInspector)
  }, [])

  useEffect(() => {
    if (validRequestedInspectorTab && validRequestedInspectorTab !== previousRequestedInspectorTab.current) {
      setInspectorOpen(true)
    }
    previousRequestedInspectorTab.current = validRequestedInspectorTab
  }, [validRequestedInspectorTab])

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
  const inspectorDecision = officialDecision ?? {
    ...primaryDecision,
    state: 'wait' as const,
    action: 'wait' as const,
    instrument: selected,
    title: '暂无当前正式行动',
    summary: demoMode ? '该标的没有有效的正式量化事件。演示K线只用于界面预览，不生成购买建议。' : '该标的当前没有有效的正式量化事件。系统不会根据界面行情临时拼出购买建议。',
    entry: '未生成',
    stop: '未生成',
    target: '未生成',
    maxLoss: '未生成',
    modelVersion: '无正式版本',
    eventId: 'NO-ACTIVE-EVENT',
  }
  const auxiliaryInspector = (
    <div className="canonical-auxiliary-inspector">
      <nav className="inspector-tabs" aria-label="行情辅助面板">
        {inspectorTabs.map((tab) => <button className={inspectorTab === tab ? 'active' : ''} type="button" onClick={() => setInspectorTab(tab)} key={tab}>{tab}</button>)}
      </nav>
      {inspectorTab === '建议' && <div className="inspector-panel">
        <div className="inspector-heading"><span>OFFICIAL ACTION</span><strong>{officialDecision ? '正式行动合同' : demoMode ? '界面演示' : '等待正式行动'}</strong></div>
        <span className={`status-chip ${officialDecision?.actionable ? 'official' : 'research'}`}><ShieldCheck size={14} /> {officialDecision?.actionable ? '当前条件可核对' : officialDecision ? '当前不可交易' : '没有当前正式事件'}</span>
        <h2>{officialDecision ? officialDecision.currentInstruction : demoMode ? '界面演示，不可交易' : '现在不买、不卖；数量 0'}</h2>
        <p>{inspectorDecision.summary}</p>
        <dl className="inspector-metrics">
          <div><dt>当前报价</dt><dd>{officialDecision?.currentPrice ?? (demoMode ? '演示数据' : '未核对')}</dd></div>
          <div><dt>数量</dt><dd>{officialDecision?.quantityHint ?? '0 股（现在不买、不卖）'}</dd></div>
          <div><dt>报价时间</dt><dd>{officialDecision?.quoteUpdatedAt ?? '没有可验证的当前报价时间'}</dd></div>
          <div><dt>事件时间</dt><dd>{officialDecision?.updatedAt ?? '没有正式事件'}</dd></div>
          <div><dt>事件参考 / 触发</dt><dd>{inspectorDecision.entry}</dd></div>
          <div><dt>止损</dt><dd>{inspectorDecision.stop}</dd></div>
          <div><dt>目标</dt><dd>{inspectorDecision.target}</dd></div>
          <div><dt>最大风险</dt><dd>{inspectorDecision.maxLoss}</dd></div>
        </dl>
        <button className="button primary wide" type="button" onClick={() => navigate(`/portfolio?market=${selected.market}&symbol=${encodeURIComponent(selected.symbol)}&event_id=${officialDecision?.officialEventId ?? ''}`)}><CircleDollarSign size={16} /> 查看官方模拟账户</button>
        <button className="button secondary wide" type="button" onClick={() => setParam('tab', '信号时间线')}>查看完整证据</button>
        <footer><span>{inspectorDecision.modelVersion}</span><span>{inspectorDecision.eventId}</span></footer>
      </div>}
      {inspectorTab === '新闻' && <div className="inspector-panel inspector-placeholder"><Newspaper size={22} /><h2>市场资讯</h2><p>这里显示已验证来源的公司新闻、财报和市场事件。没有来源或时间戳的内容不会伪装成新闻。</p><span>{currentLiveCandles.length ? '当前标的的新闻接口尚未接入' : '暂无可验证行情，暂不显示资讯'}</span></div>}
      {inspectorTab === '盘口' && <div className="inspector-panel inspector-placeholder"><Layers3 size={22} /><h2>价格深度</h2><p>这里预留买卖盘、买一到买五、卖一到卖五和逐笔成交。只有接入 Level 2 数据后才会显示，不用重复成交量占位。</p><span>当前数据源未提供可验证盘口</span></div>}
      {inspectorTab === '预警' && <div className="inspector-panel alert-panel"><div className="inspector-heading"><span>PRICE ALERT</span><strong>图内预警</strong></div><p>设定后会在 K 线上显示水平线；隐藏只影响这次查看，关闭会停用预警。</p><p className="alert-safety-note"><ShieldCheck size={15} /> 只提醒，不会自动买卖。</p><div className="inline-alert-form"><label><span>提醒价格</span><input aria-label="提醒价格" inputMode="decimal" min="0.01" step="0.01" type="number" value={alertPrice || ''} onChange={(event) => setAlertPrice(Number(event.target.value))} /></label><button className="button primary" type="button" disabled={alertBusy || !alertPrice} onClick={() => void saveAlertInChart()}><BellRing size={16} /> 保存</button></div><p className="form-status" role="status" aria-live="polite">{alertStatus}</p><div className="alert-list">{alerts.filter((item) => isAlertForInstrument(item, selected.market, selected.symbol)).map((item) => { const isActive = item.is_active !== false; const markerVisible = item.id === undefined || !hiddenAlertIds.includes(item.id); return <div key={item.id ?? `${item.symbol}-${item.target_price}`}><span><strong>{item.target_price ?? '条件预警'}</strong><small>{isActive ? <><Bell size={13} aria-hidden="true" /> 已开启</> : <><BellOff size={13} aria-hidden="true" /> 已关闭</>}</small></span>{item.id && isActive && <span className="alert-list-actions"><button className="icon-button" type="button" aria-label={`${markerVisible ? '隐藏' : '显示'} ${item.symbol} ${item.target_price ?? ''} 预警线`} title={markerVisible ? '隐藏图上预警线' : '显示图上预警线'} onClick={() => toggleAlertMarker(item.id!)}>{markerVisible ? <Eye size={16} /> : <EyeOff size={16} />}</button><button className="icon-button danger" type="button" aria-label={`关闭 ${item.symbol} ${item.target_price ?? ''} 预警`} title="关闭预警并移除价格线" disabled={alertBusy} onClick={() => void disableAlertInChart(item.id!)}><Trash2 size={16} /></button></span>}</div> })}</div></div>}
      {inspectorTab === '资料' && <div className="inspector-panel inspector-placeholder"><SlidersHorizontal size={22} /><h2>数据说明</h2><p>K 线数据：{currentLiveCandles.length ? chartStatus : demoMode ? '界面演示数据' : '尚未取得'}</p><p>当前报价：{quoteStatus || '尚未核对报价权限与来源'}</p><p>账户状态：仅在用户主动授权券商后显示实盘连接；行情连接不等于账户连接。</p><span>延迟或未验证实时权限的数据只用于研究，不会覆盖正式行动的即时交易字段。</span></div>}
    </div>
  )

  if (!searchParams.has('symbol')) {
    return <MarketOverview
      market={marketFilter}
      watchlist={currentSavedSymbols}
      authenticated={workspace.mode === 'authenticated'}
      marketDataEnabled={marketDataEnabled}
      demoMode={demoMode}
      busySymbol={watchBusy}
      onMarketChange={(market) => {
        const next = new URLSearchParams(searchParams)
        next.set('market', market)
        next.delete('symbol')
        setSearchParams(next)
      }}
      onOpen={openInstrument}
      onWatchlist={(instrument, remove) => changeWatchlist(instrument, remove)}
    />
  }

  return (
    <div className="market-workspace">
      <aside className="watchlist-panel">
        <header><div><span>WATCHLIST</span><strong>我的自选</strong></div><button className="icon-button" type="button" aria-label={searchOpen ? '关闭股票搜索' : '搜索全市场股票'} onClick={() => setSearchOpen(!searchOpen)}>{searchOpen ? <X size={17} /> : <Search size={17} />}</button></header>
        {searchOpen && <div className="watch-search"><Search size={15} /><input autoFocus aria-label="搜索全市场股票" autoComplete="off" name="watchlist-search" placeholder={marketFilter === 'US' ? '代码或名称，例如 PLTR…' : '6 位代码或公司名称…'} value={watchQuery} onChange={(event) => setWatchQuery(event.target.value)} />{searchStatus && <small role="status" aria-live="polite">{searchStatus}</small>}{remoteInstruments.length > 0 && <div className="watch-search-results">{remoteInstruments.filter((item) => item.market === marketFilter).map((instrument) => { const saved = currentSavedSymbols.includes(instrument.symbol); return <div key={instrument.symbol}><button type="button" onClick={() => openInstrument(instrument)}><strong>{instrument.symbol}</strong><small>{instrument.name}</small></button><WatchlistToggle symbol={instrument.symbol} saved={saved} busy={watchBusy === instrument.symbol} onToggle={(remove) => changeWatchlist(instrument, remove)} /></div> })}</div>}</div>}
        <div className="market-tabs"><button className={marketFilter === 'US' ? 'active' : ''} type="button" onClick={() => setMarket('US')}>美股</button><button className={marketFilter === 'CN' ? 'active' : ''} type="button" onClick={() => setMarket('CN')}>A股</button></div>
        <div className="watchlist-rows">{visibleInstruments.map((instrument) => { const pinned = currentPinnedSymbols.includes(instrument.symbol); return <div className={instrument.symbol === selected.symbol ? 'watch-row active' : 'watch-row'} key={instrument.symbol}><button className="watch-row-main" type="button" onClick={() => openInstrument(instrument)}><span><strong>{instrument.symbol}</strong><small>{instrument.name}</small></span><span className={instrument.changePct >= 0 ? 'positive-text' : 'negative-text'}><strong>{instrument.price ? instrument.price.toFixed(2) : '待加载'}</strong><small>{instrument.price ? `${instrument.changePct >= 0 ? '+' : ''}${instrument.changePct.toFixed(2)}%` : marketDataEnabled ? '打开后加载' : '行情未连接'}</small></span></button>{!demoMode && <><button className={`watch-pin ${pinned ? 'active' : ''}`} type="button" disabled={watchBusy === instrument.symbol} aria-label={pinned ? `取消置顶 ${instrument.symbol}` : `置顶 ${instrument.symbol}`} title={pinned ? '取消置顶' : '置顶'} onClick={() => void changeWatchlistPin(instrument, !pinned)}>{pinned ? <PinOff size={14} /> : <Pin size={14} />}</button><WatchlistToggle className="watch-row-toggle" symbol={instrument.symbol} saved onToggle={(remove) => changeWatchlist(instrument, remove)} busy={watchBusy === instrument.symbol} /></>}</div> })}{!visibleInstruments.length && <p className="watch-empty">自选为空，使用上方搜索股票或切换到热门关注。</p>}</div>
      </aside>

      <section className="chart-workspace">
        <header className="instrument-header"><div><span>{selected.name} · {selected.market === 'US' ? '美股' : 'A股'}</span><h1>{selected.symbol}</h1></div><div className="instrument-price"><strong>{selected.price ? selected.price.toFixed(2) : '—'}</strong>{selected.price > 0 && <span className={selected.changePct >= 0 ? 'positive-text' : 'negative-text'}>{selected.changePct >= 0 ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />}{Math.abs(selected.changePct).toFixed(2)}%</span>}</div><div className="instrument-actions"><button className="button primary" type="button" onClick={() => navigate('/portfolio')}><CircleDollarSign size={16} /> 官方模拟账户</button></div></header>
        <div className="chart-frame"><ChartWorkspace userId={workspace.data?.me.id} initialSymbol={selected.symbol} initialMarket={selected.market} initialTimeframe={timeframe} inspectorOpen={inspectorOpen} onInspectorOpenChange={setInspectorOpen} inspectorExtra={auxiliaryInspector} toolbarActions={<><button type="button" aria-label="打开价格预警" title="价格预警" disabled={!selected.price} onClick={() => setInspectorTab('预警')}><BellRing size={15} /><span>预警</span></button><button type="button" aria-label="查看技术指标" title="技术指标" onClick={() => setParam('tab', '技术指标')}><Activity size={15} /><span>指标</span></button><button className={settingsOpen ? 'active' : ''} type="button" aria-label="图表设置" title="图表设置" aria-expanded={settingsOpen} onClick={() => setSettingsOpen(!settingsOpen)}><Settings2 size={15} /><span>设置</span></button></>} candles={chartData} showGrid={chartOptions.grid} showVolume={chartOptions.volume} upColor={chartOptions.upColor} downColor={chartOptions.downColor} textColor={chartOptions.textColor} dataStatus={currentLiveCandles.length ? chartStatus : demoMode ? '当前使用界面演示数据' : chartStatus || '暂无行情'} initialQuote={marketQuote?.symbol === selected.symbol ? marketQuote : null} loadQuote={marketDataEnabled ? loadWorkspaceQuote : undefined} officialActivity={workspace.data?.portfolio.activity ?? null} alertPrices={alertPricesForInstrument} isWatchlisted={(market, symbol) => (market === 'CN' ? watchlists.a_share : watchlists.us).includes(symbol)} onWatchlistToggle={changeChartWatchlist} watchBusy={watchBusy} loadCandles={marketDataEnabled ? loadWorkspaceCandles : undefined} onSymbolChange={(symbol, market) => { const next = new URLSearchParams(searchParams); next.set('market', market); next.set('symbol', symbol); next.delete('event_id'); setSearchParams(next) }} onTimeframeChange={(nextTimeframe) => setParam('timeframe', nextTimeframe)} />{!currentLiveCandles.length && !demoMode && <div className="chart-empty-state"><CircleAlert size={22} /><strong>{marketDataEnabled ? '暂时无法读取 K 线' : '行情连接未启用'}</strong><span>{chartStatus || '登录并确认行情服务后可加载真实 K 线。'}</span></div>}</div>
        {settingsOpen && <div className="chart-settings" aria-label="图表设置"><label><input type="checkbox" checked={chartOptions.volume} onChange={(event) => setChartOptions({ ...chartOptions, volume: event.target.checked })} />成交量</label><label><input type="checkbox" checked={chartOptions.grid} onChange={(event) => setChartOptions({ ...chartOptions, grid: event.target.checked })} />网格</label><label className="chart-color-setting">上涨颜色<input type="color" value={chartOptions.upColor} onChange={(event) => setChartOptions({ ...chartOptions, upColor: event.target.value })} /></label><label className="chart-color-setting">下跌颜色<input type="color" value={chartOptions.downColor} onChange={(event) => setChartOptions({ ...chartOptions, downColor: event.target.value })} /></label><label className="chart-color-setting">文字颜色<input type="color" value={chartOptions.textColor} onChange={(event) => setChartOptions({ ...chartOptions, textColor: event.target.value })} /></label><button className="button tertiary" type="button" onClick={() => setChartOptions({ volume: true, grid: true, upColor: '#27b487', downColor: '#e4606b', textColor: '#98a2ae' })}>恢复默认</button></div>}
        <nav className="workspace-tabs">{evidenceTabs.map((tab) => <button className={activeTab === tab ? 'active' : ''} type="button" onClick={() => setParam('tab', tab)} key={tab}>{tab}</button>)}{activeTab === '期权证据' && <button className="button secondary options-tab-cta" type="button" onClick={() => navigate('/lab')}>打开期权研究入口</button>}</nav>
        <EvidencePanel tab={activeTab} candles={chartData} decision={officialDecision} source={currentLiveCandles.length ? chartStatus : demoMode ? '界面演示' : '暂无可验证行情'} />
      </section>

    </div>
  )
}

function EvidencePanel({ tab, candles: series, decision, source }: { tab: string; candles: Candle[]; decision: ReturnType<typeof recommendationToDecision>; source: string }) {
  const latest = series.at(-1)
  const recent = series.slice(-14)
  const low = recent.length ? Math.min(...recent.map((item) => item.low)) : 0
  const high = recent.length ? Math.max(...recent.map((item) => item.high)) : 0
  const start = recent[0]?.close ?? 0
  const trend = latest && start ? latest.close > start ? '近期向上' : latest.close < start ? '近期向下' : '近期横盘' : '数据不足'
  const volume = latest ? latest.volume >= 1_000_000 ? `${(latest.volume / 1_000_000).toFixed(1)}M` : latest.volume.toLocaleString(getFormatLocale()) : '无记录'
  const supportDistance = latest && low ? (latest.close - low) / latest.close * 100 : 0
  const resistanceDistance = latest && high ? (high - latest.close) / latest.close * 100 : 0
  const content = {
    概览: [['可能支撑位', recent.length ? `${low.toFixed(2)} · 下方 ${supportDistance.toFixed(1)}%` : '无记录'], ['可能压力位', recent.length ? `${high.toFixed(2)} · 上方 ${resistanceDistance.toFixed(1)}%` : '无记录'], ['白话说明', '支撑是近期较常止跌的位置；压力是近期较常遇阻的位置，不保证一定有效'], ['数据源', source]],
    技术指标: [['近期趋势', trend], ['样本数量', `${recent.length} 根`], ['最新收盘', latest ? latest.close.toFixed(2) : '无记录'], ['量能', volume]],
    新闻与事件: [['事件日历', '尚未接入可验证来源'], ['事件风险', '未评分'], ['市场情绪', '未评分'], ['数据状态', source]],
    期权证据: [['资产类别', '期权与正股分开研究'], ['主要风险', '会到期、可能归零，也可能难以成交'], ['期权链与 Greeks', '在专业研究工作台按会员权限开放'], ['当前行动', '不与正股建议混合']],
    信号时间线: [['正式事件', decision?.eventId ?? '无当前正式事件'], ['模型版本', decision?.modelVersion ?? '无正式版本'], ['记录时间', decision?.updatedAt ?? '无记录'], ['状态', decision ? '有效' : '等待']],
  }[tab] ?? []
  return <div className="market-facts">{content.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
}
