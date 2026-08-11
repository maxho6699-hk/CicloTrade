import {
  Activity,
  ArrowRight,
  BarChart3,
  BellRing,
  Bot,
  Check,
  ChevronRight,
  CircleAlert,
  Clock3,
  Gauge,
  LineChart,
  LockKeyhole,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
  TriangleAlert,
  WalletCards,
} from 'lucide-react'
import {
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react'
import { Link } from 'react-router-dom'
import { PublicPageHeader, type PublicTheme } from '../components/PublicPageHeader'
import { useLocale } from '../i18n/useLocale'

type SignalTone = 'stock' | 'option' | 'risk'

interface SignalPreview {
  tone: SignalTone
  eyebrow: string
  symbol: string
  title: string
  summary: string
  fields: Array<{ label: string; value: string }>
  note: string
}

const signalCards: SignalPreview[] = [
  {
    tone: 'stock',
    eyebrow: '正股行动',
    symbol: 'AAPL',
    title: '分批买入，不要追高',
    summary: '价格回到计划区间才开始；没有到价就先等，不需要预先下单。',
    fields: [{ label: '参考买入', value: '232.00–235.00' }, { label: '最大仓位', value: '20%' }, { label: '止损位置', value: '227.80' }, { label: '第一目标', value: '242.00' }],
    note: '大白话：跌破风险线代表这次判断失效，先控制亏损，不补仓摊平。',
  },
  {
    tone: 'option',
    eyebrow: '期权研究',
    symbol: 'NVDA',
    title: '等方向确认，再选择合约',
    summary: '先说明看涨还是看跌，再核对到期日、权利金和最大可能亏损。',
    fields: [{ label: '研究方向', value: '看涨 Call' }, { label: '持有时间', value: '3–7 天' }, { label: '最大亏损', value: '权利金' }, { label: '实时状态', value: '登录后核对' }],
    note: '研究数据若有延迟或缺少 Greeks，会直接标明，不会包装成即时交易报价。',
  },
  {
    tone: 'risk',
    eyebrow: '风险提醒',
    symbol: 'TSLA',
    title: '接近风险线，先处理仓位',
    summary: '系统优先告诉你该减仓、退出还是继续观察，不用临时猜测。',
    fields: [{ label: '当前动作', value: '降低风险' }, { label: '风险条件', value: '跌破计划线' }, { label: '禁止动作', value: '逆势加仓' }, { label: '后续检查', value: '收盘确认' }],
    note: '风险提醒不会使用“稳赚”“马上翻倍”等情绪化语言催促交易。',
  },
]

const tiers = [
  { name: '免费会员', price: 'HKD 0', period: '长期', detail: '先看懂基础行动与风险', items: ['基础策略解释', '1 条价格预警', '延迟 15 分钟行情'] },
  { name: '标准会员', price: 'HKD 298', period: '/ 月', detail: '一般用户的完整研究入口', items: ['全部 8 种策略', '10 条组合预警', '网页正式建议'] },
  { name: '高级会员', price: 'HKD 698', period: '/ 月', detail: '正股即时提醒与深度研究', items: ['即时正股 Telegram', 'CSV 导入与策略追踪', '1 个自动交易控制账号名额'], featured: true },
  { name: '专业会员', price: 'HKD 2,980', period: '/ 月', detail: '期权、代码、API 与多账户', items: ['期权链、报价 K 线、Greeks 与 IV', '正股与期权 Telegram', '最多 5 个自动交易控制账号名额'] },
]

function readTheme(): PublicTheme {
  try { return window.localStorage.getItem('ciclotrade.theme') === 'light' ? 'light' : 'dark' } catch { return 'dark' }
}

function usePublicTheme() {
  const [theme, setTheme] = useState<PublicTheme>(readTheme)
  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark'
    document.documentElement.dataset.theme = next
    document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute('content', next === 'light' ? '#e8eee9' : '#071014')
    try { window.localStorage.setItem('ciclotrade.theme', next) } catch { /* storage can be disabled */ }
    setTheme(next)
  }
  return { theme, toggleTheme }
}

