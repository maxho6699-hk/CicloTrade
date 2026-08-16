import { AlertTriangle, Bookmark, ChevronLeft, ChevronRight, Clock3, CloudOff, ListFilter, LoaderCircle, LockKeyhole, Search } from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useLocale } from '../i18n/useLocale'
import {
  SCREENER_DRAFT_KEY,
  alertPrefillUrl,
  decodeStockScreenerDraft,
  decodeStockScreenerPayload,
  decodeStockScreenerPreset,
  decodeStockScreenerRequest,
  paperPrefillUrl,
  screenerViewState,
  stockScreenerRequestError,
  type ScreenerRequestError,
  type StockScreenerPayload,
  type StockScreenerPreset,
  type StockScreenerRequest,
} from '../domain/stockScreener'
import '../styles/screener.css'

export interface StockScreenerPanelProps {
  locale?: 'zh-Hans' | 'zh-Hant'
  payload?: unknown
  preset?: unknown
  loading?: boolean
  onSavePreset?: (preset: StockScreenerPreset) => Promise<unknown>
  onPageChange?: (page: number) => void
  onQueryChange?: (request: StockScreenerRequest) => void
}

const PRESETS: StockScreenerPayload['preset'][] = ['all', 'momentum', 'pullback', 'risk_first']
const DEFAULT_SORT = { field: 'updated_at' as const, direction: 'desc' as const }

const COPY = {
  'zh-Hans': {
    kicker: 'STOCK SCREENER / RESEARCH ONLY', title: '行动型选股器', description: '只呈现通过服务端合同验证的研究候选，不提供实时报价或自动交易。',
    states: { pending: ['正在读取选股数据', '等待服务端返回可验证结果。'], success: ['数据已准备', '候选、状态与下一步由服务端提供。'], empty: ['暂无符合条件的候选', '不会用示例股票填补空白。'], stale: ['数据并非最新', '保留研究记录，但不可作为即时依据。'], offline: ['数据服务离线', '没有可展示的结果或操作。'], unknown: ['选股数据暂不可用', '服务端未连接或返回的合同未通过验证。'] },
    dataTime: '香港时间', serverPreset: '服务器筛选', presetName: '预设名称', defaultPresetName: '我的筛选', save: '保存当前筛选', saving: '正在保存…', draft: '已保留未同步草稿', saved: '已保存到服务器', conflict: '服务器版本已变化；请刷新后再保存。', failed: '无法保存；未同步草稿仍保留在此浏览器。',
    results: '条结果', page: '第', of: '页 / 共', previous: '上一页', next: '下一页', symbol: '股票代码', action: '行动', price: '参考价', change: '变动', score: '评分', status: '数据状态', updated: '香港时间', nextStep: '下一步', research: '进入研究', alert: '预填预警', paper: '预填个人模拟', blocked: '不可模拟：', disclosure: '研究、预警和个人模拟都只打开服务端允许的页面或预填草稿；不会提交交易。',
    minPrice: '最低价格', maxPrice: '最高价格', minScore: '最低评分', sort: '排序', apply: '套用筛选', reset: '重置',
    presets: { all: '全部', momentum: '动量', pullback: '回调', risk_first: '风险优先' },
    actions: { buy: '看多', short: '看空', wait: '等待', hold: '持有', reduce: '减仓', exit: '退出' },
    dataStates: { fresh: '最新', delayed: '延迟', stale: '过期', missing: '缺失' },
    health: { healthy: '正常', degraded: '降级', unavailable: '不可用' },
    blockedReasons: { market_data_not_fresh: '行情不是最新', candidate_health_not_healthy: '候选状态未通过', candidate_action_not_tradeable: '当前行动不可开仓' },
    validation: { invalid_symbol: '请输入有效的美股代码，例如 AAPL 或 BRK.B。', invalid_price_range: '价格必须大于 0，且最低价格不能高于最高价格。', invalid_score_range: '评分必须在 0–100，且最低评分不能高于最高评分。', invalid_request: '筛选条件无效，请检查后重试。' },
  },
  'zh-Hant': {
    kicker: 'STOCK SCREENER / RESEARCH ONLY', title: '行動型選股器', description: '只呈現通過服務端合約驗證的研究候選，不提供即時報價或自動交易。',
    states: { pending: ['正在讀取選股資料', '等待服務端回傳可驗證結果。'], success: ['資料已準備', '候選、狀態與下一步由服務端提供。'], empty: ['暫無符合條件的候選', '不會用示例股票填補空白。'], stale: ['資料並非最新', '保留研究記錄，但不可作為即時依據。'], offline: ['資料服務離線', '沒有可展示的結果或操作。'], unknown: ['選股資料暫不可用', '伺服器未連接或回傳的合約未通過驗證。'] },
    dataTime: '香港時間', serverPreset: '伺服器篩選', presetName: '預設名稱', defaultPresetName: '我的篩選', save: '儲存目前篩選', saving: '正在儲存…', draft: '已保留未同步草稿', saved: '已儲存到伺服器', conflict: '伺服器版本已變更；請重新整理後再儲存。', failed: '無法儲存；未同步草稿仍保留在此瀏覽器。',
    results: '筆結果', page: '第', of: '頁 / 共', previous: '上一頁', next: '下一頁', symbol: '股票代碼', action: '行動', price: '參考價', change: '變動', score: '評分', status: '資料狀態', updated: '香港時間', nextStep: '下一步', research: '進入研究', alert: '預填預警', paper: '預填個人模擬', blocked: '不可模擬：', disclosure: '研究、預警和個人模擬都只開啟伺服器允許的頁面或預填草稿；不會提交交易。',
    minPrice: '最低價格', maxPrice: '最高價格', minScore: '最低評分', sort: '排序', apply: '套用篩選', reset: '重設',
    presets: { all: '全部', momentum: '動量', pullback: '回調', risk_first: '風險優先' },
    actions: { buy: '看多', short: '看空', wait: '等待', hold: '持有', reduce: '減倉', exit: '退出' },
    dataStates: { fresh: '最新', delayed: '延遲', stale: '過期', missing: '缺失' },
    health: { healthy: '正常', degraded: '降級', unavailable: '不可用' },
    blockedReasons: { market_data_not_fresh: '行情不是最新', candidate_health_not_healthy: '候選狀態未通過', candidate_action_not_tradeable: '目前行動不可開倉' },
    validation: { invalid_symbol: '請輸入有效的美股代碼，例如 AAPL 或 BRK.B。', invalid_price_range: '價格必須大於 0，且最低價格不能高於最高價格。', invalid_score_range: '評分必須在 0–100，且最低評分不能高於最高評分。', invalid_request: '篩選條件無效，請檢查後重試。' },
  },
} as const

