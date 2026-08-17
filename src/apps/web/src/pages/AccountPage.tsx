import { BarChart3, BellRing, CheckCircle2, CircleAlert, Copy, Crown, FileText, HelpCircle, KeyRound, Languages, Laptop, Link2, LockKeyhole, LogOut, PauseCircle, PlayCircle, Settings, ShieldCheck, SlidersHorizontal, Smartphone, Sparkles, TrendingUp, UserRound } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { WorkspaceState } from '../components/WorkspaceState'
import { riskSettings } from '../data/workspace'
import { useWorkspace } from '../api/workspace-context'
import { BrowserApiError, fetchPersonalPaperAccount, saveRiskSettings, type PersonalPaperAccount } from '../api/client'
import { accountCenterApi, type AccountContent, type AccountMemory, type AccountOverview, type AppearancePayload, type DataAuthorization } from '../api/accountCenter'
import type { RiskSettings } from '../api/client'
import { useCicloTier } from '../api/use-ciclo-tier'
import { CicloCore, type CicloCoreTier } from '../components/paper/CicloCore'
import { useLocale } from '../i18n/useLocale'
import '../styles/secondary-pages.css'
import '../styles/account-center.css'

const authorizationPages: Record<string, string[]> = {
  quotes: ['today', 'discover', 'research', 'paper', 'portfolio', 'reports', 'trade'],
  research: ['today', 'discover', 'research', 'reports', 'lab', 'ai'],
  content: ['account', 'research', 'paper', 'portfolio', 'reports', 'lab'],
  ai_memory: ['account', 'research', 'ai'],
}

const authorizationKinds = ['quotes', 'research', 'content', 'ai_memory'] as const

const tierOrder: CicloCoreTier[] = ['free', 'standard', 'advanced', 'professional', 'custom']
const tierProfile: Record<CicloCoreTier, { level: string; title: string }> = {
  free: { level: 'LV.1', title: '基础形态' },
  standard: { level: 'LV.2', title: '进阶形态' },
  advanced: { level: 'LV.3', title: '高级形态' },
  professional: { level: 'LV.4', title: '专业形态' },
  custom: { level: 'LV.5', title: '定制形态' },
}

function remainingDays(value: string | null | undefined) {
  if (!value) return '长期有效'
  const expiresAt = Date.parse(value)
  if (!Number.isFinite(expiresAt)) return '日期待同步'
  return `${Math.max(0, Math.ceil((expiresAt - Date.now()) / 86_400_000))} 天`
}

function authorizationLabel(kind: string): string {
  return { quotes: '行情', research: '研究', content: '我的内容', ai_memory: 'AI 可控记忆' }[kind] ?? kind
}

function appearanceAsset(assets: Record<string, unknown>, key: 'preview' | 'full' | 'alt' | 'unlock_plan') {
  const value = assets[key]
  if (typeof value !== 'string') return null
  if (key === 'preview' || key === 'full') return /^\/media\/ciclo\/[a-z0-9-]+\.svg$/.test(value) ? value : null
  return value.slice(0, 80)
}

function completeRiskSettings(value: Partial<RiskSettings> | undefined): value is RiskSettings {
  if (!value) return false
  return Object.keys(riskSettings).every((key) => {
    const riskKey = key as keyof RiskSettings
    return typeof value[riskKey] === 'number' && Number.isFinite(value[riskKey])
  })
}

