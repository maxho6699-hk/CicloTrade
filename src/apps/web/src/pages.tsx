import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  BellRing,
  Bot,
  CircleAlert,
  ChevronRight,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { DecisionCard } from './components/DecisionCard'
import { MarketChart } from './components/MarketChart'
import { PageHeader } from './components/PageHeader'
import { WorkspaceState } from './components/WorkspaceState'
import { useWorkspace } from './api/workspace-context'
import { fetchMarketCandles, searchMarket, updateWatchlist } from './api/client'
import { recommendationToDecision } from './data/adapters'
import { candles, candidateDecisions, instruments, primaryDecision } from './data/demo'
import type { Candle, Instrument } from './types'
import { getFormatLocale, localizeText } from './i18n/runtime'
import { useLocale } from './i18n/useLocale'

export function TodayPage() {
  const navigate = useNavigate()
  const workspace = useWorkspace()
  const { formatLocale } = useLocale()
  const realDecisions = useMemo(() => workspace.data?.recommendations.items.map((item, index) => recommendationToDecision(item, index, formatLocale)).filter((item): item is NonNullable<typeof item> => item !== null) ?? [], [formatLocale, workspace.data])
  const demoMode = workspace.mode === 'demo' || workspace.mode === 'offline'
  const featured = realDecisions[0] ?? (demoMode ? primaryDecision : null)
  const queue = realDecisions.length > 1 ? realDecisions.slice(1, 4) : demoMode ? candidateDecisions : []
  const recordedExposure = workspace.data?.portfolio.positions.reduce((total, item) => total + Math.abs(item.market_value), 0) ?? (demoMode ? 10_500 : 0)
  const exposureLimit = workspace.data?.settings.risk.max_total_position ?? 50_000
  const riskScore = Math.min(100, Math.round(recordedExposure / Math.max(exposureLimit, 1) * 100))
  return (
    <div className="page today-page">
      <PageHeader kicker="TODAY / NEXT ACTION" title="今天只处理最重要的事" description="正式量化事件、持仓风险和研究候选按优先级排列。" />
      <WorkspaceState empty={workspace.mode === 'authenticated' && realDecisions.length === 0} emptyText="当前账户没有有效的正式量化事件。系统不会用演示建议填充登录账户。" />
      <section className="today-grid">
        {featured ? <DecisionCard decision={featured} demo={demoMode} /> : (
          <section className="today-empty-primary"><CircleAlert size={22} /><div><h2>等待下一条正式行动</h2><p>量化账本当前没有可执行记录。新的行动通过风控和发布审核后会出现在这里。</p></div></section>
        )}
        <aside className="side-rail">
          <section className="rail-section">
            <div className="section-heading"><span>账户风险</span><small>模拟账户</small></div>
            <strong className="risk-score">{riskScore}<small>/100</small></strong>
            <p>记录仓位约占总仓位上限 {riskScore}%。下单时仍会使用最新风控状态重新核验。</p>
            <div className="risk-bar"><i style={{ width: `${riskScore}%` }} /></div>
          </section>
          <section className="rail-section">
            <div className="section-heading"><span>推荐模型</span><small>受控迭代</small></div>
            <dl className="model-status"><div><dt>账本版本</dt><dd>{featured?.modelVersion ?? '暂无正式版本'}</dd></div><div><dt>挑战版本</dt><dd>仅影子运行</dd></div><div><dt>自动发布</dt><dd>禁止</dd></div><div><dt>发布状态</dt><dd className="warning-text">需独立审核</dd></div></dl>
          </section>
          <section className="rail-section tg-status-panel"><div><Bot size={19} /><span><strong>Telegram {workspace.data?.telegram.consented ? '已启用' : '未启用'}</strong><small>{workspace.data?.telegram.updated_at ? `状态更新 ${new Date(workspace.data.telegram.updated_at).toLocaleString(getFormatLocale(), { hour12: false })}` : '暂无真实投递时间'}</small></span></div><button className="icon-button" type="button" aria-label="打开 Telegram 通知" onClick={() => navigate('/notifications')}><ChevronRight size={17} /></button></section>
        </aside>
      </section>
      {queue.length > 0 && <section className="section-block"><header className="section-title"><div><span>RESEARCH QUEUE</span><h2>{realDecisions.length > 1 ? '更多正式量化记录' : '下一批研究候选'}</h2></div><button className="button tertiary" type="button" onClick={() => navigate('/markets?tab=信号时间线')}>查看全部 <ArrowRight size={15} /></button></header><div className="candidate-grid">{queue.map((decision) => <DecisionCard compact decision={decision} key={decision.eventId} />)}</div></section>}
    </div>
  )
}

const evidenceTabs = ['概览', '技术指标', '新闻与事件', '期权证据', '信号时间线']

