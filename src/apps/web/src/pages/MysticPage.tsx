import { CalendarDays, ChevronLeft, ChevronRight, Heart, Send, ShieldAlert, Sparkles, Users, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { PageHeader } from '../components/PageHeader'
import { useWorkspace } from '../api/workspace-context'

interface MysticPost {
  id: string
  date: string
  time: string
  title: string
  body: string
  tags: string[]
  likedBy: string[]
}

const initialPosts: MysticPost[] = [
  { id: 'm-0811-1', date: '2026-08-11', time: '09:18', title: '开盘前的市场气氛', body: '高开或低开后的第一段成交量，经常影响当天的市场节奏；这里只记录开盘气氛，不构成任何买卖依据。', tags: ['开盘观察', '娱乐参考'], likedBy: ['Mina', 'Jason', '阿明'] },
  { id: 'm-0811-2', date: '2026-08-11', time: '15:42', title: '收盘后的“感觉”记录', body: '大型科技股没有一起发力，市场情绪仍然分散。缩量或单日反弹只作为文化观察，不代表趋势已经改变。', tags: ['收盘记录'], likedBy: ['Wong', 'Mina'] },
  { id: 'm-0810-1', date: '2026-08-10', time: '20:06', title: '财报周的情绪温度', body: '财报周常出现方向判断与成交价格不同步的讨论；这条只记录市场文化感受，不进入正式量化推荐。', tags: ['财报周', '市场情绪'], likedBy: ['Jason'] },
  { id: 'm-0808-1', date: '2026-08-08', time: '12:30', title: '周末市场闲谈', body: '当很多人同时讨论同一个热门标的，叙事通常会显得拥挤。这里仅记录舆论现象，不提供跟随或反向行动指令。', tags: ['周末', '热门叙事'], likedBy: ['阿明', 'Yuki', 'Wong', 'Mina'] },
]

const calendarDays = Array.from({ length: 31 }, (_, index) => index + 1)
const PAGE_SIZE = 2

export function MysticPage() {
  const workspace = useWorkspace()
  const [posts, setPosts] = useState(initialPosts)
  const [selectedDate, setSelectedDate] = useState('')
  const [page, setPage] = useState(0)
  const [likerPostId, setLikerPostId] = useState('')
  const currentName = workspace.user?.display_name || '我'
  const activeDates = useMemo(() => new Set(posts.map((post) => post.date)), [posts])
  const filtered = useMemo(() => posts
    .filter((post) => !selectedDate || post.date === selectedDate)
    .sort((left, right) => `${right.date}T${right.time}`.localeCompare(`${left.date}T${left.time}`)), [posts, selectedDate])
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const visiblePosts = filtered.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE)
  const likerPost = posts.find((post) => post.id === likerPostId)

  useEffect(() => {
    if (!likerPostId) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setLikerPostId('')
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [likerPostId])

  const chooseDate = (date: string) => {
    setSelectedDate(date)
    setPage(0)
  }

  const toggleLike = (id: string) => {
    setPosts((current) => current.map((post) => post.id !== id ? post : {
      ...post,
      likedBy: post.likedBy.includes(currentName)
        ? post.likedBy.filter((name) => name !== currentName)
        : [...post.likedBy, currentName],
    }))
  }

  return (
    <div className="page operations-page mystic-page">
      <PageHeader kicker="MYSTIC / POSTS" title="玄学预测" description="像浏览贴文一样查看每天的市场玄学记录；可以点赞，但不会把娱乐内容混进正式交易建议。" />
      <div className="mystic-sync-strip"><Send size={17} /><span><strong>TG 发布同步</strong><small>页面已按 TG 贴文结构预留；同步接口接入后，你发布的内容会按日期自动进入这里。点赞同步尚未接入服务器，当前只在本次浏览中生效。</small></span><b>待接入</b></div>
      <div className="mystic-post-layout">
        <main className="mystic-post-stream">
          <nav className="mystic-date-filter" aria-label="贴文日期筛选"><button className={!selectedDate ? 'active' : ''} type="button" onClick={() => chooseDate('')}>全部贴文</button>{selectedDate && <button className="active" type="button" onClick={() => chooseDate('')}><X size={14} />{selectedDate}</button>}</nav>
          {visiblePosts.map((post) => {
            const liked = post.likedBy.includes(currentName)
            return <article className="mystic-post" key={post.id}>
              <div className="mystic-trading-warning" role="note"><ShieldAlert size={16} /><strong>娱乐观察 · 不可用于交易</strong></div>
              <header><img src="/brand/ciclotrade-logo.jpg" alt="CicloTrade" width="42" height="42" /><div><strong>CicloTrade</strong><span>@CicloTrade · {post.date} {post.time}</span></div><Sparkles size={17} /></header>
              <h2>{post.title}</h2><p>{post.body}</p>
              <div className="mystic-post-tags">{post.tags.map((tag) => <span key={tag}>#{tag}</span>)}</div>
              <footer><button className={liked ? 'liked' : ''} type="button" aria-pressed={liked} onClick={() => toggleLike(post.id)}><Heart size={17} fill={liked ? 'currentColor' : 'none'} />{liked ? '已点赞' : '点赞'}</button><button type="button" onClick={() => setLikerPostId(post.id)}><Users size={16} />{post.likedBy.length} 人点赞</button><span>不提供评论</span></footer>
            </article>
          })}
          {!visiblePosts.length && <div className="mystic-empty"><CalendarDays size={24} /><strong>这一天没有发布玄学贴文</strong><span>日历没有点亮的日期无法选择。</span></div>}
          {filtered.length > PAGE_SIZE && <nav className="mystic-pagination" aria-label="贴文分页"><button type="button" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft size={16} />上一页</button><span>{page + 1} / {totalPages}</span><button type="button" disabled={page + 1 >= totalPages} onClick={() => setPage((value) => Math.min(totalPages - 1, value + 1))}>下一页<ChevronRight size={16} /></button></nav>}
        </main>
        <aside className="mystic-calendar data-panel"><header className="panel-heading"><div><span>POST CALENDAR</span><h2>2026 年 8 月</h2></div><CalendarDays size={19} /></header><div className="mystic-weekdays">{['一', '二', '三', '四', '五', '六', '日'].map((day) => <span key={day}>{day}</span>)}</div><div className="mystic-calendar-grid">{Array.from({ length: 5 }, (_, index) => <i key={`offset-${index}`} />)}{calendarDays.map((day) => { const date = `2026-08-${String(day).padStart(2, '0')}`; const active = activeDates.has(date); return <button className={`${active ? 'has-post' : ''} ${selectedDate === date ? 'selected' : ''}`} type="button" disabled={!active} aria-label={`${date}${active ? '有贴文' : '没有贴文'}`} onClick={() => chooseDate(date)} key={date}>{day}{active && <i />}</button> })}</div><footer><span><i />有贴文，可选择</span><span>灰色日期没有内容</span></footer></aside>
      </div>
      {likerPost && <div className="mystic-likers-backdrop" role="presentation" onClick={() => setLikerPostId('')}><section className="mystic-likers" role="dialog" aria-modal="true" aria-label="查看点赞用户" onClick={(event) => event.stopPropagation()}><header><div><span>LIKED BY</span><h2>谁点赞了这篇贴文</h2></div><button className="icon-button" type="button" aria-label="关闭点赞用户列表" onClick={() => setLikerPostId('')}><X size={18} /></button></header>{likerPost.likedBy.map((name) => <div key={name}><span>{name.slice(0, 1).toUpperCase()}</span><strong>{name}</strong></div>)}</section></div>}
    </div>
  )
}
