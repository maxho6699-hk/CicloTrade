import {
  BarChart3,
  CalendarClock,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  FileLock2,
  History,
  LoaderCircle,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  Target,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { earningsApi, EarningsApiError, loadEarningsInitialState } from '../api/earnings.ts'
import { EarningsForecastTimeline } from '../components/EarningsForecastTimeline.tsx'
import { EarningsOptionStructure } from '../components/EarningsOptionStructure.tsx'
import { EarningsProbabilityDial } from '../components/EarningsProbabilityDial.tsx'
import type {
  EarningsDetail,
  EarningsForecastItem,
  EarningsForecastSnapshot,
  EarningsHistory,
  EarningsLockedOverview,
  EarningsOptionDetail,
  EarningsOptionReferenceItem,
  EarningsOverview,
  EarningsResearchDetail,
  EarningsStatistics,
  EarningsTab,
  SimulatedEarningsAction,
} from '../domain/earningsForecast.ts'

const TABS: Array<{ id: EarningsTab; label: string; icon: typeof CalendarClock }> = [
  { id: 'future', label: '未来 7 天', icon: CalendarClock },
  { id: 'history', label: '历史与复盘', icon: History },
  { id: 'statistics', label: '长期统计', icon: BarChart3 },
]

function timingLabel(value: EarningsForecastItem['timing']) {
  return ({ BMO: '盘前', AMC: '盘后', DURING: '盘中', UNKNOWN: '时间待确认' })[value]
}

function actionLabel(value: SimulatedEarningsAction) {
  return ({
    OBSERVE: '继续观察', PAPER_OPEN: '纸上开仓', PAPER_ADD: '纸上加仓', PAPER_REDUCE: '纸上减仓', PAPER_CLOSE: '纸上平仓',
    RESEARCH_LONG_CALL: '研究买入看涨', RESEARCH_LONG_PUT: '研究买入看跌', RESEARCH_LONG_STRADDLE: '研究买入跨式', RESEARCH_LONG_STRANGLE: '研究买入宽跨式',
  })[value]
}

function dateTime(value: string, withTime = true) {
  return new Intl.DateTimeFormat('zh-CN', withTime
    ? { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }
    : { year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value))
}

function percent(value: number, probability = false) {
  const normalized = probability ? value * 100 : value
  return `${normalized >= 0 ? '+' : ''}${normalized.toFixed(1)}%`
}

function money(value: number, currency: 'USD' | 'CNY') {
  return new Intl.NumberFormat('zh-CN', { style: 'currency', currency, maximumFractionDigits: 2 }).format(value)
}

function nearestForecast(detail: EarningsResearchDetail | null) {
  return detail?.timeline.reduce<EarningsForecastSnapshot | null>((nearest, item) => (
    !nearest || item.countdown_day < nearest.countdown_day ? item : nearest
  ), null) ?? null
}

function safePageError(error: unknown) {
  if (error instanceof EarningsApiError && error.status === 401) return '登录状态已失效，请重新登录后查看业绩预测。'
  if (error instanceof EarningsApiError && error.status === 403) return '当前账户没有读取该研究的权限。'
  return error instanceof Error ? error.message.slice(0, 300) : '业绩预测研究暂时不可用。'
}

function EarningsLockedView({ locked }: { locked: EarningsLockedOverview }) {
  return <section className="earnings-lock-shell" aria-labelledby="earnings-lock-title">
    <header>
      <span className="earnings-lock-icon"><LockKeyhole /></span>
      <div><span>LEGACY FORECAST</span><h2 id="earnings-lock-title">完整业绩预测需要历史有效权益</h2><p>{locked.description}</p></div>
      <button className="button secondary" type="button" disabled>当前不开放新购</button>
    </header>
    <div className="earnings-lock-summary">
      <span><strong>{locked.window_days} 天</strong><small>未来事件窗口</small></span>
      <span><strong>{locked.confirmed_event_count}</strong><small>已确认事件</small></span>
      <span><strong>永久</strong><small>D-7 至 D-1 快照</small></span>
      <span><strong>有限风险</strong><small>期权结构另行授权</small></span>
    </div>
    <div className="earnings-lock-preview" aria-label="锁定内容预览">
      <section><FileLock2 /><strong>方向概率与价格区间</strong><span>向上 / 横盘 / 向下概率、P10 / P50 / P90 和置信度校准</span></section>
      <section><Target /><strong>研究行动与风险边界</strong><span>失效、退出、最大亏损、证据摘要和因果假设</span></section>
      <section><BarChart3 /><strong>结果、复盘与长期统计</strong><span>方向准确率、校准误差、区间覆盖和纸上结果</span></section>
    </div>
    <p className="earnings-safety-note"><ShieldCheck size={15} /> 锁定状态不会读取或泄露股票、概率、模型、证据与历史记录。</p>
  </section>
}

function EarningsLoading() {
  return <div className="earnings-state"><LoaderCircle className="is-spinning" /><strong>正在读取封存研究</strong><span>只加载当前账户有权限读取的业绩事件与快照。</span></div>
}

function EarningsError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <section className="earnings-sync-state" aria-labelledby="earnings-sync-title">
    <header><span><CircleAlert /></span><div><small>DATA SYNC / READ-ONLY</small><h2 id="earnings-sync-title">业绩研究数据正在同步</h2><p>{message} 页面不会用推测日期、虚构公司或伪造预测填充空白。</p></div><button className="button secondary" type="button" onClick={onRetry}><RefreshCw size={15} /> 重新读取</button></header>
    <div className="earnings-sync-flow" aria-label="业绩研究流程"><article><span>01</span><div><strong>确认事件时间</strong><small>仅接收交易所时区与盘前 / 盘后状态明确的事件。</small></div></article><article><span>02</span><div><strong>封存研究快照</strong><small>D-7 至 D-1 的概率、区间、证据与模型版本永久绑定。</small></div></article><article><span>03</span><div><strong>等待结果复盘</strong><small>真实结果可验证后才计算方向、校准与区间覆盖。</small></div></article></div>
    <footer><ShieldCheck size={16} />研究行动只读，不提交订单，也不冒充个人账户业绩。</footer>
  </section>
}

