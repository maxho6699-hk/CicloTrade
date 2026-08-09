import { CircleAlert, ExternalLink, Flame, Sparkles, TrendingUp } from 'lucide-react'
import { PageHeader } from '../components/PageHeader'
import { useState } from 'react'

const stories = [
  { title: '“周一跳空”叙事示例', source: 'X · 编辑预览', heat: '待采集', summary: '大型科技股财报后的跳空行为可作为舆情主题。正式发布前必须取得公开来源、去重并完成人工审核。', time: '未发布' },
  { title: '防御型轮动叙事示例', source: 'Threads · 编辑预览', heat: '待采集', summary: '防御型轮动可作为市场文化观察，但必须与正式量价证据、推荐分数和订单完全隔离。', time: '未发布' },
  { title: '财报前波动率叙事示例', source: 'X · 编辑预览', heat: '待采集', summary: '隐含波动率讨论只能作为娱乐舆情整理，不能被润色成单边方向预测或收益保证。', time: '未发布' },
]

export function MysticPage() {
  const [sourceStatus, setSourceStatus] = useState('')
  return (
    <div className="page operations-page">
      <PageHeader kicker="MYSTIC / SOCIAL PULSE" title="市场玄学" description="公开市场文化、热门叙事与 AI 编辑摘要。它与正式推荐和风控评分完全隔离。" />
      <div className="mystic-disclaimer"><CircleAlert size={19} /><div><strong>娱乐舆情，不构成买卖依据</strong><span>采集内容必须保留公开来源、发布时间和编辑记录；不会进入量化特征、推荐分数或订单。</span></div></div>
      <div className="mystic-board">
        <section className="mystic-stream data-panel"><header className="panel-heading"><div><span>SOCIAL NARRATIVES</span><h2>编辑队列预览</h2></div><span className="status-chip research"><Flame size={14} /> 尚未连接平台API</span></header>{sourceStatus && <div className="inline-warning" role="status"><CircleAlert size={17} /><span>{sourceStatus}</span></div>}{stories.map((story) => <article className="mystic-story" key={story.title}><header><span className="source-badge"><Sparkles size={14} /> AI 编辑摘要样式</span><time>{story.time}</time></header><h3>{story.title}</h3><p>{story.summary}</p><footer><span>{story.source}</span><strong><TrendingUp size={14} /> {story.heat}</strong><button className="icon-button" type="button" title="查看来源接入状态" aria-label="查看来源接入状态" onClick={() => setSourceStatus('当前没有 X/Threads 官方API授权，因此不会伪造原文链接或绕过登录抓取。编辑流水线已保留，取得授权后才能进入人工审核队列。')}><ExternalLink size={15} /></button></footer></article>)}</section>
        <aside className="mystic-rules data-panel"><header className="panel-heading"><div><span>EDITORIAL GATE</span><h2>发布规则</h2></div></header><ol><li><span>01</span><div><strong>仅采集公开内容</strong><small>不绕过登录限制，不采集私人资料。</small></div></li><li><span>02</span><div><strong>去重并核对来源</strong><small>相同叙事合并，保留原始链接和时间。</small></div></li><li><span>03</span><div><strong>AI 总结后审核</strong><small>去掉误导性保证和未经证实的事实。</small></div></li><li><span>04</span><div><strong>与交易评分隔离</strong><small>不影响推荐、仓位、预警和订单。</small></div></li></ol><footer>高级会员权益 · 编辑记录可审计</footer></aside>
      </div>
    </div>
  )
}
