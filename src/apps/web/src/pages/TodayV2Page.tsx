import { Bell, CircleGauge, WalletCards } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import type { BootstrapPayload, RecommendationItem } from '../api/client'
import { BotMark, CicloStatusAvatar, DataSourceNote, formatMoney, formatTime, InspectorToggle, RemoteMiniCandles, safeNumber, StockTaskBadge, V2Card, V2Freshness, V2PageContext, V2PrimaryButton, V2SectionHeader, V2StatePanel, V2StatusPill, V2SecondaryButton } from '../components/v2/V2Primitives'
import { localizeText } from '../i18n/runtime'
import { useLocale } from '../i18n/useLocale'
import '../styles/today-discover-v2.css'

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

function formatDeliveryDelay(value: number | null | undefined, formatLocale: string) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return localizeText('未提供')
  return `${new Intl.NumberFormat(formatLocale, { maximumFractionDigits: 0 }).format(value)} ${localizeText('分钟')}`
}

function recommendationSummary(item: RecommendationItem) {
  if (item.rationale) return item.rationale
  if (item.state === 'locked') return '这条研究记录目前受权限或数据门限制，不能作为行动建议。'
  if (item.action === 'REDUCE' || item.action === 'EXIT') return '系统记录了需要复核的持仓或风险事项，请进入股票研究查看完整证据。'
  return '正式研究记录已同步，进入股票研究查看来源、反向证据、风险和失效条件。'
}

function actionText(item: RecommendationItem) {
  if (item.state === 'locked') return '查看受限原因'
  if (item.action === 'REDUCE' || item.action === 'EXIT') return '复核股票风险'
  return '继续股票研究'
}

function DecisionFocus({ item, authenticated, onResearch }: { item: RecommendationItem; authenticated: boolean; onResearch: () => void }) {
  const price = safeNumber(item.current_price ?? item.reference_price)
  const currency = currencyCode(item.currency)
  const quoteState = item.quote_at ? '已提供时间' : '报价时间未提供'
  return <V2Card className="v2-focus-card">
    <div className="v2-focus-head"><div className="v2-focus-head-main"><CicloStatusAvatar /><div><span className="v2-eyebrow">PRIMARY WORK ITEM</span><h2>{item.symbol || '股票代码未提供'}</h2><div className="v2-focus-meta"><V2StatusPill state={item.state === 'locked' ? 'locked' : 'info'}>{item.state === 'locked' ? '受限记录' : '研究待继续'}</V2StatusPill><span className="v2-bot-label"><BotMark /> AI 协助</span></div></div></div><StockTaskBadge symbol={item.symbol} name={item.symbol ? undefined : '股票名称未提供'} market={marketName(item.market)} /></div>
    <div className="v2-focus-grid"><div className="v2-focus-price"><span className="v2-eyebrow">CURRENT QUOTE</span><strong className={price == null || !currency ? 'v2-price-unavailable' : ''}>{price == null || !currency ? '价格或币种未提供' : itemMoney(price, currency)}</strong><small>{quoteState} · {item.available_at ? formatTime(item.available_at) : 'available 时间未提供'}</small></div><div className="v2-focus-summary"><p>{recommendationSummary(item)}</p><div className="v2-metric-strip"><div className="v2-metric"><span>研究版本</span><strong>{item.strategy_version || '未提供'}</strong></div><div className="v2-metric"><span>状态</span><strong>{item.state === 'locked' ? 'locked' : item.action || '待确认'}</strong></div><div className="v2-metric"><span>风险线</span><strong>{item.stop_price == null || !currency ? '未提供' : itemMoney(item.stop_price, currency)}</strong></div><div className="v2-metric"><span>失效条件</span><strong>{item.contract_status === 'incomplete' ? '资料不完整' : '进入研究页核对'}</strong></div></div></div></div>
    <RemoteMiniCandles symbol={item.symbol} authenticated={authenticated} label="首要股票 Mini K线" />
    <DataSourceNote source="推荐记录与行情由服务端提供" availableAt={item.available_at} recordedAt={item.occurred_at} />
    <div className="v2-cta-row"><V2PrimaryButton onClick={onResearch}>{actionText(item)}</V2PrimaryButton></div>
  </V2Card>
}

