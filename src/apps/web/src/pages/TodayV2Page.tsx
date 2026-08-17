import { Activity, AlertTriangle, CheckCircle2, CircleGauge, Clock3, History, ListChecks, ShieldAlert, Sparkles, WalletCards } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import { fetchSystemCycleResearchStatus, type BootstrapPayload, type RecommendationItem, type SystemCycleResearchStatus } from '../api/client'
import { fetchStrategyResearch97Aggregate, type StrategyResearch97AggregateLoad } from '../api/strategyResearch97'
import { BotMark, CicloStatusAvatar, DataSourceNote, formatMoney, formatTime, InspectorToggle, safeNumber, StockTaskBadge, V2Card, V2PageContext, V2PrimaryButton, V2StatePanel, V2StatusPill, V2SecondaryButton } from '../components/v2/V2Primitives'
import { localizeText } from '../i18n/runtime'
import { useLocale } from '../i18n/useLocale'
import { recommendationMissingLabels } from '../domain/actionContract'
import '../styles/today-discover-v2.css'
import '../styles/today.css'

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
  return code && typeof value === 'number' ? formatMoney(value, code) : '金额与币种未完整提供'
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

function Sparkline({ values, label, tone = 'info' }: { values: number[]; label: string; tone?: 'info' | 'positive' | 'warning' | 'negative' }) {
  if (values.length < 2) return <div className="today-sparkline-empty" role="img" aria-label={`${label}暂无足够真实历史数据`}><Activity size={14} aria-hidden="true" /><span>暂无历史趋势</span></div>
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = Math.max(max - min, 0.0001)
  const points = values.map((value, index) => `${4 + (index / Math.max(values.length - 1, 1)) * 92},${32 - ((value - min) / range) * 25}`).join(' ')
  return <div className={`today-sparkline is-${tone}`} role="img" aria-label={`${label}，${values.length} 个真实数据点`}><svg viewBox="0 0 100 36" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id={`today-spark-${tone}`} x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="currentColor" stopOpacity=".32" /><stop offset="1" stopColor="currentColor" stopOpacity="0" /></linearGradient></defs><polygon points={`4,34 ${points} 96,34`} fill={`url(#today-spark-${tone})`} /><polyline points={points} /></svg></div>
}

function recentArrivalTrend(items: RecommendationItem[]) {
  const day = 24 * 60 * 60 * 1000
  const today = new Date()
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime() - day * 6
  const values = Array.from({ length: 7 }, () => 0)
  items.forEach((item) => {
    const timestamp = Date.parse(item.occurred_at || item.available_at || '')
    if (!Number.isFinite(timestamp)) return
    const index = Math.floor((timestamp - start) / day)
    if (index >= 0 && index < values.length) values[index] += 1
  })
  return values
}

function performanceMetrics(data: BootstrapPayload | null) {
  const ordered = [...(data?.performance.items ?? [])].sort((a, b) => a.captured_at.localeCompare(b.captured_at))
  const latest = ordered.at(-1)
  const sameCurrency = latest ? ordered.filter((item) => item.currency === latest.currency) : []
  const equities = sameCurrency.slice(-16).map((item) => item.total_equity)
  let peak = equities[0] ?? 0
  let maxDrawdown = 0
  equities.forEach((value) => {
    peak = Math.max(peak, value)
    if (peak > 0) maxDrawdown = Math.max(maxDrawdown, ((peak - value) / peak) * 100)
  })
  const positions = data?.portfolio.positions ?? []
  const concentrations = (['USD', 'CNY'] as const).map((currency) => {
    const matching = positions.filter((item) => item.currency === currency)
    const total = matching.reduce((sum, item) => sum + Math.abs(item.market_value), 0)
    const largest = matching.reduce((max, item) => Math.max(max, Math.abs(item.market_value)), 0)
    return total > 0 ? (largest / total) * 100 : 0
  })
  const concentration = positions.length ? Math.max(...concentrations) : null
  const utilization = (['USD', 'CNY'] as const).map((currency) => {
    const exposure = positions.filter((item) => item.currency === currency).reduce((sum, item) => sum + Math.abs(item.market_value), 0)
    const limit = safeNumber(currency === 'USD' ? data?.settings.risk.max_total_position : data?.settings.risk.max_total_position_cny)
    return limit && limit > 0 ? (exposure / limit) * 100 : null
  }).filter((value): value is number => value != null)
  return { latest, equities, maxDrawdown, concentration, maxUtilization: utilization.length ? Math.max(...utilization) : null }
}

