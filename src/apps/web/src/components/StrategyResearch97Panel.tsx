import { AlertTriangle, ChevronLeft, ChevronRight, Clock3, Database, LockKeyhole, Search, ShieldAlert, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { BrowserApiError } from '../api/client'
import { fetchStrategyResearch97Aggregate, type StrategyResearch97AggregateLoad, type StrategyResearch97DataState, type StrategyResearch97Signal, type StrategyResearch97State } from '../api/strategyResearch97'
import { useLocale } from '../i18n/useLocale'
import '../styles/strategy-research-97.css'

type TierFilter = 'all' | 'A' | 'C'
type SignalFilter = 'all' | StrategyResearch97Signal | StrategyResearch97DataState
type LoadState = { phase: 'loading' } | { phase: 'forbidden' } | { phase: 'error'; reason?: StrategyResearch97AggregateLoad['reason'] } | { phase: 'partial'; load: StrategyResearch97AggregateLoad } | { phase: 'ready'; load: StrategyResearch97AggregateLoad }

interface ResearchCopy {
  title: string; subtitle: string; disclaimer: string; projection: string; loading: string; error: string; forbidden: string; partial: string; mismatch: string; empty: string; noMatches: string; waiting: string
  stable: string; stableDetail: string; expanded: string; expandedDetail: string; unavailable: string; state: string; version: string; universe: string; coverage: string; updated: string; expires: string; cycle: string; strategy: string; long: string; flat: string; wait: string; missing: string; fresh: string; stale: string; dataMissing: string; symbol: string; tier: string; dataState: string; signal: string; rationale: string; history: string; received: string; noHistory: string; nonExecutable: string; search: string; searchPlaceholder: string; tierFilter: string; signalFilter: string; all: string; tierA: string; tierC: string; allSignals: string; page: string; previous: string; next: string; resultCount: string; status: Record<StrategyResearch97State, string>
}

const SIMPLIFIED: ResearchCopy = {
  title: '97 标的扩容研究', subtitle: '独立研究链 · 不覆盖 13 股稳定影子链', disclaimer: '本页只展示策略服务器回传的研究证据。研究结果不可执行、不可下单、不可推送 Telegram，也不会改变官方或实盘状态。', projection: '登录后的受控研究投影 · 原始策略源不可见', loading: '正在读取扩容研究记录。', error: '扩容研究暂时无法读取；为了安全，本页不会显示不完整或未经验证的结果。', forbidden: '当前账户没有读取扩容研究的权限。研究数据不会以演示数据替代。', partial: '部分研究资源暂时不可用；已读取的数据仍保持原始状态，不会被当作完整结果。', mismatch: '研究资源的版本或证据绑定不一致；为了安全，本页不会展示这次周期。', empty: '尚未收到可展示的扩容研究周期。', noMatches: '没有符合当前筛选条件的标的；请清除筛选后再试。', waiting: '扩容研究链正在等待下一次周期；不会生成可执行操作。', stable: '13 股稳定 shadow', stableDetail: '独立链 · 稳定覆盖', expanded: '97 标的 research', expandedDetail: '本页 · 扩容观察', unavailable: '暂不可用', state: '运行状态', version: '研究版本', universe: 'Universe', coverage: '本周期覆盖', updated: '最后更新', expires: '证据过期时间', cycle: '最新周期', strategy: '策略摘要', long: 'Long 研究', flat: 'Flat 观察', wait: 'WAIT', missing: '无数据', fresh: '新鲜', stale: '已过期', dataMissing: '缺数据', symbol: '标的', tier: '层级', dataState: '数据状态', signal: '研究标签', rationale: '摘要', history: '最近研究周期', received: '收到时间', noHistory: '暂无历史周期。', nonExecutable: '仅供研究 · 不可操作', search: '搜索标的', searchPlaceholder: '输入代码…', tierFilter: '层级筛选', signalFilter: '状态筛选', all: '全部', tierA: 'Tier A · 稳定轮转', tierC: 'Tier C · 扩容观察', allSignals: '全部状态', page: '页', previous: '上一页', next: '下一页', resultCount: '个结果', status: { waiting: '等待中', healthy: '正常', stale: '已过期', degraded: '降级' },
}

const TRADITIONAL: ResearchCopy = {
  title: '97 標的擴容研究', subtitle: '獨立研究鏈 · 不覆蓋 13 股穩定影子鏈', disclaimer: '本頁只展示策略伺服器回傳的研究證據。研究結果不可執行、不可下單、不可推送 Telegram，也不會改變官方或實盤狀態。', projection: '登入後的受控研究投影 · 原始策略源不可見', loading: '正在讀取擴容研究記錄。', error: '擴容研究暫時無法讀取；為了安全，本頁不會顯示不完整或未驗證的結果。', forbidden: '當前帳戶沒有讀取擴容研究的權限。研究資料不會以演示資料替代。', partial: '部分研究資源暫時不可用；已讀取的資料仍保持原始狀態，不會被當作完整結果。', mismatch: '研究資源的版本或證據綁定不一致；為了安全，本頁不會展示這次週期。', empty: '尚未收到可展示的擴容研究週期。', noMatches: '沒有符合目前篩選條件的標的；請清除篩選後再試。', waiting: '擴容研究鏈正在等待下一次週期；不會產生可執行操作。', stable: '13 股穩定 shadow', stableDetail: '獨立鏈 · 穩定覆蓋', expanded: '97 標的 research', expandedDetail: '本頁 · 擴容觀察', unavailable: '暫不可用', state: '運行狀態', version: '研究版本', universe: 'Universe', coverage: '本週期覆蓋', updated: '最後更新', expires: '證據過期時間', cycle: '最新週期', strategy: '策略摘要', long: 'Long 研究', flat: 'Flat 觀察', wait: 'WAIT', missing: '無資料', fresh: '新鮮', stale: '已過期', dataMissing: '缺資料', symbol: '標的', tier: '層級', dataState: '資料狀態', signal: '研究標籤', rationale: '摘要', history: '最近研究週期', received: '收到時間', noHistory: '暫無歷史週期。', nonExecutable: '僅供研究 · 不可操作', search: '搜尋標的', searchPlaceholder: '輸入代碼…', tierFilter: '層級篩選', signalFilter: '狀態篩選', all: '全部', tierA: 'Tier A · 穩定輪轉', tierC: 'Tier C · 擴容觀察', allSignals: '全部狀態', page: '頁', previous: '上一頁', next: '下一頁', resultCount: '個結果', status: { waiting: '等待中', healthy: '正常', stale: '已過期', degraded: '降級' },
}

const PAGE_SIZE = 18
const QUERY_KEYS = { query: 'research_query', tier: 'research_tier', signal: 'research_signal', page: 'research_page' } as const

function parseTier(value: string | null): TierFilter { return value === 'A' || value === 'C' ? value : 'all' }
function parseSignal(value: string | null): SignalFilter {
  return value === 'long' || value === 'flat' || value === 'wait' || value === 'stale' || value === 'missing' ? value : 'all'
}
function parsePage(value: string | null): number {
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : 1
}

function displayTimestamp(value: string | null, formatLocale: string): string {
  if (!value) return '—'
  return new Date(value).toLocaleString(formatLocale, { hour12: false, dateStyle: 'medium', timeStyle: 'short' })
}

function stateClass(state: StrategyResearch97State): string { return `strategy-research-97-state ${state}` }
function signalClass(signal: StrategyResearch97Signal): string { return `strategy-research-97-signal ${signal}` }
function dataStateLabel(state: StrategyResearch97DataState, text: ResearchCopy): string { return state === 'fresh' ? text.fresh : state === 'stale' ? text.stale : text.dataMissing }
export function StrategyResearch97Panel() {
  const { locale, formatLocale } = useLocale()
  const text = locale === 'zh-Hant' ? TRADITIONAL : SIMPLIFIED
  const [searchParams, setSearchParams] = useSearchParams()
  const [loadState, setLoadState] = useState<LoadState>({ phase: 'loading' })
  const [tier, setTier] = useState<TierFilter>(() => parseTier(searchParams.get(QUERY_KEYS.tier)))
  const [signal, setSignal] = useState<SignalFilter>(() => parseSignal(searchParams.get(QUERY_KEYS.signal)))
  const [query, setQuery] = useState(() => searchParams.get(QUERY_KEYS.query) ?? '')
  const [page, setPage] = useState(() => parsePage(searchParams.get(QUERY_KEYS.page)))

  const updateUrl = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(searchParams)
    Object.entries(changes).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key))
    setSearchParams(next, { replace: true })
  }

  useEffect(() => {
    setTier(parseTier(searchParams.get(QUERY_KEYS.tier)))
    setSignal(parseSignal(searchParams.get(QUERY_KEYS.signal)))
    setQuery(searchParams.get(QUERY_KEYS.query) ?? '')
    setPage(parsePage(searchParams.get(QUERY_KEYS.page)))
  }, [searchParams])

  useEffect(() => {
    let current = true
    void fetchStrategyResearch97Aggregate().then((load) => {
      if (!current) return
      if (load.phase === 'error') setLoadState(load.forbidden ? { phase: 'forbidden' } : { phase: 'error', reason: load.reason })
      else setLoadState({ phase: load.phase, load })
    }).catch((error: unknown) => {
      if (current) setLoadState({ phase: error instanceof BrowserApiError && error.status === 403 ? 'forbidden' : 'error' })
    })
    return () => { current = false }
  }, [])

  if (loadState.phase === 'loading') return <section className="data-panel strategy-research-97-panel" aria-busy="true"><div className="strategy-research-97-message" role="status" aria-live="polite"><Clock3 aria-hidden="true" size={20} /><span>{text.loading}</span></div></section>
  if (loadState.phase === 'forbidden') return <section className="data-panel strategy-research-97-panel"><div className="strategy-research-97-message forbidden" role="alert" aria-live="assertive"><LockKeyhole aria-hidden="true" size={20} /><span>{text.forbidden}</span></div></section>
  if (loadState.phase === 'error') return <section className="data-panel strategy-research-97-panel"><div className="strategy-research-97-message error" role="alert" aria-live="assertive"><AlertTriangle aria-hidden="true" size={20} /><span>{loadState.reason === 'cross_source_mismatch' ? text.mismatch : text.error}</span></div></section>

  const load = loadState.load
  const statusData = load.status.state === 'error' ? null : load.status.data
  const latest = load.latest.state === 'error' ? null : load.latest.data
  const history = load.history.state === 'error' ? null : load.history.data
  const historyError = load.history.state === 'error'
  const cycle = latest?.cycle ?? load.data?.latest.cycle ?? null
  const partial = load.phase === 'partial'
  const stateLabel = statusData ? text.status[statusData.state] : text.unavailable
  const normalizedQuery = query.trim().toUpperCase()
  const filteredSymbols = cycle?.symbols.filter((item) => {
    const matchesTier = tier === 'all' || item.tier === tier
    const matchesSignal = signal === 'all' || item.signal === signal || item.data_state === signal
    const matchesQuery = !normalizedQuery || item.symbol.includes(normalizedQuery)
    return matchesTier && matchesSignal && matchesQuery
  }) ?? []
  const pageCount = Math.max(1, Math.ceil(filteredSymbols.length / PAGE_SIZE))
  const safePage = Math.min(page, pageCount)
  const visibleSymbols = filteredSymbols.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)

  const changeTier = (next: TierFilter) => { setTier(next); setPage(1); updateUrl({ [QUERY_KEYS.tier]: next === 'all' ? undefined : next, [QUERY_KEYS.page]: undefined }) }
  const changeSignal = (next: SignalFilter) => { setSignal(next); setPage(1); updateUrl({ [QUERY_KEYS.signal]: next === 'all' ? undefined : next, [QUERY_KEYS.page]: undefined }) }
  const changeQuery = (next: string) => { setQuery(next); setPage(1); updateUrl({ [QUERY_KEYS.query]: next.trim() ? next : undefined, [QUERY_KEYS.page]: undefined }) }
  const changePage = (next: number) => { setPage(next); updateUrl({ [QUERY_KEYS.page]: next > 1 ? String(next) : undefined }) }

  return <section className="strategy-research-97-panel">
    <article className="data-panel strategy-research-97-summary">
      <header className="panel-heading"><div><span>EXPANDED RESEARCH / 97 SYMBOLS</span><h2>{text.title}</h2><p>{text.subtitle}</p></div><span className={statusData ? stateClass(statusData.state) : 'strategy-research-97-state'}>{stateLabel}</span></header>
      <p className="strategy-research-97-disclaimer"><ShieldAlert aria-hidden="true" size={16} /><span>{text.disclaimer}<strong>{text.projection}</strong></span></p>
      {partial && <p className="strategy-research-97-partial" role="status" aria-live="polite"><AlertTriangle aria-hidden="true" size={15} />{text.partial}</p>}
      <div className="strategy-research-97-lineage" aria-label={locale === 'zh-Hant' ? '策略研究鏈分層' : '策略研究链分层'}><span><ShieldCheck aria-hidden="true" size={15} /><strong>{text.stable}</strong><small>{text.stableDetail}</small></span><i aria-hidden="true">→</i><span className="is-expanded"><Database aria-hidden="true" size={15} /><strong>{text.expanded}</strong><small>{text.expandedDetail}</small></span></div>
      <dl className="strategy-research-97-metrics">
        <div><dt>{text.state}</dt><dd className={statusData ? stateClass(statusData.state) : ''}>{stateLabel}</dd></div>
        <div><dt>{text.version}</dt><dd>{statusData?.universe.version ?? text.unavailable}</dd></div>
        <div><dt>{text.universe}</dt><dd>{statusData ? `${statusData.universe.key} · ${statusData.universe.count}` : text.unavailable}</dd></div>
        <div><dt>{text.coverage}</dt><dd>{statusData ? `${statusData.coverage_count}/97 · ${statusData.no_data_count} ${text.missing}` : '—'}</dd></div>
        <div><dt>{text.updated}</dt><dd>{displayTimestamp(statusData?.last_result_at ?? null, formatLocale)}</dd></div>
        <div><dt>{text.expires}</dt><dd>{displayTimestamp(statusData?.expires_at ?? null, formatLocale)}</dd></div>
      </dl>
      {statusData && <div className="strategy-research-97-hash"><span>UNIVERSE SHA-256</span><code>{statusData.universe.sha256}</code></div>}
    </article>

    {!cycle ? <article className="data-panel"><div className="strategy-research-97-message" role="status" aria-live="polite"><Database aria-hidden="true" size={20} /><span>{partial ? text.partial : statusData?.state === 'waiting' ? text.waiting : text.empty}</span></div></article>
          : <>
            <article className="data-panel strategy-research-97-cycle">
              <header className="panel-heading"><div><span>{cycle.strategy_key} / {cycle.strategy_version}</span><h2>{text.cycle} · {cycle.evaluation_date}</h2></div><small className="strategy-research-97-readonly">{text.nonExecutable}</small></header>
              <div className="strategy-research-97-summary-grid"><div><span>{text.strategy}</span><strong>{cycle.strategy_name}</strong></div><div><span>{text.long}</span><strong className="positive-text">{cycle.summary.long_count}</strong></div><div><span>{text.flat}</span><strong>{cycle.summary.flat_count}</strong></div><div><span>{text.wait}</span><strong className="warning-text">{cycle.summary.wait_count}</strong></div><div><span>{text.missing}</span><strong>{cycle.summary.no_data_count}</strong></div></div>
              {statusData?.state === 'stale' && <p className="strategy-research-97-stale" role="status" aria-live="polite"><AlertTriangle aria-hidden="true" size={15} />{text.stale} · {displayTimestamp(statusData.last_result_at, formatLocale)}</p>}
              <div className="strategy-research-97-filters" aria-label={locale === 'zh-Hant' ? '研究篩選' : '研究筛选'}><label><span>{text.search}</span><div><Search aria-hidden="true" size={15} /><input name="strategy-research-symbol" autoComplete="off" spellCheck={false} value={query} onChange={(event) => changeQuery(event.target.value)} placeholder={text.searchPlaceholder} aria-label={text.search} /></div></label><label><span>{text.tierFilter}</span><select name="strategy-research-tier" value={tier} onChange={(event) => changeTier(event.target.value as TierFilter)} aria-label={text.tierFilter}><option value="all">{text.all}</option><option value="A">{text.tierA}</option><option value="C">{text.tierC}</option></select></label><label><span>{text.signalFilter}</span><select name="strategy-research-signal" value={signal} onChange={(event) => changeSignal(event.target.value as SignalFilter)} aria-label={text.signalFilter}><option value="all">{text.allSignals}</option><option value="long">{text.long}</option><option value="flat">{text.flat}</option><option value="wait">{text.wait}</option><option value="stale">{text.stale}</option><option value="missing">{text.missing}</option></select></label></div>
              <p className="strategy-research-97-filter-summary" role="status" aria-live="polite"><SlidersHorizontal aria-hidden="true" size={14} />{filteredSymbols.length} {text.resultCount} · {text.page} {safePage}/{pageCount}</p>
              {visibleSymbols.length ? <div className="responsive-table strategy-research-97-table"><table><thead><tr><th>{text.symbol}</th><th>{text.tier}</th><th>{text.dataState}</th><th>{text.signal}</th><th>{text.rationale}</th></tr></thead><tbody>{visibleSymbols.map((item) => <tr key={`${item.market}-${item.symbol}`}><td data-label={text.symbol}><strong>{item.symbol}</strong><small>{item.market}</small></td><td data-label={text.tier}><span className="strategy-research-97-tier">{item.tier}</span></td><td data-label={text.dataState}><span className={`strategy-research-97-data ${item.data_state}`}>{dataStateLabel(item.data_state, text)}</span></td><td data-label={text.signal}><span className={signalClass(item.signal)}>{item.signal === 'long' ? text.long : item.signal === 'flat' ? text.flat : text.wait}</span></td><td data-label={text.rationale}>{item.rationale ?? '—'}</td></tr>)}</tbody></table></div> : <div className="strategy-research-97-message strategy-research-97-filter-empty" role="status" aria-live="polite"><Search aria-hidden="true" size={20} /><span>{text.noMatches}</span></div>}
              <nav className="strategy-research-97-pagination" aria-label={locale === 'zh-Hant' ? '研究分頁' : '研究分页'}><button type="button" className="button secondary" disabled={safePage <= 1} onClick={() => changePage(Math.max(1, safePage - 1))}><ChevronLeft aria-hidden="true" size={15} />{text.previous}</button><span aria-live="polite">{text.page} {safePage}/{pageCount}</span><button type="button" className="button secondary" disabled={safePage >= pageCount} onClick={() => changePage(Math.min(pageCount, safePage + 1))}>{text.next}<ChevronRight aria-hidden="true" size={15} /></button></nav>
            </article>
            <article className="data-panel strategy-research-97-history"><header className="panel-heading"><div><span>RECENT CYCLES / 20</span><h2>{text.history}</h2></div></header>{history?.items.length ? <div className="responsive-table strategy-research-97-history-table"><table><thead><tr><th>{text.cycle}</th><th>{text.coverage}</th><th>{text.long}</th><th>{text.flat}</th><th>{text.wait}</th><th>{text.received}</th></tr></thead><tbody>{history.items.map((item) => <tr key={item.cycle_id}><td data-label={text.cycle}><strong>{item.evaluation_date}</strong><small>{item.cycle_id}</small></td><td data-label={text.coverage}>{item.coverage_count}/97 · {item.no_data_count} {text.missing}</td><td data-label={text.long}>{item.long_count}</td><td data-label={text.flat}>{item.flat_count}</td><td data-label={text.wait}>{item.wait_count}</td><td data-label={text.received}>{displayTimestamp(item.received_at, formatLocale)}</td></tr>)}</tbody></table></div> : <div className="strategy-research-97-message" role="status" aria-live="polite"><Clock3 aria-hidden="true" size={20} /><span>{historyError ? text.partial : text.noHistory}</span></div>}</article>
          </>}
  </section>
}

export type { StrategyResearch97State }
