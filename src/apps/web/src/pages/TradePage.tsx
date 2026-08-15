import {
  ArrowRight,
  Building2,
  Cable,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  FileCheck2,
  HelpCircle,
  LockKeyhole,
  ShieldCheck,
  Unplug,
  WalletCards,
} from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import type { BrokerCatalogEntry } from '../api/client'
import { brokerAccessApi, type BrokerAccessApplication, type BrokerProvider, isBrokerAccessRejection } from '../api/brokerAccess'
import { useWorkspace } from '../api/workspace-context'
import { PageHeader } from '../components/PageHeader'

const usMarketDetail: {
  label: string
  currency: string
  execution: string
  shorting: string
} = {
  label: '美股',
  currency: 'USD',
  execution: '首期范围只覆盖美股券商接入准备；当前网页尚未开放用户绑定或实盘订单通道。',
  shorting: '未来是否可做空仍由券商保证金、可借券和账户权限决定，CicloTrade 不伪造可借券状态。',
}

const capabilityLabels: Record<BrokerCatalogEntry['capabilities'][number], string> = {
  market_data: '平台侧行情',
  us_stock_limit_orders: '受限美股限价单后端',
}

export function TradePage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const workspace = useWorkspace()
  const symbol = searchParams.get('symbol')?.toUpperCase()
  const eventId = searchParams.get('event_id')
  const authenticated = workspace.mode === 'authenticated'
  const brokerCatalog = useMemo(() => workspace.data?.membership.brokerage.capability_catalog ?? [], [workspace.data?.membership.brokerage.capability_catalog])
  const [applications, setApplications] = useState<BrokerAccessApplication[]>([])
  const [selectedProvider, setSelectedProvider] = useState<BrokerProvider | ''>('')
  const [requestReason, setRequestReason] = useState('')
  const [requestState, setRequestState] = useState<string | null>(null)
  const [loadingApplications, setLoadingApplications] = useState(false)
  const availableProviders = useMemo(() => brokerCatalog.filter((broker) => broker.connection_available), [brokerCatalog])

  useEffect(() => {
    if (!authenticated) return
    setLoadingApplications(true)
    void brokerAccessApi.list().then(setApplications).catch(() => setApplications([])).finally(() => setLoadingApplications(false))
  }, [authenticated])

  async function submitAccessRequest(event: FormEvent) {
    event.preventDefault()
    if (!selectedProvider) return
    setRequestState(null)
    try {
      const result = await brokerAccessApi.create(selectedProvider, requestReason.trim() || null, `broker-${crypto.randomUUID()}`)
      setApplications((current) => [result.application, ...current.filter((item) => item.id !== result.application.id)])
      setRequestReason('')
      setRequestState(result.replayed ? '已恢复上次相同申请。' : '申请已提交，等待人工审核。')
    } catch (error) {
      setRequestState(isBrokerAccessRejection(error) ? (error as Error).message : '网络响应未确认，请保留申请编号后重试读取。')
    }
  }

  async function withdrawAccessRequest(item: BrokerAccessApplication) {
    if (item.status !== 'submitted') return
    setRequestState(null)
    try {
      const updated = await brokerAccessApi.withdraw(item.id)
      setApplications((current) => current.map((candidate) => candidate.id === updated.id ? updated : candidate))
      setRequestState('资格申请已撤回。')
    } catch (error) {
      setRequestState(isBrokerAccessRejection(error) ? (error as Error).message : '撤回结果未确认，请刷新资格历史。')
    }
  }

  return (
    <div className="page operations-page brokerage-page">
      <PageHeader kicker="BROKERAGE / LIVE SERVICE" title="券商实盘连接" description="CicloTrade 提供实盘连接服务。会员订阅与券商连接是两条独立流程；只有你主动连接并授权个人券商后，系统才可能进入真实执行。" />

      <section className="brokerage-status-band" aria-label="券商连接状态">
        <span className="brokerage-status-icon"><Unplug size={21} /></span>
        <div><span>当前账户</span><strong>{authenticated ? workspace.user?.display_name : '尚未登录'} · 未连接券商</strong><small>网页未读取、保存或发送任何券商凭据与订单。</small></div>
        <span className="status-chip research"><ShieldCheck size={14} /> 首期 5 家 · 全部尚未开放</span>
        <button className="button primary" type="button" disabled><LockKeyhole size={16} /> 暂未开放绑定</button>
      </section>

      {authenticated && <section className="data-panel brokerage-access-panel" aria-labelledby="broker-access-title">
        <header className="panel-heading"><div><span>ELIGIBILITY / HUMAN REVIEW</span><h2 id="broker-access-title">券商资格申请</h2></div><FileCheck2 size={20} /></header>
        <p className="admin-panel-note">申请只记录资格审核，不创建券商账户、不启用执行，也不会发送 Telegram。连接可用前不会显示“已连接”或“运行中”。</p>
        {availableProviders.length ? <form className="brokerage-access-form" onSubmit={submitAccessRequest}><label>券商<select value={selectedProvider} onChange={(event) => setSelectedProvider(event.target.value as BrokerProvider)}><option value="">选择券商</option>{availableProviders.map((broker) => <option value={broker.key} key={broker.key}>{broker.display_name}</option>)}</select></label><label>申请原因（可选）<textarea value={requestReason} maxLength={500} onChange={(event) => setRequestReason(event.target.value)} rows={2} /></label><button className="button primary" type="submit" disabled={!selectedProvider}>提交资格申请</button></form> : <p className="admin-panel-note">当前五家券商 connection_available 均为 false，资格申请入口保持锁定。</p>}
        {requestState && <p role="status" className="admin-panel-note">{requestState}</p>}
        {loadingApplications ? <p className="admin-panel-note">正在读取资格历史…</p> : applications.length ? <ul className="brokerage-access-history">{applications.map((item) => <li key={item.id}><span><strong>{item.provider}</strong><small>{item.id} · {item.created_at}</small></span><span className={`admin-state ${item.status === 'approved' ? 'healthy' : item.status === 'rejected' ? 'risk' : 'pending'}`}>{item.status}</span>{item.status === 'submitted' && <button className="button tertiary" type="button" onClick={() => void withdrawAccessRequest(item)}>撤回申请</button>}</li>)}</ul> : <p className="admin-panel-note">暂无资格申请历史。</p>}
      </section>}

      {(symbol || eventId) && <section className="brokerage-context-note"><FileCheck2 size={17} /><span><strong>你从一条研究或验证记录来到这里</strong><small>{symbol ? `${symbol} · ` : ''}{eventId ? `事件 QE-${eventId} · ` : ''}本页不会把它转换成模拟订单或自动发送到券商。</small></span><button className="button tertiary" type="button" onClick={() => navigate('/portfolio')}>查看模拟验证结果</button></section>}

      <section className="brokerage-market-panel data-panel">
        <header className="panel-heading"><div><span>MARKET CAPABILITY</span><h2>市场与账户能力</h2></div><Building2 size={20} /></header>
        <div className="brokerage-market-controls"><strong>美股首发范围</strong><span>{usMarketDetail.currency} 独立账户视图</span></div>
        <div className="brokerage-capability-grid">
          <article><CircleDollarSign size={18} /><span><strong>{usMarketDetail.label}执行范围</strong><small>{usMarketDetail.execution}</small></span></article>
          <article><ShieldCheck size={18} /><span><strong>做空规则</strong><small>{usMarketDetail.shorting}</small></span></article>
          <article><LockKeyhole size={18} /><span><strong>权限来源</strong><small>会员只决定研究、数据、提醒与回测权益；真实交易权限来自你的券商账户。</small></span></article>
        </div>
      </section>

      <section className="brokerage-catalog-panel data-panel" aria-labelledby="broker-catalog-title">
        <header className="panel-heading"><div><span>US BROKER LAUNCH CATALOG</span><h2 id="broker-catalog-title">首期美股券商列表</h2></div><Building2 size={20} /></header>
        <div className="brokerage-catalog-summary">
          <span><ShieldCheck size={16} /> 当前 5 家均不可由用户绑定</span>
          <small>A 股券商及其他候补平台全部后置。会员资格也不会自动开通任何券商连接。</small>
        </div>
        <div className="brokerage-catalog-grid">
          {brokerCatalog.length ? brokerCatalog.map((broker) => (
            <article className={`brokerage-provider-card status-${broker.status}`} key={broker.key}>
              <header>
                <span className="brokerage-provider-mark" aria-hidden="true">{broker.display_name.slice(0, 1)}</span>
                <div><strong>{broker.display_name}</strong><small>美股首发范围</small></div>
                <span className="brokerage-provider-status"><Clock3 size={13} />{broker.status_label}</span>
              </header>
              <p>{broker.availability_detail}</p>
              <div className="brokerage-provider-capabilities">
                {broker.capabilities.length
                  ? broker.capabilities.map((capability) => <span key={capability}>{capabilityLabels[capability]}</span>)
                  : <span>尚无可公开能力</span>}
              </div>
              <footer><LockKeyhole size={14} /><span>暂不可申请或绑定</span></footer>
            </article>
          )) : (
            <div className="brokerage-catalog-empty"><Unplug size={20} /><span>券商目录暂未取得，请稍后刷新。页面不会用演示状态代替真实接入能力。</span></div>
          )}
        </div>
      </section>

      <section className="brokerage-workflow">
        <article className="data-panel brokerage-steps"><header className="panel-heading"><div><span>CONNECTION WORKFLOW</span><h2>接入流程</h2></div><Cable size={20} /></header><ol><li><span>01</span><div><strong>选择个人券商账户</strong><small>确认市场、币种、保证金账户和 API/终端支持范围。</small></div></li><li><span>02</span><div><strong>由你主动授权连接</strong><small>凭据不通过会员付款自动获得，也不会从本机应用静默读取。</small></div></li><li><span>03</span><div><strong>先完成权限与风险核对</strong><small>检查账户状态、订单权限、可借券、数量、价格和最大风险。</small></div></li><li><span>04</span><div><strong>明确确认后才进入真实执行</strong><small>正式接入后仍需逐笔确认或使用你明确配置的受控规则。</small></div></li></ol></article>

        <aside className="data-panel brokerage-boundary"><header className="panel-heading"><div><span>WHAT YOU CAN DO NOW</span><h2>当前可用入口</h2></div><CheckCircle2 size={20} /></header><div className="brokerage-link-list"><button type="button" onClick={() => navigate('/portfolio')}><WalletCards size={17} /><span><strong>CicloTrade模拟持仓及建议</strong><small>只读查看官方模拟记录、当前持仓与已平仓结果。</small></span><ArrowRight size={16} /></button><button type="button" onClick={() => navigate('/reports')}><FileCheck2 size={17} /><span><strong>CicloTrade模拟验证结果</strong><small>查看报告、假设、数据来源和验证状态。</small></span><ArrowRight size={16} /></button><button type="button" onClick={() => navigate('/help')}><HelpCircle size={17} /><span><strong>实盘接入服务</strong><small>确认支持范围、接入方式、时间与风险边界。</small></span><ArrowRight size={16} /></button></div></aside>
      </section>
    </div>
  )
}
