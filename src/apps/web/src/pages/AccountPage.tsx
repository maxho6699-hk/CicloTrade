import { CheckCircle2, CircleAlert, KeyRound, Languages, Laptop, Link2, LogOut, PauseCircle, PlayCircle, ShieldCheck, Smartphone, UserRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { WorkspaceState } from '../components/WorkspaceState'
import { riskSettings } from '../data/workspace'
import { useWorkspace } from '../api/workspace-context'
import { BrowserApiError, resumeOpeningPause, saveRiskSettings } from '../api/client'
import { useLocale } from '../i18n/useLocale'
import '../styles/secondary-pages.css'

export function AccountPage() {
  const workspace = useWorkspace()
  const { locale, formatLocale, setLocale, syncState } = useLocale()
  const [risk, setRisk] = useState(riskSettings)
  const [riskPreset, setRiskPreset] = useState<'conservative' | 'balanced' | 'custom'>('balanced')
  const [saveState, setSaveState] = useState('')
  const [accountNotice, setAccountNotice] = useState('')
  const [resumePassword, setResumePassword] = useState('')
  const [resumeConfirmation, setResumeConfirmation] = useState('')
  const [resumeState, setResumeState] = useState('')
  const brokerage = workspace.data?.membership.brokerage
  const execution = workspace.data?.execution_control
  const autoControlAccountLimit = brokerage?.auto_control_account_limit ?? 0
  const accountsUsed = brokerage?.accounts_used ?? 0
  const authorizedAccounts = brokerage?.accounts.filter((account) => account.authorized) ?? []

  useEffect(() => {
    if (workspace.data?.settings.risk) {
      const next = { ...riskSettings, ...workspace.data.settings.risk }
      setRisk(next)
      setRiskPreset(next.max_total_position === riskSettings.max_total_position && next.max_total_position_cny === riskSettings.max_total_position_cny ? 'balanced' : 'custom')
    }
  }, [workspace.data])

  const applyRiskPreset = (preset: 'conservative' | 'balanced') => {
    const ratio = preset === 'conservative' ? 0.5 : 1
    setRisk({ ...risk, max_position_per_symbol: riskSettings.max_position_per_symbol * ratio, max_total_position: riskSettings.max_total_position * ratio, max_daily_loss: riskSettings.max_daily_loss * ratio, max_position_per_symbol_cny: riskSettings.max_position_per_symbol_cny * ratio, max_total_position_cny: riskSettings.max_total_position_cny * ratio, max_daily_loss_cny: riskSettings.max_daily_loss_cny * ratio, cooldown_minutes: preset === 'conservative' ? 60 : riskSettings.cooldown_minutes, consecutive_loss_limit: preset === 'conservative' ? 2 : riskSettings.consecutive_loss_limit })
    setRiskPreset(preset)
  }

  const resumeOpening = async () => {
    if (workspace.mode !== 'authenticated') {
      setResumeState('请先登录后恢复个人新开仓。')
      return
    }
    try {
      const result = await resumeOpeningPause(resumePassword, resumeConfirmation)
      setResumePassword('')
      setResumeConfirmation('')
      setResumeState(result.execution_control.effective_opening_paused
        ? '个人暂停已清除，但平台仍处于全局暂停，新开仓暂未恢复。'
        : '个人新开仓暂停已解除。')
      await workspace.refresh()
    } catch (caught) {
      setResumeState(caught instanceof BrowserApiError ? caught.message : '恢复失败，请稍后重试。')
    }
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

      <div className="account-layout">
        <section className="data-panel">
          <header className="panel-heading"><div><span>RISK PREFERENCES</span><h2>风险偏好</h2></div><ShieldCheck size={20} /></header>
          <div className="risk-presets" role="group" aria-label="风险预设"><button className={riskPreset === 'conservative' ? 'active' : ''} type="button" onClick={() => applyRiskPreset('conservative')}><strong>保守</strong><small>单笔和总仓位减半，连续亏损后冷却更久</small></button><button className={riskPreset === 'balanced' ? 'active' : ''} type="button" onClick={() => applyRiskPreset('balanced')}><strong>平衡</strong><small>使用平台建议的默认上限</small></button><button className={riskPreset === 'custom' ? 'active' : ''} type="button" onClick={() => setRiskPreset('custom')}><strong>自定义</strong><small>自行调整下方金额，保存前请确认风险</small></button></div>
          <div className={`range-settings ${riskPreset !== 'custom' ? 'preset-locked' : ''}`} onInput={() => setRiskPreset('custom')}>
            <label><span><strong>美股单只股票上限</strong><small>任何新订单都不能让单一美股超过此金额</small></span><output>USD {risk.max_position_per_symbol.toLocaleString(formatLocale)}</output><input type="range" min="1000" max="50000" step="1000" value={risk.max_position_per_symbol} onChange={(event) => { setRiskPreset('custom'); setRisk({ ...risk, max_position_per_symbol: Number(event.target.value) }) }} /></label>
            <label><span><strong>美股账户总仓位</strong><small>全部美股模拟仓位的金额上限</small></span><output>USD {risk.max_total_position.toLocaleString(formatLocale)}</output><input type="range" min="5000" max="500000" step="5000" value={risk.max_total_position} onChange={(event) => { setRiskPreset('custom'); setRisk({ ...risk, max_total_position: Number(event.target.value) }) }} /></label>
            <label><span><strong>美股单日最大亏损</strong><small>达到阈值后自动暂停新开仓</small></span><output>USD {risk.max_daily_loss.toLocaleString(formatLocale)}</output><input type="range" min="500" max="50000" step="500" value={risk.max_daily_loss} onChange={(event) => setRisk({ ...risk, max_daily_loss: Number(event.target.value) })} /></label>
            <label><span><strong>A股单只股票上限</strong><small>使用人民币独立计算，不与美股敞口混合</small></span><output>CNY {risk.max_position_per_symbol_cny.toLocaleString(formatLocale)}</output><input type="range" min="5000" max="350000" step="5000" value={risk.max_position_per_symbol_cny} onChange={(event) => setRisk({ ...risk, max_position_per_symbol_cny: Number(event.target.value) })} /></label>
            <label><span><strong>A股账户总仓位</strong><small>全部A股模拟仓位的金额上限</small></span><output>CNY {risk.max_total_position_cny.toLocaleString(formatLocale)}</output><input type="range" min="10000" max="3500000" step="10000" value={risk.max_total_position_cny} onChange={(event) => setRisk({ ...risk, max_total_position_cny: Number(event.target.value) })} /></label>
            <label><span><strong>A股单日最大亏损</strong><small>达到阈值后自动暂停A股新开仓</small></span><output>CNY {risk.max_daily_loss_cny.toLocaleString(formatLocale)}</output><input type="range" min="1000" max="350000" step="1000" value={risk.max_daily_loss_cny} onChange={(event) => setRisk({ ...risk, max_daily_loss_cny: Number(event.target.value) })} /></label>
            <label><span><strong>连续亏损冷却</strong><small>{risk.consecutive_loss_limit} 次连续亏损后暂停</small></span><output>{risk.cooldown_minutes} 分钟</output><input type="range" min="5" max="240" step="5" value={risk.cooldown_minutes} onChange={(event) => setRisk({ ...risk, cooldown_minutes: Number(event.target.value) })} /></label>
          </div>
          <footer className="panel-actions"><span><CheckCircle2 size={15} /> {saveState || '修改将在下一笔订单前生效'}</span><button className="button primary" type="button" onClick={async () => {
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
          <dl><div><dt>邮箱验证</dt><dd>锁定 · 当前接口未提供</dd></div><div><dt>登录设备</dt><dd>锁定 · 当前接口未提供</dd></div><div><dt>当前 session</dt><dd>锁定 · 当前接口未提供</dd></div><div><dt>实盘 mandate</dt><dd>锁定 · 当前接口未提供</dd></div><div><dt>机器人外观</dt><dd>锁定 · 当前接口未提供</dd></div></dl>
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
          {execution?.user_opening_paused && <div className="opening-resume-form"><div><strong>恢复个人新开仓</strong><small>需要重新输入当前密码，并完整输入“恢复新开仓”。这不会解除平台全局暂停或替你授权券商。</small></div><label>当前密码<input type="password" value={resumePassword} autoComplete="current-password" onChange={(event) => setResumePassword(event.target.value)} /></label><label>确认文字<input value={resumeConfirmation} autoComplete="off" placeholder="恢复新开仓" onChange={(event) => setResumeConfirmation(event.target.value)} /></label><button className="button primary" type="button" disabled={!resumePassword || resumeConfirmation !== '恢复新开仓'} onClick={() => void resumeOpening()}>验证并恢复</button></div>}
          {resumeState && <p className="execution-resume-state" role="status">{resumeState}</p>}
        </section>
        {accountNotice && <div className="inline-warning account-notice" role="status"><ShieldCheck size={17} /><span>{accountNotice}</span><button className="button tertiary" type="button" onClick={() => setAccountNotice('')}>关闭</button></div>}
      </section>

      <section className="data-panel">
        <header className="panel-heading"><div><span>ACTIVE SESSIONS</span><h2>当前会话</h2></div><Laptop size={20} /></header>
        <div className="session-row"><Smartphone size={21} /><div><strong>当前浏览器会话</strong><small>安全凭证保存在 HttpOnly Cookie；不会在页面显示IP或令牌。</small></div><span className="positive-text">在线</span><button className="icon-button" type="button" title="当前没有可退出的其他会话" aria-label="当前没有其他会话" disabled><LogOut size={16} /></button></div>
      </section>
    </div>
  )
}