function ForecastRange({ forecast }: { forecast: EarningsForecastSnapshot }) {
  return <section className="earnings-range" aria-labelledby="earnings-range-title">
    <header className="earnings-section-heading"><div><span>PRICE INTERVAL / RESEARCH</span><h2 id="earnings-range-title">财报后价格区间</h2></div><small>参考价 {money(forecast.reference_price, forecast.currency)}</small></header>
    <div className="earnings-range-track" aria-label={`P10 ${forecast.price_p10}，P50 ${forecast.price_p50}，P90 ${forecast.price_p90}`}>
      <span className="is-p10"><i />P10 <strong>{forecast.price_p10.toFixed(2)}</strong></span>
      <span className="is-p50"><i />P50 <strong>{forecast.price_p50.toFixed(2)}</strong></span>
      <span className="is-p90"><i />P90 <strong>{forecast.price_p90.toFixed(2)}</strong></span>
    </div>
    <dl className="earnings-range-metrics">
      <div><dt>预估最大有利波动</dt><dd className="positive-text">{percent(forecast.estimated_mfe_pct)}</dd></div>
      <div><dt>预估最大不利波动</dt><dd className="negative-text">{percent(forecast.estimated_mae_pct)}</dd></div>
      <div><dt>横盘定义</dt><dd>±{forecast.flat_band_pct.toFixed(1)}%</dd></div>
      <div><dt>证据快照</dt><dd>{forecast.evidence_count} 项</dd></div>
    </dl>
  </section>
}

