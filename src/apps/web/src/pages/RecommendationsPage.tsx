import { AlertTriangle, ArrowRight, BookOpenCheck, CheckCircle2, Clock3, ShieldCheck, Sparkles } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import type { RecommendationItem } from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { StockLogo } from '../components/StockLogo'
import { formatMoney, formatTime } from '../components/v2/V2Primitives'
import '../styles/recommendations.css'

type RecommendationView = 'stock' | 'option'

const ACTION_LABELS: Record<string, string> = {
  BUY: '关注买入机会', REDUCE: '考虑减仓', EXIT: '考虑退出', SHORT: '关注做空风险', COVER: '考虑回补',
}

function money(value: number | null | undefined, currency?: string) {
  return typeof value === 'number' && Number.isFinite(value) ? formatMoney(value, currency || 'USD') : '未提供'
}

function instrumentExplanation(item: RecommendationItem) {
  if (item.instrument_type === 'stock') return '正股代表公司的直接权益，价格涨跌会直接影响持仓价值。新手应先理解公司、价格和最大可承受风险。'
  const right = item.option_right === 'CALL' ? 'Call 看涨期权' : item.option_right === 'PUT' ? 'Put 看跌期权' : '期权合约'
  return `${right}有到期日、行权价与时间价值；方向判断正确也可能因时间衰减或波动率变化而亏损。`
}

function RecommendationCard({ item, onResearch, onPractice }: { item: RecommendationItem; onResearch: () => void; onPractice: () => void }) {
  const missing = item.missing_fields ?? []
  const complete = item.contract_status === 'complete' && missing.length === 0
  const observedAt = item.occurred_at || item.available_at
  return <article className={`beginner-recommendation-card ${complete ? 'is-complete' : 'is-incomplete'}`}>
    <header><div className="beginner-recommendation-identity"><StockLogo symbol={item.symbol} size="lg" /><span><small>{item.instrument_type === 'stock' ? 'STOCK IDEA' : `${item.option_right || 'OPTION'} IDEA`}</small><strong>{item.symbol || '股票代码未提供'}</strong><em>{item.strategy_name || '策略名称未提供'} · {item.strategy_version || '版本未提供'}</em></span></div><div className="beginner-recommendation-status"><span className={`recommendation-action is-${(item.action || 'wait').toLowerCase()}`}>{ACTION_LABELS[item.action || ''] || '等待确认'}</span><span className={complete ? 'is-complete' : 'is-warning'}>{complete ? <CheckCircle2 /> : <AlertTriangle />}{complete ? '资料完整' : `缺 ${missing.length || '—'} 项资料`}</span></div></header>
    <section className="beginner-explanation"><BookOpenCheck /><div><span>0 基础说明</span><p>{instrumentExplanation(item)}</p></div></section>
    <div className="beginner-recommendation-facts">
      <div><span>参考 / 当前价</span><strong>{money(item.reference_price, item.currency)} / {money(item.current_price, item.currency)}</strong></div>
      {item.instrument_type === 'option' && <><div><span>到期 / 行权价</span><strong>{item.option_expiry || '未提供'} / {money(item.option_strike, item.currency)}</strong></div><div><span>类型 / 波动率</span><strong>{item.option_right || '未提供'} / {item.implied_volatility == null ? '未提供' : `${(item.implied_volatility * 100).toFixed(1)}%`}</strong></div></>}
      <div><span>目标参考</span><strong>{money(item.target_price, item.currency)}</strong></div>
      <div><span>风险线</span><strong>{money(item.stop_price, item.currency)}</strong></div>
      <div className="is-risk"><span>最大风险</span><strong>{money(item.max_loss, item.currency)}</strong></div>
    </div>
    <section className="beginner-reason"><span>推荐理由</span><p>{item.rationale || '服务端尚未提供可公开的推荐理由；请进入研究页核对完整证据。'}</p></section>
    <section className="beginner-risk-note"><ShieldCheck /><div><strong>先看风险，再决定是否行动</strong><p>{item.state === 'locked' ? '当前记录受权限或数据门限制，不应据此行动。' : !complete ? `资料仍不完整${missing.length ? `：${missing.join('、')}` : ''}。` : item.instrument_type === 'option' ? '期权可能归零，并受时间衰减、波动率和流动性影响。' : '正股也会出现回撤、跳空与流动性风险；最大风险未提供时不要自行补数。'}</p></div></section>
    <footer><span><Clock3 />{observedAt ? formatTime(observedAt) : '记录时间未提供'} · 所有操作需人工确认</span><div><button className="button secondary" type="button" onClick={onResearch}>查看证据</button><button className="button primary" type="button" disabled={item.state === 'locked'} onClick={onPractice}>{item.instrument_type === 'stock' ? '进入个人模拟' : '打开期权研究'}<ArrowRight /></button></div></footer>
  </article>
}