type TodayPriorityKind = 'auto-live' | 'recommendation' | 'telegram' | 'portfolio' | 'risk' | 'personal-paper'
type TodayPriority = { id: string; kind: TodayPriorityKind; title: string; detail: string; cta: string; route: string; item?: RecommendationItem }

function readPersonalPaperSeasonReference() {
  if (typeof window === 'undefined') return null
  try {
    const value = window.localStorage.getItem('ciclotrade.personalPaper.activeSeason.v1')
    return value?.trim() || null
  } catch {
    return null
  }
}

function buildTodayPriorities(data: BootstrapPayload | null): TodayPriority[] {
  if (!data) return []
  const priorities: TodayPriority[] = []
  const execution = data.execution_control
  if (execution.block_reasons.length) {
    priorities.push({ id: 'auto-live-block', kind: 'auto-live', title: '处理自动实盘阻断', detail: localizeText(execution.block_reasons[0] || '自动实盘当前有未满足条件。'), cta: '查看自动实盘控制', route: '/trade' })
  }
  const stocks = data.recommendations.items.filter((item) => item.instrument_type === 'stock')
  const recommendation = stocks.find((item) => item.actionable) ?? stocks[0]
  if (recommendation) {
    priorities.push({ id: `recommendation-${recommendation.event_id}`, kind: 'recommendation', title: recommendation.symbol || '股票研究记录', detail: recommendationSummary(recommendation), cta: actionText(recommendation), route: '/research', item: recommendation })
  }
  const telegram = data.telegram
  if (!telegram.bound || !telegram.verified || !telegram.consented) {
    priorities.push({ id: 'telegram-not-ready', kind: 'telegram', title: '完成 Telegram 通知准备', detail: 'Telegram 尚未完成绑定、验证或用户同意，通知状态需要在通知中心处理。', cta: '前往通知中心', route: '/notifications' })
  }
  const portfolio = data.portfolio
  const hasValidationData = portfolio.account_mode === 'official'
    && portfolio.scope === 'ciclotrade_system_validation'
    && (portfolio.positions.length > 0 || portfolio.orders.length > 0 || portfolio.activity?.executions.length || Object.values(portfolio.accounts).some((account) => account.status === 'recorded'))
  if (hasValidationData) {
    priorities.push({ id: 'official-portfolio-review', kind: 'portfolio', title: '复核官方验证组合', detail: '官方验证账户存在可追溯的持仓、订单或账户快照，进入组合页复核，不与个人模拟账户混用。', cta: '查看官方组合', route: '/portfolio' })
  }
  const hasRiskSettings = Object.values(data.settings.risk).some((value) => typeof value === 'number' && Number.isFinite(value))
  if (hasRiskSettings) {
    priorities.push({ id: 'risk-settings', kind: 'risk', title: '检查风险设置', detail: '风险上限来自当前账户设置，进入账户页确认股票仓位与单日亏损边界。', cta: '查看风险设置', route: '/account' })
  }
  if (readPersonalPaperSeasonReference()) {
    priorities.push({ id: 'personal-paper-season', kind: 'personal-paper', title: '继续个人模拟季', detail: '本机存在个人模拟季引用；个人模拟账户独立维护 USD 10,000 资金域。', cta: '打开个人模拟', route: '/paper' })
  }
  return priorities
}

function PriorityFocus({ task, onOpen }: { task: TodayPriority; onOpen: () => void }) {
  const state = task.kind === 'auto-live' ? 'warning' : task.kind === 'telegram' ? 'partial' : 'info'
  return <V2Card className="v2-focus-card"><div className="v2-focus-head"><div className="v2-focus-head-main"><CicloStatusAvatar /><div><span className="v2-eyebrow">PRIMARY WORK ITEM</span><h2>{task.title}</h2><div className="v2-focus-meta"><V2StatusPill state={state}>{task.kind === 'auto-live' ? '需要处理' : '待处理'}</V2StatusPill><span className="v2-bot-label"><BotMark /> AI 协助</span></div></div></div></div><div className="v2-focus-summary v2-priority-focus-summary"><p>{task.detail}</p><DataSourceNote source="今日工作区真实状态" /><div className="v2-cta-row"><V2PrimaryButton onClick={onOpen}>{task.cta}</V2PrimaryButton></div></div></V2Card>
}