function storageDraft() {
  try { return typeof window === 'undefined' ? null : decodeStockScreenerDraft(window.localStorage.getItem(SCREENER_DRAFT_KEY)) } catch { return null }
}

function saveDraft(preset: StockScreenerPreset) {
  try { window.localStorage.setItem(SCREENER_DRAFT_KEY, JSON.stringify(preset)) } catch { /* Storage is optional. */ }
}

function clearDraft() {
  try { window.localStorage.removeItem(SCREENER_DRAFT_KEY) } catch { /* Storage is optional. */ }
}

function saveStatus(error: unknown) {
  return typeof error === 'object' && error !== null && ('status' in error && (error as { status?: unknown }).status === 409 || 'code' in error && (error as { code?: unknown }).code === 'conflict') ? 'conflict' : 'failed'
}

function optionalNumber(input: HTMLInputElement) {
  return input.value === '' ? undefined : input.valueAsNumber
}

export function StockScreenerPanel({ locale: requestedLocale, payload: rawPayload, preset: rawPreset, loading = false, onSavePreset, onPageChange, onQueryChange }: StockScreenerPanelProps) {
  const { locale: activeLocale } = useLocale()
  const locale = requestedLocale ?? activeLocale
  const copy = COPY[locale]
  const payload = useMemo(() => decodeStockScreenerPayload(rawPayload), [rawPayload])
  const serverPreset = useMemo(() => decodeStockScreenerPreset(rawPreset), [rawPreset])
  const priceFormat = useMemo(() => new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }), [locale])
  const scoreFormat = useMemo(() => new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }), [locale])
  const percentFormat = useMemo(() => new Intl.NumberFormat(locale, { signDisplay: 'exceptZero', minimumFractionDigits: 2, maximumFractionDigits: 2 }), [locale])
  const timeFormat = useMemo(() => new Intl.DateTimeFormat(locale, { timeZone: 'Asia/Hong_Kong', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }), [locale])
  const [draft, setDraft] = useState<StockScreenerPreset | null>(storageDraft)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<'draft' | 'saved' | 'conflict' | 'failed' | ''>('')
  const [query, setQuery] = useState<StockScreenerRequest | null>(null)
  const [queryError, setQueryError] = useState<ScreenerRequestError | null>(null)
  const state = screenerViewState(payload, loading)
  const pageCount = payload ? Math.max(1, Math.ceil(payload.total / payload.page_size)) : 1
  const queryErrorId = 'stock-screener-query-error'

  useEffect(() => { if (serverPreset) setDraft(serverPreset) }, [serverPreset])
  useEffect(() => {
    if (payload && !draft) setDraft({ schema_version: 1, version: serverPreset?.version ?? 0, name: copy.defaultPresetName, filters: payload.filters, sort: payload.sort })
  }, [copy.defaultPresetName, draft, payload, serverPreset?.version])
  useEffect(() => {
    if (payload) {
      setQuery({ schema_version: 1, preset: payload.preset, filters: payload.filters, sort: payload.sort, page: payload.page, page_size: payload.page_size })
      setQueryError(null)
    }
  }, [payload])

  const editQuery = (update: (current: StockScreenerRequest) => StockScreenerRequest) => {
    setQuery((current) => current ? update(current) : current)
    setQueryError(null)
    setSaved('')
  }

  const validatedQuery = () => {
    if (!query) { setQueryError('invalid_request'); return null }
    const error = stockScreenerRequestError(query)
    setQueryError(error)
    return error ? null : decodeStockScreenerRequest(query)
  }

  const applyQuery = (event: FormEvent) => {
    event.preventDefault()
    const request = validatedQuery()
    if (request) onQueryChange?.(request)
  }

  const selectPreset = (preset: StockScreenerPayload['preset']) => {
    editQuery((current) => ({ ...current, preset, filters: {}, sort: DEFAULT_SORT, page: 1 }))
  }

  const resetQuery = () => {
    if (!payload) return
    const request = { schema_version: 1 as const, preset: 'all' as const, filters: {}, sort: DEFAULT_SORT, page: 1, page_size: payload.page_size }
    setQuery(request)
    setQueryError(null)
    setSaved('')
    onQueryChange?.(request)
  }

  const persistPreset = async () => {
    if (!draft) return
    const request = validatedQuery()
    if (!request) return
    const currentPreset = { ...draft, filters: request.filters, sort: request.sort }
    setDraft(currentPreset)
    saveDraft(currentPreset)
    if (!onSavePreset) { setSaved('draft'); return }
    setSaving(true)
    setSaved('')
    try {
      const response = decodeStockScreenerPreset(await onSavePreset(currentPreset))
      if (!response) throw new Error('invalid preset response')
      setDraft(response)
      clearDraft()
      setSaved('saved')
    } catch (error) {
      setSaved(saveStatus(error))
    } finally {
      setSaving(false)
    }
  }

  const stateCopy = copy.states[state]
  const StateIcon = state === 'pending' ? LoaderCircle : state === 'offline' ? CloudOff : state === 'success' ? ListFilter : AlertTriangle
  if (!payload) return <div className="page stock-screener-page">
    <header className="stock-screener-heading"><div><span>{copy.kicker}</span><h1>{copy.title}</h1><p>{copy.description}</p></div></header>
    <section className="stock-screener-state" data-state={state} role={state === 'unknown' ? 'alert' : 'status'}><StateIcon size={18} className={state === 'pending' ? 'spin' : undefined} aria-hidden="true" /><span><strong>{stateCopy[0]}</strong><small>{stateCopy[1]}</small></span></section>
  </div>

  return <div className="page stock-screener-page">
    <header className="stock-screener-heading">
      <div><span>{copy.kicker}</span><h1>{copy.title}</h1><p>{copy.description}</p></div>
      <div className="stock-screener-provenance"><small><Clock3 size={14} aria-hidden="true" />{copy.dataTime}</small></div>
    </header>
    <section className="stock-screener-state" data-state={state} role="status"><StateIcon size={18} className={state === 'pending' ? 'spin' : undefined} aria-hidden="true" /><span><strong>{stateCopy[0]}</strong><small>{stateCopy[1]}</small></span></section>
    <>
      <section className="data-panel stock-screener-controls" aria-label={copy.serverPreset}>
        <form className="stock-screener-control-row" onSubmit={applyQuery} noValidate>
          <div className="stock-screener-filter-block">
            <span>{copy.serverPreset}</span>
            <strong>{copy.presets[query?.preset ?? payload.preset]}</strong>
            <div className="stock-screener-query">
              {PRESETS.map((preset) => <button className="button tertiary stock-screener-preset" type="button" key={preset} aria-pressed={(query?.preset ?? payload.preset) === preset} onClick={() => selectPreset(preset)}>{copy.presets[preset]}</button>)}
              <input name="symbol" autoComplete="off" spellCheck={false} aria-label={copy.symbol} aria-invalid={queryError === 'invalid_symbol'} aria-describedby={queryError ? queryErrorId : undefined} placeholder={`${copy.symbol}…`} value={query?.filters.symbols?.[0] ?? ''} onChange={(event) => editQuery((current) => ({ ...current, filters: { ...current.filters, symbols: event.target.value.trim() ? [event.target.value.trim().toUpperCase()] : undefined }, page: 1 }))} />
              <select name="action" autoComplete="off" aria-label={copy.action} value={query?.filters.actions?.[0] ?? ''} onChange={(event) => editQuery((current) => ({ ...current, filters: { ...current.filters, actions: event.target.value ? [event.target.value as keyof typeof copy.actions] : undefined }, page: 1 }))}><option value="">{copy.action}</option>{Object.entries(copy.actions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
              <select name="data_state" autoComplete="off" aria-label={copy.status} value={query?.filters.data_states?.[0] ?? ''} onChange={(event) => editQuery((current) => ({ ...current, filters: { ...current.filters, data_states: event.target.value ? [event.target.value as keyof typeof copy.dataStates] : undefined }, page: 1 }))}><option value="">{copy.status}</option>{Object.entries(copy.dataStates).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
              <input name="min_price" autoComplete="off" aria-label={copy.minPrice} aria-invalid={queryError === 'invalid_price_range'} aria-describedby={queryError ? queryErrorId : undefined} type="number" inputMode="decimal" min="0.01" step="0.01" placeholder={`${copy.minPrice}…`} value={query?.filters.min_price ?? ''} onChange={(event) => { const value = optionalNumber(event.currentTarget); editQuery((current) => ({ ...current, filters: { ...current.filters, min_price: value } })) }} />
              <input name="max_price" autoComplete="off" aria-label={copy.maxPrice} aria-invalid={queryError === 'invalid_price_range'} aria-describedby={queryError ? queryErrorId : undefined} type="number" inputMode="decimal" min="0.01" step="0.01" placeholder={`${copy.maxPrice}…`} value={query?.filters.max_price ?? ''} onChange={(event) => { const value = optionalNumber(event.currentTarget); editQuery((current) => ({ ...current, filters: { ...current.filters, max_price: value } })) }} />
              <input name="min_score" autoComplete="off" aria-label={copy.minScore} aria-invalid={queryError === 'invalid_score_range'} aria-describedby={queryError ? queryErrorId : undefined} type="number" inputMode="numeric" min="0" max="100" step="1" placeholder={`${copy.minScore}…`} value={query?.filters.min_score ?? ''} onChange={(event) => { const value = optionalNumber(event.currentTarget); editQuery((current) => ({ ...current, filters: { ...current.filters, min_score: value } })) }} />
              <select name="sort" autoComplete="off" aria-label={copy.sort} value={query?.sort.field ?? 'updated_at'} onChange={(event) => editQuery((current) => ({ ...current, sort: { ...current.sort, field: event.target.value as StockScreenerRequest['sort']['field'] } }))}><option value="updated_at">{copy.dataTime}</option><option value="score">{copy.score}</option><option value="price">{copy.price}</option></select>
              <button className="button primary" type="submit">{copy.apply}</button>
              <button className="button tertiary" type="button" onClick={resetQuery}>{copy.reset}</button>
            </div>
            {queryError && <p className="stock-screener-field-error" id={queryErrorId} role="alert"><AlertTriangle size={15} aria-hidden="true" />{copy.validation[queryError]}</p>}
          </div>
          <div className="stock-screener-draft">
            <label><span>{copy.presetName}</span><input name="preset_name" autoComplete="off" value={draft?.name ?? ''} disabled={saving} onChange={(event) => { setDraft((current) => current ? { ...current, name: event.target.value } : current); setSaved('') }} maxLength={80} /></label>
            <button className="button tertiary" type="button" disabled={saving || !draft?.name.trim()} onClick={persistPreset}><Bookmark size={15} aria-hidden="true" />{saving ? copy.saving : copy.save}</button>
            {saved && <small data-save-state={saved} aria-live="polite">{copy[saved]}</small>}
          </div>
        </form>
      </section>
      <section className="data-panel stock-screener-results">
        <header className="panel-heading"><div><span>SERVER-VERIFIED RESEARCH</span><h2><ListFilter size={18} aria-hidden="true" />{payload.total} {copy.results}</h2></div><small>{copy.page} {payload.page} {copy.of} {pageCount}</small></header>
        {payload.items.length === 0 ? <div className="stock-screener-empty"><Search size={20} aria-hidden="true" /><span>{stateCopy[1]}</span></div> : <div className="responsive-table stock-screener-table"><table><thead><tr><th>{copy.symbol}</th><th>{copy.action}</th><th>{copy.price}</th><th>{copy.change}</th><th>{copy.score}</th><th>{copy.status}</th><th>{copy.updated}</th><th>{copy.nextStep}</th></tr></thead><tbody>{payload.items.map((row) => <tr key={row.symbol}><td data-label={copy.symbol}><strong translate="no">{row.symbol}</strong><small>{row.name}</small></td><td data-label={copy.action}>{copy.actions[row.action]}</td><td data-label={copy.price}>{priceFormat.format(row.price)}</td><td data-label={copy.change}>{percentFormat.format(row.change_pct)}%</td><td data-label={copy.score}>{row.score === null ? '—' : scoreFormat.format(row.score)}</td><td data-label={copy.status}><span className={`stock-screener-status ${row.data_state}`}>{copy.dataStates[row.data_state]}</span><span className={`stock-screener-status ${row.health}`}>{copy.health[row.health]}</span></td><td data-label={copy.updated}>{timeFormat.format(new Date(row.updated_at))}</td><td data-label={copy.nextStep}><div className="stock-screener-actions"><Link className="button tertiary" to={row.research_url}>{copy.research}</Link><Link className="button tertiary" to={alertPrefillUrl(row.alert_prefill)}>{copy.alert}</Link>{row.paper_prefill && row.actionable ? <Link className="button tertiary" to={paperPrefillUrl(row.paper_prefill)}>{copy.paper}</Link> : <small>{copy.blocked}{row.blocked_reason ? copy.blockedReasons[row.blocked_reason as keyof typeof copy.blockedReasons] ?? <span translate="no">{row.blocked_reason}</span> : copy.blockedReasons.candidate_action_not_tradeable}</small>}</div></td></tr>)}</tbody></table></div>}
        <footer className="stock-screener-footer"><p><LockKeyhole size={15} aria-hidden="true" />{copy.disclosure}</p><div><button className="button tertiary" type="button" disabled={payload.page <= 1 || !onPageChange} onClick={() => onPageChange?.(payload.page - 1)}><ChevronLeft size={16} aria-hidden="true" />{copy.previous}</button><span>{copy.page} {payload.page} {copy.of} {pageCount}</span><button className="button tertiary" type="button" disabled={payload.page >= pageCount || !onPageChange} onClick={() => onPageChange?.(payload.page + 1)}>{copy.next}<ChevronRight size={16} aria-hidden="true" /></button></div></footer>
      </section>
    </>
  </div>
}