function TodayKpis({ data, mode, priorities }: { data: BootstrapPayload | null; mode: string; priorities: TodayPriority[] }) {
  const authenticated = mode === 'authenticated'
  const stocks = data?.recommendations.items.filter((item) => item.instrument_type === 'stock') ?? []
  const latestStock = [...stocks].sort((a, b) => (b.occurred_at || b.available_at || '').localeCompare(a.occurred_at || a.available_at || ''))[0]
  const complete = stocks.filter((item) => item.contract_status === 'complete' && item.state === 'official').length
  const validity = stocks.length ? (complete / stocks.length) * 100 : null
  const metrics = performanceMetrics(data)
  const marketDelay = data?.market_data.delivery_delay_minutes
  const delayTrend = data ? [data.recommendations.delivery.stock, data.recommendations.delivery.option, ...(marketDelay == null ? [] : [marketDelay])] : []
  const researchSource = data?.recommendations.source || '研究来源未提供'
  const researchUpdatedAt = latestStock?.occurred_at || latestStock?.available_at
  const waitingValue = mode === 'loading' ? '读取中' : authenticated ? String(priorities.length) : '登录后查看'
  const researchValue = mode === 'loading' ? '读取中' : !authenticated ? '登录后查看' : validity == null ? '暂无研究记录' : `${validity.toFixed(0)}%`
  const riskValue = mode === 'loading' ? '读取中' : !authenticated ? '登录后查看' : metrics.maxUtilization == null ? '暂无风险上限' : `${metrics.maxUtilization.toFixed(0)}%`
  const healthValue = mode === 'loading' ? '读取中' : !authenticated ? '待验证' : marketDelay == null ? (data?.market_data.freshness || '状态未知') : `${marketDelay.toFixed(0)}m`
  return <section className="today-kpi-grid" aria-label="今日关键指标">
    <V2Card className={`today-kpi-card ${authenticated && priorities.length ? 'is-warning' : 'is-positive'}`}><header><span><ListChecks size={15} />待处理事项</span><i className={authenticated && priorities.length ? 'is-warning' : 'is-online'} /></header><strong className={authenticated ? '' : 'is-copy'}>{waitingValue}</strong><Sparkline values={authenticated ? recentArrivalTrend(stocks) : []} label="七日工作到达趋势" tone={priorities.length ? 'warning' : 'positive'} /><footer><span>{authenticated ? `${priorities.length} 条真实工作状态` : '账户工作区尚未解锁'}</span><small>{authenticated ? `${researchSource} · ${researchUpdatedAt ? formatTime(researchUpdatedAt) : '更新时间未提供'}` : '登录后读取来源与时间'}</small></footer></V2Card>
    <V2Card className={`today-kpi-card ${validity != null && validity >= 80 ? 'is-positive' : 'is-warning'}`}><header><span><Sparkles size={15} />研究有效性</span><i className={validity == null ? 'is-waiting' : validity >= 80 ? 'is-online' : 'is-warning'} /></header><strong className={validity == null ? 'is-copy' : ''}>{researchValue}</strong><Sparkline values={authenticated ? stocks.slice(-12).map((item) => item.contract_status === 'complete' && item.state === 'official' ? 100 : 0) : []} label="近期研究完整度" tone={validity != null && validity >= 80 ? 'positive' : 'warning'} /><footer><span>{authenticated && stocks.length ? `${complete}/${stocks.length} 条资料完整` : '没有虚构研究记录'}</span><small>{authenticated ? `${researchSource} · ${researchUpdatedAt ? formatTime(researchUpdatedAt) : '更新时间未提供'}` : '登录后读取来源与时间'}</small></footer></V2Card>
    <V2Card className={`today-kpi-card ${metrics.maxUtilization != null && metrics.maxUtilization >= 80 ? 'is-negative' : metrics.maxUtilization != null && metrics.maxUtilization >= 60 ? 'is-warning' : 'is-positive'}`}><header><span><ShieldAlert size={15} />账户风险</span><i className={metrics.maxUtilization == null ? 'is-waiting' : metrics.maxUtilization >= 80 ? 'is-risk' : metrics.maxUtilization >= 60 ? 'is-warning' : 'is-online'} /></header><strong className={metrics.maxUtilization == null ? 'is-copy' : ''}>{riskValue}</strong><Sparkline values={authenticated ? metrics.equities : []} label="账户权益趋势" tone={metrics.maxDrawdown >= 5 ? 'negative' : 'positive'} /><footer><span>{metrics.latest ? `${metrics.latest.currency} 快照 · 回撤 ${metrics.maxDrawdown.toFixed(2)}%` : '暂无真实绩效快照'}</span><small>{metrics.latest ? `官方验证组合 · ${formatTime(metrics.latest.captured_at)}` : '绩效来源与时间未提供'}</small></footer></V2Card>
    <V2Card className={`today-kpi-card ${data?.market_data.freshness === '不可用' ? 'is-negative' : marketDelay ? 'is-warning' : 'is-info'}`}><header><span><CircleGauge size={15} />数据健康</span><i className={!authenticated ? 'is-waiting' : data?.market_data.freshness === '不可用' ? 'is-risk' : marketDelay ? 'is-warning' : 'is-online'} /></header><strong className={typeof marketDelay === 'number' ? '' : 'is-copy'}>{healthValue}</strong><Sparkline values={authenticated ? delayTrend : []} label="数据投递延迟" tone={marketDelay ? 'warning' : 'info'} /><footer><span>{authenticated ? (data?.market_data.freshness || '行情状态未提供') : '登录后核验真实数据源'}</span><small>{authenticated ? `${data?.market_data.display_source || '行情来源未提供'} · ${data?.market_data.observed_at ? formatTime(data.market_data.observed_at) : '观测时间未提供'}` : '登录后读取来源与时间'}</small></footer></V2Card>
  </section>
}

