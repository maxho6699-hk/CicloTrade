import { AlertTriangle, Bookmark, ChevronLeft, ChevronRight, Clock3, FlaskConical, ListFilter, LockKeyhole, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useLocale } from '../i18n/useLocale'
import {
  alertDraftUrl,
  DEFAULT_SCREENER_FILTERS,
  filterAndSortScreenerRows,
  pagedScreenerRows,
  personalPaperPrefillUrl,
  researchUrl,
  SCREENER_PRESETS,
  type ScreenerServiceState,
  type ScreenerSort,
  type StockScreenerFilters,
  type StockScreenerRow,
} from '../domain/stockScreener'
import '../styles/screener.css'

export interface StockScreenerPanelProps {
  locale?: 'zh-Hans' | 'zh-Hant'
}

const SAMPLE_ROWS: StockScreenerRow[] = [
  { symbol: 'AAPL', name: 'Apple', market: 'US', trend: 'rising', risk: 'low', marketCap: 'large', score: 82 },
  { symbol: 'MSFT', name: 'Microsoft', market: 'US', trend: 'rising', risk: 'low', marketCap: 'large', score: 79 },
  { symbol: 'NVDA', name: 'NVIDIA', market: 'US', trend: 'rising', risk: 'high', marketCap: 'large', score: 76 },
  { symbol: 'META', name: 'Meta Platforms', market: 'US', trend: 'neutral', risk: 'medium', marketCap: 'large', score: 71 },
  { symbol: '600519', name: '贵州茅台', market: 'CN', trend: 'rising', risk: 'medium', marketCap: 'large', score: 74 },
  { symbol: '300750', name: '宁德时代', market: 'CN', trend: 'neutral', risk: 'high', marketCap: 'large', score: 66 },
  { symbol: '600036', name: '招商银行', market: 'CN', trend: 'rising', risk: 'low', marketCap: 'large', score: 72 },
  { symbol: '002594', name: '比亚迪', market: 'CN', trend: 'falling', risk: 'high', marketCap: 'large', score: 58 },
]

const COPY = {
  'zh-Hans': {
    kicker: 'STOCK SCREENER / RESEARCH ONLY', title: '行动型选股器', description: '用可复查条件缩小研究范围；不提供实时价格，不提交交易。', example: '示例筛选结果 · 非实时 · 不可交易', state: '数据服务未接入 · 工具框架降级运行', dataTime: '数据时间：未接入真实数据', presets: '预设', trend: '趋势优先', stable: '低风险大盘', value: 'A股观察', custom: '自定义筛选', market: '市场', allMarkets: '全部市场', trendLabel: '趋势', allTrends: '全部趋势', rising: '向上', neutral: '横盘', falling: '向下', risk: '风险', allRisk: '全部风险', low: '低', medium: '中', high: '高', cap: '市值', allCaps: '全部市值', large: '大盘', mid: '中盘', small: '小盘', save: '保存本机草稿', saved: '已保存到此浏览器，未同步', clear: '清除草稿', sort: '排序', score: '评分（高到低）', symbol: '代码（A–Z）', riskSort: '风险（低到高）', results: '条结果', symbolCol: '标的', marketCol: '市场', trendCol: '趋势', riskCol: '风险', capCol: '市值', scoreCol: '研究评分', actions: '下一步', research: '进入研究', alert: '预填预警', paper: '预填个人模拟', page: '第', of: '页 / 共', previous: '上一页', next: '下一页', disclosure: '预警与个人模拟只打开预填草稿；仍需在目标页面自行核验，当前不会创建记录或提交交易。', status: { loading: '正在读取选股数据。', ready: '数据已准备。', empty: '没有符合当前条件的结果。', error: '数据暂时不可用。', delayed: '数据延迟，不能作为即时依据。', locked: '当前权限不能查看选股数据。', degraded: '数据服务未接入，以下仅为界面示例。' },
  },
  'zh-Hant': {
    kicker: 'STOCK SCREENER / RESEARCH ONLY', title: '行動型選股器', description: '用可複查條件縮小研究範圍；不提供即時價格，不提交交易。', example: '示例篩選結果 · 非即時 · 不可交易', state: '資料服務未接入 · 工具框架降級運行', dataTime: '資料時間：未接入真實資料', presets: '預設', trend: '趨勢優先', stable: '低風險大盤', value: 'A股觀察', custom: '自訂篩選', market: '市場', allMarkets: '全部市場', trendLabel: '趨勢', allTrends: '全部趨勢', rising: '向上', neutral: '橫盤', falling: '向下', risk: '風險', allRisk: '全部風險', low: '低', medium: '中', high: '高', cap: '市值', allCaps: '全部市值', large: '大盤', mid: '中盤', small: '小盤', save: '儲存本機草稿', saved: '已儲存到此瀏覽器，未同步', clear: '清除草稿', sort: '排序', score: '評分（高到低）', symbol: '代碼（A–Z）', riskSort: '風險（低到高）', results: '筆結果', symbolCol: '標的', marketCol: '市場', trendCol: '趨勢', riskCol: '風險', capCol: '市值', scoreCol: '研究評分', actions: '下一步', research: '進入研究', alert: '預填預警', paper: '預填個人模擬', page: '第', of: '頁 / 共', previous: '上一頁', next: '下一頁', disclosure: '預警與個人模擬只開啟預填草稿；仍需在目標頁面自行核驗，目前不會建立記錄或提交交易。', status: { loading: '正在讀取選股資料。', ready: '資料已準備。', empty: '沒有符合目前條件的結果。', error: '資料暫時不可用。', delayed: '資料延遲，不能作為即時依據。', locked: '目前權限不能檢視選股資料。', degraded: '資料服務未接入，以下僅為介面示例。' },
  },
} as const

