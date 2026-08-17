import { ArrowRight, FileText, LockKeyhole, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import { PageHeader } from '../components/PageHeader'
import '../styles/secondary-pages.css'

const policyEntries = [
  { title: '隐私政策', description: '说明账户资料、可控记忆、内容与授权的用途；可在个人中心查看或撤销已提供的数据权限。', route: '/account', action: '管理数据权限' },
  { title: '用户协议', description: '产品提供研究、模拟与人工审阅工具；账户域隔离，AI 不拥有订单提交、付款或权益审批权限。', route: '/help', action: '查看使用边界' },
  { title: '退款政策', description: '退款资格、金额与处理时间以服务端订单、支付渠道及当时有效政策为准，不承诺固定结果。', route: '/membership', action: '查看订单与套餐' },
  { title: '风险披露', description: '股票、期权、做空与实盘均可能亏损；实盘必须由用户主动授权券商并通过风险门控。', route: '/trade', action: '查看交易风险门' },
  { title: '免责声明', description: '行情、研究与 AI 回答仅供审阅，不构成个性化投资建议、收益保证或自动交易指令。', route: '/research', action: '进入研究页核验' },
  { title: '联系方式', description: '需要协助、提交政策问题或反馈时，请使用站内帮助与反馈入口；页面不编造未公开的联系方式。', route: '/feedback', action: '提交站内反馈' },
] as const

export function LegalPage() {
  const workspace = useWorkspace()
  const policy = workspace.data?.membership.policy
  const hasPolicyIndex = Boolean(policy?.key && policy.version !== null && policy.sha256)

  return (
    <div className="page operations-page legal-page">
      <PageHeader kicker="LEGAL / POLICY INDEX" title="法律政策与账户边界" description="按隐私、协议、退款、风险、免责与联系六类整理可用入口；未接入的正式文本与同意收据不会被伪造。" />
      <section className="legal-boundary data-panel">
        <ShieldCheck size={21} aria-hidden="true" />
        <div><strong>六类政策入口</strong><p>以下内容用于清楚说明现有产品边界，不代表你已接受任何新条款；正式政策仍以服务端发布的有效版本为准。</p></div>
      </section>
      <section className="legal-entry-grid" aria-label="政策入口">
        {policyEntries.map((entry) => <article className="legal-entry data-panel" key={entry.title}><FileText size={19} aria-hidden="true" /><div><h2>{entry.title}</h2><p>{entry.description}</p><Link to={entry.route}>{entry.action} <ArrowRight size={14} /></Link></div></article>)}
      </section>
      <section className="legal-receipt data-panel" aria-labelledby="legal-receipt-title">
        <LockKeyhole size={20} aria-hidden="true" />
        <div>
          <span>VERSIONED CONSENT RECEIPT</span>
          <h2 id="legal-receipt-title">版本化同意收据：未接入</h2>
          <p>当前 API 没有返回用户对政策的版本化同意收据，因此页面不会显示“已同意”、签署时间或可验证收据。</p>
          {hasPolicyIndex ? <dl><div><dt>服务端政策索引</dt><dd>{policy?.key}</dd></div><div><dt>政策版本</dt><dd>{policy?.version}</dd></div><div><dt>政策摘要 SHA-256</dt><dd>{policy?.sha256}</dd></div></dl> : <small>当前会话没有可核验的政策索引。</small>}
        </div>
      </section>
      <footer className="legal-footer data-panel"><span>© {new Date().getFullYear()} CicloTrade</span><p>研究不是订单，模拟不等于实盘；所有投资与授权决定均由用户本人作出。需要帮助可前往 <Link to="/help">帮助中心</Link>，需要联系可使用 <Link to="/feedback">反馈建议</Link>。</p></footer>
    </div>
  )
}
