import { BookOpenCheck, CircleHelp, LifeBuoy, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import '../styles/secondary-pages.css'

const topics = [
  ['研究域', '研究候选只读查看来源、时间、覆盖与风险，不会自动变成个人模拟、官方验证、券商订单或 Telegram 推送。', '/research'],
  ['官方验证域', '今日行动、官方验证组合和报告只读取 CicloTrade 官方验证模拟记录，不混入个人模拟余额或券商收益。', '/today'],
  ['个人模拟域', '每笔股票订单都由你确认，使用独立的个人模拟账本；不会连接券商或自动提交真实订单。', '/paper'],
  ['券商实盘域', '实盘必须在交易页单独完成券商授权、账户/环境匹配与 mandate；会员付款不会自动连接券商。', '/trade'],
  ['AI 与行动边界', 'AI 只生成解释、证据摘要和个人模拟草稿；不能提交券商订单、激活自动实盘或绕过风险确认。', '/discover'],
  ['数据与通知', '行情页面标注数据来源与新鲜度；Telegram 只在绑定、验证、授权三阶段和真实偏好可证明时显示可用。', '/notifications'],
]

export function HelpPage() {
  return (
    <div className="page operations-page">
      <PageHeader kicker="HELP / PRODUCT SUPPORT" title="帮助与支持" description="查看数据口径、行动状态、安全边界和当前服务范围。" />
      <div className="mystic-disclaimer"><ShieldCheck size={19} /><div><strong>购买建议必须同时核对来源与新鲜度</strong><span>正式量化事件也可能是历史记录；模拟验证前仍需重新核对价格、仓位和最大风险。</span></div></div>
      <div className="help-layout">
        <section className="data-panel">
          <header className="panel-heading"><div><span>PRODUCT GUIDE</span><h2>常见问题</h2></div><CircleHelp size={20} /></header>
          <div className="help-topics">{topics.map(([title, body, route]) => <article key={title}><BookOpenCheck size={18} /><div><strong>{title}</strong><p>{body}</p><Link to={route}>打开真实页面</Link></div></article>)}</div>
        </section>
        <aside className="data-panel help-contact">
          <LifeBuoy size={25} />
          <h2>账户支持</h2>
          <p>密码、身份验证、券商凭据与付款争议继续由原安全服务台处理。新界面不会在浏览器中显示或收集这些敏感凭据。</p>
          <Link className="button secondary wide" to="/account">查看账户与安全</Link>
          <Link className="button tertiary wide" to="/trade">查看实盘边界</Link>
        </aside>
      </div>
    </div>
  )
}