function priorityState(task: TodayPriority) {
  if (task.kind === 'auto-live') return { label: '阻断待处理', state: 'warning' as const }
  if (task.kind === 'telegram') return { label: '通知未就绪', state: 'partial' as const }
  if (task.item?.state === 'locked') return { label: '访问受限', state: 'locked' as const }
  if (task.item?.actionable) return { label: task.item.action || '可复核', state: 'success' as const }
  return { label: '待复核', state: 'info' as const }
}

function priorityLabel(task: TodayPriority) {
  if (task.kind === 'auto-live' || task.item?.action === 'EXIT') return 'P0 立即处理'
  if (task.item?.actionable || task.kind === 'risk' || task.item?.action === 'REDUCE') return 'P1 今日复核'
  return 'P2 排队处理'
}

function evidenceState(task: TodayPriority) {
  if (!task.item) return { complete: null, label: '状态证据', missing: [] as string[] }
  const missing = task.item.missing_fields ?? []
  const complete = task.item.contract_status === 'complete' && missing.length === 0
  return { complete, label: complete ? '证据完整' : '证据不完整', missing }
}

function evidenceCompleteness(task: TodayPriority) {
  if (!task.item) return { value: null, completed: 0, total: 0 }
  const item = task.item
  const hasValue = (value: unknown) => value !== null && value !== undefined && String(value).trim() !== ''
  const checks = [
    item.market,
    item.symbol,
    item.currency,
    item.current_price ?? item.reference_price,
    item.quote_at,
    item.stop_price,
    item.target_price,
    item.max_loss,
    item.rationale,
    item.strategy_name,
    item.strategy_version,
  ]
  const completed = checks.filter(hasValue).length
  return { value: Math.round((completed / checks.length) * 100), completed, total: checks.length }
}