const DRAFT_KEY = 'ciclotrade.stock-screener.draft.v1'

function nativeValue<T extends string>(event: React.ChangeEvent<HTMLSelectElement>) { return event.target.value as T }

export function StockScreenerPanel({ locale: requestedLocale }: StockScreenerPanelProps) {
  const { locale: activeLocale } = useLocale()
  const locale = requestedLocale ?? activeLocale
  const copy = COPY[locale]
  const navigate = useNavigate()
  const [filters, setFilters] = useState<StockScreenerFilters>(DEFAULT_SCREENER_FILTERS)
  const [sort, setSort] = useState<ScreenerSort>('score-desc')
  const [page, setPage] = useState(0)
  const [saved, setSaved] = useState(false)
  const state: ScreenerServiceState = 'degraded'
  const rows = useMemo(() => filterAndSortScreenerRows(SAMPLE_ROWS, filters, sort), [filters, sort])
  const paged = useMemo(() => pagedScreenerRows(rows, page), [page, rows])
  const setFilter = <K extends keyof StockScreenerFilters>(key: K, value: StockScreenerFilters[K]) => { setFilters((current) => ({ ...current, [key]: value })); setPage(0) }
  const choosePreset = (id: typeof SCREENER_PRESETS[number]['id']) => { setFilters(SCREENER_PRESETS.find((preset) => preset.id === id)!.filters); setPage(0) }
  const saveDraft = () => {
    try { window.localStorage.setItem(DRAFT_KEY, JSON.stringify({ filters, sort })); setSaved(true) } catch { setSaved(false) }
  }
  const clearDraft = () => {
    try { window.localStorage.removeItem(DRAFT_KEY) } catch { /* Browser storage can be unavailable. */ }
    setSaved(false)
  }

  return <div className="page stock-screener-page">
    <header className="stock-screener-heading">
      <div><span>{copy.kicker}</span><h1>{copy.title}</h1><p>{copy.description}</p></div>
      <div className="stock-screener-provenance"><span className="status-chip mystic"><FlaskConical size={14} />{copy.example}</span><small><Clock3 size={13} />{copy.dataTime}</small></div>
    </header>
    <section className="stock-screener-state" data-state={state} role="status"><AlertTriangle size={18} /><span><strong>{copy.state}</strong><small>{copy.status[state]}</small></span></section>
    <section className="data-panel stock-screener-controls" aria-label={copy.custom}>
      <div className="stock-screener-control-row"><div><span>{copy.presets}</span><div className="stock-screener-presets">{SCREENER_PRESETS.map((preset) => <button key={preset.id} className="button tertiary" type="button" onClick={() => choosePreset(preset.id)}>{copy[preset.id]}</button>)}</div></div><div className="stock-screener-draft"><button className="button tertiary" type="button" onClick={saveDraft}><Bookmark size={15} />{copy.save}</button><button className="button tertiary" type="button" onClick={clearDraft}>{copy.clear}</button>{saved && <small>{copy.saved}</small>}</div></div>
      <div className="stock-screener-filters">
        <label><span>{copy.market}</span><select value={filters.market} onChange={(event) => setFilter('market', nativeValue<StockScreenerFilters['market']>(event))}><option value="all">{copy.allMarkets}</option><option value="US">US</option><option value="CN">A股</option></select></label>
        <label><span>{copy.trendLabel}</span><select value={filters.trend} onChange={(event) => setFilter('trend', nativeValue<StockScreenerFilters['trend']>(event))}><option value="all">{copy.allTrends}</option><option value="rising">{copy.rising}</option><option value="neutral">{copy.neutral}</option><option value="falling">{copy.falling}</option></select></label>
        <label><span>{copy.risk}</span><select value={filters.risk} onChange={(event) => setFilter('risk', nativeValue<StockScreenerFilters['risk']>(event))}><option value="all">{copy.allRisk}</option><option value="low">{copy.low}</option><option value="medium">{copy.medium}</option><option value="high">{copy.high}</option></select></label>
        <label><span>{copy.cap}</span><select value={filters.marketCap} onChange={(event) => setFilter('marketCap', nativeValue<StockScreenerFilters['marketCap']>(event))}><option value="all">{copy.allCaps}</option><option value="large">{copy.large}</option><option value="mid">{copy.mid}</option><option value="small">{copy.small}</option></select></label>
        <label><span>{copy.sort}</span><select value={sort} onChange={(event) => { setSort(nativeValue<ScreenerSort>(event)); setPage(0) }}><option value="score-desc">{copy.score}</option><option value="symbol-asc">{copy.symbol}</option><option value="risk-asc">{copy.riskSort}</option></select></label>
      </div>
    </section>
    <section className="data-panel stock-screener-results">
      <header className="panel-heading"><div><span>FILTERED RESEARCH / LOCAL EXAMPLE</span><h2><ListFilter size={18} />{rows.length} {copy.results}</h2></div><small>{copy.status.empty}</small></header>
      {rows.length === 0 ? <div className="stock-screener-empty"><Search size={20} /><span>{copy.status.empty}</span></div> : <div className="responsive-table stock-screener-table"><table><thead><tr><th>{copy.symbolCol}</th><th>{copy.marketCol}</th><th>{copy.trendCol}</th><th>{copy.riskCol}</th><th>{copy.capCol}</th><th>{copy.scoreCol}</th><th>{copy.actions}</th></tr></thead><tbody>{paged.rows.map((row) => <tr key={`${row.market}-${row.symbol}`}><td data-label={copy.symbolCol}><strong>{row.symbol}</strong><small>{row.name}</small></td><td data-label={copy.marketCol}>{row.market === 'CN' ? 'A股' : 'US'}</td><td data-label={copy.trendCol}>{copy[row.trend]}</td><td data-label={copy.riskCol}><span className={`stock-screener-risk ${row.risk}`}>{copy[row.risk]}</span></td><td data-label={copy.capCol}>{copy[row.marketCap]}</td><td data-label={copy.scoreCol}><b>{row.score}</b><small>/100</small></td><td data-label={copy.actions}><div className="stock-screener-actions"><button className="button tertiary" type="button" onClick={() => navigate(researchUrl(row))}>{copy.research}</button><button className="button tertiary" type="button" onClick={() => navigate(alertDraftUrl(row))}>{copy.alert}</button><button className="button tertiary" type="button" onClick={() => navigate(personalPaperPrefillUrl(row))}>{copy.paper}</button></div></td></tr>)}</tbody></table></div>}
      <footer className="stock-screener-footer"><p><LockKeyhole size={15} />{copy.disclosure}</p><div><button className="button tertiary" type="button" disabled={paged.page === 0} onClick={() => setPage(paged.page - 1)}><ChevronLeft size={16} />{copy.previous}</button><span>{copy.page} {paged.page + 1} {copy.of} {paged.pageCount}</span><button className="button tertiary" type="button" disabled={paged.page >= paged.pageCount - 1} onClick={() => setPage(paged.page + 1)}>{copy.next}<ChevronRight size={16} /></button></div></footer>
    </section>
  </div>
}