function ForecastResearch({ forecast }: { forecast: EarningsForecastSnapshot }) {
  return <section className="earnings-research-grid">
    <article className="earnings-action-panel">
      <header className="earnings-section-heading"><div><span>SIMULATED ACTION / NO ORDER</span><h2>研究行动合同</h2></div><span className="earnings-boundary-chip"><ShieldCheck size={14} /> 不可自动下单</span></header>
      <div className="earnings-action-primary"><span>当前研究动作</span><strong>{actionLabel(forecast.simulated_action)}</strong><small>数量未提供 · 入场价未提供 · 仅供研究</small></div>
      <dl>
        <div><dt>最大定义亏损</dt><dd>{money(forecast.risk.max_loss_amount, forecast.risk.currency)}</dd></div>
        <div><dt>失效条件</dt><dd>{forecast.action_contract.invalidation}</dd></div>
        <div><dt>退出条件</dt><dd>{forecast.action_contract.exit}</dd></div>
        <div><dt>展期规则</dt><dd>{forecast.action_contract.roll}</dd></div>
      </dl>
    </article>
    <article className="earnings-evidence-panel">
      <header className="earnings-section-heading"><div><span>EVIDENCE / CAUSAL</span><h2>证据与机制假设</h2></div><small>{forecast.evidence_count} 项证据</small></header>
      <p className="earnings-narrative">{forecast.narrative.summary}</p>
      <div className="earnings-evidence-columns">
        <section><strong>支持证据</strong>{forecast.narrative.supporting_evidence.length ? <ul>{forecast.narrative.supporting_evidence.map((item) => <li key={item}>{item}</li>)}</ul> : <span>暂无支持证据摘要</span>}</section>
        <section><strong>反向证据</strong>{forecast.narrative.counter_evidence.length ? <ul>{forecast.narrative.counter_evidence.map((item) => <li key={item}>{item}</li>)}</ul> : <span>暂无反向证据摘要</span>}</section>
      </div>
      <div className="earnings-causal-list">{forecast.causal_graph.claims.length ? forecast.causal_graph.claims.map((claim) => <div key={`${claim.claim}-${claim.confidence}`}><span><strong>{Math.round(claim.confidence * 100)}%</strong><small>{claim.evidence_count} 项证据</small></span><p>{claim.claim}</p></div>) : <p>当前快照没有可公开的机制假设。</p>}</div>
      <footer><span>模型 {forecast.model_artifact_sha256.slice(0, 12)}…</span><span>证据 {forecast.evidence_manifest_sha256.slice(0, 12)}…</span></footer>
    </article>
  </section>
}

function FutureWorkspace({
  overview,
  detail,
  selectedDay,
  onSelectDay,
  onSelectEvent,
  option,
  optionReferences,
  selectedOptionId,
  onSelectOption,
  optionLoading,
}: {
  overview: Extract<EarningsOverview, { state: 'research' }>
  detail: EarningsDetail | null
  selectedDay: number | null
  onSelectDay: (forecast: EarningsForecastSnapshot) => void
  onSelectEvent: (item: EarningsForecastItem) => void
  option: EarningsOptionDetail | null
  optionReferences: EarningsOptionReferenceItem[]
  selectedOptionId: string | null
  onSelectOption: (optionId: string) => void
  optionLoading: boolean
}) {
  if (overview.data_state === 'no_data') return <div className="earnings-state"><CalendarClock /><strong>未来 7 天没有已确认事件</strong><span>页面不会用虚构公司、历史财报或推测日期填充空白。</span></div>
  const research = detail?.state === 'research' ? detail : null
  const forecast = research?.timeline.find((item) => item.countdown_day === selectedDay) ?? nearestForecast(research)

  return <div className="earnings-future-layout">
    <aside className="earnings-event-list" aria-label="未来七天业绩事件">
      <header><span>UPCOMING / 7D</span><strong>已确认事件</strong><small>{overview.items.length} 项</small></header>
      {overview.items.map((item) => <button className={research?.event_id === item.event_id ? 'is-selected' : ''} type="button" onClick={() => onSelectEvent(item)} key={item.event_id}>
        <span className="earnings-event-date"><strong>{dateTime(item.scheduled_at, false).slice(5)}</strong><small>{timingLabel(item.timing)}</small></span>
        <span><strong>{item.symbol}</strong><small>{item.fiscal_period} · {item.market}</small></span>
        <span className={`earnings-event-state is-${item.forecast_state}`}><i />{item.forecast_state === 'sealed' ? `D-${item.latest_forecast?.countdown_day}` : '等待'}</span>
        <ChevronRight size={15} />
      </button>)}
    </aside>

    <main className="earnings-event-workspace">
      {detail?.state === 'locked' ? <EarningsLockedView locked={detail} /> : !research ? <EarningsLoading /> : <>
        <header className="earnings-event-header">
          <div><span>{research.market} · {research.fiscal_period}</span><h1>{research.symbol}</h1><p>{dateTime(research.scheduled_at)} · {timingLabel(research.timing)} · {research.exchange_timezone}</p></div>
          <span className="earnings-research-badge"><FileLock2 size={15} /> 研究快照 · 永久保留</span>
        </header>
        {forecast ? <>
          <div className="earnings-primary-grid"><EarningsProbabilityDial forecast={forecast} /><ForecastRange forecast={forecast} /></div>
          <EarningsForecastTimeline forecasts={research.timeline} selectedDay={forecast.countdown_day} onSelect={onSelectDay} />
          <ForecastResearch forecast={forecast} />
          <EarningsOptionStructure
            option={option}
            currency={forecast.currency}
            references={optionReferences}
            selectedOptionId={selectedOptionId}
            onSelectReference={onSelectOption}
            loading={optionLoading}
          />
        </> : <div className="earnings-state"><Clock3 /><strong>该事件正在等待首个封存快照</strong><span>只有 D-7 至 D-1 的合规快照写入后才会显示概率、区间和行动。</span></div>}
      </>}
    </main>
  </div>
}