function TodayActionCard({ items, authenticated, source, onOpen }: { items: TodayPriority[]; authenticated: boolean; source: string; onOpen: (task: TodayPriority) => void }) {
  const latestAt = items.map((task) => task.item?.occurred_at || task.item?.available_at || '').filter(Boolean).sort().at(-1)
  return <V2Card className="today-action-card"><header className="today-card-heading"><div><span className="v2-eyebrow">TODAY ACTION MATRIX</span><h2>今日行动矩阵</h2></div><V2StatusPill state={items.length ? 'warning' : 'success'}>{items.length ? `${items.length} 项优先` : '已清空'}</V2StatusPill></header>{items.length ? <div className="today-action-table"><div className="today-action-columns" aria-hidden="true"><span>股票 / 工作项</span><span>行动状态</span><span>当前价</span><span>失效条件</span><span>最大风险</span><span>证据摘要</span><span>操作</span></div>{items.map((task) => {
    const item = task.item
    const status = priorityState(task)
    const price = item ? safeNumber(item.current_price ?? item.reference_price) : null
    const updatedAt = item?.occurred_at || item?.available_at
    const evidence = evidenceState(task)
    const completeness = evidenceCompleteness(task)
    const missingCopy = recommendationMissingLabels(evidence.missing).join('、')
    return <article className={`today-action-row ${evidence.complete === false ? 'has-evidence-gap' : ''}`} key={task.id}>
      <div className="today-action-info">
        <header className="today-action-card-head">
          <div className="today-action-identity">{item ? <StockTaskBadge symbol={item.symbol} name={item.symbol ? undefined : '股票名称未提供'} market={marketName(item.market)} /> : <><span className={`today-task-mark is-${task.kind}`}><BotMark /></span><span><strong>{task.title}</strong><small>系统工作项</small></span></>}</div>
          <div className="today-action-badges"><span className={`today-priority-badge is-${priorityLabel(task).slice(0, 2).toLowerCase()}`}>{priorityLabel(task)}</span><V2StatusPill state={status.state}>{status.label}</V2StatusPill><span className={`today-evidence-badge is-${evidence.complete === true ? 'complete' : evidence.complete === false ? 'missing' : 'na'}`}>{evidence.label}</span></div>
        </header>
        <div className="today-action-facts">
          <div><span>当前价</span><strong>{item ? (price == null ? '暂无数据' : itemMoney(price, item.currency)) : '不涉及行情'}</strong><small>{item?.quote_at ? formatTime(item.quote_at) : item ? '报价时间：暂无数据' : '系统状态'}</small></div>
          <div><span>失效条件</span><strong>{item ? item.state === 'locked' ? '权限 / 数据门受限' : item.stop_price == null ? '暂无数据' : itemMoney(item.stop_price, item.currency) : '按工作项复核'}</strong><small>{item ? '进入研究页核对完整条件' : '由账户状态决定'}</small></div>
          <div className="is-risk"><span>最大风险</span><strong>{item?.max_loss == null ? item ? '暂无数据' : '不产生金额估算' : itemMoney(item.max_loss, item.currency)}</strong><small>{item ? '只显示服务端研究记录' : '不替用户估算'}</small></div>
          <div className={evidence.complete === false ? 'is-gap' : ''}><span>资料缺口</span><strong>{item ? missingCopy || (evidence.complete ? '无已知缺口' : '暂无数据') : '不适用'}</strong><small>{item ? `${evidence.missing.length} 个缺失字段` : '系统状态工作项'}</small></div>
        </div>
      </div>
      <div className="today-action-operation">
        <div className="today-action-evidence">
          <div className="today-evidence-progress"><span><b>证据完整度</b><strong>{completeness.value == null ? '不适用' : `${completeness.value}%`}</strong></span><i role="progressbar" aria-label="证据完整度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={completeness.value ?? undefined} aria-valuetext={completeness.value == null ? '系统状态工作项不适用证券证据进度' : `${completeness.value}%`}><b style={{ width: `${completeness.value ?? 0}%` }} /></i><small>{completeness.value == null ? '系统状态由账户工作区直接提供' : `${completeness.completed}/${completeness.total} 个核心字段已提供`}</small></div>
          <span>证据与理由</span><p>{task.detail}</p><small>{item ? `${source} · ${updatedAt ? formatTime(updatedAt) : '记录时间未提供'} · ${item.strategy_name || '策略未提供'} ${item.strategy_version || ''}` : 'Workspace 工作状态 · 状态时间未提供'}</small>
        </div>
        <footer className="today-action-cta"><div><ShieldAlert size={14} /><span>{item?.actionable ? '可进入人工复核' : '先核对资料与风险，再决定下一步'}</span></div><V2PrimaryButton onClick={() => onOpen(task)}>{task.cta}</V2PrimaryButton></footer>
      </div>
    </article>
  })}</div> : <V2StatePanel state={authenticated ? 'empty' : 'locked'} title={authenticated ? '今天没有待处理工作' : '请登录查看今日行动'} detail={authenticated ? '当前账户没有可验证的真实工作项；系统不会用演示建议填充。' : '登录后显示真实股票研究、账户和通知工作项。'} />}<footer className="today-panel-source"><DataSourceNote source={authenticated ? source : '登录后读取真实工作来源'} availableAt={latestAt} /><span>所有行动均需人工确认，AI 不会自动下单。</span></footer></V2Card>
}

function PriorityQueue({ items, authenticated, source, onOpen }: { items: TodayPriority[]; authenticated: boolean; source: string; onOpen: (task: TodayPriority) => void }) {
  const latestAt = items.map((task) => task.item?.occurred_at || task.item?.available_at || '').filter(Boolean).sort().at(-1)
  return <V2Card className="today-queue-card"><header className="today-card-heading"><div><span className="v2-eyebrow">WAITING QUEUE</span><h2>后续工作队列</h2></div><span className="v2-count-label">最多显示 5 条</span></header>{items.length ? <div className="today-queue-list">{items.slice(0, 5).map((task, index) => { const status = priorityState(task); const updatedAt = task.item?.occurred_at || task.item?.available_at; return <article key={task.id}><span className="today-queue-index">{String(index + 1).padStart(2, '0')}</span><div className="today-queue-copy"><strong>{task.item ? `${task.item.symbol || '股票代码未提供'} · ${task.item.action || '研究待确认'}` : task.title}</strong><small>{task.detail}</small><span><V2StatusPill state={status.state}>{status.label}</V2StatusPill><em>{task.item ? source : 'Workspace'} · {updatedAt ? formatTime(updatedAt) : '状态时间未提供'}</em></span></div><V2SecondaryButton onClick={() => onOpen(task)}>查看</V2SecondaryButton></article> })}</div> : <V2StatePanel state={authenticated ? 'empty' : 'locked'} title={authenticated ? '暂无更多待办' : '队列尚未解锁'} detail={authenticated ? '新的真实工作状态出现后会显示在这里。' : '登录后读取账户工作队列。'} />}<footer className="today-panel-source"><DataSourceNote source={authenticated ? source : '登录后读取账户工作队列'} availableAt={latestAt} /><span>{items.length ? `${Math.min(items.length, 5)} 条等待人工复核` : '当前没有真实队列项'}</span></footer></V2Card>
}

