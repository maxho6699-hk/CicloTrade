import { CalendarDays, Filter, GitCompareArrows, ListFilter, RotateCcw } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import type { RecommendationItem } from '../api/client'
import { BotMark, DataSourceNote, EvidenceSummary, formatMoney, formatTime, InspectorToggle, RemoteMiniCandles, safeNumber, SearchField, StockTaskBadge, V2Card, V2Freshness, V2PageContext, V2PrimaryButton, V2SectionHeader, V2StatePanel, V2StatusPill, V2SecondaryButton } from '../components/v2/V2Primitives'
import '../styles/today-discover-v2.css'

type DiscoverView = '候选股票' | '事件发现' | '研究覆盖'

function marketName(market?: string) {
  if (market === 'CN' || market === 'A股') return 'A股'
  if (market === 'HK' || market === '港股') return '港股'
  if (market === 'US' || market === '美股') return '美股'
  return '市场未提供'
}

function currencyCode(currency?: string): 'USD' | 'CNY' | 'HKD' | null {
  return currency === 'USD' || currency === 'CNY' || currency === 'HKD' ? currency : null
}

function itemMoney(value: number | null | undefined, currency?: string) {
  const code = currencyCode(currency)
  return code ? formatMoney(value, code) : '价格或币种未提供'
}

function quoteLabel(item: RecommendationItem) {
  const value = safeNumber(item.current_price ?? item.reference_price)
  return value == null ? '报价不可用' : itemMoney(value, item.currency)
}

function actionLabel(item: RecommendationItem) {
  if (item.state === 'locked') return '受限'
  if (item.action === 'REDUCE' || item.action === 'EXIT') return '风险复核'
  return item.action || '研究中'
}

function CandidateRow({ item, authenticated, selected, onSelect }: { item: RecommendationItem; authenticated: boolean; selected: boolean; onSelect: () => void }) {
  return <tr className={`v2-candidate-row ${selected ? 'is-selected' : ''}`} aria-selected={selected}>
    <td><button className="v2-symbol-button" type="button" onClick={onSelect}><StockTaskBadge symbol={item.symbol} name={item.symbol ? undefined : '股票名称未提供'} market={marketName(item.market)} /></button></td>
    <td><div className="v2-price-cell"><strong>{quoteLabel(item)}</strong><small>{item.quote_at ? `报价 ${formatTime(item.quote_at)}` : '报价时间未提供'}</small></div></td>
    <td>{item.symbol && item.market ? <RemoteMiniCandles symbol={item.symbol} authenticated={authenticated} label={`${item.symbol} Mini K线`} /> : <span>缺少市场上下文</span>}</td>
    <td><V2StatusPill state={item.state === 'locked' ? 'locked' : item.contract_status === 'incomplete' ? 'partial' : 'info'}>{actionLabel(item)}</V2StatusPill></td>
    <td><div className="v2-candidate-evidence"><span>支持 / 反向</span><strong>— / —</strong><small>服务端评分未提供</small></div></td>
    <td><V2Freshness freshness={item.quote_at ? '已提供时间' : '缺少时间'} observedAt={item.quote_at || item.available_at || undefined} /></td>
  </tr>
}

function DiscoverInspector({ item, authenticated, onResearch, onPaper, onAlert }: { item: RecommendationItem | null; authenticated: boolean; onResearch: () => void; onPaper: () => void; onAlert: () => void }) {
  if (!item) return <V2Card><div className="v2-discover-inspector"><div className="v2-inspector-label"><strong>筛选条件</strong><Filter size={15} aria-hidden="true" /></div><div className="v2-inspector-list"><div><span>研究链</span><strong>稳定与扩展</strong></div><div><span>研究状态</span><strong>全部真实记录</strong></div><div><span>事件窗口</span><strong>未指定</strong></div><div><span>数据状态</span><strong>不补造行情</strong></div></div><V2StatePanel state="empty" title="尚未选择股票" detail="从候选列表选择一只股票，右侧会显示摘要与唯一研究入口。" /></div></V2Card>
  const price = safeNumber(item.current_price ?? item.reference_price)
  return <V2Card><div className="v2-discover-inspector"><div className="v2-inspector-stock"><StockTaskBadge symbol={item.symbol} name={item.symbol ? undefined : '股票名称未提供'} market={marketName(item.market)} /><div className="v2-inspector-stock-price"><strong>{price == null ? '—' : itemMoney(price, item.currency)}</strong><V2StatusPill state={item.state === 'locked' ? 'locked' : 'info'}>{item.state === 'locked' ? '受限' : '研究中'}</V2StatusPill></div></div><RemoteMiniCandles symbol={item.symbol} authenticated={authenticated} label={`${item.symbol || '股票'} 摘要 Mini K线`} /><EvidenceSummary evidence={{ unknownCount: item.contract_status === 'incomplete' ? (item.missing_fields?.length || 1) : undefined, status: item.state === 'locked' ? 'locked' : '服务端评分未返回' }} /><DataSourceNote source="候选股票记录" availableAt={item.available_at} recordedAt={item.occurred_at} /><div className="v2-discover-actions"><V2PrimaryButton onClick={onResearch}>进入股票研究</V2PrimaryButton><V2SecondaryButton onClick={onPaper}>创建个人模拟预填</V2SecondaryButton><button className="v2-button v2-button-tertiary" type="button" onClick={onAlert}>建立预警草稿</button></div></div></V2Card>
}

