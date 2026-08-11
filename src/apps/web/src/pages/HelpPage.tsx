import { BookOpenCheck, CircleHelp, LifeBuoy, ShieldCheck } from 'lucide-react'
import { PageHeader } from '../components/PageHeader'

const topics = [
  ['正式行动与研究候选', '正式行动来自不可变量化日志；研究候选和演示行动不会被展示成已批准建议。'],
  ['行情与历史记录', '每个页面都会标注实时、延迟、历史快照、已停用或界面演示，避免混用数据新鲜度。'],
  ['模拟交易、做空与实盘', '官方模拟账户的美股卖出可以直接形成空头；实盘由用户主动连接个人券商，是否可做空只看券商保证金、可借券与授权状态。A 股不支持做空，模拟账户与实盘账户始终分开。'],
  ['会员与付款', '所有会员订单都是一次性购买，到期停止，不会自动续费或自动扣款。'],
  ['市场玄学', 'X/Threads 舆情只进入编辑区，不进入量化特征、推荐分数、风控或订单。'],
]

export function HelpPage() {
  return (
    <div className="page operations-page">
      <PageHeader kicker="HELP / PRODUCT SUPPORT" title="帮助与支持" description="查看数据口径、行动状态、安全边界和当前服务范围。" />
      <div className="mystic-disclaimer"><ShieldCheck size={19} /><div><strong>购买建议必须同时核对来源与新鲜度</strong><span>正式量化事件也可能是历史记录；模拟验证前仍需重新核对价格、仓位和最大风险。</span></div></div>
      <div className="help-layout">
        <section className="data-panel">
          <header className="panel-heading"><div><span>PRODUCT GUIDE</span><h2>常见问题</h2></div><CircleHelp size={20} /></header>
          <div className="help-topics">{topics.map(([title, body]) => <article key={title}><BookOpenCheck size={18} /><div><strong>{title}</strong><p>{body}</p></div></article>)}</div>
        </section>
        <aside className="data-panel help-contact">
          <LifeBuoy size={25} />
          <h2>账户支持</h2>
          <p>密码、身份验证、券商凭据与付款争议继续由原安全服务台处理。新界面不会在浏览器中显示或收集这些敏感凭据。</p>
          <a className="button secondary wide" href="/account">查看账户与安全</a>
        </aside>
      </div>
    </div>
  )
}
