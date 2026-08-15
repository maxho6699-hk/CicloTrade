import { ArrowRight, FileText, LockKeyhole, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import { PageHeader } from '../components/PageHeader'
import '../styles/secondary-pages.css'

const policyEntries = [
  { title: '账户域隔离', description: '研究、官方验证模拟、个人模拟与券商实盘使用不同页面和数据边界。', route: '/help' },
  { title: '风险与实盘授权', description: '实盘必须由用户在交易页单独完成券商授权、账户匹配与风险门检查。', route: '/trade' },
  { title: '会员与付款', description: '会员订单、到期和权益以服务端订单与当前 entitlement 为准。', route: '/membership' },
  { title: 'Telegram 通知', description: '只展示真实绑定、验证、授权三阶段与服务端返回的通知偏好。', route: '/notifications' },
] as const

export function LegalPage() {
  const workspace = useWorkspace()
  const policy = workspace.data?.membership.policy
  const hasPolicyIndex = Boolean(policy?.key && policy.version !== null && policy.sha256)

  return (
    <div className="page operations-page legal-page">
      <PageHeader kicker="LEGAL / POLICY INDEX" title="政策与账户边界" description="这里仅列出当前合同可以确认的政策入口；完整法律文本与版本化同意收据未在本界面伪造。" />
      <section className="legal-boundary data-panel">
        <ShieldCheck size={21} aria-hidden="true" />
        <div><strong>静态政策入口</strong><p>以下入口用于阅读已经实现的产品边界与安全说明，不代表用户已经接受任何新条款。</p></div>
      </section>
      <section className="legal-entry-grid" aria-label="政策入口">
        {policyEntries.map((entry) => <article className="legal-entry data-panel" key={entry.title}><FileText size={19} aria-hidden="true" /><div><h2>{entry.title}</h2><p>{entry.description}</p><Link to={entry.route}>打开真实页面 <ArrowRight size={14} /></Link></div></article>)}
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
    </div>
  )
}
