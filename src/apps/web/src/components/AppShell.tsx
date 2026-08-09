import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  Bell,
  Bot,
  CandlestickChart,
  ChevronRight,
  CircleGauge,
  CreditCard,
  FileChartColumn,
  FlaskConical,
  House,
  Languages,
  HelpCircle,
  LoaderCircle,
  Menu,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  UserRound,
  WalletCards,
  X,
} from 'lucide-react'
import { NavLink, useNavigate } from 'react-router-dom'
import { searchMarket, type MarketSearchItem } from '../api/client'
import { useWorkspace } from '../api/workspace-context'
import { useLocale } from '../i18n/useLocale'
import { localeSearchText } from '../i18n/runtime'

interface AppShellProps {
  children: ReactNode
}

const navItems = [
  { to: '/today', label: '今日', icon: House },
  { to: '/markets', label: '市场', icon: CandlestickChart },
  { to: '/portfolio', label: '组合', icon: WalletCards },
  { to: '/trade', label: '交易', icon: CircleGauge },
  { to: '/reports', label: '报告', icon: FileChartColumn },
  { to: '/notifications', label: '通知', icon: Bell },
  { to: '/account', label: '账户', icon: UserRound },
]

const moreItems = [
  { to: '/reports', label: '报告中心', icon: FileChartColumn },
  { to: '/notifications', label: 'Telegram 通知', icon: Bot },
  { to: '/membership', label: '会员与账单', icon: CreditCard },
  { to: '/mystic', label: '市场玄学', icon: Sparkles },
  { to: '/account', label: '账户设置', icon: Settings },
  { to: '/help', label: '帮助与支持', icon: HelpCircle },
]

const navKeywords: Record<string, string> = {
  '/today': '今日 行动 推荐 买入 卖出',
  '/markets': '市场 自选 股票 行情 K线',
  '/portfolio': '组合 持仓 盈亏 订单',
  '/trade': '交易 下单 买入 卖出 模拟交易',
  '/reports': '报告 回测 收益 回撤 模型',
  '/notifications': '通知 Telegram 推送 预警',
  '/account': '账户 安全 风控 语言 券商',
}