export function RecommendationsPage() {
  const workspace = useWorkspace()
  const navigate = useNavigate()
  const [view, setView] = useState<RecommendationView>('stock')
  const items = useMemo(() => [...(workspace.data?.recommendations.items ?? [])]
    .filter((item) => item.instrument_type === view)
    .sort((a, b) => (b.occurred_at || b.available_at || '').localeCompare(a.occurred_at || a.available_at || '')), [view, workspace.data])
  const openResearch = (item: RecommendationItem) => navigate(`/research?market=${encodeURIComponent(item.market || 'US')}&symbol=${encodeURIComponent(item.symbol || '')}${item.event_id ? `&event_id=${item.event_id}` : ''}${item.instrument_type === 'option' ? '&tab=期权证据' : ''}`)
  const openPractice = (item: RecommendationItem) => item.instrument_type === 'stock'
    ? navigate(`/paper?market=${encodeURIComponent(item.market || 'US')}&symbol=${encodeURIComponent(item.symbol || '')}&source=recommendation&reference=${item.event_id}`)
    : navigate(`/research?market=${encodeURIComponent(item.market || 'US')}&symbol=${encodeURIComponent(item.symbol || '')}&tab=期权证据`)

  return <div className="page recommendations-page">
    <PageHeader kicker="BEGINNER RECOMMENDATIONS" title="正股与期权推荐" description="为 0 基础用户拆解服务端正式推荐：先看它是什么、为什么出现、资料是否完整和最大风险，再由你决定是否继续研究或进入个人模拟。" />
    <section className="recommendation-guide"><Sparkles /><div><strong>推荐不是订单</strong><p>本页只整理真实研究记录，不替你补数据、不承诺收益，也不会自动下单。期权推荐会额外提示到期、行权价与时间衰减。</p></div></section>
    <div className="recommendation-tabs" role="tablist" aria-label="推荐类型"><button className={view === 'stock' ? 'active' : ''} type="button" role="tab" aria-selected={view === 'stock'} onClick={() => setView('stock')}>正股推荐 <span>{workspace.data?.recommendations.items.filter((item) => item.instrument_type === 'stock').length ?? 0}</span></button><button className={view === 'option' ? 'active' : ''} type="button" role="tab" aria-selected={view === 'option'} onClick={() => setView('option')}>期权推荐 <span>{workspace.data?.recommendations.items.filter((item) => item.instrument_type === 'option').length ?? 0}</span></button></div>
    {items.length ? <section className="beginner-recommendation-grid" aria-label={view === 'stock' ? '正股推荐列表' : '期权推荐列表'}>{items.map((item) => <RecommendationCard key={item.event_id} item={item} onResearch={() => openResearch(item)} onPractice={() => openPractice(item)} />)}</section> : <section className="recommendation-empty" role="status"><BookOpenCheck /><div><h2>当前没有真实{view === 'stock' ? '正股' : '期权'}推荐</h2><p>新的正式研究记录通过服务端发布后会显示在这里；当前不会用示例代码、虚构理由或预测价格填充。</p></div><button className="button secondary" type="button" onClick={() => navigate('/discover')}>前往发现股票</button></section>}
  </div>
}

export default RecommendationsPage
