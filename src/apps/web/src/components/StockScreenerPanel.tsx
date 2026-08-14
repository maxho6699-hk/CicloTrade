import { AlertTriangle, Bookmark, ChevronLeft, ChevronRight, Clock3, CloudOff, ListFilter, LoaderCircle, LockKeyhole, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useLocale } from '../i18n/useLocale'
import {
  SCREENER_DRAFT_KEY,
  alertPrefillUrl,
  decodeStockScreenerDraft,
  decodeStockScreenerPayload,
  decodeStockScreenerPreset,
  paperPrefillUrl,
  screenerViewState,
  type StockScreenerPreset,
} from '../domain/stockScreener'
import '../styles/screener.css'

export interface StockScreenerPanelProps {
  locale?: 'zh-Hans' | 'zh-Hant'
  payload?: unknown
  preset?: unknown
  loading?: boolean
  onSavePreset?: (preset: StockScreenerPreset) => Promise<unknown>
  onPageChange?: (page: number) => void
}

const COPY = {
  'zh-Hans': {
    kicker: 'STOCK SCREENER / RESEARCH ONLY', title: '行动型选股器', description: '只呈现通过服务端合同验证的研究候选，不提供实时报价或自动交易。',
    states: { pending: ['正在读取选股数据', '等待服务端返回可验证结果。'], success: ['数据已准备', '候选、状态与下一步由服务端提供。'], empty: ['暂无符合条件的候选', '不会用示例标的填补空白。'], stale: ['数据并非最新', '保留研究记录，但不可作为即时依据。'], offline: ['数据服务离线', '没有可展示的结果或操作。'], unknown: ['选股数据暂不可用', '服务端未连接或返回的合同未通过验证。'] },
    dataTime: '香港时间', serverPreset: '服务器筛选', presetName: '预设名称', save: '保存到服务器', saving: '正在保存', draft: '已保留未同步草稿', saved: '已保存到服务器', conflict: '服务器版本已变化；请刷新后再保存。', failed: '无法保存；未同步草稿仍保留在此浏览器。',
    results: '条结果', page: '第', of: '页 / 共', previous: '上一页', next: '下一页', symbol: '标的', action: '行动', price: '参考价', change: '变动', score: '评分', status: '数据状态', updated: '香港时间', nextStep: '下一步', research: '进入研究', alert: '预填预警', paper: '预填个人模拟', blocked: '不可模拟：', disclosure: '研究、预警和个人模拟都只打开服务端允许的页面或预填草稿；不会提交交易。',
  },
  'zh-Hant': {
    kicker: 'STOCK SCREENER / RESEARCH ONLY', title: '行動型選股器', description: '只呈現通過服務端合約驗證的研究候選，不提供即時報價或自動交易。',
    states: { pending: ['正在讀取選股資料', '等待服務端回傳可驗證結果。'], success: ['資料已準備', '候選、狀態與下一步由服務端提供。'], empty: ['暫無符合條件的候選', '不會用示例標的填補空白。'], stale: ['資料並非最新', '保留研究記錄，但不可作為即時依據。'], offline: ['資料服務離線', '沒有可展示的結果或操作。'], unknown: ['選股資料暫不可用', '服務端未連接或回傳的合約未通過驗證。'] },
    dataTime: '香港時間', serverPreset: '服務端篩選', presetName: '預設名稱', save: '儲存到服務端', saving: '正在儲存', draft: '已保留未同步草稿', saved: '已儲存到服務端', conflict: '服務端版本已變更；請重新整理後再儲存。', failed: '無法儲存；未同步草稿仍保留在此瀏覽器。',
    results: '筆結果', page: '第', of: '頁 / 共', previous: '上一頁', next: '下一頁', symbol: '標的', action: '行動', price: '參考價', change: '變動', score: '評分', status: '資料狀態', updated: '香港時間', nextStep: '下一步', research: '進入研究', alert: '預填預警', paper: '預填個人模擬', blocked: '不可模擬：', disclosure: '研究、預警和個人模擬都只開啟服務端允許的頁面或預填草稿；不會提交交易。',
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

export function StockScreenerPanel({ locale: requestedLocale, payload: rawPayload, preset: rawPreset, loading = false, onSavePreset, onPageChange }: StockScreenerPanelProps) {
  const { locale: activeLocale } = useLocale()
  const locale = requestedLocale ?? activeLocale
  const copy = COPY[locale]
  const navigate = useNavigate()
  const payload = useMemo(() => decodeStockScreenerPayload(rawPayload), [rawPayload])
  const serverPreset = useMemo(() => decodeStockScreenerPreset(rawPreset), [rawPreset])
  const [draft, setDraft] = useState<StockScreenerPreset | null>(storageDraft)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<'draft' | 'saved' | 'conflict' | 'failed' | ''>('')
  const state = screenerViewState(payload, loading)
  const pageCount = payload ? Math.max(1, Math.ceil(payload.total / payload.page_size)) : 1

  useEffect(() => { if (serverPreset) setDraft(serverPreset) }, [serverPreset])
  useEffect(() => {
    if (payload && !draft) setDraft({ schema_version: 1, version: serverPreset?.version ?? 0, name: '我的筛选', filters: payload.filters, sort: payload.sort })
  }, [draft, payload, serverPreset?.version])

  const persistPreset = async () => {
    if (!draft) return
    saveDraft(draft)
    if (!onSavePreset) { setSaved('draft'); return }
    setSaving(true); setSaved('')
    try {
      const response = decodeStockScreenerPreset(await onSavePreset(draft))
      if (!response) throw new Error('invalid preset response')
      setDraft(response); clearDraft(); setSaved('saved')
    } catch (error) { setSaved(saveStatus(error)) } finally { setSaving(false) }
  }

  const stateCopy = copy.states[state]
  const StateIcon = state === 'pending' ? LoaderCircle : state === 'offline' ? CloudOff : state === 'success' ? ListFilter : AlertTriangle
  if (!payload) return <div className="page stock-screener-page">
    <header className="stock-screener-heading"><div><span>{copy.kicker}</span><h1>{copy.title}</h1><p>{copy.description}</p></div></header>
    <section className="stock-screener-state" data-state={state} role={state === 'unknown' ? 'alert' : 'status'}><StateIcon size={18} className={state === 'pending' ? 'spin' : undefined} /><span><strong>{stateCopy[0]}</strong><small>{stateCopy[1]}</small></span></section>
  </div>

  return <div className="page stock-screener-page">
    <header className="stock-screener-heading"><div><span>{copy.kicker}</span><h1>{copy.title}</h1><p>{copy.description}</p></div><div className="stock-screener-provenance"><small><Clock3 size={14} />{copy.dataTime}</small></div></header>
    <section className="stock-screener-state" data-state={state} role="status"><StateIcon size={18} className={state === 'pending' ? 'spin' : undefined} /><span><strong>{stateCopy[0]}</strong><small>{stateCopy[1]}</small></span></section>
    {state === 'empty' ? <section className="data-panel stock-screener-empty"><Search size={20} /><span>{stateCopy[1]}</span></section> : <>
      <section className="data-panel stock-screener-controls" aria-label={copy.serverPreset}>
        <div className="stock-screener-control-row"><div><span>{copy.serverPreset}</span><strong>{payload.preset}</strong></div><div className="stock-screener-draft"><label><span>{copy.presetName}</span><input value={draft?.name ?? ''} onChange={(event) => setDraft((current) => current ? { ...current, name: event.target.value } : current)} maxLength={80} /></label><button className="button tertiary" type="button" disabled={saving || !draft?.name.trim()} onClick={persistPreset}><Bookmark size={15} />{saving ? copy.saving : copy.save}</button>{saved && <small data-save-state={saved}>{copy[saved]}</small>}</div></div>
      </section>
      <section className="data-panel stock-screener-results">
        <header className="panel-heading"><div><span>SERVER-VERIFIED RESEARCH</span><h2><ListFilter size={18} />{payload.total} {copy.results}</h2></div><small>{copy.page} {payload.page} {copy.of} {pageCount}</small></header>
        {payload.items.length === 0 ? <div className="stock-screener-empty"><Search size={20} /><span>{stateCopy[1]}</span></div> : <div className="responsive-table stock-screener-table"><table><thead><tr><th>{copy.symbol}</th><th>{copy.action}</th><th>{copy.price}</th><th>{copy.change}</th><th>{copy.score}</th><th>{copy.status}</th><th>{copy.updated}</th><th>{copy.nextStep}</th></tr></thead><tbody>{payload.items.map((row) => <tr key={row.symbol}><td data-label={copy.symbol}><strong>{row.symbol}</strong><small>{row.name}</small></td><td data-label={copy.action}>{row.action}</td><td data-label={copy.price}>{row.price.toFixed(2)}</td><td data-label={copy.change}>{row.change_pct.toFixed(2)}%</td><td data-label={copy.score}>{row.score === null ? '—' : row.score.toFixed(0)}</td><td data-label={copy.status}><span className={`stock-screener-status ${row.data_state}`}>{row.data_state}</span><span className={`stock-screener-status ${row.health}`}>{row.health}</span></td><td data-label={copy.updated}>{row.hong_kong_time}</td><td data-label={copy.nextStep}><div className="stock-screener-actions"><button className="button tertiary" type="button" onClick={() => navigate(row.research_url)}>{copy.research}</button><button className="button tertiary" type="button" onClick={() => navigate(alertPrefillUrl(row.alert_prefill))}>{copy.alert}</button>{row.paper_prefill && row.actionable ? <button className="button tertiary" type="button" onClick={() => navigate(paperPrefillUrl(row.paper_prefill!))}>{copy.paper}</button> : <small>{copy.blocked}{row.blocked_reason ?? 'not_actionable'}</small>}</div></td></tr>)}</tbody></table></div>}
        <footer className="stock-screener-footer"><p><LockKeyhole size={15} />{copy.disclosure}</p><div><button className="button tertiary" type="button" disabled={payload.page <= 1 || !onPageChange} onClick={() => onPageChange?.(payload.page - 1)}><ChevronLeft size={16} />{copy.previous}</button><span>{copy.page} {payload.page} {copy.of} {pageCount}</span><button className="button tertiary" type="button" disabled={payload.page >= pageCount || !onPageChange} onClick={() => onPageChange?.(payload.page + 1)}>{copy.next}<ChevronRight size={16} /></button></div></footer>
      </section>
    </>}
  </div>
}