export function MarketsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const workspace = useWorkspace()
  const { formatLocale } = useLocale()
  const [searchOpen, setSearchOpen] = useState(false)
  const [watchQuery, setWatchQuery] = useState('')
  const [remoteInstruments, setRemoteInstruments] = useState<Instrument[]>([])
  const [searchStatus, setSearchStatus] = useState('')
  const [watchlists, setWatchlists] = useState({ us: [] as string[], a_share: [] as string[] })
  const [watchBusy, setWatchBusy] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [chartOptions, setChartOptions] = useState({ volume: true, grid: true })
  const [liveCandles, setLiveCandles] = useState<Candle[]>([])
  const [chartStatus, setChartStatus] = useState('')
  const requestedSymbol = (searchParams.get('symbol') ?? 'AAPL').toUpperCase()
  const timeframe = searchParams.get('timeframe') ?? '日线'
  const activeTab = searchParams.get('tab') ?? '概览'
  const marketFilter = searchParams.get('market') === 'CN' ? 'CN' : 'US'
  const demoMode = workspace.mode === 'demo' || workspace.mode === 'offline'
  const marketDataEnabled = workspace.mode === 'authenticated' && Boolean(workspace.data?.market_data) && workspace.data?.market_data.freshness !== '已停用'
  const currentSavedSymbols = marketFilter === 'CN' ? watchlists.a_share : watchlists.us
  const catalog = useMemo(() => [...instruments, ...remoteInstruments.filter((item) => !instruments.some((existing) => existing.symbol === item.symbol))], [remoteInstruments])
  const savedInstruments = useMemo<Instrument[]>(() => currentSavedSymbols.map((symbol) => catalog.find((item) => item.symbol === symbol) ?? {
    symbol, name: symbol, market: marketFilter, price: 0, changePct: 0, currency: marketFilter === 'CN' ? 'CNY' : 'USD',
  }), [catalog, currentSavedSymbols, marketFilter])
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
    setSearchParams(next)
    setWatchQuery('')
    setRemoteInstruments([])
  }
  const visibleInstruments = demoMode
    ? instruments.filter((instrument) => instrument.market === marketFilter)
    : savedInstruments

  useEffect(() => {
    const stored = workspace.data?.settings.watchlists
    if (stored) setWatchlists({ us: stored.us ?? [], a_share: stored.a_share ?? [] })
  }, [workspace.data?.settings.watchlists])

  useEffect(() => {
    let active = true
    setLiveCandles([])
    if (!marketDataEnabled) { setChartStatus(''); return () => { active = false } }
    setChartStatus('正在读取受控行情…')
    void fetchMarketCandles(selectedBase.symbol, timeframe).then((payload) => {
      if (!active) return
      setLiveCandles(payload.items)
      setChartStatus(`${payload.status.display_source} · ${payload.status.freshness}`)
    }).catch((caught) => {
      if (!active) return
      setChartStatus(caught instanceof Error ? caught.message : '行情读取失败。')
    })
    return () => { active = false }
  }, [marketDataEnabled, selectedBase.symbol, timeframe])

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
    setSearchParams(next)
  }

  const changeWatchlist = async (instrument: Instrument, remove: boolean) => {
    setWatchBusy(instrument.symbol)
    setSearchStatus('')
    try {
      const payload = await updateWatchlist(instrument.market, instrument.symbol, remove)
      setWatchlists(payload.watchlists)
      setSearchStatus(remove ? `${instrument.symbol} 已从自选移除` : `${instrument.symbol} 已加入自选`)
      if (!remove) openInstrument(instrument)
    } catch (caught) {
      setSearchStatus(caught instanceof Error ? caught.message : '自选更新失败。')
    } finally {
      setWatchBusy('')
    }
  }

  const selected = useMemo(() => {
    if (liveCandles.length < 2) return selectedBase
    const latest = liveCandles.at(-1)!
    const previous = liveCandles.at(-2)!
    return { ...selectedBase, price: latest.close, changePct: previous.close ? (latest.close - previous.close) / previous.close * 100 : 0 }
  }, [liveCandles, selectedBase])
  const chartData = useMemo(() => {
    if (liveCandles.length) return liveCandles
    if (!demoMode) return []
    const scale = (selected.price || 100) / 213.45
    return candles.map((item) => ({ ...item, open: item.open * scale, high: item.high * scale, low: item.low * scale, close: item.close * scale }))
  }, [demoMode, liveCandles, selected.price])
  const officialDecision = useMemo(() => {
    const item = workspace.data?.recommendations.items.find((candidate) => candidate.symbol === selected.symbol)
    return item ? recommendationToDecision(item, 0, formatLocale) : null
  }, [formatLocale, selected.symbol, workspace.data])
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

  return (
    <div className="market-workspace">
      <aside className="watchlist-panel"><header><div><span>WATCHLIST</span><strong>我的自选</strong></div><button className="icon-button" type="button" aria-label={searchOpen ? '关闭股票搜索' : '搜索全市场股票'} onClick={() => setSearchOpen(!searchOpen)}>{searchOpen ? <X size={17} /> : <Search size={17} />}</button></header>{searchOpen && <div className="watch-search"><Search size={15} /><input autoFocus aria-label="搜索全市场股票" placeholder={marketFilter === 'US' ? '代码或名称，例如 PLTR' : '6 位代码或公司名称'} value={watchQuery} onChange={(event) => setWatchQuery(event.target.value)} />{searchStatus && <small>{searchStatus}</small>}{remoteInstruments.length > 0 && <div className="watch-search-results">{remoteInstruments.filter((item) => item.market === marketFilter).map((instrument) => { const saved = currentSavedSymbols.includes(instrument.symbol); return <div key={instrument.symbol}><button type="button" onClick={() => openInstrument(instrument)}><strong>{instrument.symbol}</strong><small>{instrument.name}</small></button><button className="icon-button" type="button" disabled={saved || watchBusy === instrument.symbol} aria-label={saved ? `${instrument.symbol} 已在自选` : `将 ${instrument.symbol} 加入自选`} title={saved ? '已在自选' : '加入自选'} onClick={() => void changeWatchlist(instrument, false)}>{saved ? <ShieldCheck size={15} /> : <Plus size={16} />}</button></div> })}</div>}</div>}<div className="market-tabs"><button className={marketFilter === 'US' ? 'active' : ''} type="button" onClick={() => setMarket('US')}>美股</button><button className={marketFilter === 'CN' ? 'active' : ''} type="button" onClick={() => setMarket('CN')}>A股</button></div><div className="watchlist-rows">{visibleInstruments.map((instrument) => <div className={instrument.symbol === selected.symbol ? 'watch-row active' : 'watch-row'} key={instrument.symbol}><button className="watch-row-main" type="button" onClick={() => openInstrument(instrument)}><span><strong>{instrument.symbol}</strong><small>{instrument.name}</small></span><span className={instrument.changePct >= 0 ? 'positive-text' : 'negative-text'}><strong>{instrument.price ? instrument.price.toFixed(2) : '待加载'}</strong><small>{instrument.price ? `${instrument.changePct >= 0 ? '+' : ''}${instrument.changePct.toFixed(2)}%` : marketDataEnabled ? '打开后加载' : '行情未连接'}</small></span></button>{!demoMode && <button className="watch-remove" type="button" disabled={watchBusy === instrument.symbol} aria-label={`从自选移除 ${instrument.symbol}`} title="从自选移除" onClick={() => void changeWatchlist(instrument, true)}><Trash2 size={15} /></button>}</div>)}{!visibleInstruments.length && <p className="watch-empty">自选为空，使用上方搜索按钮添加股票。</p>}</div></aside>

      <section className="chart-workspace">
        <header className="instrument-header"><div><span>{selected.name} · {selected.market === 'US' ? '美股' : 'A股'}</span><h1>{selected.symbol}</h1></div><div className="instrument-price"><strong>{selected.price ? selected.price.toFixed(2) : '—'}</strong>{selected.price > 0 && <span className={selected.changePct >= 0 ? 'positive-text' : 'negative-text'}>{selected.changePct >= 0 ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />}{Math.abs(selected.changePct).toFixed(2)}%</span>}</div><div className="instrument-actions"><button className="button secondary" type="button" disabled={!selected.price} onClick={() => navigate(`/notifications?symbol=${selected.symbol}&price=${selected.price.toFixed(2)}`)}><BellRing size={16} /> 预警</button><button className="button primary" type="button" onClick={() => navigate(`/trade?symbol=${selected.symbol}`)}>模拟交易</button></div></header>
        <div className="chart-toolbar"><div>{['1分', '5分', '15分', '1小时', '日线'].map((item) => <button className={timeframe === item ? 'active' : ''} type="button" onClick={() => setParam('timeframe', item)} key={item}>{item}</button>)}</div><div><button type="button" onClick={() => setParam('tab', '技术指标')}><Activity size={15} /> 指标</button><button className={settingsOpen ? 'active' : ''} type="button" aria-expanded={settingsOpen} onClick={() => setSettingsOpen(!settingsOpen)}><Settings2 size={15} /> 图表设置</button></div></div>
        {settingsOpen && <div className="chart-settings" aria-label="图表设置"><label><input type="checkbox" checked={chartOptions.volume} onChange={(event) => setChartOptions({ ...chartOptions, volume: event.target.checked })} />成交量</label><label><input type="checkbox" checked={chartOptions.grid} onChange={(event) => setChartOptions({ ...chartOptions, grid: event.target.checked })} />网格</label><button className="button tertiary" type="button" onClick={() => setChartOptions({ volume: true, grid: true })}>恢复默认</button></div>}
        <div className="chart-frame"><MarketChart candles={chartData} market={selected.market} symbol={selected.symbol} timeframe={timeframe} showGrid={chartOptions.grid} showVolume={chartOptions.volume} dataStatus={liveCandles.length ? chartStatus : demoMode ? '当前使用界面演示数据' : chartStatus || '暂无行情'} paperActivity={liveCandles.length ? workspace.data?.portfolio.activity : null} />{!liveCandles.length && !demoMode && <div className="chart-empty-state"><CircleAlert size={22} /><strong>{marketDataEnabled ? '暂时无法读取 K 线' : '行情连接未启用'}</strong><span>{chartStatus || '登录并确认行情服务后可加载真实 K 线。'}</span></div>}<span className={`chart-demo-label ${liveCandles.length ? 'real' : ''}`}>{liveCandles.length ? localizeText(chartStatus) : demoMode ? <>{localizeText('界面演示行情')} · {localizeText(timeframe)}</> : '无演示回退'}</span></div>
        <nav className="workspace-tabs">{evidenceTabs.map((tab) => <button className={activeTab === tab ? 'active' : ''} type="button" onClick={() => setParam('tab', tab)} key={tab}>{tab}</button>)}</nav>
        <EvidencePanel tab={activeTab} candles={chartData} decision={officialDecision} source={liveCandles.length ? chartStatus : demoMode ? '界面演示' : '暂无可验证行情'} />
      </section>

      <aside className="market-inspector"><div className="inspector-heading"><span>OFFICIAL ACTION</span><strong>{officialDecision ? '正式行动' : demoMode ? '界面演示行动' : '等待正式行动'}</strong></div><span className={`status-chip ${officialDecision ? 'official' : 'research'}`}><ShieldCheck size={14} /> {officialDecision ? '已写入量化日志' : '没有当前正式事件'}</span><h2>{inspectorDecision.title}</h2><p>{inspectorDecision.summary}</p><dl className="inspector-metrics"><div><dt>关注</dt><dd>{inspectorDecision.entry}</dd></div><div><dt>止损</dt><dd>{inspectorDecision.stop}</dd></div><div><dt>目标</dt><dd>{inspectorDecision.target}</dd></div><div><dt>最大风险</dt><dd>{inspectorDecision.maxLoss}</dd></div></dl><button className="button primary wide" type="button" onClick={() => navigate(`/trade?symbol=${selected.symbol}`)}>用模拟盘验证 <ArrowRight size={16} /></button><button className="button secondary wide" type="button" onClick={() => setParam('tab', '信号时间线')}>查看完整证据</button><footer><span>{inspectorDecision.modelVersion}</span><span>{inspectorDecision.eventId}</span></footer></aside>
    </div>
  )
}

