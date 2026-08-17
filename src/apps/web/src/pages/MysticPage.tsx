import { LockKeyhole, ShieldAlert } from 'lucide-react'
import { Link } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import '../styles/secondary-pages.css'

export function MysticPage() {
  return (
    <div className="page operations-page mystic-page">
      <PageHeader
        kicker="MYSTIC / POSTS"
        title="玄学预测"
        description="此页面只在服务端提供真实发布记录后展示；当前不会用本地贴文、点赞或日期填充内容。"
      />
      <section className="mystic-locked-panel data-panel" aria-labelledby="mystic-locked-title">
        <div className="mystic-locked-icon"><LockKeyhole size={25} aria-hidden="true" /></div>
        <div>
          <span className="status-chip research"><ShieldAlert size={14} /> 已锁定</span>
          <h2 id="mystic-locked-title">真实玄学发布接口尚未提供</h2>
          <p>当前 API 没有返回 Telegram 发布内容、发布时间、点赞或互动记录。页面保持空状态，不显示演示贴文，也不把娱乐内容混进股票研究或交易行动。</p>
          <div className="mystic-boundary-grid" aria-label="娱乐功能边界">
            <article><strong>当前状态</strong><span>服务端无公开记录，页面保持真实空态。</span></article>
            <article><strong>不会展示</strong><span>不虚构贴文、互动数字、发布日期或命中率。</span></article>
            <article><strong>研究分流</strong><span>股票判断、风险证据和模拟交易继续在研究区处理。</span></article>
          </div>
          <Link className="button secondary" to="/research">返回股票研究</Link>
        </div>
      </section>
    </div>
  )
}
