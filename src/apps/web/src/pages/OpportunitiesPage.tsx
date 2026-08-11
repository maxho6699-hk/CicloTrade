import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  CandlestickChart,
  Clock3,
  ChevronDown,
  ChevronLeft,
  Layers3,
  LockKeyhole,
  ShieldAlert,
  Sparkles,
} from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import { PageHeader } from '../components/PageHeader'
import { WatchlistToggle } from '../components/WatchlistToggle'
import { SegmentedControl } from '../components/ui/SegmentedControl'
import { getFormatLocale } from '../i18n/runtime'
import type { Market } from '../types'
import { assessRecommendationContract } from '../domain/actionContract'

type OpportunityKind = 'stock' | 'short' | 'option'
type OpportunityFilter = 'all' | OpportunityKind
type OpportunityContract = 'execute' | 'wait' | 'manage'

interface OpportunityPreview {
  id: string
  symbol: string
  underlying: string
  name: string
  market: Market
  kind: OpportunityKind
  action: string
  actionTone: 'positive' | 'negative' | 'neutral'
  contract: OpportunityContract
  priceRange: string
  quantityLine: string
  riskLine: string
  targetLine: string
  reason: string
  verification: string
  hasEvidence: boolean
  eventId?: number
  quoteLine?: string
  updatedAt: string
  sortValue?: string
  demo: boolean
}