function ContinueWorkCard({ latest, personalSeason, authenticated, source, onResearch, onPaper, onDiscover }: { latest: RecommendationItem | null; personalSeason: string | null; authenticated: boolean; source: string; onResearch: (item: RecommendationItem) => void; onPaper: () => void; onDiscover: () => void }) {
  const latestAt = latest?.occurred_at || latest?.available_at
  return <V2Card className="today-continue-card"><header className="today-card-heading"><div><span className="v2-eyebrow">RESUME CONTEXT</span><h2>继续上次工作</h2></div><History size={16} aria-hidden="true" /></header>{latest ? <div className="today-resume-row"><StockTaskBadge symbol={latest.symbol} name={latest.symbol ? undefined : '股票名称未提供'} market={marketName(latest.market)} /><div><strong>{latest.strategy_name || '策略名称未提供'}</strong><small>{latestAt ? `最近记录 ${formatTime(latestAt)}` : '记录时间未提供'} · {recommendationSummary(latest)}</small></div><V2PrimaryButton onClick={() => onResearch(latest)}>继续股票研究</V2PrimaryButton></div> : personalSeason ? <div className="today-resume-row"><span className="today-task-mark is-personal-paper"><WalletCards size={16} /></span><div><strong>个人模拟季</strong><small>个人模拟在独立账户页读取，不混入官方验证持仓。</small></div><V2PrimaryButton onClick={onPaper}>打开个人模拟</V2PrimaryButton></div> : <V2StatePanel state={authenticated ? 'empty' : 'locked'} title={authenticated ? '没有可恢复的工作上下文' : '工作上下文尚未解锁'} detail={authenticated ? '当前没有真实研究记录或个人模拟季引用。' : '登录后读取最近的真实研究上下文。'} action={authenticated ? <V2SecondaryButton onClick={onDiscover}>去发现股票</V2SecondaryButton> : undefined} />}<footer className="today-panel-source"><DataSourceNote source={latest ? source : personalSeason ? '本机个人模拟季引用' : authenticated ? '当前账户工作上下文' : '登录后读取真实工作上下文'} availableAt={latestAt} /><span>{latest ? '研究上下文可继续' : personalSeason ? '个人模拟账户域独立' : '没有伪造恢复记录'}</span></footer></V2Card>
}

function AccountRiskCard({ data, authenticated, onPortfolio, onPaper }: { data: BootstrapPayload | null; authenticated: boolean; onPortfolio: () => void; onPaper: () => void }) {
  const metrics = performanceMetrics(data)
  if (!authenticated) return <V2Card className="today-risk-card"><header className="today-card-heading"><div><span className="v2-eyebrow">ACCOUNT RISK</span><h2>账户与持仓风险</h2></div><WalletCards size={16} /></header><V2StatePanel state="locked" title="需要登录查看" detail="演示状态不展示真实账户资产、回撤或持仓集中度。" /></V2Card>
  if (!metrics.latest) return <V2Card className="today-risk-card"><header className="today-card-heading"><div><span className="v2-eyebrow">ACCOUNT RISK</span><h2>账户与持仓风险</h2></div><WalletCards size={16} /></header><V2StatePanel state="empty" title="暂无账户绩效快照" detail="Workspace 尚未提供可用的真实资产与回撤记录。" action={<V2SecondaryButton onClick={onPortfolio}>查看官方组合</V2SecondaryButton>} /></V2Card>
  const riskState = metrics.maxDrawdown >= 10 || (metrics.concentration ?? 0) >= 50 ? 'warning' : 'success'
  return <V2Card className="today-risk-card"><header className="today-card-heading"><div><span className="v2-eyebrow">ACCOUNT RISK</span><h2>账户与持仓风险</h2></div><V2StatusPill state={riskState}>{riskState === 'warning' ? '需要复核' : '范围内'}</V2StatusPill></header><div className="today-account-asset"><span>账户资产</span><strong>{formatMoney(metrics.latest.total_equity, currencyCode(metrics.latest.currency) || 'USD')}</strong><small>{formatTime(metrics.latest.captured_at)} · {metrics.latest.currency} · 官方验证域</small></div><div className="today-risk-metrics"><div><span>最大回撤</span><strong className={metrics.maxDrawdown >= 5 ? 'is-negative' : ''}>{metrics.maxDrawdown.toFixed(2)}%</strong><i><b style={{ width: `${Math.min(metrics.maxDrawdown, 100)}%` }} /></i></div><div><span>持仓集中度</span><strong className={(metrics.concentration ?? 0) >= 50 ? 'is-negative' : ''}>{metrics.concentration == null ? '暂无持仓数据' : `${metrics.concentration.toFixed(1)}%`}</strong><i><b style={{ width: `${Math.min(metrics.concentration ?? 0, 100)}%` }} /></i></div><div><span>风险上限占用</span><strong>{metrics.maxUtilization == null ? '风险上限未提供' : `${metrics.maxUtilization.toFixed(1)}%`}</strong><i><b style={{ width: `${Math.min(metrics.maxUtilization ?? 0, 100)}%` }} /></i></div></div><footer className="today-card-footer"><div className="today-card-source"><i className={riskState === 'warning' ? 'is-warning' : 'is-online'} /><span>{data?.portfolio.account_mode || '账户模式未提供'} · {data?.portfolio.scope || '账户范围未提供'}</span><small>官方验证组合 · {formatTime(metrics.latest.captured_at)}</small></div><div><V2SecondaryButton onClick={onPortfolio}>官方组合</V2SecondaryButton><V2SecondaryButton onClick={onPaper}>个人模拟</V2SecondaryButton></div></footer></V2Card>
}