function EarningsHistoryView({ history }: { history: EarningsHistory | null }) {
  if (!history) return <EarningsLoading />
  if (history.state === 'locked') return <EarningsLockedView locked={history} />
  if (!history.items.length) return <div className="earnings-state"><History /><strong>暂无已完成事件</strong><span>结果与复盘只会在可验证结果按时可用后出现。</span></div>
  return <section className="earnings-history-table"><header className="earnings-section-heading"><div><span>OUTCOME / POSTMORTEM</span><h2>历史结果与复盘</h2></div><small>{history.items.length} 个事件</small></header><div role="table">
    <div className="earnings-table-head" role="row"><span role="columnheader">事件</span><span role="columnheader">结果检查点</span><span role="columnheader">实际波动</span><span role="columnheader">复盘</span></div>
    {history.items.map((item) => { const outcome = item.outcomes.at(-1); const postmortem = item.postmortems.at(-1); return <div role="row" key={item.event_id}><span role="cell"><strong>{item.symbol}</strong><small>{item.fiscal_period} · {dateTime(item.scheduled_at, false)}</small></span><span role="cell">{outcome?.checkpoint ?? '等待结果'}</span><span role="cell" className={outcome && outcome.return_pct >= 0 ? 'positive-text' : 'negative-text'}>{outcome ? percent(outcome.return_pct) : '暂无结果'}</span><span role="cell">{postmortem ? <><strong>{postmortem.direction_correct ? '方向正确' : '方向偏差'}</strong><small>{postmortem.stage} · {dateTime(postmortem.completed_at, false)}</small></> : '等待复盘'}</span></div>})}
  </div></section>
}

function EarningsStatisticsView({ statistics }: { statistics: EarningsStatistics | null }) {
  if (!statistics) return <EarningsLoading />
  if (statistics.state === 'locked') return <EarningsLockedView locked={statistics} />
  const metrics = statistics.metrics
  const items = [
    ['样本数', metrics.sample_size.toLocaleString('zh-CN'), '已完成 D-1 与结果对齐'],
    ['方向准确率', percent(metrics.direction_accuracy, true), '多分类方向命中'],
    ['Brier 分数', metrics.multiclass_brier_score.toFixed(3), '越低越好'],
    ['校准误差', percent(metrics.expected_calibration_error, true), '概率与实际偏差'],
    ['区间覆盖率', percent(metrics.interval_coverage, true), '实际价格落入 P10—P90'],
    ['过度自信率', percent(metrics.overconfidence_rate, true), `高置信样本 ${metrics.high_confidence_sample_size}`],
    ['纸上总损益', metrics.paper_total_pnl === null ? '不可用' : metrics.paper_total_pnl.toFixed(2), metrics.paper_total_pnl === null ? '缺少真实封存净值，不展示伪零' : '研究记录，不是账户收益'],
    ['纸上最大回撤', metrics.paper_max_drawdown === null ? '不可用' : percent(metrics.paper_max_drawdown), metrics.paper_max_drawdown === null ? '缺少真实封存净值，不展示伪零' : '按封存规则统计'],
  ]
  return <section className="earnings-statistics"><header className="earnings-section-heading"><div><span>LONG-RUN / CALIBRATION</span><h2>长期预测统计</h2></div><span className="earnings-boundary-chip"><CheckCircle2 size={14} /> 只读结果</span></header><div className="earnings-stat-grid">{items.map(([label, value, note]) => <article key={label}><span>{label}</span><strong>{value}</strong><small>{note}</small></article>)}</div><p className="earnings-safety-note"><ShieldCheck size={15} /> 统计只使用按时间可用的 D-1 快照与事后结果；不会回写预测，也不会冒充个人账户收益。</p></section>
}