function PriorityQueue({ items, onOpen }: { items: TodayPriority[]; onOpen: (task: TodayPriority) => void }) {
  return <V2Card><V2SectionHeader eyebrow="WAITING QUEUE" title="后续工作" action={<span className="v2-count-label">最多显示 5 条</span>} /><div className="v2-task-list">{items.length ? items.slice(0, 5).map((task) => <div className="v2-task-item" key={task.id}><div><strong>{task.item ? `${task.item.symbol || '股票代码未提供'} · ${task.item.action || '研究待确认'}` : task.title}</strong><small>{task.detail}</small></div><V2SecondaryButton onClick={() => onOpen(task)}>查看</V2SecondaryButton></div>) : <V2StatePanel state="empty" title="暂无后续工作" detail="新的真实工作状态出现后会显示在这里。" />}</div></V2Card>
}

function AccountRiskInspector() {
  const workspace = useWorkspace()
  const navigate = useNavigate()
  const data = workspace.data
  const positions = data?.portfolio.positions ?? []
  const usdExposure = positions.filter((item) => item.currency === 'USD').reduce((sum, item) => sum + Math.abs(item.market_value), 0)
  const cnyExposure = positions.filter((item) => item.currency === 'CNY').reduce((sum, item) => sum + Math.abs(item.market_value), 0)
  const usdLimit = safeNumber(data?.settings.risk.max_total_position) ?? null
  const cnyLimit = safeNumber(data?.settings.risk.max_total_position_cny) ?? null
  const rows = [{ currency: 'USD', exposure: usdExposure, limit: usdLimit }, { currency: 'CNY', exposure: cnyExposure, limit: cnyLimit }]
  return <V2Card><div className="v2-inspector-section"><div className="v2-inspector-label"><strong>账户与持仓风险</strong><WalletCards size={15} aria-hidden="true" /></div>{workspace.mode !== 'authenticated' ? <V2StatePanel state={workspace.mode === 'loading' ? 'loading' : 'offline'} title={workspace.mode === 'loading' ? '正在读取风险' : '需要登录查看'} detail={workspace.mode === 'loading' ? '账户域与持仓快照正在同步。' : '演示状态不展示真实账户风险。'} /> : <div><div className="v2-account-domain"><span>账户域</span><strong>{data?.portfolio.account_mode || 'account_mode 未提供'} · {data?.portfolio.scope || 'scope 未提供'}</strong></div>{rows.map((row) => { const ratio = row.limit && row.limit > 0 ? Math.min(1, row.exposure / row.limit) : null; return <div className="v2-risk-line" key={row.currency}><header><span>{row.currency} 账户占用</span><strong>{formatMoney(row.exposure, row.currency)}</strong></header><div className="v2-risk-meter"><i style={{ width: ratio == null ? '0%' : `${ratio * 100}%` }} /></div><small>{row.limit == null ? '上限未提供，不能计算占用比例' : `上限 ${formatMoney(row.limit, row.currency)}`}</small></div> })}<div className="v2-account-domain"><span>个人模拟</span><small>个人模拟在独立账户页读取，不混入官方验证持仓。</small><V2SecondaryButton onClick={() => navigate('/paper')}>查看个人模拟账户</V2SecondaryButton></div></div>}</div></V2Card>
}

function DataHealthInspector() {
  const workspace = useWorkspace()
  const { formatLocale } = useLocale()
  const data = workspace.data
  const execution = data?.execution_control
  const market = data?.market_data
  return <V2Card><div className="v2-inspector-section"><div className="v2-inspector-label"><strong>数据、模型与自动化</strong><CircleGauge size={15} aria-hidden="true" /></div>{workspace.mode !== 'authenticated' ? <V2StatePanel state="locked" title="真实状态不可见" detail="登录后才读取行情、研究链和自动实盘只读状态。" /> : <div className="v2-inspector-list"><div><span>行情</span><strong><V2Freshness freshness={market?.freshness} observedAt={market?.observed_at} detail={market?.detail} /></strong></div><div><span>研究来源</span><strong>{data?.recommendations.source || '未提供'}</strong></div><div><span>建议投递延迟（股票/期权）</span><strong>股票 {formatDeliveryDelay(data?.recommendations.delivery?.stock, formatLocale)} · 期权 {formatDeliveryDelay(data?.recommendations.delivery?.option, formatLocale)}</strong></div><div><span>自动实盘</span><strong>{execution?.auto_trading_service_enabled ? execution.effective_opening_paused ? '已暂停' : '只读运行摘要' : '未启用'}</strong></div>{execution?.block_reasons?.length ? <div><span>暂停原因</span><strong>{localizeText(execution.block_reasons[0])}</strong></div> : null}</div>}</div></V2Card>
}

