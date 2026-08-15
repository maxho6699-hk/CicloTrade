import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  BellRing,
  BookOpenCheck,
  Bot,
  CandlestickChart,
  CalendarClock,
  ChartCandlestick,
  ChevronRight,
  ClipboardCheck,
  LockKeyhole,
  MessageSquareText,
  FlaskConical,
  Gauge,
  Grid2X2,
  House,
  Languages,
  HelpCircle,
  LifeBuoy,
  ListFilter,
  LogOut,
  LoaderCircle,
  Moon,
  Radar,
  RadioTower,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  Sun,
  Target,
  Trash2,
  UserRound,
  WalletCards,
  X,
  type LucideIcon,
} from 'lucide-react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
  FEATURE_CATALOG_UPDATED_EVENT,
  fetchFeatureCatalog,
  searchMarket,
  type MarketSearchItem,
} from '../api/client'
import { useWorkspace } from '../api/workspace-context'
import { useLocale } from '../i18n/useLocale'
import { localeSearchText } from '../i18n/runtime'
import { WatchlistToggle } from './WatchlistToggle'
import type { Market } from '../types'
import { applyTheme, readStoredTheme, type Theme } from '../theme'
import { displayDeliveryDelay, displayFreshness } from '../domain/dataSourcePresentation'
import { localizeFeature, type FeatureCatalogPayload } from '../domain/featureCatalog'

interface AppShellProps {
  children: ReactNode
}

const navItems = [
  { to: '/today', key: 'today', icon: House },
  { to: '/discover', key: 'discover', icon: Radar },
  { to: '/research', key: 'research', icon: CandlestickChart },
  { to: '/paper', key: 'paper', icon: WalletCards },
  { to: '/portfolio', key: 'portfolio', icon: ClipboardCheck },
  { to: '/more', key: 'more', icon: Grid2X2 },
] as const

const mobileNavItems = [
  { to: '/today', key: 'today', icon: House },
  { to: '/discover', key: 'discover', icon: Radar },
  { to: '/research', key: 'market', icon: CandlestickChart },
  { to: '/paper', key: 'paper', icon: WalletCards },
  { to: '/more', key: 'moreShort', icon: Grid2X2 },
] as const
const feedbackItem = { to: '/feedback', label: '反馈建议', icon: MessageSquareText }
const promotionItem = { to: '/promotion', label: '推广中心', icon: Target }
const NAV_COPY = {
  'zh-Hans': { today: '今日', discover: '发现', research: '行情与研究', market: '行情', paper: '模拟', portfolio: '组合与复盘', more: '更多功能', moreShort: '更多', pinned: '固定工具' },
  'zh-Hant': { today: '今日', discover: '發現', research: '行情與研究', market: '行情', paper: '模擬', portfolio: '組合與複盤', more: '更多功能', moreShort: '更多', pinned: '固定工具' },
} as const
const AI_COPY = {
  'zh-Hans': {
    label: 'Ciclo AI',
    status: '当前未启用',
    title: 'AI 工作台正在安全接入',
    detail: '真实 AI 会话与任务服务尚未启用。当前入口不会生成假回答，也不能提交模拟或实盘订单。',
    action: '先进入行情与研究',
  },
  'zh-Hant': {
    label: 'Ciclo AI',
    status: '目前未啟用',
    title: 'AI 工作台正在安全接入',
    detail: '真實 AI 會話與任務服務尚未啟用。目前入口不會生成假回答，也不能提交模擬或實盤訂單。',
    action: '先進入行情與研究',
  },
} as const
const FEATURE_ICONS: Record<string, LucideIcon> = {
  BellRing, BookOpenCheck, CalendarClock, ChartCandlestick, ClipboardCheck, Gauge, Grid2X2,
  LifeBuoy, ListFilter, RadioTower, ShieldCheck, Sparkles, Target, WalletCards,
}

const SEARCH_HISTORY_STORAGE_KEY = 'ciclotrade.searchHistory'
const MAX_RECENT_SEARCHES = 6

interface CommandHistoryItem {
  to: string
  label: string
  meta: string
}

interface CommandItem extends CommandHistoryItem {
  icon: LucideIcon
  keywords: string
  market?: Market
  symbol?: string
}

function storedSearchHistory(): CommandHistoryItem[] {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(SEARCH_HISTORY_STORAGE_KEY) ?? '[]')
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item): item is CommandHistoryItem => (
      typeof item === 'object' && item !== null
      && typeof item.to === 'string' && typeof item.label === 'string' && typeof item.meta === 'string'
    )).slice(0, MAX_RECENT_SEARCHES)
  } catch {
    return []
  }
}

