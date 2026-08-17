import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  Bot,
  CandlestickChart,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  Swords,
  FlaskConical,
  Grid2X2,
  House,
  Languages,
  LogOut,
  LoaderCircle,
  Moon,
  Radar,
  Search,
  ShieldCheck,
  Sparkles,
  Sun,
  Trash2,
  UserRound,
  WalletCards,
  X,
  type LucideIcon,
} from 'lucide-react'
import { autoLiveApi, type AutoLivePauseResult, type AutoLiveSnapshot } from '../api/autoLive'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
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
import { MORE_NAV_ROUTES, PRIMARY_NAV_GROUPS, type PrimaryNavGroupKey, type PrimaryNavIconName } from '../domain/featureCatalog'
import { deriveAutoLiveKillSwitchView, type AutoLiveSnapshotState } from '../domain/autoLiveSafety'
import { createSessionIdempotencyRegistry } from '../domain/sessionIdempotency'
import { useCicloTier } from '../api/use-ciclo-tier'
import { CicloCore } from './paper/CicloCore'

interface AppShellProps {
  children: ReactNode
}

const NAV_ICONS: Record<PrimaryNavIconName, LucideIcon> = {
  Radar, House, Sparkles, CandlestickChart, Swords, WalletCards, ClipboardCheck, UserRound, ShieldCheck,
}
const navItems = PRIMARY_NAV_GROUPS.map((group) => ({
  ...group,
  to: group.route,
  icon: NAV_ICONS[group.icon],
  routes: group.items.map((item) => item.route),
  items: group.items.map((item) => ({ ...item, to: item.route, icon: NAV_ICONS[item.icon] })),
}))
const mobileNavItems = navItems.map(({ to, key, icon, routes }) => ({ to, key, icon, routes }))
const NAV_COPY = {
  'zh-Hans': { opportunity: '机会', opportunityMobile: '机会', judgment: '研判', simulation: '模拟战绩', simulationShort: '模拟', mine: '我的', today: '今日', discover: '发现', recommendations: '推荐', research: '行情研究', paper: '个人模拟', portfolio: '组合复盘', deliberation: '多空观点对照', account: '账户', membership: '会员', more: '更多功能' },
  'zh-Hant': { opportunity: '機會', opportunityMobile: '機會', judgment: '研判', simulation: '模擬戰績', simulationShort: '模擬', mine: '我的', today: '今日', discover: '發現', recommendations: '推薦', research: '行情研究', paper: '個人模擬', portfolio: '組合複盤', deliberation: '多空觀點對照', account: '帳戶', membership: '會員', more: '更多功能' },
} as const
const AI_COPY = {
  'zh-Hans': {
    label: 'Ciclo AI',
  },
  'zh-Hant': {
    label: 'Ciclo AI',
  },
} as const
const SEARCH_HISTORY_STORAGE_KEY = 'ciclotrade.searchHistory'
const MAX_RECENT_SEARCHES = 6

function pathMatchesRoute(pathname: string, route: string) {
  return pathname === route || pathname.startsWith(`${route}/`)
}

function visualFamilyForPath(pathname: string, locale: 'zh-Hans' | 'zh-Hant') {
  const traditional = locale === 'zh-Hant'
  if (pathname === '/paper' || pathname === '/portfolio') return { key: 'assets', label: traditional ? '帳戶資產' : '账户资产' }
  if (pathname === '/membership' || pathname === '/promotion') return { key: 'growth', label: traditional ? '商業增長' : '商业增长' }
  if (['/more', '/notifications', '/help', '/feedback', '/mystic'].includes(pathname)) return { key: 'service', label: traditional ? '系統服務' : '系统服务' }
  if (pathname === '/admin') return { key: 'admin', label: traditional ? '管理後台' : '管理后台' }
  if (['/earnings', '/reports', '/lab', '/trade', '/ai', '/workflow', '/deliberation'].some((route) => pathname === route || pathname.startsWith(`${route}/`))) return { key: 'intelligence', label: traditional ? 'AI 能力' : 'AI 能力' }
  return { key: 'workspace', label: traditional ? '核心工作台' : '核心工作台' }
}

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