type ResearchHealth = { loading: boolean; stable: SystemCycleResearchStatus | null; expanded: StrategyResearch97AggregateLoad | null; stableError: boolean }

function useResearchHealth(authenticated: boolean) {
  const [health, setHealth] = useState<ResearchHealth>({ loading: false, stable: null, expanded: null, stableError: false })
  useEffect(() => {
    if (!authenticated) {
      setHealth({ loading: false, stable: null, expanded: null, stableError: false })
      return
    }
    let active = true
    setHealth({ loading: true, stable: null, expanded: null, stableError: false })
    void Promise.allSettled([fetchSystemCycleResearchStatus(), fetchStrategyResearch97Aggregate()]).then(([stable, expanded]) => {
      if (!active) return
      setHealth({ loading: false, stable: stable.status === 'fulfilled' ? stable.value : null, expanded: expanded.status === 'fulfilled' ? expanded.value : null, stableError: stable.status === 'rejected' })
    })
    return () => { active = false }
  }, [authenticated])
  return health
}

function DataHealthCard({ data, authenticated }: { data: BootstrapPayload | null; authenticated: boolean }) {
  const { formatLocale } = useLocale()
  const health = useResearchHealth(authenticated)
  const stable = health.stable
  const expanded = health.expanded?.phase === 'ready' ? health.expanded.data?.status : null
  const modelVersion = data?.recommendations.items.find((item) => item.strategy_version)?.strategy_version
  const telegram = data?.telegram
  const marketDelay = data?.market_data.delivery_delay_minutes
  const expandedLabel = health.loading ? '正在读取真实状态' : expanded ? `${expanded.coverage_count}/97 覆盖 · ${expanded.no_data_count} 缺资料` : health.expanded?.forbidden ? '当前权限不可查看' : health.expanded?.phase === 'partial' ? '部分研究资源不可用' : '研究链状态不可用'
  return <V2Card className="today-health-card"><header className="today-card-heading"><div><span className="v2-eyebrow">SYSTEM HEALTH</span><h2>数据、模型与自动化健康</h2></div><CircleGauge size={16} /></header>{!authenticated ? <V2StatePanel state="locked" title="真实状态不可见" detail="登录后才读取行情、研究链、模型与通知状态。" /> : <div className="today-health-grid">
    <article><i className={data?.market_data.freshness === '不可用' ? 'is-risk' : marketDelay ? 'is-warning' : 'is-online'} /><span>行情延迟</span><strong>{marketDelay == null ? (data?.market_data.freshness || '延迟未提供') : formatDeliveryDelay(marketDelay, formatLocale)}</strong><small>{data?.market_data.display_source || '行情来源未提供'}</small></article>
    <article><i className={stable?.available ? 'is-online' : health.loading ? 'is-waiting' : 'is-risk'} /><span>13 股稳定链</span><strong>{health.loading ? '正在读取真实状态' : stable?.available ? `${stable.coverage_count}/${stable.stock_count} 覆盖 · ${stable.no_data_count} 缺资料` : health.stableError ? '研究链读取失败' : '研究链状态不可用'}</strong><small>{stable?.last_result_at ? `最近结果 ${formatTime(stable.last_result_at)}` : '暂无新鲜结果时间'}</small></article>
    <article><i className={expanded?.available ? expanded.state === 'healthy' ? 'is-online' : 'is-warning' : health.loading ? 'is-waiting' : 'is-risk'} /><span>97 只股票扩容链</span><strong>{expandedLabel}</strong><small>{expanded?.last_result_at ? `最近结果 ${formatTime(expanded.last_result_at)}` : '暂无新鲜结果时间'}</small></article>
    <article><i className={modelVersion ? 'is-online' : 'is-waiting'} /><span>模型版本</span><strong>{modelVersion || '模型版本未提供'}</strong><small>{data?.recommendations.source || '研究来源未提供'}</small></article>
    <article><i className={telegram?.bound && telegram.verified && telegram.consented ? 'is-online' : 'is-warning'} /><span>通知状态</span><strong>{telegram?.bound && telegram.verified && telegram.consented ? 'Telegram 已启用' : 'Telegram 尚未就绪'}</strong><small>{telegram?.updated_at ? `最近更新 ${formatTime(telegram.updated_at)}` : '通知更新时间未提供'}</small></article>
  </div>}<footer className="today-health-footer"><div><span>建议投递延迟（股票/期权）</span><strong>股票 {formatDeliveryDelay(data?.recommendations.delivery.stock, formatLocale)} · 期权 {formatDeliveryDelay(data?.recommendations.delivery.option, formatLocale)}</strong></div><DataSourceNote source={data?.market_data.display_source || data?.recommendations.source || '系统健康来源未提供'} availableAt={data?.market_data.observed_at || stable?.last_result_at || expanded?.last_result_at || undefined} /><small>{data?.execution_control.effective_opening_paused ? '新开仓已暂停，等待人工复核' : 'AI 仅辅助研究，不自动下单'}</small></footer></V2Card>
}