export function AppShell({ children }: AppShellProps) {
  const [commandOpen, setCommandOpen] = useState(false)
  const [commandQuery, setCommandQuery] = useState('')
  const [marketMatches, setMarketMatches] = useState<MarketSearchItem[]>([])
  const [commandStatus, setCommandStatus] = useState('')
  const [commandWatchBusy, setCommandWatchBusy] = useState('')
  const [theme, setTheme] = useState<Theme>(readStoredTheme)
  const [aiPanelOpen, setAiPanelOpen] = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [recentSearches, setRecentSearches] = useState<CommandHistoryItem[]>(storedSearchHistory)
  const [featureCatalog, setFeatureCatalog] = useState<FeatureCatalogPayload | null>(null)
  const commandInput = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const workspace = useWorkspace()
  const { locale, setLocale, syncState } = useLocale()
  const realData = workspace.mode === 'authenticated'
  const marketStatus = workspace.data?.market_data
  const marketDelay = displayDeliveryDelay(marketStatus?.delivery_delay_minutes)
  const marketFreshness = displayFreshness(marketStatus?.freshness)
  const marketDisconnected = marketFreshness === '已停用' || marketFreshness === '未启用或暂不可用'
  const telegramReady = Boolean(workspace.data?.telegram.bound && workspace.data?.telegram.verified && workspace.data?.telegram.consented)
  const hasModelSnapshots = Boolean(workspace.data?.performance.items.length)
  const isSuperAdmin = workspace.user?.admin_role === 'super_admin'
  const navCopy = NAV_COPY[locale]
  const aiCopy = AI_COPY[locale]
  const secondaryTools = useMemo(() => {
    if (!featureCatalog) return []
    const byKey = new Map(featureCatalog.items.map((item) => [item.key, item]))
    return featureCatalog.preferences.pinned.flatMap((key) => {
      const item = byKey.get(key)
      if (!item || item.primaryNav || !item.pinAllowed || item.availability !== 'available' || !item.placements.includes('secondary_nav')) return []
      const copy = localizeFeature(item, locale)
      return [{ to: item.route, label: copy.title, icon: FEATURE_ICONS[item.icon] }]
    })
  }, [featureCatalog, locale])
  const marketStatusLabel = !realData
    ? workspace.mode === 'offline' ? '离线演示' : '界面演示'
    : marketDisconnected ? '未连接' : marketDelay || (marketFreshness === '状态未记录' && marketStatus?.is_realtime ? '实时权限已验证' : marketFreshness)

  useEffect(() => {
    applyTheme(theme, true)
  }, [theme])

  useEffect(() => {
    let active = true
    const refresh = () => {
      void fetchFeatureCatalog().then((payload) => { if (active) setFeatureCatalog(payload) }).catch(() => { if (active) setFeatureCatalog(null) })
    }
    const receive = (event: Event) => {
      const detail = (event as CustomEvent<FeatureCatalogPayload>).detail
      if (active && detail) setFeatureCatalog(detail)
      else refresh()
    }
    refresh()
    window.addEventListener(FEATURE_CATALOG_UPDATED_EVENT, receive)
    return () => { active = false; window.removeEventListener(FEATURE_CATALOG_UPDATED_EVENT, receive) }
  }, [])

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
    if (!commandOpen) return
    commandInput.current?.focus()
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setCommandOpen(false)
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [commandOpen])

  useEffect(() => {
    if (!aiPanelOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setAiPanelOpen(false)
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [aiPanelOpen])

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
    const savedItems: CommandItem[] = [
      ...saved.us.map((symbol): CommandItem => ({ to: `/markets?market=US&symbol=${symbol}`, label: `${symbol} 行情`, icon: CandlestickChart, keywords: `${symbol} ${localeSearchText('自选')} ${localeSearchText('美股')}`, meta: '我的自选', market: 'US', symbol })),
      ...saved.a_share.map((symbol): CommandItem => ({ to: `/markets?market=CN&symbol=${symbol}`, label: `${symbol} 行情`, icon: CandlestickChart, keywords: `${symbol} ${localeSearchText('自选')} ${localeSearchText('A股')}`, meta: '我的自选', market: 'CN', symbol })),
    ].filter((item) => !needle || `${localeSearchText(item.label)} ${localeSearchText(item.keywords)}`.toLowerCase().includes(needle))
    const demoItems: CommandItem[] = workspace.mode !== 'authenticated' ? [
      { to: '/markets?market=US&symbol=AAPL', label: 'AAPL · Apple', icon: CandlestickChart, keywords: 'AAPL Apple 美股', meta: '界面演示标的', market: 'US' as const, symbol: 'AAPL' },
      { to: '/markets?market=US&symbol=NVDA', label: 'NVDA · NVIDIA', icon: CandlestickChart, keywords: 'NVDA NVIDIA 美股', meta: '界面演示标的', market: 'US' as const, symbol: 'NVDA' },
      { to: '/markets?market=US&symbol=TSLA', label: 'TSLA · Tesla', icon: CandlestickChart, keywords: 'TSLA Tesla 美股', meta: '界面演示标的', market: 'US' as const, symbol: 'TSLA' },
      { to: '/markets?market=US&symbol=MSFT', label: 'MSFT · Microsoft', icon: CandlestickChart, keywords: 'MSFT Microsoft 美股', meta: '界面演示标的', market: 'US' as const, symbol: 'MSFT' },
      { to: '/markets?market=CN&symbol=600519', label: '600519 · 贵州茅台', icon: CandlestickChart, keywords: '600519 贵州茅台 A股', meta: '界面演示标的', market: 'CN' as const, symbol: '600519' },
    ].filter((item) => !needle || `${item.label} ${item.keywords}`.toLowerCase().includes(needle)) : []
    const marketItems: CommandItem[] = marketMatches.map((item) => ({
      to: `/markets?market=${item.market}&symbol=${item.symbol.replace(/\.(SS|SZ)$/, '')}`,
      label: `${item.symbol.replace(/\.(SS|SZ)$/, '')} · ${item.name}`,
      icon: CandlestickChart,
      keywords: `${item.symbol} ${item.name}`,
      meta: item.market === 'CN' ? 'A股市场' : '美股市场',
      market: item.market as Market,
      symbol: item.symbol.replace(/\.(SS|SZ)$/, ''),
    }))
    const directMarket = /^\d{6}$/.test(query) ? 'CN' : 'US'
    const direct: CommandItem[] = /^(?:[A-Za-z][A-Za-z0-9.=-]{0,14}|\d{6})$/.test(query)
      ? [{ to: `/markets?market=${directMarket}&symbol=${query.toUpperCase()}`, label: `${locale === 'zh-Hant' ? '開啟' : '打开'} ${query.toUpperCase()} 行情`, icon: CandlestickChart, keywords: query, meta: '直接打开代码', market: directMarket, symbol: query.toUpperCase() }]
      : []
    const unique = new Map<string, CommandItem>()
    for (const item of [...savedItems, ...demoItems, ...marketItems, ...direct]) unique.set(`${item.to}|${item.label}`, item)
    return [...unique.values()]
  }, [commandQuery, locale, marketMatches, workspace.data?.settings.watchlists, workspace.mode])

  const runCommand = (item: CommandHistoryItem) => {
    const nextHistory = [item, ...recentSearches.filter((entry) => entry.to !== item.to || entry.label !== item.label)].slice(0, MAX_RECENT_SEARCHES)
    setRecentSearches(nextHistory)
    try { window.localStorage.setItem(SEARCH_HISTORY_STORAGE_KEY, JSON.stringify(nextHistory)) } catch { /* storage can be disabled */ }
    navigate(item.to)
    setCommandOpen(false)
    setCommandQuery('')
  }

  const toggleCommandWatchlist = async (item: CommandItem, remove: boolean) => {
    if (!item.market || !item.symbol || !realData) return
    const busyKey = `${item.market}:${item.symbol}`
    setCommandWatchBusy(busyKey)
    try {
      await workspace.changeWatchlist(item.market, item.symbol, remove)
      setCommandStatus(remove ? `${item.symbol} 已从自选移除` : `${item.symbol} 已加入自选`)
    } catch (caught) {
      setCommandStatus(caught instanceof Error ? caught.message : '自选更新失败。')
    } finally {
      setCommandWatchBusy('')
    }
  }

  const removeRecentSearch = (item: CommandHistoryItem) => {
    const nextHistory = recentSearches.filter((entry) => entry.to !== item.to || entry.label !== item.label)
    setRecentSearches(nextHistory)
    try { window.localStorage.setItem(SEARCH_HISTORY_STORAGE_KEY, JSON.stringify(nextHistory)) } catch { /* storage can be disabled */ }
  }

  const clearRecentSearches = () => {
    setRecentSearches([])
    try { window.localStorage.removeItem(SEARCH_HISTORY_STORAGE_KEY) } catch { /* storage can be disabled */ }
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <aside className="sidebar" aria-label="主要导航">
        <NavLink className="brand" to="/today" aria-label="CicloTrade 今日工作台">
          <img src="/brand/ciclotrade-logo.jpg" alt="" />
          <span><strong>CicloTrade</strong><small>DECISION TERMINAL</small></span>
        </NavLink>
        <nav>
          {navItems.map(({ to, key, icon: Icon }) => (
            <NavLink key={to} className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'} to={to}>
              <Icon size={18} /> <span>{navCopy[key]}</span>
            </NavLink>
          ))}
          {secondaryTools.length > 0 && <section className="secondary-tools" aria-label={navCopy.pinned}>
            <h2>{navCopy.pinned}</h2>
            {secondaryTools.map(({ to, label, icon: Icon }) => <NavLink key={to} className={({ isActive }) => isActive ? 'nav-item secondary active' : 'nav-item secondary'} to={to}><Icon size={17} /><span>{label}</span></NavLink>)}
          </section>}
        </nav>
        <div className="sidebar-bottom">
          <NavLink className="sidebar-feedback" to={feedbackItem.to} state={{ sourcePage: pathname }}><MessageSquareText size={16} /><span>{feedbackItem.label}</span><ChevronRight size={15} /></NavLink>
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
          <button className="command-search" type="button" aria-haspopup="dialog" onClick={() => setCommandOpen(true)}>
            <Search size={17} />
            <span>搜索股票</span>
            <kbd>Ctrl K</kbd>
          </button>
          <div className="global-status" aria-label="系统状态">
            <div className="ai-launcher">
              <button
                className="ai-pill"
                type="button"
                aria-haspopup="dialog"
                aria-expanded={aiPanelOpen}
                onClick={() => {
                  setUserMenuOpen(false)
                  setAiPanelOpen((current) => !current)
                }}
              >
                <Bot aria-hidden="true" />
                <span>{aiCopy.label}</span>
              </button>
              {aiPanelOpen && <section className="ai-unavailable-popover" role="dialog" aria-modal="false" aria-labelledby="ciclo-ai-title">
                <header>
                  <span className="ai-unavailable-icon"><LockKeyhole aria-hidden="true" /></span>
                  <span><strong id="ciclo-ai-title">{aiCopy.title}</strong><small>{aiCopy.status}</small></span>
                </header>
                <p>{aiCopy.detail}</p>
                <NavLink to="/research" onClick={() => setAiPanelOpen(false)}>{aiCopy.action}<ChevronRight aria-hidden="true" /></NavLink>
              </section>}
            </div>
            <span className="live-status"><i /> {realData ? marketDisconnected ? '行情未连接' : marketStatusLabel : '界面演示'}</span>
            <span>行情 · {marketStatusLabel}</span>
            <NavLink to="/notifications"><Bot size={16} /> TG {telegramReady ? '已验证' : realData ? '未连接' : '演示'}</NavLink>
            <button className="locale-button" type="button" title={locale === 'zh-Hant' ? '切换为简体中文' : '切换为繁体中文'} aria-label={locale === 'zh-Hant' ? '切换为简体中文' : '切换为繁体中文'} onClick={() => void setLocale(locale === 'zh-Hant' ? 'zh-Hans' : 'zh-Hant')}><Languages size={17} /><span>{locale === 'zh-Hant' ? '繁' : '简'}</span></button>
            <button className="theme-button" type="button" title={theme === 'dark' ? '切换为浅色界面' : '切换为深色界面'} aria-label={theme === 'dark' ? '切换为浅色界面' : '切换为深色界面'} aria-pressed={theme === 'light'} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
              {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <span className="sr-only" role="status" aria-live="polite">{syncState === 'saving' ? '正在同步语言偏好' : syncState === 'saved' ? '语言偏好已保存' : syncState === 'error' ? '账户同步失败，语言偏好已保存在本机' : ''}</span>
            <button className="user-menu" type="button" aria-label="打开账户菜单" aria-expanded={userMenuOpen} onClick={() => { setAiPanelOpen(false); setUserMenuOpen((current) => !current) }}><UserRound size={17} /></button>
            {userMenuOpen && <div className="account-popover">
              <header><strong>{workspace.user?.display_name ?? 'CicloTrade 用户'}</strong><small>{workspace.user?.plan_display_name ?? '账户'}</small></header>
              <a href="/" onClick={() => setUserMenuOpen(false)}><House size={16} /> 返回欢迎页</a>
              <NavLink to="/notifications" onClick={() => setUserMenuOpen(false)}><BellRing size={16} /> 消息通知</NavLink>
              <NavLink to="/account" onClick={() => setUserMenuOpen(false)}><Settings size={16} /> 用户设定</NavLink>
              <NavLink to="/membership" onClick={() => setUserMenuOpen(false)}><ShieldCheck size={16} /> 订阅会员</NavLink>
              <NavLink to={promotionItem.to} onClick={() => setUserMenuOpen(false)}><Target size={16} /> {promotionItem.label}</NavLink>
              <NavLink to="/help" onClick={() => setUserMenuOpen(false)}><HelpCircle size={16} /> 帮助与支持</NavLink>
              <NavLink to="/feedback" onClick={() => setUserMenuOpen(false)}><MessageSquareText size={16} /> 反馈建议</NavLink>
              {isSuperAdmin && <NavLink to="/admin" onClick={() => setUserMenuOpen(false)}><ShieldCheck size={16} /> 超级管理</NavLink>}
              <button type="button" onClick={() => void workspace.logout().then(() => navigate('/'))}><LogOut size={16} /> 登出账户</button>
            </div>}
          </div>
        </header>

        <div className="status-strip" role="status" aria-live="polite">
          <span><i className="positive-dot" /> {realData ? marketDisconnected ? '真实行情数据未连接' : `${marketDelay ? '受控行情已连接' : '真实行情数据已连接'} · ${marketStatusLabel}` : '界面演示数据'}</span>
          <span><ShieldCheck size={14} /> {realData ? '风控设置已载入' : '风险状态为演示'}</span>
          <span><FlaskConical size={14} /> {hasModelSnapshots ? '模型快照已载入' : '模型运行状态未提供'}</span>
          <strong>{realData ? 'MARKET DATA · VERIFIED STATUS' : 'DEMO DATA'} · 不构成投资建议</strong>
        </div>

        <main id="main-content">{children}</main>
      </div>

      <nav className="mobile-nav" aria-label="移动端主要导航">
        {mobileNavItems.map(({ to, key, icon: Icon }) => (
          <NavLink key={to} className={({ isActive }) => isActive ? 'active' : ''} to={to}>
            <Icon size={20} /><span>{navCopy[key]}</span>
          </NavLink>
        ))}
      </nav>

      {commandOpen && (
        <div className="command-backdrop" role="presentation" onClick={() => setCommandOpen(false)}>
          <section className="command-palette stock-picker" role="dialog" aria-modal="true" aria-label="搜索股票" onClick={(event) => event.stopPropagation()}>
            <header><Search size={19} /><input ref={commandInput} aria-label="搜索股票代码或名称" placeholder="输入股票代码或名称" value={commandQuery} onChange={(event) => setCommandQuery(event.target.value)} /><button className="icon-button" type="button" aria-label="关闭股票搜索" onClick={() => setCommandOpen(false)}><X size={19} /></button></header>
            <div className="command-results">
              {!commandQuery.trim() && recentSearches.length > 0 && (
                <section className="recent-searches" aria-label="最近搜索">
                  <div className="command-section-heading"><span>最近搜索</span><button type="button" onClick={clearRecentSearches}><Trash2 size={14} />清空</button></div>
                  {recentSearches.map((item) => (
                    <div className="recent-search-row" key={`${item.to}-${item.label}`}>
                      <button type="button" onClick={() => runCommand(item)}><Search size={17} /><span><strong>{item.label}</strong><small>{item.meta}</small></span><ChevronRight size={16} /></button>
                      <button className="recent-remove" type="button" title={`移除 ${item.label}`} aria-label={`移除 ${item.label}`} onClick={() => removeRecentSearch(item)}><X size={15} /></button>
                    </div>
                  ))}
                </section>
              )}
              {!commandQuery.trim() && <div className="command-section-heading command-suggestions-heading"><span>选择股票</span></div>}
              {commandItems.map((item) => {
                const Icon = item.icon
                const saved = item.market && item.symbol
                  ? (item.market === 'CN' ? workspace.data?.settings.watchlists.a_share : workspace.data?.settings.watchlists.us)?.includes(item.symbol) ?? false
                  : false
                const busyKey = item.market && item.symbol ? `${item.market}:${item.symbol}` : ''
                return (
                  <div className="command-result-row" key={`${item.to}-${item.label}`}>
                    <button className="command-result-main" type="button" onClick={() => runCommand(item)}><Icon size={18} /><span><strong>{item.label}</strong><small>{item.meta}</small></span><ChevronRight size={16} /></button>
                    {realData && item.market && item.symbol && <WatchlistToggle symbol={item.symbol} saved={saved} busy={commandWatchBusy === busyKey} onToggle={(remove) => toggleCommandWatchlist(item, remove)} />}
                  </div>
                )
              })}
              {commandStatus && <p className="command-status">{commandStatus.startsWith('正在') && <LoaderCircle size={15} />}{commandStatus}</p>}
              {!commandItems.length && !commandStatus && <p>没有匹配结果</p>}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