export function AccountPage() {
  const workspace = useWorkspace()
  const tier = useCicloTier()
  const { locale, formatLocale, setLocale, syncState } = useLocale()
  const [risk, setRisk] = useState(riskSettings)
  const [riskPreset, setRiskPreset] = useState<'conservative' | 'balanced' | 'custom'>('balanced')
  const [saveState, setSaveState] = useState('')
  const [accountNotice, setAccountNotice] = useState('')
  const [accountOverview, setAccountOverview] = useState<AccountOverview | null>(null)
  const [appearance, setAppearance] = useState<AppearancePayload | null>(null)
  const [memories, setMemories] = useState<AccountMemory[]>([])
  const [content, setContent] = useState<AccountContent[]>([])
  const [authorizations, setAuthorizations] = useState<DataAuthorization[]>([])
  const [accountCenterState, setAccountCenterState] = useState('')
  const [memoryKey, setMemoryKey] = useState('')
  const [memoryValue, setMemoryValue] = useState('')
  const [memoryBusy, setMemoryBusy] = useState(false)
  const [authorizationBusy, setAuthorizationBusy] = useState<string | null>(null)
  const [appearanceBusy, setAppearanceBusy] = useState<string | null>(null)
  const [notificationState, setNotificationState] = useState('')
  const [paperAccount, setPaperAccount] = useState<PersonalPaperAccount | null>(null)
  const [paperAccountState, setPaperAccountState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [searchParams] = useSearchParams()
  const notificationKind = searchParams.get('notification_kind')
  const notificationPublicId = searchParams.get('notification_public_id')
  const notificationVersion = Number(searchParams.get('notification_version'))
  const brokerage = workspace.data?.membership.brokerage
  const execution = workspace.data?.execution_control
  const autoControlAccountLimit = brokerage?.auto_control_account_limit ?? 0
  const accountsUsed = brokerage?.accounts_used ?? 0
  const authorizedAccounts = brokerage?.accounts.filter((account) => account.authorized) ?? []
  const hasVerifiedRisk = completeRiskSettings(workspace.data?.settings.risk)
  const tierIndex = tierOrder.indexOf(tier)
  const profileTier = tierProfile[tier]
  const accountPublicId = accountOverview?.account.public_id ?? (workspace.user ? `UID-${workspace.user.id}` : '未登录')
  const planName = accountOverview?.membership.plan ?? workspace.user?.plan_display_name ?? '会员状态待同步'
  const expiresAt = accountOverview?.membership.subscription_expire ?? workspace.user?.subscription_expire
  const reportCount = content.filter((item) => /report|research|研报|研究/i.test(item.content_key)).length
  const currentPlan = workspace.data?.membership.plans.find((plan) => plan.key === workspace.user?.plan)
  const growthBenefit = currentPlan?.features.find((feature) => /成长|加速/.test(feature)) ?? (tier === 'free' ? '标准成长速度' : '成长权益以套餐页为准')
  const growthSpeedMatch = currentPlan?.features.map((feature) => feature.match(/(\d+(?:\.\d+)?)\s*(?:x|×|倍)/i)).find(Boolean)
  const growthSpeed = tier === 'free' ? '1.0×' : growthSpeedMatch?.[1] ? `${growthSpeedMatch[1]}×` : '暂无数据'
  const weeklyActivity = useMemo(() => {
    const today = new Date()
    const events = [...content.map((item) => item.created_at), ...memories.map((item) => item.created_at)]
    return Array.from({ length: 7 }, (_, index) => {
      const day = new Date(today.getFullYear(), today.getMonth(), today.getDate() - (6 - index))
      const key = day.toDateString()
      return {
        label: day.toLocaleDateString(formatLocale, { weekday: 'short' }),
        value: events.filter((value) => new Date(value).toDateString() === key).length,
      }
    })
  }, [content, formatLocale, memories])
  const weeklyMax = Math.max(1, ...weeklyActivity.map((item) => item.value))
  const weeklyPoints = weeklyActivity.map((item, index) => `${4 + index * 15.3},${82 - (item.value / weeklyMax) * 58}`).join(' ')
  const todayActivity = weeklyActivity.at(-1)?.value ?? 0
  const formatUsd = (value: number) => new Intl.NumberFormat(formatLocale, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value)

  useEffect(() => {
    if (!notificationKind || !notificationPublicId || !Number.isSafeInteger(notificationVersion) || notificationVersion < 1) return
    if (!['content', 'memory', 'appearance'].includes(notificationKind)) return
    const matched = notificationKind === 'content'
      ? content.some((item) => item.public_id === notificationPublicId && item.content_version === notificationVersion)
      : notificationKind === 'memory'
        ? memories.some((item) => item.public_id === notificationPublicId)
        : appearance?.items.some((item) => item.public_id === notificationPublicId && (item.asset_version === String(notificationVersion) || item.asset_version === `v${notificationVersion}`)) === true
    if (!matched) {
      if (content.length || memories.length || appearance !== null) setNotificationState('通知链接已失效或目标已变化；未自动选择其他项目。')
      return
    }
    setNotificationState('已定位通知指向的账户项目。')
    window.requestAnimationFrame(() => {
      const element = document.querySelector<HTMLElement>(`[data-notification-target="${notificationPublicId}"]`)
      if (element instanceof HTMLDetailsElement) element.open = true
      const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      element?.scrollIntoView({ block: 'center', behavior: reducedMotion ? 'auto' : 'smooth' })
      element?.focus({ preventScroll: true })
    })
  }, [appearance, content, memories, notificationKind, notificationPublicId, notificationVersion])

  useEffect(() => {
    if (workspace.mode !== 'authenticated') return
    let active = true
    setAccountCenterState('正在读取账户中心…')
    void Promise.all([
      accountCenterApi.overview(),
      accountCenterApi.appearance(),
      accountCenterApi.memories(),
      accountCenterApi.content(),
      Promise.all(authorizationKinds.map((kind) => accountCenterApi.authorization(kind).catch(() => null))),
    ]).then(([overview, nextAppearance, nextMemories, nextContent, nextAuthorizations]) => {
      if (!active) return
      setAccountOverview(overview)
      setAppearance(nextAppearance)
      setMemories(nextMemories.items)
      setContent(nextContent.items)
      setAuthorizations(nextAuthorizations.filter((item): item is DataAuthorization => item !== null))
      setAccountCenterState('账户中心已同步；未配置能力会保持锁定。')
    }).catch(() => {
      if (active) setAccountCenterState('账户中心接口尚未完整配置；已隐藏无法证明的状态。')
    })
    return () => { active = false }
  }, [workspace.mode])

  useEffect(() => {
    if (workspace.mode !== 'authenticated') {
      setPaperAccount(null)
      setPaperAccountState('idle')
      return
    }
    let active = true
    let seasonId = ''
    try { seasonId = window.localStorage.getItem('ciclotrade.personalPaper.activeSeason.v1')?.trim() ?? '' } catch { /* local storage can be disabled */ }
    if (!seasonId) {
      setPaperAccount(null)
      setPaperAccountState('idle')
      return
    }
    setPaperAccountState('loading')
    void fetchPersonalPaperAccount(seasonId).then((account) => {
      if (!active) return
      setPaperAccount(account)
      setPaperAccountState('ready')
    }).catch(() => {
      if (!active) return
      setPaperAccount(null)
      setPaperAccountState('error')
    })
    return () => { active = false }
  }, [workspace.mode])

  const deleteMemory = async (memory: AccountMemory) => {
    try {
      await accountCenterApi.deleteMemory(memory.public_id)
      setMemories((items) => items.filter((item) => item.public_id !== memory.public_id))
    } catch {
      setAccountCenterState('记忆删除失败；服务端未确认前不会从列表移除。')
    }
  }

  const selectAppearance = async (manifestPublicId: string) => {
    setAppearanceBusy(manifestPublicId)
    try {
      await accountCenterApi.selectAppearance(manifestPublicId)
      setAppearance(await accountCenterApi.appearance())
      setAccountCenterState('外观选择已更新。')
    } catch (caught) {
      setAccountCenterState(caught instanceof Error ? caught.message : '外观选择未完成。')
    } finally {
      setAppearanceBusy(null)
    }
  }

  const saveMemory = async () => {
    const key = memoryKey.trim()
    const preference = memoryValue.trim()
    if (!key || !preference) {
      setAccountCenterState('请填写记忆名称与内容。')
      return
    }
    setMemoryBusy(true)
    try {
      await accountCenterApi.saveMemory(key, { preference }, null)
      const payload = await accountCenterApi.memories()
      setMemories(payload.items)
      setMemoryKey('')
      setMemoryValue('')
      setAccountCenterState('可控记忆已保存；你可以随时删除。')
    } catch (caught) {
      setAccountCenterState(caught instanceof Error ? caught.message : '可控记忆保存失败。')
    } finally {
      setMemoryBusy(false)
    }
  }

  const updateAuthorization = async (kind: string, authorized: boolean) => {
    setAuthorizationBusy(kind)
    try {
      await accountCenterApi.setAuthorization(kind, authorized ? 'revoked' : 'granted', { pages: authorizationPages[kind] })
      const next = await accountCenterApi.authorization(kind)
      setAuthorizations((items) => [...items.filter((item) => item.data_kind !== kind), next])
      setAccountCenterState(`${authorizationLabel(kind)}授权已${next.authorized ? '开启' : '撤销'}。`)
    } catch (caught) {
      setAccountCenterState(caught instanceof Error ? caught.message : '数据授权更新失败。')
    } finally {
      setAuthorizationBusy(null)
    }
  }

  useEffect(() => {
    if (completeRiskSettings(workspace.data?.settings.risk)) {
      const next = workspace.data.settings.risk
      setRisk(next)
      setRiskPreset(next.max_total_position === riskSettings.max_total_position && next.max_total_position_cny === riskSettings.max_total_position_cny ? 'balanced' : 'custom')
    }
  }, [workspace.data])

  const applyRiskPreset = (preset: 'conservative' | 'balanced') => {
    if (!hasVerifiedRisk) return
    const ratio = preset === 'conservative' ? 0.5 : 1
    setRisk({ ...risk, max_position_per_symbol: riskSettings.max_position_per_symbol * ratio, max_total_position: riskSettings.max_total_position * ratio, max_daily_loss: riskSettings.max_daily_loss * ratio, max_position_per_symbol_cny: riskSettings.max_position_per_symbol_cny * ratio, max_total_position_cny: riskSettings.max_total_position_cny * ratio, max_daily_loss_cny: riskSettings.max_daily_loss_cny * ratio, cooldown_minutes: preset === 'conservative' ? 60 : riskSettings.cooldown_minutes, consecutive_loss_limit: preset === 'conservative' ? 2 : riskSettings.consecutive_loss_limit })
    setRiskPreset(preset)
  }

  const copyAccountPublicId = async () => {
    try {
      await navigator.clipboard.writeText(accountPublicId)
      setAccountCenterState('用户 ID 已复制。')
    } catch {
      setAccountCenterState('浏览器未允许复制，请手动选择用户 ID。')
    }
  }

  return (
    <div className="page operations-page">
      <PageHeader kicker="PROFILE / PERSONAL CENTER" title="个人中心" description="集中查看会员、智能体成长、个人模拟账户、投资偏好与授权设置。AI 只辅助研究，不会替你下单。" />
      <WorkspaceState />

      <section className="profile-identity-bar data-panel">
        <span className="profile-identity-avatar"><UserRound size={34} /></span>
        <div className="profile-identity-copy">
          <div><h2 data-no-localize>{workspace.user?.display_name ?? 'CicloTrade 用户'}</h2><span className="profile-verified"><ShieldCheck size={13} />已认证</span></div>
          <p><span data-no-localize>{accountPublicId}</span><button type="button" aria-label="复制用户 ID" title="复制用户 ID" onClick={() => void copyAccountPublicId()}><Copy size={13} /></button></p>
        </div>
        <span className="profile-membership-pill"><Crown size={15} /><strong>{planName}</strong><small>成长权益启用中</small></span>
        <dl className="profile-identity-stats">
          <div><dt>使用时长</dt><dd>待同步</dd></div>
          <div><dt>生成报告数</dt><dd>{reportCount}</dd></div>
          <div><dt>AI 对话数</dt><dd>待同步</dd></div>
        </dl>
        <div className="profile-identity-actions"><button className="button secondary" type="button" disabled title="编辑资料接口尚未提供">编辑资料</button><a className="button tertiary" href="#account-security-details">账户安全</a></div>
      </section>

      <section className="profile-overview-layout">
        <section className="profile-agent-card data-panel">
          <div className="profile-agent-copy">
            <header><div><span>MY CICLO AGENT</span><h2>我的智能体</h2></div><Sparkles size={20} /></header>
            <div className="profile-agent-level"><strong>{profileTier.level}</strong><span>{profileTier.title}</span><small>{planName}</small></div>
            <div className="profile-agent-progress">
              <div><span>会员形态进度</span><strong>{tierIndex + 1} / {tierOrder.length}</strong></div>
              <i><b style={{ width: `${((tierIndex + 1) / tierOrder.length) * 100}%` }} /></i>
              <p>成长积分与预计升级天数需由服务端同步；当前只按真实会员等级展示机器人形态。</p>
            </div>
            <dl className="profile-agent-facts">
              <div><dt>成长积分</dt><dd>待同步</dd></div>
              <div><dt>距离下等级</dt><dd>{tierIndex === tierOrder.length - 1 ? '已到当前最高形态' : '等待成长积分'}</dd></div>
              <div><dt>预计升级</dt><dd>待同步</dd></div>
              <div><dt>会员加速</dt><dd>{growthSpeed}</dd></div>
            </dl>
            <div className="profile-agent-actions"><Link className="button secondary" to="/membership">成长规则</Link><Link className="button primary" to="/membership">管理套餐</Link></div>
          </div>
          <div className="profile-agent-stage">
            <div className="profile-agent-glow" aria-hidden="true" />
            <CicloCore label={`${profileTier.level} ${profileTier.title}`} tier={tier} />
            <span className="profile-agent-badge" aria-hidden="true">A</span>
            <ol className="profile-evolution-path" aria-label="智能体进化路径">
              {tierOrder.map((item, index) => <li className={`${index < tierIndex ? 'is-complete' : ''} ${index === tierIndex ? 'is-current' : ''}`} key={item}><i>{index < tierIndex ? '✓' : index + 1}</i><span>LV.{index + 1}</span><small>{tierProfile[item].title}</small></li>)}
            </ol>
          </div>
        </section>

        <aside className="profile-side-stack">
          <section className="profile-growth-card profile-card data-panel">
            <header><div><span>TODAY GROWTH</span><h2>今日成长</h2></div><TrendingUp size={19} /></header>
            <dl><div><dt>基础积分</dt><dd>待同步</dd></div><div><dt>会员加速</dt><dd>{tier === 'free' ? '未启用' : '套餐已启用'}</dd></div><div><dt>今日累计</dt><dd>{todayActivity} 条活动</dd></div><div><dt>连续活跃</dt><dd>待同步</dd></div></dl>
            <div className="profile-growth-chart" role="img" aria-label={`最近七日账户内容与记忆活动，共 ${weeklyActivity.reduce((sum, item) => sum + item.value, 0)} 条`}>
              <svg viewBox="0 0 100 90" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="profile-growth-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#7c63ff" stopOpacity=".48" /><stop offset="1" stopColor="#4b85ff" stopOpacity=".04" /></linearGradient></defs><path d="M4 24H96 M4 53H96 M4 82H96" /><polygon points={`4,84 ${weeklyPoints} 96,84`} /><polyline points={weeklyPoints} /></svg>
              <div>{weeklyActivity.map((item) => <span key={item.label}>{item.label}</span>)}</div>
            </div>
            <a href="#profile-memory">查看成长记录</a>
          </section>

          <section className="profile-plan-card profile-card data-panel">
            <span className="profile-plan-icon"><Crown size={20} /></span><div className="profile-plan-copy"><small>当前套餐</small><strong>{planName}</strong><span>{expiresAt ? `有效期至 ${new Date(expiresAt).toLocaleDateString(formatLocale)}` : '长期有效或日期待同步'}</span><em>{growthBenefit}</em></div><dl><div><dt>剩余</dt><dd>{remainingDays(expiresAt)}</dd></div><div><dt>成长速度</dt><dd>{growthSpeed}</dd></div></dl><Link className="button secondary" to="/membership">管理套餐</Link>
          </section>

          <section className="profile-paper-card profile-card data-panel">
            <header><div><span>PERSONAL PAPER ACCOUNT</span><h2>个人模拟账户</h2></div><span className="profile-paper-shield"><ShieldCheck size={27} /></span></header>
            <dl>
              <div><dt>模拟资产</dt><dd>{paperAccount ? formatUsd(paperAccount.total_equity) : paperAccountState === 'loading' ? '读取中' : paperAccountState === 'error' ? '暂不可用' : '尚未创建'}</dd></div>
              <div><dt>累计收益</dt><dd className={paperAccount && paperAccount.total_equity - paperAccount.season.initial_cash < 0 ? 'negative-text' : 'positive-text'}>{paperAccount ? formatUsd(paperAccount.total_equity - paperAccount.season.initial_cash) : '—'}</dd></div>
              <div><dt>当前持仓</dt><dd>{paperAccount ? `${paperAccount.positions.length} 个` : '—'}</dd></div>
            </dl>
            <p>{paperAccount ? `独立 USD 账户域 · 更新于 ${new Date(paperAccount.as_of).toLocaleString(formatLocale, { hour12: false })}` : '个人模拟账户与官方验证组合、券商实盘完全隔离。'}</p>
            <footer><Link className="button primary" to="/paper">查看模拟账户</Link><a className="button secondary" href="#account-security-details">风险设置</a></footer>
          </section>
        </aside>
      </section>

      <section className="profile-lower-grid">
        <section className="profile-card data-panel" id="profile-memory"><header><div><span>INVESTMENT MEMORY</span><h2>投资偏好记忆</h2></div><SlidersHorizontal size={19} /></header><dl className="profile-preference-list"><div><dt>风险偏好</dt><dd>{hasVerifiedRisk ? riskPreset === 'conservative' ? '保守' : riskPreset === 'balanced' ? '平衡' : '自定义' : '待同步'}</dd></div><div><dt>投资周期</dt><dd>服务端未提供</dd></div><div><dt>关注方向</dt><dd>{memories.length ? memories.slice(0, 2).map((item) => item.memory_key).join(' · ') : '尚未配置'}</dd></div><div><dt>分析偏好</dt><dd>{memories.length ? `${memories.length} 条可控记忆` : '尚未配置'}</dd></div></dl><footer><span>AI 自动更新：{authorizations.find((item) => item.data_kind === 'ai_memory')?.authorized ? '已授权' : '未授权'}</span><a href="#account-memory-editor">管理记忆</a></footer></section>

        <section className="profile-card data-panel"><header><div><span>DATA AUTHORIZATION</span><h2>数据与授权</h2></div><ShieldCheck size={19} /></header><div className="profile-authorization-list">{authorizationKinds.map((kind) => { const item = authorizations.find((entry) => entry.data_kind === kind); const configured = item?.policy_state === 'configured'; const label = { quotes: '自选股与行情', research: '模拟持仓与研究', content: '研报历史与内容', ai_memory: '对话行为与记忆' }[kind]; return <div key={kind}><span><strong>{label}</strong><small>{configured ? item?.authorized ? '已授权' : '未授权' : '政策未配置'}</small></span><button type="button" role="switch" aria-checked={Boolean(item?.authorized)} className={item?.authorized ? 'is-on' : ''} disabled={!configured || authorizationBusy !== null} onClick={() => void updateAuthorization(kind, Boolean(item?.authorized))}><i /></button></div> })}</div><footer><span>授权按当前账户隔离</span><a href="#account-security-details">管理授权</a></footer></section>

        <section className="profile-card data-panel"><header><div><span>MY CONTENT</span><h2>我的内容</h2></div><FileText size={19} /></header><nav className="profile-link-list"><Link to="/reports"><span>我的研报</span><strong>{reportCount}</strong></Link><Link to="/paper"><span>个人模拟记录</span><strong>{paperAccount?.recent_orders.length ?? 0}</strong></Link><Link to="/reports"><span>下载与验证记录</span><strong>查看</strong></Link><Link to="/ai"><span>对话历史</span><strong>打开</strong></Link></nav></section>

        <section className="profile-card data-panel"><header><div><span>MESSAGES / SETTINGS</span><h2>消息与设置</h2></div><Settings size={19} /></header><nav className="profile-link-list"><Link to="/notifications"><span><BellRing size={15} />消息与预警</span><strong>查看</strong></Link><a href="#account-security-details"><span><ShieldCheck size={15} />账户与安全</span><strong>管理</strong></a><a href="#account-security-details"><span><KeyRound size={15} />隐私与授权</span><strong>管理</strong></a><Link to="/help"><span><HelpCircle size={15} />帮助与反馈</span><strong>打开</strong></Link></nav></section>

        <section className="profile-card profile-usage-card data-panel"><header><div><span>AI USAGE</span><h2>本周期 AI 用量</h2></div><BarChart3 size={19} /></header><div className="profile-usage-row"><div><span>模型调用</span><strong>待同步</strong></div><i><b style={{ width: 0 }} /></i><small>后端尚未提供账户级配额</small></div><div className="profile-usage-row"><div><span>报告生成</span><strong>{reportCount} / 配额待同步</strong></div><i><b style={{ width: 0 }} /></i><small>仅显示可验证的报告索引数量</small></div><Link to="/membership">查看用量与套餐</Link></section>
      </section>

      <h2 className="profile-settings-heading" id="account-security-details">账户与安全详细设置</h2>

      <section className="language-preference data-panel">
        <div><Languages size={20} /><span><strong>界面语言</strong><small>默认繁体中文；登录后会同步到当前账户。</small></span></div>
        <div className="segmented-control" role="radiogroup" aria-label="界面语言">
          <button className={locale === 'zh-Hant' ? 'active' : ''} type="button" role="radio" aria-checked={locale === 'zh-Hant'} onClick={() => void setLocale('zh-Hant')}>繁体中文</button>
          <button className={locale === 'zh-Hans' ? 'active' : ''} type="button" role="radio" aria-checked={locale === 'zh-Hans'} onClick={() => void setLocale('zh-Hans')}>简体中文</button>
        </div>
        {syncState === 'error' && <small className="warning-text" role="status">账户同步失败，当前选择已保存在本机。</small>}
      </section>

      <section className="account-center-grid">
        <section className="data-panel account-center-identity">
          <header className="panel-heading"><div><span>MEMBERSHIP / IDENTITY</span><h2>会员与身份</h2></div><UserRound size={20} /></header>
          <div className="account-center-summary"><strong>{accountOverview?.account.display_name ?? workspace.user?.display_name ?? '演示账户'}</strong><span>{accountOverview?.membership.plan ?? workspace.user?.plan_display_name ?? '尚未配置会员资料'}</span><small>{accountOverview?.membership.subscription_expire ? `有效期至 ${accountOverview.membership.subscription_expire}` : '有效期未配置'}</small></div>
          <p className="account-center-note">会员资格只负责可见权益；机器人外观与 L0-L4 能力阶段在下方分开显示。</p>
          <Link className="button tertiary" to="/membership">查看会员权益</Link>
        </section>
        <section className="data-panel account-center-levels">
          <header className="panel-heading"><div><span>AGENT CAPABILITY</span><h2>L0-L4 能力阶段</h2></div><LockKeyhole size={20} /></header>
          <div className="agent-level-list">{(['L0', 'L1', 'L2', 'L3', 'L4'] as const).map((level) => { const entry = accountOverview?.agent_levels[level]; const locked = !entry || entry.policy_state !== 'configured' || entry.level === null; return <div className={locked ? 'is-locked' : 'is-ready'} key={level}><strong>{level}</strong><span>{locked ? '锁定 · 服务端未配置' : `已配置 · 阶段 ${entry?.level}`}</span></div> })}</div>
        </section>
      </section>

      <p className="account-center-status" role="status">{accountCenterState}</p>
      {notificationState && <p className="account-center-status" role="status">{notificationState}</p>}

      <section className="account-center-grid">
        <section className="data-panel account-center-appearance">
          <header className="panel-heading"><div><span>APPEARANCE / ENTITLEMENT</span><h2>机器人外观</h2></div><CircleAlert size={20} /></header>
          <p className="account-center-note">外观选择只影响显示，不代表收益、交易权限或更高等级能力。</p>
          {appearance?.items.length ? <div className="appearance-list">{appearance.items.map((item) => { const preview = appearanceAsset(item.assets, 'preview'); const alt = appearanceAsset(item.assets, 'alt') ?? item.skin_id; const unlockPlan = appearanceAsset(item.assets, 'unlock_plan') ?? '对应会员'; const selected = appearance.current.public_id === item.public_id; const linked = notificationKind === 'appearance' && notificationPublicId === item.public_id && (item.asset_version === String(notificationVersion) || item.asset_version === `v${notificationVersion}`); return <button data-notification-target={linked ? item.public_id : undefined} tabIndex={linked ? 0 : undefined} aria-pressed={selected} className={`appearance-option ${selected ? 'is-selected' : ''} ${linked ? 'notification-target-match' : ''}`} disabled={!item.entitled || appearanceBusy !== null} key={item.public_id} type="button" onClick={() => void selectAppearance(item.public_id)}>{preview ? <img src={preview} width={320} height={320} alt={alt} /> : <span className="appearance-image-missing"><CircleAlert size={20} /></span>}<span><strong>{alt}</strong><small>{item.asset_version} · {item.entitled ? `已由${unlockPlan}解锁` : `${unlockPlan}解锁 · 当前锁定`}</small></span><span>{appearanceBusy === item.public_id ? '保存中…' : selected ? '当前' : item.entitled ? '选择' : '锁定'}</span></button> })}</div> : <div className="account-center-empty"><LockKeyhole size={18} /><span>暂无真实外观 manifest；当前选择为空并保持锁定。</span></div>}
        </section>
        <section className="data-panel account-center-authorizations">
          <header className="panel-heading"><div><span>DATA AUTHORIZATION</span><h2>数据授权</h2></div><ShieldCheck size={20} /></header>
          <div className="authorization-list">{authorizationKinds.map((kind) => { const item = authorizations.find((entry) => entry.data_kind === kind); const configured = item?.policy_state === 'configured'; return <div key={kind}><span><strong>{authorizationLabel(kind)}</strong><small>{configured ? `已绑定服务端政策 · ${authorizationPages[kind].length} 个页面范围` : '锁定 · 服务端政策未配置或已变化'}</small></span><button className={`button ${item?.authorized ? 'secondary' : 'tertiary'}`} type="button" disabled={!configured || authorizationBusy !== null} onClick={() => void updateAuthorization(kind, Boolean(item?.authorized))}>{authorizationBusy === kind ? '保存中…' : item?.authorized ? '撤销授权' : '授权使用'}</button></div> })}</div>
        </section>
      </section>

      <section className="data-panel account-center-memory" id="account-memory-editor">
        <header className="panel-heading"><div><span>CONTROLLED MEMORY</span><h2>可控记忆</h2></div><KeyRound size={20} /></header>
        <div className="memory-create-form"><label><span>记忆名称</span><input value={memoryKey} maxLength={128} placeholder="例如：研究表达偏好" onChange={(event) => setMemoryKey(event.target.value)} /></label><label><span>记忆内容</span><input value={memoryValue} maxLength={500} placeholder="例如：先列风险与反向证据" onChange={(event) => setMemoryValue(event.target.value)} /></label><button className="button primary" type="button" disabled={memoryBusy || !memoryKey.trim() || !memoryValue.trim()} onClick={() => void saveMemory()}>{memoryBusy ? '保存中…' : '保存记忆'}</button></div>
        {memories.length ? <div className="memory-list">{memories.map((memory) => { const linked = notificationKind === 'memory' && notificationPublicId === memory.public_id; return <article data-notification-target={linked ? memory.public_id : undefined} tabIndex={linked ? 0 : undefined} className={linked ? 'notification-target-match' : undefined} key={memory.public_id}><div><strong>{memory.memory_key}</strong><small>来源：账户中心 · 范围：当前账户 · {memory.expires_at ? `到期 ${memory.expires_at}` : '未设置到期时间'}</small></div><button className="button tertiary" type="button" onClick={() => void deleteMemory(memory)}>删除</button></article> })}</div> : <div className="account-center-empty"><LockKeyhole size={18} /><span>暂无可控记忆；服务端没有数据时不展示演示记忆。</span></div>}
      </section>

      <section className="data-panel account-center-content">
        <header className="panel-heading"><div><span>CONTENT INDEX</span><h2>我的内容</h2></div><CircleAlert size={20} /></header>
        {content.length ? <div className="content-index-list">{content.map((item) => { const linked = notificationKind === 'content' && notificationPublicId === item.public_id && notificationVersion === item.content_version; return <details data-notification-target={linked ? item.public_id : undefined} tabIndex={linked ? 0 : undefined} className={linked ? 'notification-target-match' : undefined} open={linked || undefined} key={item.public_id}><summary><span><strong>{item.content_key}</strong><small>版本 {item.content_version} · {item.expires_at ? `到期 ${item.expires_at}` : '未设置到期时间'}</small></span><span>查看内容</span></summary><pre>{JSON.stringify(item.content, null, 2)}</pre></details> })}</div> : <div className="account-center-empty"><LockKeyhole size={18} /><span>我的内容 API 已返回空列表；尚未配置可展示内容，不复制 /paper 或 /trade 的完整控制。</span></div>}
        <div className="account-center-links"><Link to="/paper">打开个人模拟摘要</Link><Link to="/trade">打开交易摘要</Link></div>
      </section>

      <div className="account-layout">
        <section className="data-panel">
          <header className="panel-heading"><div><span>RISK PREFERENCES</span><h2>风险偏好</h2></div><ShieldCheck size={20} /></header>
          {!hasVerifiedRisk && <p className="warning-text" role="status">服务端未返回完整风控设置，风险参数与保存操作已锁定；本地默认值不会作为账户设置提交。</p>}
          <div className="risk-presets" role="group" aria-label="风险预设"><button className={riskPreset === 'conservative' ? 'active' : ''} type="button" disabled={!hasVerifiedRisk} onClick={() => applyRiskPreset('conservative')}><strong>保守</strong><small>单笔和总仓位减半，连续亏损后冷却更久</small></button><button className={riskPreset === 'balanced' ? 'active' : ''} type="button" disabled={!hasVerifiedRisk} onClick={() => applyRiskPreset('balanced')}><strong>平衡</strong><small>使用平台建议的默认上限</small></button><button className={riskPreset === 'custom' ? 'active' : ''} type="button" disabled={!hasVerifiedRisk} onClick={() => setRiskPreset('custom')}><strong>自定义</strong><small>自行调整下方金额，保存前请确认风险</small></button></div>
          <div className={`range-settings ${riskPreset !== 'custom' ? 'preset-locked' : ''}`} onInput={() => setRiskPreset('custom')}>
            <label><span><strong>美股单只股票上限</strong><small>任何新订单都不能让单一美股超过此金额</small></span><output>USD {risk.max_position_per_symbol.toLocaleString(formatLocale)}</output><input type="range" min="1000" max="50000" step="1000" value={risk.max_position_per_symbol} disabled={!hasVerifiedRisk} onChange={(event) => { setRiskPreset('custom'); setRisk({ ...risk, max_position_per_symbol: Number(event.target.value) }) }} /></label>
            <label><span><strong>美股账户总仓位</strong><small>全部美股模拟仓位的金额上限</small></span><output>USD {risk.max_total_position.toLocaleString(formatLocale)}</output><input type="range" min="5000" max="500000" step="5000" value={risk.max_total_position} disabled={!hasVerifiedRisk} onChange={(event) => { setRiskPreset('custom'); setRisk({ ...risk, max_total_position: Number(event.target.value) }) }} /></label>
            <label><span><strong>美股单日最大亏损</strong><small>达到阈值后自动暂停新开仓</small></span><output>USD {risk.max_daily_loss.toLocaleString(formatLocale)}</output><input type="range" min="500" max="50000" step="500" value={risk.max_daily_loss} disabled={!hasVerifiedRisk} onChange={(event) => setRisk({ ...risk, max_daily_loss: Number(event.target.value) })} /></label>
            <label><span><strong>A股单只股票上限</strong><small>使用人民币独立计算，不与美股敞口混合</small></span><output>CNY {risk.max_position_per_symbol_cny.toLocaleString(formatLocale)}</output><input type="range" min="5000" max="350000" step="5000" value={risk.max_position_per_symbol_cny} disabled={!hasVerifiedRisk} onChange={(event) => setRisk({ ...risk, max_position_per_symbol_cny: Number(event.target.value) })} /></label>
            <label><span><strong>A股账户总仓位</strong><small>全部A股模拟仓位的金额上限</small></span><output>CNY {risk.max_total_position_cny.toLocaleString(formatLocale)}</output><input type="range" min="10000" max="3500000" step="10000" value={risk.max_total_position_cny} disabled={!hasVerifiedRisk} onChange={(event) => setRisk({ ...risk, max_total_position_cny: Number(event.target.value) })} /></label>
            <label><span><strong>A股单日最大亏损</strong><small>达到阈值后自动暂停A股新开仓</small></span><output>CNY {risk.max_daily_loss_cny.toLocaleString(formatLocale)}</output><input type="range" min="1000" max="350000" step="1000" value={risk.max_daily_loss_cny} disabled={!hasVerifiedRisk} onChange={(event) => setRisk({ ...risk, max_daily_loss_cny: Number(event.target.value) })} /></label>
            <label><span><strong>连续亏损冷却</strong><small>{risk.consecutive_loss_limit} 次连续亏损后暂停</small></span><output>{risk.cooldown_minutes} 分钟</output><input type="range" min="5" max="240" step="5" value={risk.cooldown_minutes} disabled={!hasVerifiedRisk} onChange={(event) => setRisk({ ...risk, cooldown_minutes: Number(event.target.value) })} /></label>
          </div>
          <footer className="panel-actions"><span><CheckCircle2 size={15} /> {saveState || '修改将在下一笔订单前生效'}</span><button className="button primary" type="button" disabled={!hasVerifiedRisk} onClick={async () => {
            if (!hasVerifiedRisk) { setSaveState('服务端未返回完整风控设置，无法保存。'); return }
            if (workspace.mode !== 'authenticated') { setSaveState('请先登录后保存真实设置'); return }
            try {
              await saveRiskSettings(risk)
              setSaveState('风控设置已保存并立即生效')
              try { await workspace.refresh() } catch { setSaveState('风控设置已保存，但页面刷新失败；下单仍会读取最新设置') }
            } catch (caught) { setSaveState(caught instanceof BrowserApiError ? caught.message : '保存失败') }
          }}>保存风控设置</button></footer>
        </section>

        <aside className="data-panel security-panel">
          <header className="panel-heading"><div><span>SECURITY</span><h2>登录安全</h2></div><KeyRound size={20} /></header>
          <dl><div><dt>邮箱验证</dt><dd>锁定 · 当前接口未提供</dd></div><div><dt>登录设备</dt><dd>锁定 · 当前接口未提供</dd></div><div><dt>会话明细</dt><dd>当前页面已通过登录保护；设备/IP 明细不可验证</dd></div><div><dt>实盘 mandate</dt><dd><Link to="/trade">前往交易控制台管理</Link></dd></div><div><dt>机器人外观</dt><dd>{appearance?.current.skin_id ? `当前 ${appearance.current.skin_id}` : '尚无可用外观'}</dd></div></dl>
          <button className="button secondary wide" type="button" disabled title="当前接口未提供" onClick={() => setAccountNotice('修改密码仍由原安全中心处理；新界面不会绕过现有 IP、失败次数和会话规则。')}>修改密码（锁定）</button>
        </aside>
      </div>

      <section className="data-panel broker-panel">
        <header className="panel-heading"><div><span>BROKER & EXECUTION</span><h2>券商与交易权限</h2></div><span className={`status-chip ${execution?.can_increase_exposure ? 'official' : 'research'}`}>{execution?.can_increase_exposure ? <PlayCircle size={14} /> : <PauseCircle size={14} />} {execution?.can_increase_exposure ? '具备新增敞口条件' : '新增敞口未开放'}</span></header>
        <div className="broker-row"><Link2 size={19} /><div><strong>自动交易控制账号名额</strong><small>{autoControlAccountLimit > 0 ? `服务端当前允许登记 ${autoControlAccountLimit} 个受控账号；每个账号仍需主动完成券商授权。` : '服务端当前未提供可用的受控账号名额。'}</small></div><span className={autoControlAccountLimit > 0 ? 'positive-text' : 'warning-text'}>{accountsUsed} / {autoControlAccountLimit}</span><Link className="button tertiary" to="/trade">打开交易控制台</Link></div>
        <div className="broker-row"><ShieldCheck size={19} /><div><strong>个人券商连接</strong><small>{authorizedAccounts.length ? `${authorizedAccounts.length} 个账户已标记为授权可用；页面不会显示外部账号 ID 或券商凭据。` : '尚未发现已授权可用的个人券商账户。'}</small></div><span className={authorizedAccounts.length ? 'positive-text' : 'warning-text'}>{authorizedAccounts.length ? '已授权' : '未连接'}</span><Link className="button secondary" to="/trade">前往实盘接入</Link></div>
        {brokerage?.accounts.map((account) => <article className="broker-account-summary" key={account.id}><span className={account.authorized ? 'positive-text' : 'warning-text'}>{account.authorized ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}</span><div><strong>{account.alias}</strong><small>{account.provider} · {account.mode === 'live' ? '实盘' : '模拟'} · {account.status}</small></div><span>{account.last_checked ? `检查于 ${new Date(account.last_checked).toLocaleString(formatLocale, { hour12: false })}` : '尚未检查'}</span></article>)}
        <section className={`execution-control-card ${execution?.effective_opening_paused ? 'paused' : 'ready'}`}>
          <div className="execution-control-heading"><span>{execution?.effective_opening_paused ? <PauseCircle size={20} /> : <PlayCircle size={20} />}</span><div><strong>{execution?.effective_opening_paused ? '新开仓当前暂停' : '新开仓安全状态正常'}</strong><small>暂停只针对开多、加多、开空、加空和反手增加的敞口；已授权通道仍应保留减仓和平仓能力。</small></div></div>
          <dl><div><dt>平台总开关</dt><dd>{execution?.auto_trading_service_enabled ? '服务开放' : '服务暂停'}</dd></div><div><dt>平台新开仓</dt><dd>{execution?.global_opening_paused ? '全局暂停' : '未暂停'}</dd></div><div><dt>个人新开仓</dt><dd>{execution?.user_opening_paused ? '个人暂停' : '未暂停'}</dd></div><div><dt>退出风险</dt><dd>{execution?.can_reduce_exposure ? '保留减仓/平仓' : '暂无授权执行通道'}</dd></div></dl>
          {!!execution?.block_reasons.length && <ul>{execution.block_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
          {execution?.user_opening_paused && <div className="opening-resume-form"><div><strong>恢复必须在交易控制台完成</strong><small>交易控制台会重新核对会员、Telegram、券商授权、账户环境、mandate、策略与风险版本、数据健康和 kill-switch；恢复后也不会自动启动 runtime。</small></div><Link className="button primary" to="/trade">前往交易控制台核验</Link></div>}
        </section>
        {accountNotice && <div className="inline-warning account-notice" role="status"><ShieldCheck size={17} /><span>{accountNotice}</span><button className="button tertiary" type="button" onClick={() => setAccountNotice('')}>关闭</button></div>}
      </section>

      <section className="data-panel">
        <header className="panel-heading"><div><span>ACTIVE SESSIONS</span><h2>当前会话</h2></div><Laptop size={20} /></header>
        <div className="session-row"><Smartphone size={21} /><div><strong>当前页面已通过登录保护</strong><small>服务端尚未提供设备、IP、最后活动时间或其他 session 清单；页面不会用固定“在线”冒充会话回执。</small></div><span className="warning-text">明细不可验证</span><button className="icon-button" type="button" title="当前没有可验证的其他会话" aria-label="当前没有可验证的其他会话" disabled><LogOut size={16} /></button></div>
      </section>
    </div>
  )
}