function EvidencePanel({ tab, candles: series, decision, source }: { tab: string; candles: Candle[]; decision: ReturnType<typeof recommendationToDecision>; source: string }) {
  const latest = series.at(-1)
  const recent = series.slice(-14)
  const low = recent.length ? Math.min(...recent.map((item) => item.low)) : 0
  const high = recent.length ? Math.max(...recent.map((item) => item.high)) : 0
  const atr = recent.length ? recent.reduce((sum, item) => sum + item.high - item.low, 0) / recent.length : 0
  const start = recent[0]?.close ?? 0
  const trend = latest && start ? latest.close > start ? '近期向上' : latest.close < start ? '近期向下' : '近期横盘' : '数据不足'
  const volume = latest ? latest.volume >= 1_000_000 ? `${(latest.volume / 1_000_000).toFixed(1)}M` : latest.volume.toLocaleString(getFormatLocale()) : '无记录'
  const content = {
    概览: [['近14根区间', recent.length ? `${low.toFixed(2)}–${high.toFixed(2)}` : '无记录'], ['最新成交量', volume], ['平均真实波幅', atr ? atr.toFixed(2) : '无记录'], ['数据源', source]],
    技术指标: [['近期趋势', trend], ['样本数量', `${recent.length} 根`], ['最新收盘', latest ? latest.close.toFixed(2) : '无记录'], ['量能', volume]],
    新闻与事件: [['事件日历', '尚未接入可验证来源'], ['事件风险', '未评分'], ['市场情绪', '未评分'], ['数据状态', source]],
    期权证据: [['期权链', '尚未接入当前页面'], ['隐含波动率', '无记录'], ['Greeks', '无记录'], ['使用状态', '不进入当前行动']],
    信号时间线: [['正式事件', decision?.eventId ?? '无当前正式事件'], ['模型版本', decision?.modelVersion ?? '无正式版本'], ['记录时间', decision?.updatedAt ?? '无记录'], ['状态', decision ? '有效' : '等待']],
  }[tab] ?? []
  return <div className="market-facts">{content.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
}