function DiscoverFooter({ count }: { count: number }) {
  return <div className="v2-search-footer"><span>{count ? `显示 ${count} 条真实股票记录` : '暂无真实股票记录'}</span><span>选择股票后只刷新右侧摘要，不自动跳页</span></div>
}

export function DiscoverV2Page() {
  const workspace = useWorkspace()
  const navigate = useNavigate()
  const [inspectorOpen, setInspectorOpen] = useState(() => typeof window === 'undefined' || window.matchMedia('(min-width: 1071px)').matches)
  const [searchParams, setSearchParams] = useSearchParams()
  const query = searchParams.get('q') ?? ''
  const marketParam = searchParams.get('market')
  const market: '全部' | '美股' | 'A股' = marketParam === '美股' || marketParam === 'A股' ? marketParam : '全部'
  const viewParam = searchParams.get('view')
  const view: DiscoverView = viewParam === '事件发现' || viewParam === '研究覆盖' ? viewParam : '候选股票'
  const selectedParam = Number(searchParams.get('selected'))
  const selectedId = Number.isSafeInteger(selectedParam) && selectedParam > 0 ? selectedParam : null
  const pageParam = Number(searchParams.get('page'))
  const page = Number.isSafeInteger(pageParam) && pageParam > 0 ? pageParam : 1
  const pageSize = 20
  const updateParams = (updates: Record<string, string | null>) => {
    const next = new URLSearchParams(searchParams)
    Object.entries(updates).forEach(([key, value]) => value === null ? next.delete(key) : next.set(key, value))
    setSearchParams(next)
  }
  const records = useMemo(() => (workspace.data?.recommendations.items ?? []).filter((item) => item.instrument_type === 'stock'), [workspace.data])
  const filtered = useMemo(() => records.filter((item) => {
    const matchesQuery = !query.trim() || item.symbol?.toLowerCase().includes(query.trim().toLowerCase())
    const matchesMarket = market === '全部' || marketName(item.market) === market
    return matchesQuery && matchesMarket
  }), [market, query, records])
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize))
  const currentPage = Math.min(page, pageCount)
  const visibleRecords = filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize)
  const selected = filtered.find((item) => item.event_id === selectedId) ?? null
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
  const isAuth = workspace.mode === 'authenticated'
  const tableContent = workspace.mode === 'loading' ? <V2StatePanel state="loading" title="正在读取股票候选" detail="候选记录、研究状态与数据时效正在同步。" /> : !isAuth ? <V2StatePanel state={workspace.mode === 'offline' ? 'offline' : 'locked'} title={workspace.mode === 'offline' ? '候选池暂时不可用' : '请登录查看真实候选'} detail={workspace.error || '演示状态不展示虚构股票列表。'} /> : view !== '候选股票' ? <V2StatePanel state="locked" title={`${view}暂不可用`} detail="当前 Workspace 只提供候选股票记录；该视图等待对应真实 API DTO。" /> : filtered.length ? <div className="v2-candidate-table"><table><thead><tr><th>股票</th><th>价格状态</th><th>Mini K线</th><th>研究状态</th><th>证据完整度</th><th>数据新鲜度</th></tr></thead><tbody>{visibleRecords.map((item) => <CandidateRow key={item.event_id} item={item} authenticated={isAuth} selected={selected?.event_id === item.event_id} onSelect={() => updateParams({ selected: String(item.event_id) })} />)}</tbody></table><div className="v2-pagination"><button className="v2-button v2-button-secondary v2-button-small" type="button" disabled={currentPage <= 1} onClick={() => updateParams({ page: String(currentPage - 1) })}>上一页</button><span>第 {currentPage} / {pageCount} 页</span><button className="v2-button v2-button-secondary v2-button-small" type="button" disabled={currentPage >= pageCount} onClick={() => updateParams({ page: String(currentPage + 1) })}>下一页</button></div></div> : <div className="v2-discover-empty"><V2StatePanel state="empty" title="当前没有匹配的股票记录" detail="调整市场或搜索条件；系统不会用演示数据填充候选池。" /></div>
  return <div className="v2-page discover-v2-page"><div className="v2-page-top"><div className="v2-page-top-copy"><span className="v2-eyebrow">DISCOVER / STOCK SCOPE</span><h1>发现下一只值得研究的股票</h1><p>在真实候选记录中搜索、筛选和比较；发现页不直接给出交易结论，也不完成模拟订单。</p></div><div className="v2-page-top-meta"><V2StatusPill state={isAuth ? 'success' : 'info'}>{isAuth ? '候选池已连接' : '安全只读状态'}</V2StatusPill><BotMark /><InspectorToggle open={inspectorOpen} onClick={() => setInspectorOpen((value) => !value)} label="打开股票摘要" /></div></div><V2PageContext task="候选股票范围" account="研究域" market={market === '全部' ? '全部市场' : market} freshness={workspace.data?.market_data.freshness} observedAt={workspace.data?.market_data.observed_at} detail={workspace.data?.market_data.detail} /><div className="v2-layout"><main className="v2-main-column"><V2Card><div className="v2-filter-bar"><SearchField value={query} onChange={(value) => updateParams({ q: value || null, page: '1', selected: null })} /><select className="v2-filter-select" value={market} onChange={(event) => updateParams({ market: event.target.value === '全部' ? null : event.target.value, page: '1', selected: null })} aria-label="市场"><option>全部</option><option>美股</option><option>A股</option></select><button className="v2-button v2-button-secondary v2-button-small" type="button" onClick={() => updateParams({ q: null, market: null, page: null, selected: null })}><RotateCcw size={14} /> 清除</button></div><div className="v2-view-tabs" role="tablist" aria-label="候选视图">{(['候选股票', '事件发现', '研究覆盖'] as DiscoverView[]).map((item) => <button key={item} className={view === item ? 'is-active' : ''} role="tab" aria-selected={view === item} type="button" onClick={() => updateParams({ view: item === '候选股票' ? null : item, page: '1', selected: null })}>{item}</button>)}</div>{tableContent}<DiscoverFooter count={isAuth && view === '候选股票' ? visibleRecords.length : 0} /></V2Card><V2Card><V2SectionHeader eyebrow="COVERAGE STATUS" title="研究覆盖状态" action={<span className="v2-count-label"><ListFilter size={13} /> {view}</span>} /><div className="v2-metric-strip v2-coverage-strip"><div className="v2-metric"><span>已扫描</span><strong>—</strong></div><div className="v2-metric"><span>已有结果</span><strong>{isAuth ? records.length : '—'}</strong></div><div className="v2-metric"><span>有效</span><strong>{isAuth ? records.filter((item) => item.state === 'official').length : '—'}</strong></div><div className="v2-metric"><span>缺资料</span><strong>{isAuth ? records.filter((item) => item.contract_status === 'incomplete').length : '—'}</strong></div></div></V2Card></main><aside className={`v2-inspector ${inspectorOpen ? 'is-open' : ''}`} aria-label="股票筛选与摘要"><DiscoverInspector item={selected} authenticated={isAuth} onResearch={() => research(selected)} onPaper={() => paper(selected)} onAlert={() => alertDraft(selected)} /><V2Card><div className="v2-inspector-section"><div className="v2-inspector-label"><strong>发现页规则</strong><GitCompareArrows size={15} aria-hidden="true" /></div><div className="v2-inspector-list"><div><span>唯一主入口</span><strong>进入股票研究</strong></div><div><span>研究结论</span><strong>不在此页生成</strong></div><div><span>模拟交易</span><strong>仅传安全预填</strong></div><div><span>事件视图</span><strong><CalendarDays size={13} /> 真实记录后开放</strong></div></div></div></V2Card></aside></div></div>
}

export default DiscoverV2Page