const demoOpportunities: OpportunityPreview[] = [
  { id: 'demo-aapl-long', symbol: 'AAPL', underlying: 'AAPL', name: 'Apple', market: 'US', kind: 'stock', action: '界面演示 · 不可交易', actionTone: 'neutral', contract: 'wait', priceRange: '示例关注 210.00–214.00', quantityLine: '现在不买、不卖 · 数量 0', riskLine: '203.00', targetLine: '228.00 先锁定部分利润', reason: '这只是信息层级演示，没有真实当前报价。', verification: '演示记录没有真实回测运行 ID，不展示虚假胜率。', hasEvidence: false, quoteLine: '没有真实报价时间', updatedAt: '14:32', demo: true },
  { id: 'demo-nvda-wait', symbol: 'NVDA', underlying: 'NVDA', name: 'NVIDIA', market: 'US', kind: 'stock', action: '暂不买卖 · 等待回落确认', actionTone: 'neutral', contract: 'wait', priceRange: '176.00–179.00', quantityLine: '未触发前为 0', riskLine: '触发后再生成', targetLine: '触发后再生成', reason: '趋势仍强，当前位置追价的风险偏高。', verification: '等待条件未发生，不能生成行动回测结论。', hasEvidence: false, updatedAt: '14:18', demo: true },
  { id: 'demo-tsla-short', symbol: 'TSLA', underlying: 'TSLA', name: 'Tesla', market: 'US', kind: 'short', action: '暂不做空 · 等待跌破', actionTone: 'neutral', contract: 'wait', priceRange: '跌破 312.00', quantityLine: '未触发前为 0', riskLine: '326.00', targetLine: '触发后按波动重新计算', reason: '波动扩大，只有跌破触发位才进入做空观察。', verification: '尚未触发，不能当作立即做空建议。', hasEvidence: false, updatedAt: '13:54', demo: true },
  { id: 'demo-msft-long', symbol: 'MSFT', underlying: 'MSFT', name: 'Microsoft', market: 'US', kind: 'stock', action: '暂不买卖 · 等待站稳', actionTone: 'neutral', contract: 'wait', priceRange: '448.00–452.00', quantityLine: '未触发前为 0', riskLine: '439.00', targetLine: '站稳后再计算', reason: '价格接近压力位，先等站稳再决定。', verification: '等待条件没有发生。', hasEvidence: false, updatedAt: '13:20', demo: true },
  { id: 'demo-meta-long', symbol: 'META', underlying: 'META', name: 'Meta Platforms', market: 'US', kind: 'stock', action: '界面演示 · 不可交易', actionTone: 'neutral', contract: 'wait', priceRange: '示例关注 618.00–624.00', quantityLine: '现在不买、不卖 · 数量 0', riskLine: '602.00', targetLine: '650.00 附近分批止盈', reason: '这只是信息层级演示，没有真实当前报价。', verification: '演示记录不伪造回测成绩。', hasEvidence: false, quoteLine: '没有真实报价时间', updatedAt: '12:48', demo: true },
  { id: 'demo-amzn-short', symbol: 'AMZN', underlying: 'AMZN', name: 'Amazon', market: 'US', kind: 'short', action: '暂不做空 · 等反弹失败', actionTone: 'neutral', contract: 'wait', priceRange: '反弹受阻 226.00', quantityLine: '未触发前为 0', riskLine: '232.50', targetLine: '触发后再计算', reason: '当前不是立即卖出，等待反弹失败的证据。', verification: '等待触发，不是可执行合同。', hasEvidence: false, updatedAt: '12:15', demo: true },
  { id: 'demo-aapl-call', symbol: 'AAPL 2026-09-18 C220', underlying: 'AAPL', name: 'Apple 看涨期权', market: 'US', kind: 'option', action: '暂不买入 · 等待 IV 回落', actionTone: 'neutral', contract: 'wait', priceRange: '权利金待报价', quantityLine: '未取得 Bid / Ask 前为 0', riskLine: '最大亏损为权利金', targetLine: '报价接入后计算', reason: '方向偏多，但隐含波动率过高时不追价。', verification: '真实期权链与回测结果只对专业会员显示。', hasEvidence: false, updatedAt: '11:42', demo: true },
  { id: 'demo-tsla-put', symbol: 'TSLA 2026-09-18 P300', underlying: 'TSLA', name: 'Tesla 看跌期权', market: 'US', kind: 'option', action: '暂不买入 · 等正股跌破', actionTone: 'neutral', contract: 'wait', priceRange: '标的跌破 312.00', quantityLine: '未触发前为 0', riskLine: '到期与 IV 风险', targetLine: '触发后计算组合退出', reason: '期权不是替代止损，先等正股触发条件。', verification: '等待条件没有发生。', hasEvidence: false, updatedAt: '11:08', demo: true },
  { id: 'demo-nvda-spread', symbol: 'NVDA 180/170 PUT SPREAD', underlying: 'NVDA', name: 'NVIDIA 看跌价差', market: 'US', kind: 'option', action: '仅管理已有持仓保护', actionTone: 'neutral', contract: 'manage', priceRange: '组合报价待接入', quantityLine: '仅按已有正股仓位配比', riskLine: '限定最大亏损', targetLine: '按保护目标退出', reason: '用于持仓保护研究，不是单独的方向性推荐。', verification: '没有已有持仓时不可建立此保护合同。', hasEvidence: false, updatedAt: '10:36', demo: true },
]

function formatOpportunityTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat(getFormatLocale(), { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false, timeZoneName: 'short' }).format(date)
}

const filterOptions = [
  { value: 'all', label: '全部机会', icon: <Layers3 size={15} /> },
  { value: 'stock', label: '正股机会', icon: <ArrowUpRight size={15} /> },
  { value: 'short', label: '做空方向', icon: <ArrowDownRight size={15} /> },
  { value: 'option', label: '期权研究', icon: <Sparkles size={15} /> },
] satisfies Array<{ value: OpportunityFilter; label: string; icon: ReactNode }>

const contractGroups: Array<{ key: OpportunityContract; title: string; description: string }> = [
  { key: 'execute', title: '推荐入场 · 可以核对执行', description: '只收录方向、价格、数量与风险字段完整的买入或做空行动。' },
  { key: 'wait', title: '等待机会 · 暂不买卖', description: '条件尚未发生；可以设置预警，但不能把它当成立即行动。' },
  { key: 'manage', title: '仅管理已有持仓', description: '减仓、退出、平空或保护组合只适用于已有仓位，不用于新开仓。' },
]

