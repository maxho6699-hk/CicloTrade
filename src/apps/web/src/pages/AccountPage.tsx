import { CheckCircle2, KeyRound, Languages, Laptop, LogOut, ShieldCheck, Smartphone, UserRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { PageHeader } from '../components/PageHeader'
import { WorkspaceState } from '../components/WorkspaceState'
import { riskSettings } from '../data/workspace'
import { useWorkspace } from '../api/workspace-context'
import { BrowserApiError, saveRiskSettings } from '../api/client'
import { useLocale } from '../i18n/useLocale'

export function AccountPage() {
  const workspace = useWorkspace()
  const { locale, formatLocale, setLocale, syncState } = useLocale()
  const [risk, setRisk] = useState(riskSettings)
  const [saveState, setSaveState] = useState('')
  const [accountNotice, setAccountNotice] = useState('')

  useEffect(() => {
    if (workspace.data?.settings.risk) setRisk({ ...riskSettings, ...workspace.data.settings.risk })
  }, [workspace.data])

  return (
    <div className="page operations-page">
      <PageHeader kicker="ACCOUNT / SECURITY" title="账户与安全" description="身份、会员、风控偏好、会话和券商连接集中管理。高风险开关不会默认开启。" />
      <WorkspaceState />
      <section className="profile-strip data-panel">
        <span className="profile-avatar"><UserRound size={26} /></span><div><span>当前账户</span>{workspace.user ? <h2 data-no-localize>{workspace.user.display_name}</h2> : <h2>演示账户</h2>}<p>{workspace.user ? `${workspace.user.plan_display_name} · ${workspace.user.subscription_expire ?? '长期有效'}` : '登录后读取真实会员与风控设置'}</p></div><span className={`status-chip ${workspace.user ? 'official' : 'research'}`}><ShieldCheck size={14} /> {workspace.user ? '真实会话' : '演示模式'}</span><button className="button secondary" type="button" onClick={() => workspace.user ? void workspace.logout() : location.assign('/login')}>{workspace.user ? '退出登录' : '登录账户'}</button>
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
          <div className="range-settings">
            <label><span><strong>美股单标的上限</strong><small>任何新订单都不能让单一美股超过此金额</small></span><output>USD {risk.max_position_per_symbol.toLocaleString(formatLocale)}</output><input type="range" min="1000" max="50000" step="1000" value={risk.max_position_per_symbol} onChange={(event) => setRisk({ ...risk, max_position_per_symbol: Number(event.target.value) })} /></label>
            <label><span><strong>美股账户总仓位</strong><small>全部美股模拟仓位的金额上限</small></span><output>USD {risk.max_total_position.toLocaleString(formatLocale)}</output><input type="range" min="5000" max="500000" step="5000" value={risk.max_total_position} onChange={(event) => setRisk({ ...risk, max_total_position: Number(event.target.value) })} /></label>
            <label><span><strong>美股单日最大亏损</strong><small>达到阈值后自动暂停新开仓</small></span><output>USD {risk.max_daily_loss.toLocaleString(formatLocale)}</output><input type="range" min="500" max="50000" step="500" value={risk.max_daily_loss} onChange={(event) => setRisk({ ...risk, max_daily_loss: Number(event.target.value) })} /></label>
            <label><span><strong>A股单标的上限</strong><small>使用人民币独立计算，不与美股敞口混合</small></span><output>CNY {risk.max_position_per_symbol_cny.toLocaleString(formatLocale)}</output><input type="range" min="5000" max="350000" step="5000" value={risk.max_position_per_symbol_cny} onChange={(event) => setRisk({ ...risk, max_position_per_symbol_cny: Number(event.target.value) })} /></label>
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
          <dl><div><dt>邮箱验证</dt><dd>当前接口未提供</dd></div><div><dt>登录设备</dt><dd>未记录</dd></div><div><dt>绑定 IP</dt><dd>沿用原安全规则</dd></div><div><dt>最近登录</dt><dd>当前会话</dd></div></dl>
          <button className="button secondary wide" type="button" onClick={() => setAccountNotice('修改密码仍由原安全中心处理；新界面不会绕过现有IP、失败次数和会话规则。')}>修改密码</button>
        </aside>
      </div>

      <section className="data-panel broker-panel">
        <header className="panel-heading"><div><span>BROKER & EXECUTION</span><h2>券商与交易权限</h2></div><span className="status-chip research">仅登记 · 凭据未配置</span></header>
        <div className="broker-row"><div><strong>Tiger Brokers</strong><small>模拟账户 · PAPER · 新界面未读取券商凭据</small></div><span className="warning-text">待配置</span><button className="button secondary" type="button" onClick={() => setAccountNotice('券商管理尚未迁移。当前新界面只能提交模拟订单，不会连接或修改真实券商账户。')}>管理</button></div>
        <div className="danger-setting"><div><strong>允许实盘自动交易</strong><small>当前版本永久关闭；未来仍需平台开关、签约白名单和逐单确认。</small></div><button className="toggle" type="button" role="switch" aria-checked="false" aria-label="实盘自动交易已关闭" disabled><i /></button></div>
        {accountNotice && <div className="inline-warning account-notice" role="status"><ShieldCheck size={17} /><span>{accountNotice}</span><button className="button tertiary" type="button" onClick={() => setAccountNotice('')}>关闭</button></div>}
      </section>

      <section className="data-panel">
        <header className="panel-heading"><div><span>ACTIVE SESSIONS</span><h2>当前会话</h2></div><Laptop size={20} /></header>
        <div className="session-row"><Smartphone size={21} /><div><strong>当前浏览器会话</strong><small>安全凭证保存在 HttpOnly Cookie；不会在页面显示IP或令牌。</small></div><span className="positive-text">在线</span><button className="icon-button" type="button" title="当前没有可退出的其他会话" aria-label="当前没有其他会话" disabled><LogOut size={16} /></button></div>
      </section>
    </div>
  )
}
