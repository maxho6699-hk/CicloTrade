import {
  ArrowRight,
  Building2,
  Cable,
  CheckCircle2,
  CircleDollarSign,
  FileCheck2,
  HelpCircle,
  LockKeyhole,
  ShieldCheck,
  Unplug,
  WalletCards,
} from 'lucide-react'
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import { PageHeader } from '../components/PageHeader'
import { SegmentedControl } from '../components/ui/SegmentedControl'

type BrokerMarket = 'US' | 'HK' | 'CN'

const marketDetails: Record<BrokerMarket, {
  label: string
  currency: string
  execution: string
  shorting: string
}> = {
  US: {
    label: '美股',
    currency: 'USD',
    execution: '支持正股买卖与券商允许的订单类型。成交能力以已连接账户为准。',
    shorting: '可以做空，不需要 CicloTrade 额外审核；券商仍会检查保证金、可借券和账户权限。',
  },
  HK: {
    label: '港股',
    currency: 'HKD',
    execution: '港股资金和持仓必须与 USD、CNY 分开显示。当前网页尚未接入港股账户数据。',
    shorting: '是否可卖空取决于券商、标的与当地市场规则，CicloTrade 不伪造可借券状态。',
  },
  CN: {
    label: 'A股',
    currency: 'CNY',
    execution: '支持已连接券商账户的普通买卖；当前网页尚未接入实盘订单通道。',
    shorting: '普通 A 股账户不提供裸卖空。页面不会把卖出数量穿过空仓。',
  },
}

const marketOptions = [
  { value: 'US', label: '美股' },
  { value: 'HK', label: '港股' },
  { value: 'CN', label: 'A股' },
] satisfies Array<{ value: BrokerMarket; label: string }>

export function TradePage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const workspace = useWorkspace()
  const [market, setMarket] = useState<BrokerMarket>('US')
  const symbol = searchParams.get('symbol')?.toUpperCase()
  const eventId = searchParams.get('event_id')
  const detail = marketDetails[market]
  const authenticated = workspace.mode === 'authenticated'

  return (
    <div className="page operations-page brokerage-page">
      <PageHeader kicker="BROKERAGE / LIVE SERVICE" title="券商实盘连接" description="CicloTrade 提供实盘连接服务。会员订阅与券商连接是两条独立流程；只有你主动连接并授权个人券商后，系统才可能进入真实执行。" />

      <section className="brokerage-status-band" aria-label="券商连接状态">
        <span className="brokerage-status-icon"><Unplug size={21} /></span>
        <div><span>当前账户</span><strong>{authenticated ? workspace.user?.display_name : '尚未登录'} · 未连接券商</strong><small>网页未读取、保存或发送任何券商凭据与订单。</small></div>
        <span className="status-chip research"><ShieldCheck size={14} /> 服务可提供 · 尚未接入</span>
        <button className="button primary" type="button" onClick={() => navigate('/help')}><Cable size={16} /> 联系接入服务</button>
      </section>

      {(symbol || eventId) && <section className="brokerage-context-note"><FileCheck2 size={17} /><span><strong>你从一条研究或验证记录来到这里</strong><small>{symbol ? `${symbol} · ` : ''}{eventId ? `事件 QE-${eventId} · ` : ''}本页不会把它转换成模拟订单或自动发送到券商。</small></span><button className="button tertiary" type="button" onClick={() => navigate('/portfolio')}>查看模拟验证结果</button></section>}

      <section className="brokerage-market-panel data-panel">
        <header className="panel-heading"><div><span>MARKET CAPABILITY</span><h2>市场与账户能力</h2></div><Building2 size={20} /></header>
        <div className="brokerage-market-controls"><SegmentedControl ariaLabel="选择券商市场" value={market} options={marketOptions} onChange={setMarket} /><span>{detail.currency} 独立账户视图</span></div>
        <div className="brokerage-capability-grid">
          <article><CircleDollarSign size={18} /><span><strong>{detail.label}执行范围</strong><small>{detail.execution}</small></span></article>
          <article><ShieldCheck size={18} /><span><strong>做空规则</strong><small>{detail.shorting}</small></span></article>
          <article><LockKeyhole size={18} /><span><strong>权限来源</strong><small>会员只决定研究、数据、提醒与回测权益；真实交易权限来自你的券商账户。</small></span></article>
        </div>
      </section>

      <section className="brokerage-workflow">
        <article className="data-panel brokerage-steps"><header className="panel-heading"><div><span>CONNECTION WORKFLOW</span><h2>接入流程</h2></div><Cable size={20} /></header><ol><li><span>01</span><div><strong>选择个人券商账户</strong><small>确认市场、币种、保证金账户和 API/终端支持范围。</small></div></li><li><span>02</span><div><strong>由你主动授权连接</strong><small>凭据不通过会员付款自动获得，也不会从本机应用静默读取。</small></div></li><li><span>03</span><div><strong>先完成权限与风险核对</strong><small>检查账户状态、订单权限、可借券、数量、价格和最大风险。</small></div></li><li><span>04</span><div><strong>明确确认后才进入真实执行</strong><small>正式接入后仍需逐笔确认或使用你明确配置的受控规则。</small></div></li></ol></article>

        <aside className="data-panel brokerage-boundary"><header className="panel-heading"><div><span>WHAT YOU CAN DO NOW</span><h2>当前可用入口</h2></div><CheckCircle2 size={20} /></header><div className="brokerage-link-list"><button type="button" onClick={() => navigate('/portfolio')}><WalletCards size={17} /><span><strong>CicloTrade模拟持仓及建议</strong><small>只读查看官方模拟记录、当前持仓与已平仓结果。</small></span><ArrowRight size={16} /></button><button type="button" onClick={() => navigate('/reports')}><FileCheck2 size={17} /><span><strong>CicloTrade模拟验证结果</strong><small>查看报告、假设、数据来源和验证状态。</small></span><ArrowRight size={16} /></button><button type="button" onClick={() => navigate('/help')}><HelpCircle size={17} /><span><strong>实盘接入服务</strong><small>确认支持范围、接入方式、时间与风险边界。</small></span><ArrowRight size={16} /></button></div></aside>
      </section>
    </div>
  )
}