function SignalIcon({ tone }: { tone: SignalTone }) {
  if (tone === 'stock') return <TrendingUp size={16} />
  if (tone === 'option') return <Activity size={16} />
  return <TriangleAlert size={16} />
}

function SignalCard({ card, compact = false }: { card: SignalPreview; compact?: boolean }) {
  return (
    <article className={`signal-preview ${card.tone}${compact ? ' compact' : ''}`}>
      <header>
        <span className="signal-type"><SignalIcon tone={card.tone} />{card.eyebrow}</span>
        <span className="signal-demo">DEMO · 非实时</span>
      </header>
      <div className="signal-title-row"><strong>{card.symbol}</strong><span>{card.title}</span></div>
      <p>{card.summary}</p>
      <dl>{card.fields.map((field) => <div key={field.label}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl>
      <footer><ShieldCheck size={14} /><span>{card.note}</span></footer>
    </article>
  )
}

function FeaturePoint({ icon, title, text }: { icon: ReactNode; title: string; text: string }) {
  return <div><span>{icon}</span><strong>{title}</strong><small>{text}</small></div>
}

interface RailDragState {
  pointerId: number | null
  startX: number
  startY: number
  startScrollLeft: number
  axis: 'x' | 'y' | null
  dragging: boolean
  suppressClick: boolean
}

function SignalRail() {
  const railRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<RailDragState>({
    pointerId: null,
    startX: 0,
    startY: 0,
    startScrollLeft: 0,
    axis: null,
    dragging: false,
    suppressClick: false,
  })

  function snapToNearestCard() {
    const rail = railRef.current
    if (!rail) return

    const railRect = rail.getBoundingClientRect()
    const cards = Array.from(rail.querySelectorAll<HTMLElement>('.signal-preview'))
    const nearest = cards.reduce<{ card: HTMLElement; distance: number } | null>((current, card) => {
      const target = card.getBoundingClientRect().left - railRect.left + rail.scrollLeft
      const distance = Math.abs(target - rail.scrollLeft)
      return current && current.distance <= distance ? current : { card, distance }
    }, null)
    if (!nearest) return

    const target = nearest.card.getBoundingClientRect().left - railRect.left + rail.scrollLeft
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    rail.scrollTo({ left: target, behavior: reduceMotion ? 'auto' : 'smooth' })
  }

  function finishPointerDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const rail = railRef.current
    const drag = dragRef.current
    if (!rail || drag.pointerId !== event.pointerId) return

    if (rail.hasPointerCapture(event.pointerId)) rail.releasePointerCapture(event.pointerId)
    rail.classList.remove('is-dragging')
    if (drag.dragging) {
      drag.suppressClick = true
      snapToNearestCard()
      window.setTimeout(() => { drag.suppressClick = false }, 0)
    }
    drag.pointerId = null
    drag.axis = null
    drag.dragging = false
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.pointerType === 'touch' || event.button !== 0) return
    const rail = railRef.current
    if (!rail || rail.scrollWidth <= rail.clientWidth) return

    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startScrollLeft: rail.scrollLeft,
      axis: null,
      dragging: false,
      suppressClick: false,
    }
    rail.setPointerCapture(event.pointerId)
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const rail = railRef.current
    const drag = dragRef.current
    if (!rail || drag.pointerId !== event.pointerId) return

    const deltaX = event.clientX - drag.startX
    const deltaY = event.clientY - drag.startY
    if (!drag.axis && Math.max(Math.abs(deltaX), Math.abs(deltaY)) >= 7) {
      drag.axis = Math.abs(deltaX) > Math.abs(deltaY) * 1.15 ? 'x' : 'y'
    }
    if (drag.axis !== 'x') return

    if (!drag.dragging) {
      drag.dragging = true
      rail.classList.add('is-dragging')
      window.getSelection()?.removeAllRanges()
    }
    event.preventDefault()
    rail.scrollLeft = drag.startScrollLeft - deltaX
  }

  function handleClickCapture(event: ReactMouseEvent<HTMLDivElement>) {
    if (!dragRef.current.suppressClick) return
    event.preventDefault()
    event.stopPropagation()
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    const rail = railRef.current
    if (!rail || rail.scrollWidth <= rail.clientWidth) return

    const cards = Array.from(rail.querySelectorAll<HTMLElement>('.signal-preview'))
    if (!cards.length) return
    const railRect = rail.getBoundingClientRect()
    const targets = cards.map((card) => card.getBoundingClientRect().left - railRect.left + rail.scrollLeft)
    const currentIndex = targets.reduce((nearest, target, index) => (
      Math.abs(target - rail.scrollLeft) < Math.abs(targets[nearest] - rail.scrollLeft) ? index : nearest
    ), 0)
    const nextIndex = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? cards.length - 1
        : Math.max(0, Math.min(cards.length - 1, currentIndex + (event.key === 'ArrowRight' ? 1 : -1)))
    event.preventDefault()
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    rail.scrollTo({ left: targets[nextIndex], behavior: reduceMotion ? 'auto' : 'smooth' })
  }

  return (
    <div
      className="telegram-evidence-grid"
      ref={railRef}
      role="region"
      aria-label="Telegram 推送示例卡片，可使用左右方向键切换"
      tabIndex={0}
      onClickCapture={handleClickCapture}
      onKeyDown={handleKeyDown}
      onPointerCancel={finishPointerDrag}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={finishPointerDrag}
    >
      {signalCards.map((card) => <SignalCard card={card} key={card.tone} />)}
    </div>
  )
}