function WeekTrajectory({ data, priorities, authenticated }: { data: BootstrapPayload | null; priorities: TodayPriority[]; authenticated: boolean }) {
  const todayKey = new Date().toDateString()
  const processed = data?.portfolio.activity?.executions.filter((item) => new Date(item.executed_at).toDateString() === todayKey).length ?? 0
  const recommendations = data?.recommendations.items ?? []
  const started = recommendations.length
  const inProgress = recommendations.filter((item) => item.state === 'official' && item.contract_status === 'complete' && item.actionable).length
  const blocked = (data?.execution_control.block_reasons.length ?? 0) + recommendations.filter((item) => item.state === 'locked').length
  const failed = recommendations.filter((item) => item.contract_status === 'incomplete').length
  const nodes = [
    { key: 'started', label: '开始', value: started, detail: started ? `${started} 条研究记录已进入流程` : '本周尚无研究记录', tone: 'info', Icon: Clock3 },
    { key: 'running', label: '进行中', value: inProgress, detail: inProgress ? `${inProgress} 条可继续人工复核` : '暂无进行中研究', tone: 'running', Icon: Activity },
    { key: 'blocked', label: '阻塞', value: blocked, detail: blocked ? `${blocked} 项受权限或执行门阻断` : '当前未见阻断', tone: blocked ? 'warning' : 'positive', Icon: ShieldAlert },
    { key: 'completed', label: '完成', value: processed, detail: processed ? `${processed} 条真实执行回执` : '今日暂无执行回执', tone: 'positive', Icon: CheckCircle2 },
    { key: 'failed', label: '失败', value: failed, detail: failed ? `${failed} 条资料契约未完成` : '未见资料校验失败', tone: failed ? 'negative' : 'positive', Icon: AlertTriangle },
    { key: 'pending', label: '待确认', value: priorities.length, detail: priorities.length ? `${priorities.length} 项等待人工确认` : '待确认队列已清空', tone: 'info', Icon: ListChecks },
  ]
  return <V2Card className="today-trajectory-card"><header className="today-card-heading"><div><span className="v2-eyebrow">WEEKLY WORK TRACE</span><h2>本周工作轨迹</h2></div><span className="today-heading-note"><Clock3 size={14} />开始 → 进行中 → 阻塞 → 完成 → 失败 → 待确认</span></header>{authenticated ? <ol className="today-trajectory">{nodes.map((node) => <li className={`is-${node.tone} ${node.value > 0 ? 'has-count' : 'is-empty'} ${node.key === 'completed' ? 'is-completed' : ''}`} key={node.key}><div className="today-trajectory-marker"><node.Icon aria-hidden="true" /><strong aria-label={`${node.value} 项`}>{node.value}</strong></div><span>{node.label}</span><small>{node.detail}</small></li>)}</ol> : <V2StatePanel state="locked" title="工作轨迹尚未解锁" detail="登录后聚合开始、进行中、阻塞、完成、失败与待确认记录。" />}<footer><DataSourceNote source="Workspace、官方验证组合与研究记录" availableAt={data?.market_data.observed_at} /><span>研究与风险信息只供人工决策，不会自动产生订单。</span></footer></V2Card>
}