export function EarningsForecastPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedTab = searchParams.get('tab')
  const tab: EarningsTab = requestedTab === 'history' || requestedTab === 'statistics' ? requestedTab : 'future'
  const [overview, setOverview] = useState<EarningsOverview | null>(null)
  const [detail, setDetail] = useState<EarningsDetail | null>(null)
  const [history, setHistory] = useState<EarningsHistory | null>(null)
  const [statistics, setStatistics] = useState<EarningsStatistics | null>(null)
  const [option, setOption] = useState<EarningsOptionDetail | null>(null)
  const [selectedOptionId, setSelectedOptionId] = useState<string | null>(null)
  const [optionLoading, setOptionLoading] = useState(false)
  const [selectedDay, setSelectedDay] = useState<number | null>(null)
  const [phase, setPhase] = useState<'loading' | 'ready' | 'error'>('loading')
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const detailRequest = useRef<AbortController | null>(null)
  const optionRequest = useRef<AbortController | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setPhase('loading')
    setError('')
    void loadEarningsInitialState(earningsApi, controller.signal).then(({ overview: loadedOverview, detail: loadedDetail }) => {
      setOverview(loadedOverview)
      setDetail(loadedDetail)
      setSelectedDay(loadedDetail?.state === 'research' ? nearestForecast(loadedDetail)?.countdown_day ?? null : null)
      setPhase('ready')
    }).catch((caught) => {
      if (controller.signal.aborted) return
      setError(safePageError(caught))
      setPhase('error')
    })
    return () => controller.abort()
  }, [revision])

  useEffect(() => () => {
    detailRequest.current?.abort()
    optionRequest.current?.abort()
  }, [])

  useEffect(() => {
    if (phase !== 'ready') return
    const controller = new AbortController()
    if (tab === 'history' && !history) void earningsApi.fetchHistory(null, controller.signal).then(setHistory).catch((caught) => setError(safePageError(caught)))
    if (tab === 'statistics' && !statistics) void earningsApi.fetchStatistics(controller.signal).then(setStatistics).catch((caught) => setError(safePageError(caught)))
    return () => controller.abort()
  }, [history, phase, statistics, tab])

  const selectTab = (nextTab: EarningsTab) => {
    const next = new URLSearchParams(searchParams)
    if (nextTab === 'future') next.delete('tab'); else next.set('tab', nextTab)
    setSearchParams(next)
  }

  const selectEvent = useCallback((item: EarningsForecastItem) => {
    detailRequest.current?.abort()
    optionRequest.current?.abort()
    const controller = new AbortController()
    detailRequest.current = controller
    setDetail(null)
    setSelectedDay(null)
    setOption(null)
    setSelectedOptionId(null)
    setOptionLoading(false)
    void earningsApi.fetchDetail(item.event_id, controller.signal).then((loaded) => {
      if (controller.signal.aborted) return
      setDetail(loaded)
      setSelectedDay(loaded.state === 'research' ? nearestForecast(loaded)?.countdown_day ?? null : null)
    }).catch((caught) => { if (!controller.signal.aborted) setError(safePageError(caught)) })
  }, [])

  const researchDetail = detail?.state === 'research' ? detail : null
  const selectedForecast = useMemo(() => (
    researchDetail?.timeline.find((item) => item.countdown_day === selectedDay)
      ?? nearestForecast(researchDetail)
  ), [researchDetail, selectedDay])
  const optionReferences = selectedForecast?.option_research.state === 'available'
    ? selectedForecast.option_research.items
    : []

  useEffect(() => {
    optionRequest.current?.abort()
    setOptionLoading(false)
    const reference = selectedForecast?.option_research
    if (!researchDetail || !selectedForecast || !reference) {
      setOption(null)
      setSelectedOptionId(null)
      return
    }
    if (reference.state === 'locked') {
      setOption(reference)
      setSelectedOptionId(null)
      return
    }
    if (reference.state === 'no_data') {
      setOption(null)
      setSelectedOptionId(null)
      return
    }
    const nextOptionId = reference.items.some((item) => item.option_id === selectedOptionId)
      ? selectedOptionId
      : reference.items[0]?.option_id ?? null
    if (nextOptionId !== selectedOptionId) {
      setOption(null)
      setSelectedOptionId(nextOptionId)
      return
    }
    if (!nextOptionId) {
      setOption(null)
      return
    }
    const controller = new AbortController()
    optionRequest.current = controller
    setOption(null)
    setOptionLoading(true)
    void earningsApi.fetchOptionDetail(researchDetail.event_id, nextOptionId, controller.signal).then((loaded) => {
      if (controller.signal.aborted) return
      setOption(loaded)
      setOptionLoading(false)
    }).catch((caught) => {
      if (controller.signal.aborted) return
      setOption(null)
      setOptionLoading(false)
      setError(safePageError(caught))
    })
    return () => controller.abort()
  }, [researchDetail, selectedForecast, selectedOptionId])

  const researchOverview = overview?.state === 'research' ? overview : null
  const headerCount = useMemo(() => overview?.state === 'research' ? overview.items.length : overview?.confirmed_event_count ?? 0, [overview])
  const showGuidance = phase !== 'ready' || overview?.state !== 'research' || headerCount === 0

  return <div className="page earnings-page">
    <header className="earnings-command-header">
      <div><span><CalendarClock size={15} /> EARNINGS INTELLIGENCE</span><h1>业绩预测</h1><p>未来 7 天事件、D-7 至 D-1 永久快照、概率区间、研究行动、证据与事后复盘。</p></div>
      <div className="earnings-command-status"><span><i />研究专用</span><span><FileLock2 size={14} />永久快照</span><strong>{headerCount} 个已确认事件</strong></div>
    </header>
    <nav className="earnings-tabs" aria-label="业绩预测视图">{TABS.map(({ id, label, icon: Icon }) => <button className={tab === id ? 'is-selected' : ''} type="button" aria-pressed={tab === id} onClick={() => selectTab(id)} key={id}><Icon size={15} />{label}</button>)}</nav>
    {error && phase === 'ready' && <div className="earnings-inline-error" role="status"><CircleAlert size={15} />{error}</div>}
    {showGuidance && <section className="page-assist-grid" aria-label="业绩预测规则摘要">
      <article><FileLock2 size={18} /><div><span>快照规则</span><h2>D-7 至 D-1 永久封存</h2><p>只显示按时间可用的真实研究快照，不回写历史预测。</p></div></article>
      <article><History size={18} /><div><span>复盘条件</span><h2>可验证结果出现后才复盘</h2><p>没有确认事件或结果时，页面不会用推测日期与虚构公司填充。</p></div></article>
      <article><ShieldCheck size={18} /><div><span>权益与边界</span><h2>仅限已有历史有效权益</h2><p>当前不开放新购；研究行动不会提交订单或冒充账户收益。</p></div></article>
    </section>}
    {phase === 'loading' ? <EarningsLoading /> : phase === 'error' ? <EarningsError message={error} onRetry={() => setRevision((value) => value + 1)} /> : overview?.state === 'locked' ? <EarningsLockedView locked={overview} /> : researchOverview ? (
      tab === 'future' ? <FutureWorkspace overview={researchOverview} detail={detail} selectedDay={selectedDay} onSelectDay={(forecast) => setSelectedDay(forecast.countdown_day)} onSelectEvent={selectEvent} option={option} optionReferences={optionReferences} selectedOptionId={selectedOptionId} onSelectOption={setSelectedOptionId} optionLoading={optionLoading} />
        : tab === 'history' ? <EarningsHistoryView history={history} />
          : <EarningsStatisticsView statistics={statistics} />
    ) : <EarningsError message="业绩预测响应不完整。" onRetry={() => setRevision((value) => value + 1)} />}
  </div>
}