export function WelcomePage() {
  const { locale, setLocale } = useLocale()
  const { theme, toggleTheme } = usePublicTheme()

  return (
    <div className="public-page welcome-page">
      <a className="skip-link" href="#welcome-main">跳到主要内容</a>
      <PublicPageHeader
        variant="welcome"
        locale={locale}
        theme={theme}
        onLocaleToggle={() => void setLocale(locale === 'zh-Hant' ? 'zh-Hans' : 'zh-Hant')}
        onThemeToggle={toggleTheme}
      />

      <main id="welcome-main">
        <section className="welcome-hero" aria-labelledby="welcome-title">
          <div className="welcome-hero-copy">
            <span className="public-kicker"><span className="status-light" />CicloTrade 决策终端</span>
            <h1 id="welcome-title">今天做什么，风险在哪里。</h1>
            <p>把量化结果翻成普通人看得懂的下一步：买什么、什么价位、最多买多少、何时止损。想自己研究时，再进入完整 K 线、新闻、预警和专业工具。</p>
            <div className="welcome-actions"><Link className="button primary" to="/login">登录并查看今日行动 <ArrowRight size={17} /></Link><a className="button secondary" href="#preview">先看产品界面 <ChevronRight size={16} /></a></div>
            <div className="welcome-trust"><span><ShieldCheck size={16} />正式行动与研究候选分开</span><span><LockKeyhole size={16} />固定有效期，不自动续费</span><span><WalletCards size={16} />券商必须由用户主动授权</span></div>
          </div>

          <figure className="hero-product-stage" id="preview" role="img" aria-label="CicloTrade 工作台真实融屏预览">
            <img className="hero-fused hero-fused-dark" src="/media/landing/workspace-hero-fused-dark.webp" width="1800" height="1012" alt="" aria-hidden="true" fetchPriority="high" />
            <img className="hero-fused hero-fused-light" src="/media/landing/workspace-hero-fused-light.webp" width="1800" height="1012" alt="" aria-hidden="true" fetchPriority="high" />
            <figcaption><span><Sparkles size={13} />工作台预览已按显示器角度离线融屏</span><small>界面结构来自 CicloTrade；画面数字仅展示字段和版式，不是实时行情或个人建议。</small></figcaption>
          </figure>
        </section>

        <section className="welcome-proof-strip" aria-label="平台能力摘要">
          <FeaturePoint icon={<ShieldCheck size={17} />} title="正式行动" text="通过风控与发布记录后才显示" />
          <FeaturePoint icon={<Target size={17} />} title="风险边界" text="入场、止损、目标、失效条件同屏" />
          <FeaturePoint icon={<WalletCards size={17} />} title="官方验证" text="Buy、Sell、持有时间与结果可回看" />
          <FeaturePoint icon={<BellRing size={17} />} title="即时提醒" text="网站与 Telegram 返回同一条记录" />
        </section>

        <section className="welcome-section path-section" id="paths">
          <header className="welcome-section-heading"><span className="public-kicker">THREE WORKFLOWS</span><h2>同一套数据，按你的熟悉程度来解释。</h2><p>入口不同，证据、风险、预警和结果保持一致；不需要一开始就学会所有术语。</p></header>
          <div className="path-grid">
            <article><span className="path-index">01</span><span className="path-icon beginner"><Target size={21} /></span><small>零基础 · 大白话</small><h3>直接看下一步</h3><p>用“可以买、先等、不要追、已有仓位怎么处理”说明行动，旁边直接放止损、目标和最大风险。</p><ul><li>正股与期权分开</li><li>行动与等待分开</li><li>为什么这样做讲清楚</li></ul><Link to="/login?returnTo=%2Ftoday">查看今日行动 <ArrowRight size={15} /></Link></article>
            <article><span className="path-index">02</span><span className="path-icon research"><LineChart size={21} /></span><small>一般用户 · 市场术语</small><h3>自己看懂行情</h3><p>热门股票、涨跌幅榜、波幅榜、自选、新闻、预警和可操作 K 线集中在市场工作区。</p><ul><li>看趋势与支撑压力</li><li>管理自选与价格预警</li><li>回到行动卡核对风险</li></ul><Link to="/login?returnTo=%2Fmarkets">打开市场观察 <ArrowRight size={15} /></Link></article>
            <article><span className="path-index">03</span><span className="path-icon pro"><BarChart3 size={21} /></span><small>专业用户 · 专业术语</small><h3>验证策略与连接执行</h3><p>策略回测、期权证据、官方验证结果、API 和券商连接按真实权限逐步开放。</p><ul><li>期权链、Greeks 与 IV</li><li>策略、报告与压力测试</li><li>券商主动授权与风险闸门</li></ul><Link to="/login?returnTo=%2Fmembership">查看专业权限 <ArrowRight size={15} /></Link></article>
          </div>
        </section>

        <section className="welcome-section telegram-section" id="telegram">
          <div className="telegram-intro">
            <div className="telegram-copy"><span className="public-kicker"><Bot size={14} />CicloTrade AI / TELEGRAM</span><h2>市场有变化，不必一直守着网页。</h2><p>每条推送写明操作、参考区间、风险线、目标和失效条件；点回网站还能继续看完整证据。</p><div className="telegram-flow"><span>策略扫描</span><ArrowRight size={14} /><span>风险闸门</span><ArrowRight size={14} /><span>网站行动卡</span><ArrowRight size={14} /><span>Telegram</span></div><Link className="text-link" to="/login?returnTo=%2Fnotifications">登录查看通知中心 <ArrowRight size={15} /></Link></div>
            <figure className="telegram-device"><img src="/media/landing/telegram-scene-fused.webp" width="1100" height="1375" alt="CicloTrade AI 消息已完整融入手机屏幕的场景" loading="lazy" /><figcaption><Clock3 size={13} />手机屏幕内容为版式示例，不是周末实时行情。</figcaption></figure>
          </div>
          <div className="telegram-evidence-heading"><div><span>三类推送，一眼分清用途</span><small>股票行动、期权研究、风险处理使用不同颜色和字段顺序。</small></div><b>推送版式示例 · 非实时</b></div>
          <SignalRail />
        </section>

        <section className="welcome-section official-section">
          <div className="official-account-panel">
            <div className="official-proof" aria-label="CicloTrade 官方验证账户结果版式示例">
              <div className="official-proof-bar"><span>OFFICIAL ACCOUNT / SAMPLE</span><b>已平仓</b></div>
              <div className="official-proof-head"><div><small>官方验证账户</small><h3>AAPL · 完整交易记录</h3></div><span>DEMO · 非业绩承诺</span></div>
              <div className="trade-timeline"><div className="trade-node buy"><b>Buy</b><span>228.40</span><small>记录开仓</small></div><i /><div className="trade-node hold"><b>Hold</b><span>6 天</span><small>跟踪风险</small></div><i /><div className="trade-node sell"><b>Sell</b><span>235.10</span><small>记录退出</small></div></div>
              <div className="official-result"><span><small>结果字段</small><b>+2.93%</b></span><p>真实记录会同时保留入场原因、风险线变化、退出原因和数据时间。</p></div>
              <footer><ShieldCheck size={14} />示例数字只用于展示页面结构，不代表真实或未来收益。</footer>
            </div>
            <div className="official-account-copy"><span className="public-kicker">OFFICIAL VERIFICATION ACCOUNT</span><h2>用户不用自己模拟下单，只看 CicloTrade 官方验证账户。</h2><p>系统记录 Buy、Sell、持有时间和盈亏。登录后查看的是量化日志与官方验证快照，不是用户个人模拟订单，也不会冒充个人券商收益。</p><div className="official-metrics"><div><strong>Buy</strong><span>记录开仓</span></div><ChevronRight size={17} /><div><strong>Hold</strong><span>跟踪风险</span></div><ChevronRight size={17} /><div><strong>Sell</strong><span>记录退出</span></div><ChevronRight size={17} /><div><strong>P&amp;L</strong><span>核对结果</span></div></div><Link className="button secondary" to="/login?returnTo=%2Freports">登录查看策略验证结果 <ArrowRight size={16} /></Link></div>
          </div>
        </section>

        <section className="welcome-section membership-section" id="membership">
          <header className="welcome-section-heading"><span className="public-kicker">ONE-TIME MEMBERSHIP</span><h2>先按需要选择，不用担心自动扣款。</h2><p>以下为每月有效期价格。季度、年度和定制方案在登录后的会员页统一比较；所有订阅到期停止，不自动续费。</p></header>
          <div className="welcome-tier-grid">{tiers.map((tier) => <article className={tier.featured ? 'featured' : ''} key={tier.name}>{tier.featured && <span className="tier-badge">正股深度研究</span>}<header><h3>{tier.name}</h3><p>{tier.detail}</p><div><strong>{tier.price}</strong><small>{tier.period}</small></div></header><ul>{tier.items.map((item) => <li key={item}><Check size={15} />{item}</li>)}</ul><Link to="/login?returnTo=%2Fmembership">登录查看完整权益 <ArrowRight size={14} /></Link></article>)}</div>
          <div className="membership-boundary"><Gauge size={18} /><span><strong>重要权限边界：</strong>高级会员开放即时正股 Telegram 与 1 个自动交易控制账号名额；期权链、期权报价 K 线、Greeks、IV、组合研究和最多 5 个账号名额仅专业会员开放。会员本身不会自动连接券商。</span></div>
        </section>

        <section className="welcome-notice"><span className="notice-icon"><CircleAlert size={19} /></span><div><strong>数据和风险说明</strong><p>页面会明确区分真实记录、演示数据、行情延迟、研究候选与正式量化事件。任何研究或建议都不保证收益，也不会绕过用户确认、券商授权和风控流程。</p></div><Link to="/login?returnTo=%2Fhelp" aria-label="登录后查看风险说明"><ChevronRight size={19} /></Link></section>
      </main>

      <footer className="public-footer"><span>© CicloTrade · AI-assisted decision intelligence</span><span><Link to="/login?returnTo=%2Fhelp">帮助与风险说明</Link><Link to="/login">登录</Link></span></footer>
    </div>
  )
}