export function TodayV2Page() {
  const workspace = useWorkspace()
  const navigate = useNavigate()
  const [inspectorOpen, setInspectorOpen] = useState(() => typeof window === 'undefined' || window.matchMedia('(min-width: 1071px)').matches)
  const priorities = useMemo(() => buildTodayPriorities(workspace.data), [workspace.data])
  const actionItems = useMemo(() => {
    const urgent = priorities.find((task) => task.kind === 'auto-live')
    const research = priorities.filter((task) => task.item)
    const selected = [...(urgent ? [urgent] : []), ...research]
    return (selected.length ? selected : priorities.slice(0, 1)).filter((task, index, items) => items.findIndex((candidate) => candidate.id === task.id) === index).slice(0, 4)
  }, [priorities])
  const actionIds = new Set(actionItems.map((task) => task.id))
  const queuedItems = priorities.filter((task) => !actionIds.has(task.id)).slice(0, 5)
  const latestStock = useMemo(() => [...(workspace.data?.recommendations.items ?? [])].filter((item) => item.instrument_type === 'stock').sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))[0] ?? null, [workspace.data])
  const personalSeason = readPersonalPaperSeasonReference()
  const observedAt = workspace.data?.market_data.observed_at
  const marketFreshness = workspace.data?.market_data.freshness
  const researchSource = workspace.data?.recommendations.source || '研究来源未提供'
  const locked = workspace.mode !== 'authenticated'
  const authenticated = !locked
  const openResearch = (item: RecommendationItem) => {
    if (!item.symbol?.trim() || !item.market?.trim() || !Number.isSafeInteger(item.event_id) || item.event_id <= 0) return
    navigate(`/research?market=${encodeURIComponent(item.market)}&symbol=${encodeURIComponent(item.symbol)}&event_id=${item.event_id}`)
  }
  const openPriority = (task: TodayPriority) => {
    if (task.item) return openResearch(task.item)
    if (task.route === '/paper') return navigate('/paper?market=US')
    if (task.route === '/portfolio') return navigate('/portfolio?market=US')
    if (task.route === '/research') return navigate('/research?market=US')
    return navigate(task.route)
  }
  return <div className="v2-page today-v2-page">
    <header className="today-heading"><div className="today-heading-copy"><span className="v2-eyebrow">TODAY / CONTROL DESK</span><h1>今天先处理什么</h1><p>把真实股票工作、账户风险、研究链与数据状态融汇成一张高密度行动桌面。</p></div><div className="today-heading-meta"><V2StatusPill state={authenticated ? 'success' : 'info'}>{authenticated ? '真实工作区' : '安全只读状态'}</V2StatusPill><CicloStatusAvatar size="sm" label="Ciclo AI 状态助手" /><V2SecondaryButton onClick={() => navigate('/recommendations')}>新手推荐</V2SecondaryButton><InspectorToggle open={inspectorOpen} onClick={() => setInspectorOpen((value) => !value)} label="打开风险面板" /></div></header>
    <V2PageContext task="今日优先级" account="研究域 / 官方验证只读" market="美股与 A股" freshness={marketFreshness} observedAt={observedAt} detail={workspace.data?.market_data.detail} />
    <TodayKpis data={workspace.data} mode={workspace.mode} priorities={priorities} />
    <TodayActionCard items={actionItems} authenticated={authenticated} source={researchSource} onOpen={openPriority} />
    <div className="v2-layout today-dashboard">
      <main className="v2-main-column today-main-column"><PriorityQueue items={queuedItems} authenticated={authenticated} source={researchSource} onOpen={openPriority} /><ContinueWorkCard latest={latestStock} personalSeason={personalSeason} authenticated={authenticated} source={researchSource} onResearch={openResearch} onPaper={() => navigate('/paper?market=US')} onDiscover={() => navigate('/discover')} /></main>
      <aside className={`v2-inspector today-status-rail ${inspectorOpen ? 'is-open' : ''}`} aria-label="账户风险与系统健康"><AccountRiskCard data={workspace.data} authenticated={authenticated} onPortfolio={() => navigate('/portfolio?market=US')} onPaper={() => navigate('/paper?market=US')} /><DataHealthCard data={workspace.data} authenticated={authenticated} /></aside>
    </div>
    <WeekTrajectory data={workspace.data} priorities={priorities} authenticated={authenticated} />
  </div>
}

export default TodayV2Page