export function AppShell({ children }: AppShellProps) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [commandOpen, setCommandOpen] = useState(false)
  const [commandQuery, setCommandQuery] = useState('')
  const [marketMatches, setMarketMatches] = useState<MarketSearchItem[]>([])
  const [commandStatus, setCommandStatus] = useState('')
  const mobileMenuButton = useRef<HTMLButtonElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const commandInput = useRef<HTMLInputElement>(null)
  const mobileSheet = useRef<HTMLElement>(null)
  const navigate = useNavigate()
  const workspace = useWorkspace()
  const { locale, setLocale, syncState } = useLocale()
  const realData = workspace.mode === 'authenticated'

  useEffect(() => {
    const openCommand = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setCommandOpen(true)
      }
    }
    document.addEventListener('keydown', openCommand)
    return () => document.removeEventListener('keydown', openCommand)
  }, [])

  useEffect(() => {
    if (!mobileMenuOpen) return
    const trigger = mobileMenuButton.current
    closeButton.current?.focus()
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileMenuOpen(false)
      if (event.key !== 'Tab' || !mobileSheet.current) return
      const focusable = [...mobileSheet.current.querySelectorAll<HTMLElement>('a[href], button:not([disabled])')]
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable.at(-1)
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('keydown', handleKey)
      trigger?.focus()
    }
  }, [mobileMenuOpen])

  useEffect(() => {
    if (!commandOpen) return
    commandInput.current?.focus()
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setCommandOpen(false)
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [commandOpen])

  useEffect(() => {
    let active = true
    const query = commandQuery.trim()
    if (!commandOpen || query.length < 2 || !realData) {
      setMarketMatches([])
      setCommandStatus(query.length >= 2 && !realData ? '登录后可搜索完整美股与 A 股目录' : '')
      return () => { active = false }
    }
    setCommandStatus('正在搜索全市场…')
    const timer = window.setTimeout(() => {
      void searchMarket(query, '全部').then((payload) => {
        if (!active) return
        setMarketMatches(payload.items)
        setCommandStatus(payload.items.length ? `找到 ${payload.items.length} 个市场标的` : '市场目录没有匹配结果')
      }).catch((caught) => {
        if (!active) return
        setMarketMatches([])
        setCommandStatus(caught instanceof Error ? caught.message : '市场搜索暂时不可用')
      })
    }, 320)
    return () => { active = false; window.clearTimeout(timer) }
  }, [commandOpen, commandQuery, realData])

  const commandItems = useMemo(() => {
    const query = commandQuery.trim()
    const needle = query.toLowerCase()
    const saved = workspace.data?.settings.watchlists ?? { us: [], a_share: [] }
    const staticItems = [
      ...navItems.map((item) => ({ ...item, keywords: navKeywords[item.to] ?? item.label, meta: '页面' })),
      { to: '/membership', label: '会员与账单', icon: CreditCard, keywords: '会员 订阅 订单 付款 人工对账', meta: '功能' },
      { to: '/mystic', label: '市场玄学', icon: Sparkles, keywords: '玄学 X Threads 舆情', meta: '功能' },
      { to: '/notifications', label: 'Telegram 推送与价格预警', icon: Bot, keywords: 'TG 推送 通知 预警', meta: '功能' },
      { to: '/trade', label: '模拟交易与专业版实盘', icon: CircleGauge, keywords: '下单 买入 卖出 自动交易', meta: '功能' },
      ...saved.us.map((symbol) => ({ to: `/markets?market=US&symbol=${symbol}`, label: `${symbol} 行情`, icon: CandlestickChart, keywords: `${symbol} ${localeSearchText('自选')} ${localeSearchText('美股')}`, meta: '我的自选' })),
      ...saved.a_share.map((symbol) => ({ to: `/markets?market=CN&symbol=${symbol}`, label: `${symbol} 行情`, icon: CandlestickChart, keywords: `${symbol} ${localeSearchText('自选')} ${localeSearchText('A股')}`, meta: '我的自选' })),
    ].filter((item) => !needle || `${localeSearchText(item.label)} ${localeSearchText(item.keywords)}`.toLowerCase().includes(needle))
    const marketItems = marketMatches.map((item) => ({
      to: `/markets?market=${item.market}&symbol=${item.symbol.replace(/\.(SS|SZ)$/, '')}`,
      label: `${item.symbol.replace(/\.(SS|SZ)$/, '')} · ${item.name}`,
      icon: CandlestickChart,
      keywords: `${item.symbol} ${item.name}`,
      meta: item.market === 'CN' ? 'A股市场' : '美股市场',
    }))
    const directMarket = /^\d{6}$/.test(query) ? 'CN' : 'US'
    const direct = /^(?:[A-Za-z][A-Za-z0-9.=-]{0,14}|\d{6})$/.test(query)
      ? [{ to: `/markets?market=${directMarket}&symbol=${query.toUpperCase()}`, label: `${locale === 'zh-Hant' ? '開啟' : '打开'} ${query.toUpperCase()} 行情`, icon: CandlestickChart, keywords: query, meta: '直接打开代码' }]
      : []
    const unique = new Map<string, typeof staticItems[number]>()
    for (const item of [...staticItems, ...marketItems, ...direct]) unique.set(`${item.to}|${item.label}`, item)
    return [...unique.values()]
  }, [commandQuery, locale, marketMatches, workspace.data?.settings.watchlists])

  const runCommand = (to: string) => {
    navigate(to)
    setCommandOpen(false)
    setCommandQuery('')
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <aside className="sidebar" aria-label="主要导航">
        <NavLink className="brand" to="/today" aria-label="CicloTrade 今日工作台">
          <img src="/brand/ciclotrade-mark.webp" alt="" />
          <span><strong>CicloTrade</strong><small>DECISION TERMINAL</small></span>
        </NavLink>
        <nav>
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'} to={to}>
              <Icon size={18} /> <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <NavLink className="membership-mini" to="/membership">
            <span className="membership-mark"><ShieldCheck size={16} /></span>
            <span><small>当前方案</small><strong>{workspace.user?.plan_display_name ?? '演示模式'}</strong></span>
            <ChevronRight size={16} />
          </NavLink>
          <p>专业决策终端 · 受控演示</p>
        </div>
      </aside>

      <div className="shell-content">
        <header className="topbar">
          <button ref={mobileMenuButton} className="mobile-menu-button" type="button" aria-label="打开更多菜单" aria-expanded={mobileMenuOpen} onClick={() => setMobileMenuOpen(true)}>
            <Menu size={20} />
          </button>
          <button className="command-search" type="button" aria-haspopup="dialog" onClick={() => setCommandOpen(true)}>
            <Search size={17} />
            <span>搜索股票、页面或功能</span>
            <kbd>Ctrl K</kbd>
          </button>
          <div className="global-status" aria-label="系统状态">
            <span className="live-status"><i /> 美股开盘</span>
            <span>数据 · {realData ? '真实记录' : workspace.mode === 'offline' ? '离线演示' : '演示'}</span>
            <NavLink to="/notifications"><Bot size={16} /> TG {workspace.data?.telegram.consented ? '正常' : '演示'}</NavLink>
            <button className="locale-button" type="button" title={locale === 'zh-Hant' ? '切换为简体中文' : '切换为繁体中文'} aria-label={locale === 'zh-Hant' ? '切换为简体中文' : '切换为繁体中文'} onClick={() => void setLocale(locale === 'zh-Hant' ? 'zh-Hans' : 'zh-Hant')}><Languages size={17} /><span>{locale === 'zh-Hant' ? '繁' : '简'}</span></button>
            <span className="sr-only" role="status" aria-live="polite">{syncState === 'saving' ? '正在同步语言偏好' : syncState === 'saved' ? '语言偏好已保存' : syncState === 'error' ? '账户同步失败，语言偏好已保存在本机' : ''}</span>
            <button className="user-menu" type="button" aria-label={realData ? '打开账户' : '登录账户'} onClick={() => navigate(realData ? '/account' : '/login')}><UserRound size={17} /></button>
          </div>
        </header>

        <div className="status-strip" role="status" aria-live="polite">
          <span><i className="positive-dot" /> {realData ? '真实记录连接 · 受控写入' : '界面演示数据'}</span>
          <span><ShieldCheck size={14} /> 风险闸门正常</span>
          <span><FlaskConical size={14} /> 挑战模型仅影子运行</span>
          <strong>{realData ? 'REAL RECORDS · PROTECTED WRITES' : 'DEMO DATA'} · 不构成投资建议</strong>
        </div>

        <main id="main-content">{children}</main>
      </div>

      <nav className="mobile-nav" aria-label="移动端主要导航">
        {navItems.slice(0, 4).map(({ to, label, icon: Icon }) => (
          <NavLink key={to} className={({ isActive }) => isActive ? 'active' : ''} to={to}>
            <Icon size={20} /><span>{label}</span>
          </NavLink>
        ))}
        <button type="button" onClick={() => setMobileMenuOpen(true)}><Menu size={20} /><span>更多</span></button>
      </nav>

      {mobileMenuOpen && (
        <div className="sheet-backdrop" role="presentation" onClick={() => setMobileMenuOpen(false)}>
          <section ref={mobileSheet} className="mobile-sheet" role="dialog" aria-modal="true" aria-label="更多功能" onClick={(event) => event.stopPropagation()}>
            <header><img src="/brand/ciclotrade-mark.webp" alt="" /><h2>更多功能</h2><button ref={closeButton} className="icon-button" type="button" aria-label="关闭" onClick={() => setMobileMenuOpen(false)}><X size={20} /></button></header>
            {moreItems.map(({ to, label, icon: Icon }) => (
              <NavLink key={to} to={to} onClick={() => setMobileMenuOpen(false)}><Icon size={19} />{label}<ChevronRight size={17} /></NavLink>
            ))}
          </section>
        </div>
      )}

      {commandOpen && (
        <div className="command-backdrop" role="presentation" onClick={() => setCommandOpen(false)}>
          <section className="command-palette" role="dialog" aria-modal="true" aria-label="全局搜索" onClick={(event) => event.stopPropagation()}>
            <header><Search size={19} /><input ref={commandInput} aria-label="搜索股票、页面或功能" placeholder="输入股票代码、页面或功能" value={commandQuery} onChange={(event) => setCommandQuery(event.target.value)} /><button className="icon-button" type="button" aria-label="关闭搜索" onClick={() => setCommandOpen(false)}><X size={19} /></button></header>
            <div className="command-results">
              {commandItems.map(({ to, label, icon: Icon, meta }) => <button type="button" key={`${to}-${label}`} onClick={() => runCommand(to)}><Icon size={18} /><span><strong>{label}</strong><small>{meta}</small></span><ChevronRight size={16} /></button>)}
              {commandStatus && <p className="command-status">{commandStatus.startsWith('正在') && <LoaderCircle size={15} />}{commandStatus}</p>}
              {!commandItems.length && !commandStatus && <p>没有匹配结果</p>}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