function NotificationInspector() {
  const workspace = useWorkspace()
  const telegram = workspace.data?.telegram
  return <V2Card><div className="v2-inspector-section"><div className="v2-inspector-label"><strong>通知状态</strong><Bell size={15} aria-hidden="true" /></div>{workspace.mode !== 'authenticated' ? <V2StatePanel state="locked" title="通知状态未读取" detail="Today 只显示摘要；绑定和偏好设置在通知中心处理。" /> : <div className="v2-lifecycle"><Bell size={15} aria-hidden="true" /><span><strong>{telegram?.consented ? 'Telegram 已启用' : 'Telegram 未启用'}</strong><small>{telegram?.updated_at ? `最近更新 ${formatTime(telegram.updated_at)}` : '没有真实更新时间'}</small></span></div>}</div></V2Card>
}

export function TodayV2Page() {
  const workspace = useWorkspace()
  const navigate = useNavigate()
  const [inspectorOpen, setInspectorOpen] = useState(() => typeof window === 'undefined' || window.matchMedia('(min-width: 1071px)').matches)
  const priorities = useMemo(() => buildTodayPriorities(workspace.data), [workspace.data])
  const primary = priorities[0] ?? null
  const observedAt = workspace.data?.market_data.observed_at
  const marketFreshness = workspace.data?.market_data.freshness
  const openResearch = (item: RecommendationItem) => navigate(`/research?symbol=${encodeURIComponent(item.symbol || '')}&event_id=${item.event_id}`)
  const openPriority = (task: TodayPriority) => task.item ? openResearch(task.item) : navigate(task.route)
  const content = workspace.mode === 'loading' ? <V2Card><V2StatePanel state="loading" title="正在准备今日工作台" detail="账户、研究任务和风险摘要正在同步。" /></V2Card> : workspace.mode !== 'authenticated' ? <V2Card><V2StatePanel state={workspace.mode === 'offline' ? 'offline' : 'locked'} title={workspace.mode === 'offline' ? '数据服务暂时不可用' : '请登录查看今日工作'} detail={workspace.error || '演示模式不使用虚构股票数据；登录后显示你的真实工作项。'} /></V2Card> : primary ? primary.item ? <DecisionFocus item={primary.item} authenticated onResearch={() => openResearch(primary.item as RecommendationItem)} /> : <PriorityFocus task={primary} onOpen={() => openPriority(primary)} /> : <V2Card><V2StatePanel state="empty" title="今天没有待处理工作" detail="当前账户没有可验证的真实任务；系统不会用演示建议填充登录账户。" action={<V2SecondaryButton onClick={() => navigate('/discover')}>去发现股票</V2SecondaryButton>} /></V2Card>
  return <div className="v2-page today-v2-page"><div className="v2-page-top"><div className="v2-page-top-copy"><span className="v2-eyebrow">TODAY / CONTROL DESK</span><h1>今天先处理什么</h1><p>把需要复核的股票工作、账户风险和数据状态集中到一个安全入口。</p></div><div className="v2-page-top-meta"><V2StatusPill state={workspace.mode === 'authenticated' ? 'success' : 'info'}>{workspace.mode === 'authenticated' ? '真实工作区' : '安全只读状态'}</V2StatusPill><CicloStatusAvatar size="sm" label="Ciclo AI 状态助手" /><InspectorToggle open={inspectorOpen} onClick={() => setInspectorOpen((value) => !value)} label="打开风险面板" /></div></div><V2PageContext task="今日优先级" account="研究域 / 官方验证只读" market="美股与 A股" freshness={marketFreshness} observedAt={observedAt} detail={workspace.data?.market_data.detail} /><div className="v2-layout"><main className="v2-main-column">{content}{workspace.mode === 'authenticated' && <PriorityQueue items={priorities.slice(1)} onOpen={openPriority} />}</main><aside className={`v2-inspector ${inspectorOpen ? 'is-open' : ''}`} aria-label="今日风险与健康"><AccountRiskInspector /><DataHealthInspector /><NotificationInspector /></aside></div></div>
}

export default TodayV2Page