function GlobalAutoLiveKillSwitch({ snapshot, snapshotState }: { snapshot: AutoLiveSnapshot | null; snapshotState: AutoLiveSnapshotState }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<AutoLivePauseResult | null>(null)
  const [pauseUnknown, setPauseUnknown] = useState(false)
  const idempotency = useRef(createSessionIdempotencyRegistry('ciclotrade.autoLivePending.v1'))
  const view = deriveAutoLiveKillSwitchView(snapshot, snapshotState, result, pauseUnknown)
  if (!view.visible) return null
  const compactLabel = view.tone === 'unknown'
    ? snapshotState === 'loading' ? '状态读取中' : pauseUnknown ? '暂停未知' : '状态未知'
    : view.tone === 'failed' ? '暂停失败'
      : view.tone === 'partial' ? result ? `暂停 ${result.confirmed}/${result.total}` : '暂停确认中'
        : view.tone === 'stale' ? '状态过期'
          : '暂停实盘'
  const pauseAll = async () => {
    if (busy) return
    setBusy(true)
    const fingerprint = JSON.stringify({ scope: 'aggregate' })
    const key = idempotency.current.key('pause-aggregate', fingerprint)
    try {
      const next = await autoLiveApi.pause({ scope: 'aggregate' }, key)
      setResult(next)
      setPauseUnknown(false)
      idempotency.current.clear('pause-aggregate', fingerprint)
    } catch { setPauseUnknown(true) }
    finally { setBusy(false) }
  }
  return <button className={`global-auto-live-kill-switch is-${view.tone}`} type="button" title={view.label} onClick={() => void pauseAll()} disabled={busy} aria-label={view.label}><ShieldCheck size={15} /><span role="status" aria-live="polite">{busy ? '暂停中…' : compactLabel}</span></button>
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
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [expandedNavGroup, setExpandedNavGroup] = useState<PrimaryNavGroupKey | null>(null)
  const [recentSearches, setRecentSearches] = useState<CommandHistoryItem[]>(storedSearchHistory)
  const [autoLiveSnapshot, setAutoLiveSnapshot] = useState<AutoLiveSnapshot | null>(null)
  const [autoLiveSnapshotState, setAutoLiveSnapshotState] = useState<AutoLiveSnapshotState>('idle')
  const commandInput = useRef<HTMLInputElement>(null)
  const commandTrigger = useRef<HTMLButtonElement>(null)
  const commandPalette = useRef<HTMLElement>(null)
  const userMenuTrigger = useRef<HTMLButtonElement>(null)
  const accountPopover = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const workspace = useWorkspace()
  const cicloTier = useCicloTier()
  const { locale, setLocale, syncState } = useLocale()
  const visualFamily = visualFamilyForPath(pathname, locale)
  const realData = workspace.mode === 'authenticated'
  const marketStatus = workspace.data?.market_data
  const marketDelay = displayDeliveryDelay(marketStatus?.delivery_delay_minutes)
  const marketFreshness = displayFreshness(marketStatus?.freshness)
  const marketDisconnected = marketFreshness === '已停用' || marketFreshness === '未启用或暂不可用'
  const telegramReady = Boolean(workspace.data?.telegram.bound && workspace.data?.telegram.verified && workspace.data?.telegram.consented)
  const hasModelSnapshots = Boolean(workspace.data?.performance.items.length)
  const isSuperAdmin = workspace.user?.admin_role === 'super_admin'
  const aiAvailable = workspace.mode === 'authenticated'
  const navCopy = NAV_COPY[locale]
  const activePrimaryNav = navItems.find((item) => item.routes.some((route) => pathMatchesRoute(pathname, route)))
  const moreNavActive = MORE_NAV_ROUTES.some((route) => pathMatchesRoute(pathname, route))
  const ActiveRouteIcon = activePrimaryNav?.icon ?? Grid2X2
  const activeRouteLabel = activePrimaryNav ? navCopy[activePrimaryNav.key] : moreNavActive ? navCopy.more : visualFamily.label
  const aiCopy = AI_COPY[locale]
  const marketStatusLabel = !realData
    ? workspace.mode === 'offline' ? '离线演示' : '界面演示'
    : marketDisconnected ? '未连接' : marketDelay || (marketFreshness === '状态未记录' && marketStatus?.is_realtime ? '实时权限已验证' : marketFreshness)

  useEffect(() => {
    setExpandedNavGroup(navItems.find((item) => item.routes.some((route) => pathMatchesRoute(pathname, route)))?.key ?? null)
  }, [pathname])

  useEffect(() => {
    let active = true
    const refreshAutoLive = () => {
      if (!realData) { setAutoLiveSnapshot(null); setAutoLiveSnapshotState('idle'); return }
      setAutoLiveSnapshotState('loading')
      void autoLiveApi.snapshot().then((payload) => {
        if (!active) return
        setAutoLiveSnapshot(payload)
        setAutoLiveSnapshotState('fresh')
      }).catch(() => {
        if (active) setAutoLiveSnapshotState('stale')
      })
    }
    refreshAutoLive()
    const timer = window.setInterval(refreshAutoLive, 15_000)
    return () => { active = false; window.clearInterval(timer) }
  }, [realData])

  useEffect(() => {
    applyTheme(theme, true)
  }, [theme])

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
    const previousOverflow = document.body.style.overflow
    const trigger = commandTrigger.current
    document.body.style.overflow = 'hidden'
    commandInput.current?.focus()
    const closeOnEscapeAndTrapFocus = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setCommandOpen(false)
        return
      }
      if (event.key !== 'Tab') return
      const focusable = Array.from(commandPalette.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), [href], select:not([disabled]), textarea:not([disabled])') ?? [])
      if (!focusable.length) {
        event.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', closeOnEscapeAndTrapFocus)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', closeOnEscapeAndTrapFocus)
      trigger?.focus()
    }
  }, [commandOpen])

  useEffect(() => {
    if (!userMenuOpen) return
    const trigger = userMenuTrigger.current
    const firstItem = accountPopover.current?.querySelector<HTMLElement>('[role="menuitem"]')
    firstItem?.focus()
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      setUserMenuOpen(false)
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('keydown', closeOnEscape)
      trigger?.focus()
    }
  }, [userMenuOpen])

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
        setCommandStatus(payload.items.length ? `找到 ${payload.items.length} 只股票` : '股票目录没有匹配结果')
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
      ...saved.us.map((symbol): CommandItem => ({ to: `/research?market=US&symbol=${symbol}`, label: `${symbol} 行情`, icon: CandlestickChart, keywords: `${symbol} ${localeSearchText('自选')} ${localeSearchText('美股')}`, meta: '我的自选', market: 'US', symbol })),
      ...saved.a_share.map((symbol): CommandItem => ({ to: `/research?market=CN&symbol=${symbol}`, label: `${symbol} 行情`, icon: CandlestickChart, keywords: `${symbol} ${localeSearchText('自选')} ${localeSearchText('A股')}`, meta: '我的自选', market: 'CN', symbol })),
    ].filter((item) => !needle || `${localeSearchText(item.label)} ${localeSearchText(item.keywords)}`.toLowerCase().includes(needle))
    const marketItems: CommandItem[] = marketMatches.map((item) => ({
      to: `/research?market=${item.market}&symbol=${item.symbol.replace(/\.(SS|SZ)$/, '')}`,
      label: `${item.symbol.replace(/\.(SS|SZ)$/, '')} · ${item.name}`,
      icon: CandlestickChart,
      keywords: `${item.symbol} ${item.name}`,
      meta: item.market === 'CN' ? 'A股市场' : '美股市场',
      market: item.market as Market,
      symbol: item.symbol.replace(/\.(SS|SZ)$/, ''),
    }))
    const directMarket = /^\d{6}$/.test(query) ? 'CN' : 'US'
    const direct: CommandItem[] = /^(?:[A-Za-z][A-Za-z0-9.=-]{0,14}|\d{6})$/.test(query)
      ? [{ to: `/research?market=${directMarket}&symbol=${query.toUpperCase()}`, label: `${locale === 'zh-Hant' ? '開啟' : '打开'} ${query.toUpperCase()} 行情`, icon: CandlestickChart, keywords: query, meta: '直接打开代码', market: directMarket, symbol: query.toUpperCase() }]
      : []
    const unique = new Map<string, CommandItem>()
    for (const item of [...savedItems, ...marketItems, ...direct]) unique.set(`${item.to}|${item.label}`, item)
    return [...unique.values()]
  }, [commandQuery, locale, marketMatches, workspace.data?.settings.watchlists])

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
    <div className={`app-shell visual-family-${visualFamily.key}`} data-visual-family={visualFamily.key}>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <aside className="sidebar" aria-label="主要导航">
        <NavLink className="brand" to="/today" aria-label="CicloTrade 今日工作台">
          <img src="/brand/ciclotrade-logo.jpg" alt="" width="32" height="32" />
          <span><strong>CicloTrade</strong><small>DECISION TERMINAL</small></span>
        </NavLink>
        <nav>
          {navItems.map(({ to, key, icon: Icon, routes, items }) => {
            const active = routes.some((route) => pathMatchesRoute(pathname, route))
            const expanded = expandedNavGroup === key
            const childrenId = `nav-group-${key}`
            return <section className={`nav-group ${active ? 'is-active' : ''} ${expanded ? 'is-expanded' : ''}`} key={to}>
              <div className="nav-group-header">
                <NavLink className={`nav-item nav-group-link ${active ? 'active' : ''}`} to={to} aria-label={navCopy[key]} title={navCopy[key]} onClick={() => setExpandedNavGroup(key)}>
                  <Icon size={20} aria-hidden="true" /><span>{navCopy[key]}</span>
                </NavLink>
                <button className="nav-group-toggle" type="button" aria-label={`${expanded ? '收起' : '展开'}${navCopy[key]}子菜单`} title={`${expanded ? '收起' : '展开'}${navCopy[key]}子菜单`} aria-controls={childrenId} aria-expanded={expanded} onClick={() => setExpandedNavGroup(expanded ? null : key)}>
                  <ChevronDown size={16} aria-hidden="true" />
                </button>
              </div>
              <div id={childrenId} className="nav-group-children" aria-label={`${navCopy[key]} 二级导航`}>
                {items.map(({ to: childTo, key: childKey, icon: ChildIcon, ...item }) => (
                  <NavLink key={childTo} className={({ isActive }) => `nav-item nav-child ${item.featured ? 'featured' : ''} ${isActive ? 'active' : ''}`.trim()} to={childTo} aria-label={navCopy[childKey]} title={navCopy[childKey]} onClick={() => setExpandedNavGroup(key)}>
                    <ChildIcon size={16} aria-hidden="true" /><span>{navCopy[childKey]}</span>
                  </NavLink>
                ))}
              </div>
            </section>
          })}
        </nav>
        <div className="sidebar-bottom">
          <p>专业决策终端 · 受控演示</p>
        </div>
      </aside>

      <div className="shell-content">
        <header className="topbar">
          <div className="topbar-leading">
            <div className="page-route-chip" aria-label={`${visualFamily.label} · ${activeRouteLabel}`}>
              <div className="page-route-core"><CicloCore label={`${activeRouteLabel} 会员机器人`} size="compact" tier={cicloTier} /></div>
              <ActiveRouteIcon className="page-route-icon" size={18} aria-hidden="true" />
              <span><small>{visualFamily.label}</small><strong>{activeRouteLabel}</strong></span>
            </div>
            <button ref={commandTrigger} className="command-search" type="button" aria-haspopup="dialog" aria-controls="command-palette" aria-expanded={commandOpen} onClick={() => setCommandOpen(true)}>
              <Search size={17} />
              <span>搜索股票</span>
              <kbd>Ctrl K</kbd>
            </button>
          </div>
          <div className="global-status" aria-label="系统状态">
            <div className="ai-launcher">
              <NavLink className={`ai-pill ${aiAvailable ? '' : 'is-locked'}`} to={aiAvailable ? '/ai' : '/membership'} aria-label={aiAvailable ? aiCopy.label : `${aiCopy.label} · 当前会员未解锁`} onClick={() => setUserMenuOpen(false)}>
                <Bot aria-hidden="true" />
                <span>{aiAvailable ? 'Ciclo AI' : 'Ciclo AI・未解锁'}</span>
              </NavLink>
            </div>
            <span className="live-status" title={realData ? marketDisconnected ? '行情未连接' : marketStatusLabel : '界面演示'}><i /> {realData ? marketDisconnected ? '行情未连' : marketStatusLabel : '演示'}</span>
            <GlobalAutoLiveKillSwitch snapshot={autoLiveSnapshot} snapshotState={realData && autoLiveSnapshotState === 'idle' ? 'loading' : autoLiveSnapshotState} />
              <span className="market-status-compact" title={`行情・${marketStatusLabel}`}><CandlestickChart size={15} />行情・{marketStatusLabel}</span>
            <NavLink to="/notifications" title={`Telegram ${telegramReady ? '已验证' : realData ? '未连接' : '演示'}`}><Bot size={16} />TG {telegramReady ? '已连线' : realData ? '未连线' : '演示'}</NavLink>
            <button className="locale-button" type="button" title={locale === 'zh-Hant' ? '切换为简体中文' : '切换为繁体中文'} aria-label={locale === 'zh-Hant' ? '切换为简体中文' : '切换为繁体中文'} onClick={() => void setLocale(locale === 'zh-Hant' ? 'zh-Hans' : 'zh-Hant')}><Languages size={17} /><span>{locale === 'zh-Hant' ? '繁' : '简'}</span></button>
            <button className="theme-button" type="button" title={theme === 'dark' ? '切换为浅色界面' : '切换为深色界面'} aria-label={theme === 'dark' ? '切换为浅色界面' : '切换为深色界面'} aria-pressed={theme === 'light'} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
              {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <span className="sr-only" role="status" aria-live="polite">{syncState === 'saving' ? '正在同步语言偏好' : syncState === 'saved' ? '语言偏好已保存' : syncState === 'error' ? '账户同步失败，语言偏好已保存在本机' : ''}</span>
            <button ref={userMenuTrigger} className="user-menu" type="button" aria-label="打开账户菜单" aria-haspopup="menu" aria-controls="account-menu" aria-expanded={userMenuOpen} onClick={() => setUserMenuOpen((current) => !current)}><UserRound size={17} /></button>
            {userMenuOpen && <div ref={accountPopover} id="account-menu" className="account-popover" role="menu" aria-label="账户菜单">
              <header role="presentation"><strong>{workspace.user?.display_name ?? 'CicloTrade 用户'}</strong><small>{workspace.user?.plan_display_name ?? '账户'}</small></header>
              <a role="menuitem" href="/" onClick={() => setUserMenuOpen(false)}><House size={16} /> 返回欢迎页</a>
              <NavLink role="menuitem" to="/account" onClick={() => setUserMenuOpen(false)}><UserRound size={16} /> 个人中心</NavLink>
              <NavLink role="menuitem" to="/membership" onClick={() => setUserMenuOpen(false)}><ShieldCheck size={16} /> 订阅会员</NavLink>
              <NavLink role="menuitem" to="/more" onClick={() => setUserMenuOpen(false)}><Grid2X2 size={16} /> {navCopy.more}</NavLink>
              {isSuperAdmin && <NavLink role="menuitem" to="/admin" onClick={() => setUserMenuOpen(false)}><ShieldCheck size={16} /> 超级管理</NavLink>}
              <button role="menuitem" type="button" onClick={() => { setUserMenuOpen(false); void workspace.logout().then(() => navigate('/')) }}><LogOut size={16} /> 登出账户</button>
            </div>}
          </div>
        </header>

        <div className="status-strip" role="status" aria-live="polite">
          <span><i className="positive-dot" /> {realData ? marketDisconnected ? '真实行情数据未连接' : `${marketDelay ? '受控行情已连接' : '真实行情数据已连接'} · ${marketStatusLabel}` : '界面演示数据'}</span>
          <span><ShieldCheck size={14} /> {realData ? '风控设置已载入' : '风险状态为演示'}</span>
          <span><FlaskConical size={14} /> {hasModelSnapshots ? '模型快照已载入' : '模型运行状态未提供'}</span>
          <strong>{realData ? 'MARKET DATA · VERIFIED STATUS' : 'DEMO DATA'} · 不构成投资建议</strong>
          <div className="global-auto-live-mobile-inline"><GlobalAutoLiveKillSwitch snapshot={autoLiveSnapshot} snapshotState={realData && autoLiveSnapshotState === 'idle' ? 'loading' : autoLiveSnapshotState} /></div>
        </div>

        <main id="main-content">{children}</main>
        <footer className="app-disclaimer">本平台提供的数据与分析仅供参考，不构成投资建议</footer>
      </div>

      <nav className="mobile-nav" aria-label="移动端主要导航">
        {mobileNavItems.map(({ to, key, icon: Icon, routes }) => {
          const active = routes.some((route) => pathMatchesRoute(pathname, route))
          const label = key === 'opportunity' ? navCopy.opportunityMobile : key === 'simulation' ? navCopy.simulationShort : navCopy[key]
          return <NavLink key={to} className={active ? 'active' : ''} to={to} aria-label={label} title={label} aria-current={active ? 'page' : undefined}>
            <Icon size={21} aria-hidden="true" /><span>{label}</span>
          </NavLink>
        })}
      </nav>

      {commandOpen && (
        <div className="command-backdrop" role="presentation" onClick={() => setCommandOpen(false)}>
          <section ref={commandPalette} id="command-palette" className="command-palette stock-picker" role="dialog" aria-modal="true" aria-label="搜索股票" onClick={(event) => event.stopPropagation()}>
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
