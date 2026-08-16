import { CheckCircle2, CircleAlert, KeyRound, Languages, Laptop, Link2, LockKeyhole, LogOut, PauseCircle, PlayCircle, ShieldCheck, Smartphone, UserRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { WorkspaceState } from '../components/WorkspaceState'
import { riskSettings } from '../data/workspace'
import { useWorkspace } from '../api/workspace-context'
import { BrowserApiError, saveRiskSettings } from '../api/client'
import { accountCenterApi, type AccountContent, type AccountMemory, type AccountOverview, type AppearancePayload, type DataAuthorization } from '../api/accountCenter'
import type { RiskSettings } from '../api/client'
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

  return (
    <div className="page operations-page">
      <PageHeader kicker="ACCOUNT / SECURITY" title="账户与安全" description="身份、会员、风控偏好、会话和券商连接集中管理。高风险开关不会默认开启。" />
      <WorkspaceState />
      <section className="profile-strip data-panel">
        <span className="profile-avatar"><UserRound size={26} /></span><div><span>当前账户</span>{workspace.user ? <h2 data-no-localize>{workspace.user.display_name}</h2> : <h2>演示账户</h2>}<p>{workspace.user ? `${workspace.user.plan_display_name} · ${workspace.user.subscription_expire ?? '长期有效'}` : '登录后读取真实会员与风控设置'}</p></div><span className={`status-chip ${workspace.user ? 'official' : 'research'}`}><ShieldCheck size={14} /> {workspace.user ? '真实会话' : '演示模式'}</span><button className="button secondary" type="button" onClick={() => workspace.user ? void workspace.logout().then(() => location.assign('/')) : location.assign('/login')}>{workspace.user ? '退出登录' : '登录账户'}</button>
      </section>

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

      <section className="data-panel account-center-memory">
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