export function OpportunitiesPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const workspace = useWorkspace()
  const requestedFilter = searchParams.get('type')
  const filter: OpportunityFilter = requestedFilter === 'stock' || requestedFilter === 'short' || requestedFilter === 'option' ? requestedFilter : 'all'
  const [showAllMobile, setShowAllMobile] = useState(false)
  const [watchBusy, setWatchBusy] = useState('')
  const demoMode = workspace.mode === 'demo' || workspace.mode === 'offline'
  const capabilities = workspace.data?.membership.capabilities ?? []
  const hasOptionResearch = ['option_chain', 'option_quote_chart', 'option_greeks', 'option_iv', 'option_strategy', 'option_strategy_multi_leg'].every((capability) => capabilities.includes(capability))
  const optionLocked = filter === 'option' && !hasOptionResearch
  const evidenceEventIds = useMemo(() => new Set((workspace.data?.portfolio.activity?.executions ?? []).flatMap((execution) => {
    const match = execution.execution_id.match(/^QE-(\d+)-/)
    return match ? [Number(match[1])] : []
  })), [workspace.data?.portfolio.activity?.executions])
  const realItems = useMemo<OpportunityPreview[]>(() => (workspace.data?.recommendations.items ?? [])
    .filter((item) => item.state === 'official' && item.symbol)
    .map((item) => {
      const shortRelated = item.position_action?.includes('short') || item.position_action === 'reverse_to_short'
      const kind: OpportunityKind = item.instrument_type === 'option' ? 'option' : shortRelated ? 'short' : 'stock'
      const currency = item.currency === 'CNY' ? 'CNY' : 'USD'
      const assessment = assessRecommendationContract(item)
      const incomplete = !assessment.actionable
      const isCover = item.action === 'COVER'
      const isShort = item.action === 'SHORT'
      const contract: OpportunityContract = incomplete
        ? 'wait'
        : item.action === 'BUY' || item.action === 'SHORT'
          ? 'execute'
          : 'manage'
      const quantity = Math.abs(Number(item.quantity_hint ?? item.quantity_delta ?? 0))
      return {
        id: `official-${item.event_id}`,
        symbol: item.symbol!,
        underlying: item.symbol!,
        name: item.instrument_type === 'option' ? '期权研究记录' : item.strategy_name,
        market: item.market === 'CN' || item.market === 'A股' ? 'CN' : 'US',
        kind,
        action: incomplete ? '现在不买、不卖 · 数量 0' : isShort ? '卖出做空' : isCover ? '仅处理已有空头 · 买入平空' : item.action === 'BUY' ? '买入 / 增持' : item.action === 'REDUCE' ? '仅处理已有多头 · 减仓' : item.action === 'EXIT' ? '仅处理已有持仓 · 退出' : '现在不买、不卖 · 数量 0',
        actionTone: incomplete ? 'neutral' : item.action === 'BUY' || isCover ? 'positive' : item.action === 'REDUCE' || item.action === 'EXIT' || isShort ? 'negative' : 'neutral',
        contract,
        priceRange: item.reference_price == null ? '事件参考价未记录' : `事件参考 ${currency} ${Number(item.reference_price).toFixed(2)}`,
        quantityLine: contract === 'wait' ? `现在不买、不卖 · 数量 0 ${item.instrument_type === 'option' ? '张' : '股'}` : quantity > 0 ? `${Number.isInteger(quantity) ? quantity.toFixed(0) : quantity.toFixed(2)} ${item.instrument_type === 'option' ? '张' : '股'}` : contract === 'manage' ? '以现有持仓为上限' : '缺少数量 · 暂不可执行',
        riskLine: item.stop_price == null ? `缺少 ${(item.missing_fields ?? []).slice(0, 3).join(' / ') || '风险字段'}` : `${currency} ${Number(item.stop_price).toFixed(2)}`,
        targetLine: item.target_price == null ? '未记录 · 不可猜测' : `${currency} ${Number(item.target_price).toFixed(2)}`,
        reason: item.rationale || '这条记录缺少白话理由，只能查看原始证据，不能作为行动建议。',
        verification: `策略 ${item.strategy_name} · 版本 ${item.strategy_version}。只有绑定真实回测运行 ID 后才显示历史成绩。`,
        hasEvidence: evidenceEventIds.has(item.event_id),
        eventId: item.event_id,
        quoteLine: assessment.quoteFreshness === 'fresh'
          ? `${currency} ${assessment.price.toFixed(2)} · ${formatOpportunityTime(String(assessment.quoteAt))}`
          : assessment.blockReason,
        updatedAt: formatOpportunityTime(item.occurred_at),
        sortValue: item.occurred_at,
        demo: false,
      }
    }), [evidenceEventIds, workspace.data?.recommendations.items])
  const sourceItems = useMemo(() => realItems.length ? realItems : demoMode ? demoOpportunities.map((item) => item.contract === 'wait' ? { ...item, quantityLine: `现在不买、不卖 · 数量 0 ${item.kind === 'option' ? '张' : '股'}` } : item) : [], [demoMode, realItems])
  const shownItems = useMemo(() => optionLocked ? [] : sourceItems
    .filter((item) => hasOptionResearch || item.kind !== 'option')
    .filter((item) => filter === 'all' || item.kind === filter)
    .sort((a, b) => (b.sortValue ?? b.updatedAt).localeCompare(a.sortValue ?? a.updatedAt)), [filter, hasOptionResearch, optionLocked, sourceItems])
  const selectedCandidate = sourceItems.find((item) => item.id === searchParams.get('id'))
  const selectedOpportunity = selectedCandidate?.kind === 'option' && !hasOptionResearch ? undefined : selectedCandidate
  const groupedItems = contractGroups.map((group) => ({
    ...group,
    items: shownItems.filter((item) => item.contract === group.key),
  })).filter((group) => group.items.length > 0)
  const watchlists = workspace.data?.settings.watchlists ?? { us: [], a_share: [] }

  const changeWatchlist = async (item: OpportunityPreview, remove: boolean) => {
    setWatchBusy(item.underlying)
    try {
      await workspace.changeWatchlist(item.market, item.underlying, remove)
    } finally {
      setWatchBusy('')
    }
  }

  if (selectedCandidate?.kind === 'option' && !hasOptionResearch) {
    return (
      <div className="page opportunity-detail-page">
        <button className="button tertiary opportunity-back" type="button" onClick={() => { const next = new URLSearchParams(searchParams); next.delete('id'); next.set('type', 'option'); setSearchParams(next) }}><ChevronLeft size={16} />返回机会中心</button>
        <section className="opportunity-option-lock data-panel"><LockKeyhole size={28} /><div><span>PROFESSIONAL OPTIONS RESEARCH</span><h2>期权研究只对专业会员开放</h2><p>当前页面不会显示合约代码、Call / Put、执行价、报价或策略信息。升级专业会员后，仅显示经过授权的真实数据来源。</p></div><button className="button primary" type="button" onClick={() => navigate('/membership')}>查看专业会员权益</button></section>
      </div>
    )
  }

  if (selectedOpportunity) {
    const saved = (selectedOpportunity.market === 'CN' ? watchlists.a_share : watchlists.us).includes(selectedOpportunity.underlying)
    const contractGroup = contractGroups.find((group) => group.key === selectedOpportunity.contract)!
    return (
      <div className="page opportunity-detail-page">
        <button className="button tertiary opportunity-back" type="button" onClick={() => { const next = new URLSearchParams(searchParams); next.delete('id'); setSearchParams(next) }}><ChevronLeft size={16} />返回机会中心</button>
        <section className={`opportunity-detail-hero ${selectedOpportunity.contract}`}>
          <header><span>{contractGroup.title}</span><small><Clock3 size={13} /> {selectedOpportunity.updatedAt}</small></header>
          <div><span><strong>{selectedOpportunity.symbol}</strong><small>{selectedOpportunity.name} · {selectedOpportunity.market === 'US' ? '美股' : 'A股'}</small></span><b>{selectedOpportunity.action}</b></div>
          <p>{selectedOpportunity.reason}</p>
        </section>
        <div className="opportunity-detail-layout">
          <main className="opportunity-thesis data-panel">
            <header className="panel-heading"><div><span>PLAIN-LANGUAGE CONTRACT</span><h2>最简单的行动说明</h2></div><ShieldAlert size={19} /></header>
            <dl><div><dt>现在能不能交易</dt><dd>{selectedOpportunity.demo ? '不可以，这是界面演示' : selectedOpportunity.contract === 'execute' ? '可以先核对下单前条件' : selectedOpportunity.contract === 'wait' ? '不可以；现在不买、不卖' : '只有已经持仓的人才处理'}</dd></div><div><dt>当前报价与时间</dt><dd>{selectedOpportunity.quoteLine || '没有可验证的当前报价时间'}</dd></div><div><dt>事件参考 / 触发条件</dt><dd>{selectedOpportunity.priceRange}</dd></div><div><dt>数量</dt><dd>{selectedOpportunity.quantityLine}</dd></div><div><dt>止损 / 风险线</dt><dd>{selectedOpportunity.riskLine}</dd></div><div><dt>可能到哪里止盈</dt><dd>{selectedOpportunity.targetLine}</dd></div><div><dt>为什么</dt><dd>{selectedOpportunity.reason}</dd></div></dl>
            <section className="opportunity-analysis-block"><h3>策略论证</h3><p>{selectedOpportunity.verification}</p><ul><li>触发条件未完成时，页面固定显示“暂不买卖”。</li><li>止盈、止损和数量缺失时，不允许把记录标成可执行。</li><li>期权必须同时核对 Bid、Ask、价差、IV、成交量、未平仓量与到期风险。</li></ul></section>
          </main>
          <aside className="opportunity-proof data-panel">
            <header className="panel-heading"><div><span>OFFICIAL PROOF</span><h2>官方模拟验证</h2></div><CandlestickChart size={19} /></header>
            {selectedOpportunity.hasEvidence ? <><p>该正式事件已在 CicloTrade 官方模拟账户产生关联成交，可以进入 K 线查看买卖标记和盈利/亏损区间。</p><button className="button primary wide" type="button" onClick={() => navigate(`/markets?market=${selectedOpportunity.market}&symbol=${encodeURIComponent(selectedOpportunity.underlying)}&event_id=${selectedOpportunity.eventId ?? ''}&panel=建议`)}>检视 K 线证据 <ArrowRight size={15} /></button></> : <div className="opportunity-no-proof"><ShieldAlert size={21} /><strong>还没有可检视的 K 线证据</strong><span>只有官方模拟账户已经执行、并能关联正式事件的标的才显示证据按钮。</span></div>}
            {workspace.mode === 'authenticated' && <WatchlistToggle className="wide" symbol={selectedOpportunity.underlying} saved={saved} busy={watchBusy === selectedOpportunity.underlying} variant="label" onToggle={(remove) => changeWatchlist(selectedOpportunity, remove)} />}
          </aside>
        </div>
      </div>
    )
  }

  return (
    <div className="page opportunities-page">
      <PageHeader kicker="OPPORTUNITY CENTER" title="机会中心" description="先分清推荐入场、等待机会和仅持仓处理；打开卡片查看完整论证，只有官方已执行事件才提供 K 线证据。" />
      <section className="opportunity-toolbar" aria-label="机会筛选">
        <SegmentedControl ariaLabel="机会类型" value={filter} options={filterOptions} onChange={(value) => { const next = new URLSearchParams(searchParams); if (value === 'all') next.delete('type'); else next.set('type', value); setSearchParams(next); setShowAllMobile(false) }} />
        <span className="opportunity-order-note">按正式记录时间排列</span>
      </section>
      {demoMode && <div className="opportunity-demo-notice"><ShieldAlert size={17} /><span><strong>当前为界面演示</strong><small>以下卡片用于展示机会中心的信息层级，不是真实行情或交易建议。</small></span></div>}
      {filter === 'short' && <div className="opportunity-demo-notice"><ShieldAlert size={17} /><span><strong>建立空头 = 卖出做空</strong><small>这不是卖出现有持仓。只有触发条件出现，并确认券商保证金、可借券与账户权限后才考虑执行。</small></span></div>}
      {optionLocked && <section className="opportunity-option-lock data-panel"><LockKeyhole size={28} /><div><span>PROFESSIONAL OPTIONS RESEARCH</span><h2>期权研究只对专业会员开放</h2><p>期权链、期权报价 K 线、Bid / Ask、价差、Greeks、IV、成交量、未平仓量，以及单腿与多腿组合内容都会保持隐藏，不泄露合约字段。</p></div><button className="button primary" type="button" onClick={() => navigate('/membership')}>查看专业会员权益</button></section>}
      {!optionLocked && shownItems.length ? groupedItems.map((group) => (
        <section className={`opportunity-contract-section ${group.key}`} key={group.key} aria-labelledby={`opportunity-${group.key}`}>
          <header><div><span>{group.key === 'execute' ? 'ACTIONABLE' : group.key === 'wait' ? 'WAITING' : 'POSITION ONLY'}</span><h2 id={`opportunity-${group.key}`}>{group.title}</h2><p>{group.description}</p></div><strong>{group.items.length}</strong></header>
          <div className={`opportunity-grid ${showAllMobile ? 'show-all-mobile' : ''}`}>
            {group.items.map((item) => {
              const saved = (item.market === 'CN' ? watchlists.a_share : watchlists.us).includes(item.underlying)
              const mobileIndex = shownItems.findIndex((candidate) => candidate.id === item.id)
              return (
                <article className={`opportunity-card ${item.actionTone} ${mobileIndex >= 4 ? 'mobile-hidden' : ''}`} key={item.id}>
                  <button className="opportunity-card-main" type="button" onClick={() => { const next = new URLSearchParams(searchParams); next.set('id', item.id); setSearchParams(next) }}>
                    <header><span className={`opportunity-kind ${item.kind}`}>{item.kind === 'stock' ? '正股' : item.kind === 'short' ? '做空' : '期权'}</span><small><Clock3 size={12} /> {item.updatedAt}</small></header>
                    <div className="opportunity-symbol"><span><strong>{item.symbol}</strong><small>{item.name} · {item.market === 'US' ? '美股' : 'A股'}</small></span><CandlestickChart size={19} /></div>
                    <div className="opportunity-action"><span>{item.contract === 'execute' ? '当前行动' : item.contract === 'wait' ? '当前状态' : '持仓处理'}</span><strong>{item.action}</strong></div>
                    <dl><div><dt>关注 / 触发</dt><dd>{item.priceRange}</dd></div><div><dt>数量</dt><dd>{item.quantityLine}</dd></div></dl>
                    <p>{item.reason}</p>
                    <footer><span>{item.demo ? '界面演示' : '官方记录'}</span><strong>查看研究论证 <ArrowRight size={14} /></strong></footer>
                  </button>
                  {workspace.mode === 'authenticated' && <WatchlistToggle className="opportunity-watch" symbol={item.underlying} saved={saved} busy={watchBusy === item.underlying} variant="label" onToggle={(remove) => changeWatchlist(item, remove)} />}
                </article>
              )
            })}
          </div>
        </section>
      )) : !optionLocked && <div className="opportunity-empty"><ShieldAlert size={22} /><div><h2>当前没有符合条件的机会</h2><p>登录账户不会用演示记录补位。新的正式记录通过风控与发布审核后才会出现。</p></div></div>}
      {shownItems.length > 4 && <button className="button tertiary opportunity-mobile-more" type="button" aria-expanded={showAllMobile} onClick={() => setShowAllMobile((current) => !current)}>{showAllMobile ? '收起机会' : `展开其余 ${shownItems.length - 4} 条`}<ChevronDown size={15} /></button>}
    </div>
  )
}
