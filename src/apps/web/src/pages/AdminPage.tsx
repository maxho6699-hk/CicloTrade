import { AlertTriangle, CheckCircle2, CircleAlert, CreditCard, Gauge, LoaderCircle, RefreshCw, ShieldAlert, UsersRound } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  BrowserApiError,
  fetchAdminAudit,
  fetchAdminBrokers,
  fetchAdminManualClaims,
  fetchAdminOverview,
  fetchAdminUsers,
  reviewAdminManualClaim,
  updateAdminUserAutoTrading,
  type AdminAuditEntry,
  type AdminBrokerAccount,
  type AdminManualClaim,
  type AdminOverview,
  type AdminUser,
} from '../api/client'
import { PageHeader } from '../components/PageHeader'

type AdminData = {
  overview: AdminOverview
  users: AdminUser[]
  claims: AdminManualClaim[]
  brokers: AdminBrokerAccount[]
  audit: AdminAuditEntry[]
}

const emptyData: AdminData = { overview: {}, users: [], claims: [], brokers: [], audit: [] }

function errorMessage(caught: unknown, fallback: string) {
  return caught instanceof BrowserApiError ? caught.message : fallback
}

function numberValue(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function countLabel(value: unknown) {
  return new Intl.NumberFormat('zh-HK').format(numberValue(value))
}

function maskBrokerAccount(value: unknown) {
  const text = typeof value === 'string' ? value.trim() : ''
  if (!text) return '未提供'
  if (/[*•]/.test(text)) return text
  return text.length <= 4 ? '••••' : `•••• ${text.slice(-4)}`
}

function displayDate(value: unknown) {
  if (typeof value !== 'string' || !value) return '未提供'
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString('zh-HK', { hour12: false, timeZone: 'Asia/Hong_Kong' }) : value
}

export function AdminPage() {
  const [data, setData] = useState<AdminData>(emptyData)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [reviewingId, setReviewingId] = useState<number | null>(null)
  const [reviewMode, setReviewMode] = useState<{ id: number; decision: 'approve' | 'reject' } | null>(null)
  const [reviewReason, setReviewReason] = useState('')
  const [reviewReference, setReviewReference] = useState('')
  const [reviewPassword, setReviewPassword] = useState('')
  const [autoTradingOpen, setAutoTradingOpen] = useState(false)
  const [autoTradingEnabled, setAutoTradingEnabled] = useState(true)
  const [autoTradingPassword, setAutoTradingPassword] = useState('')
  const [autoTradingBusy, setAutoTradingBusy] = useState(false)
  const modalRef = useRef<HTMLFormElement | null>(null)
  const modalTriggerRef = useRef<HTMLButtonElement | null>(null)
  const modalBusyRef = useRef(false)

  modalBusyRef.current = reviewingId !== null || autoTradingBusy

  const activeModal = reviewMode ? `review-${reviewMode.id}-${reviewMode.decision}` : autoTradingOpen ? 'auto-trading' : ''

  const metrics = useMemo(() => [
    { label: '活跃账户', value: data.overview.active_users, total: data.overview.users, Icon: UsersRound, tone: 'healthy' },
    { label: '待人工审核', value: data.overview.pending_orders, total: Math.max(1, data.claims.length), Icon: CreditCard, tone: 'warning' },
    { label: '订阅账户', value: data.overview.subscribers, total: data.overview.users, Icon: Gauge, tone: 'gold' },
    { label: '24h 严重风险', value: data.overview.critical_risk, total: Math.max(1, numberValue(data.overview.critical_risk)), Icon: AlertTriangle, tone: 'risk' },
  ], [data.claims.length, data.overview])

  async function load() {
    setLoading(true); setError('')
    try {
      const [overview, users, claims, brokers, audit] = await Promise.all([
        fetchAdminOverview(), fetchAdminUsers(), fetchAdminManualClaims(), fetchAdminBrokers(), fetchAdminAudit(),
      ])
      setData({ overview, users, claims, brokers, audit })
    } catch (caught) {
      setError(errorMessage(caught, '管理数据暂时不可用。未显示任何推断数据。'))
      setData(emptyData)
    } finally { setLoading(false) }
  }

  useEffect(() => { void load() }, [])

  useEffect(() => {
    if (!activeModal) return
    const modal = modalRef.current
    if (!modal) return
    const trigger = modalTriggerRef.current
    const focusableSelector = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
    const focusableItems = () => Array.from(modal.querySelectorAll<HTMLElement>(focusableSelector)).filter((item) => !item.hasAttribute('hidden'))
    const focusFirst = () => (focusableItems()[0] ?? modal).focus()
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (modalBusyRef.current) return
        event.preventDefault()
        event.stopPropagation()
        if (activeModal === 'auto-trading') setAutoTradingOpen(false)
        else setReviewMode(null)
        return
      }
      if (event.key !== 'Tab') return
      const items = focusableItems()
      if (!items.length) {
        event.preventDefault()
        modal.focus()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    const handleFocusIn = (event: FocusEvent) => {
      if (!modal.contains(event.target as Node)) focusFirst()
    }
    focusFirst()
    document.addEventListener('keydown', handleKeyDown, true)
    document.addEventListener('focusin', handleFocusIn, true)
    return () => {
      document.removeEventListener('keydown', handleKeyDown, true)
      document.removeEventListener('focusin', handleFocusIn, true)
      trigger?.focus()
    }
  }, [activeModal])

  async function submitReview(event: FormEvent) {
    event.preventDefault()
    if (!reviewMode || reviewingId !== null) return
    if (!reviewPassword || (reviewMode.decision === 'approve' && !reviewReference.trim()) || (reviewMode.decision === 'reject' && !reviewReason.trim())) return
    setReviewingId(reviewMode.id); setError(''); setNotice('')
    try {
      await reviewAdminManualClaim(reviewMode.id, reviewMode.decision === 'approve'
        ? { decision: 'approve', password: reviewPassword, settlement_reference: reviewReference.trim() }
        : { decision: 'reject', password: reviewPassword, rejection_reason: reviewReason.trim() })
      setNotice(`付款凭证 #${reviewMode.id} 已提交审核决定。`)
      setReviewMode(null); setReviewReason(''); setReviewReference(''); setReviewPassword('')
      await load()
    } catch (caught) { setError(errorMessage(caught, '付款凭证审核未完成。')) } finally { setReviewingId(null) }
  }

  async function submitAutoTrading(event: FormEvent) {
    event.preventDefault()
    if (autoTradingBusy || !autoTradingPassword) return
    const confirmation = autoTradingEnabled ? '恢复用户实盘服务' : '暂停用户实盘服务'
    setAutoTradingBusy(true); setError(''); setNotice('')
    try {
      const result = await updateAdminUserAutoTrading({ enabled: autoTradingEnabled, confirmation, password: autoTradingPassword })
      setNotice(`${confirmation}已提交${typeof result.affected_users === 'number' ? ` · 影响 ${result.affected_users} 个账户` : ''}。`)
      setAutoTradingOpen(false); setAutoTradingPassword('')
      await load()
    } catch (caught) { setError(errorMessage(caught, '用户实盘服务状态未改变。')) } finally { setAutoTradingBusy(false) }
  }

  return <div className="page operations-page admin-page">
    <PageHeader kicker="SUPER ADMIN / HUMAN REVIEW" title="超级管理" description="审核账户、人工付款与受控服务状态。所有操作保留审计记录；不可用数据不会被替换为演示值。" />
    <section className="admin-boundary" aria-label="管理台边界"><ShieldAlert size={18} /><span><strong>受控管理台</strong> 影子候选仅供研究，待人工审核；官方模拟不执行券商下单。</span></section>
    {error && <p className="form-error" role="alert"><CircleAlert size={16} />{error}</p>}
    {notice && <p className="admin-notice" role="status" aria-live="polite"><CheckCircle2 size={16} />{notice}</p>}
    <div className="admin-toolbar"><span role="status" aria-live="polite">{loading ? <><LoaderCircle className="spin" size={16} />正在读取管理数据…</> : '数据按当前管理员权限读取'}</span><button className="button secondary" type="button" disabled={loading} onClick={() => void load()}><RefreshCw size={16} />刷新</button><button className="button danger" type="button" onClick={(event) => { modalTriggerRef.current = event.currentTarget; setAutoTradingOpen(true) }}><AlertTriangle size={16} />用户实盘服务</button></div>
    <section className="admin-metric-grid" aria-label="运营概览">
      {metrics.map(({ label, value, total, Icon, tone }) => {
        const percentage = Math.min(100, Math.round(numberValue(value) / Math.max(1, numberValue(total)) * 100))
        return <article className={`admin-metric admin-metric-${tone}`} key={label}><Icon size={18} /><div className="admin-ring" style={{ '--admin-ring-value': percentage } as React.CSSProperties}><strong>{countLabel(value)}</strong><small>{percentage}%</small></div><span>{label}</span></article>
      })}
    </section>
    <div className="admin-grid">
      <section className="data-panel admin-panel admin-claims"><header className="panel-heading"><div><span>PAYMENT REVIEW</span><h2>人工付款凭证</h2></div><CreditCard size={20} /></header><p className="admin-panel-note">只有“待人工审核”的凭证可提交决定；批准与拒绝均写入审计记录。</p>{loading ? <AdminState label="正在读取付款凭证…" /> : data.claims.length ? <div className="responsive-table"><table><thead><tr><th>凭证</th><th>账户</th><th>金额</th><th>状态</th><th>操作</th></tr></thead><tbody>{data.claims.map((claim) => <tr key={claim.id}><td><strong>#{claim.id}</strong><small>{claim.order_no}</small></td><td>{claim.user_email ?? '未提供'}</td><td>{typeof claim.amount === 'number' ? `${claim.currency ?? ''} ${claim.amount.toLocaleString('zh-HK')}` : '未提供'}</td><td><span className={`admin-state ${claim.status === 'submitted' ? 'pending' : claim.status}`}>{claim.status === 'submitted' ? '待人工审核' : claim.status}</span></td><td>{claim.status === 'submitted' ? <span className="admin-row-actions"><button type="button" onClick={(event) => { modalTriggerRef.current = event.currentTarget; setReviewMode({ id: claim.id, decision: 'approve' }) }}>批准</button><button type="button" onClick={(event) => { modalTriggerRef.current = event.currentTarget; setReviewMode({ id: claim.id, decision: 'reject' }) }}>拒绝</button></span> : '已处理'}</td></tr>)}</tbody></table></div> : <AdminState label="没有待处理的人工付款凭证。" />}</section>
      <section className="data-panel admin-panel"><header className="panel-heading"><div><span>BROKER BOUNDARY</span><h2>券商连接</h2></div><ShieldAlert size={20} /></header><p className="admin-panel-note">账户标识始终掩码；本页不提供连接、交易或生产激活控制。</p>{loading ? <AdminState label="正在读取券商状态…" /> : data.brokers.length ? <ul className="admin-broker-list">{data.brokers.map((broker, index) => <li key={String(broker.id ?? index)}><span><strong>{broker.broker ?? '券商'}</strong><small>{maskBrokerAccount(broker.account_masked)}</small></span><span className="admin-state neutral">{broker.status ?? '状态未提供'}</span></li>)}</ul> : <AdminState label="没有可显示的券商连接记录。" />}</section>
      <section className="data-panel admin-panel"><header className="panel-heading"><div><span>ACCOUNT SCOPE</span><h2>账户概况</h2></div><UsersRound size={20} /></header>{loading ? <AdminState label="正在读取账户概况…" /> : data.users.length ? <ul className="admin-user-list">{data.users.slice(0, 6).map((user) => <li key={user.id}><span><strong>{user.display_name}</strong><small>{user.email}</small></span><span className={`admin-state ${user.is_active ? 'healthy' : 'risk'}`}>{user.is_active ? '活跃' : '已停用'}</span></li>)}</ul> : <AdminState label="没有可显示的账户记录。" />}</section>
      <section className="data-panel admin-panel"><header className="panel-heading"><div><span>AUDIT TRAIL</span><h2>最近审计记录</h2></div><ShieldAlert size={20} /></header>{loading ? <AdminState label="正在读取审计记录…" /> : data.audit.length ? <ol className="admin-audit-list">{data.audit.slice(0, 6).map((item, index) => <li key={String(item.id ?? index)}><strong>{item.action_type ?? '管理操作'}</strong><span>{item.actor_display ?? '管理员'} · {displayDate(item.created_at)}</span></li>)}</ol> : <AdminState label="没有可显示的审计记录。" />}</section>
    </div>
    {reviewMode && <div className="admin-modal-backdrop" role="presentation"><form ref={modalRef} className="admin-modal" tabIndex={-1} onSubmit={submitReview} aria-modal="true" role="dialog" aria-labelledby="claim-review-title"><header><h2 id="claim-review-title">{reviewMode.decision === 'approve' ? '批准付款凭证' : '拒绝付款凭证'}</h2><button className="icon-button" type="button" aria-label="关闭审核窗口" onClick={() => setReviewMode(null)}>×</button></header><p>此决定会提交当前超级管理员密码，由服务端原子审核并写入审计记录。请只依据已核验的付款资料操作。</p>{reviewMode.decision === 'approve' ? <label>结算参考号<input value={reviewReference} onChange={(event) => setReviewReference(event.target.value)} required maxLength={160} autoComplete="off" /></label> : <label>拒绝原因<input value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} required maxLength={500} autoComplete="off" /></label>}<label>管理员密码<input type="password" name="claim-review-password" value={reviewPassword} onChange={(event) => setReviewPassword(event.target.value)} autoComplete="current-password" required /></label><footer><button className="button secondary" type="button" disabled={reviewingId !== null} onClick={() => setReviewMode(null)}>取消</button><button className={`button ${reviewMode.decision === 'approve' ? 'primary' : 'danger'}`} type="submit" disabled={reviewingId !== null || !reviewPassword || (reviewMode.decision === 'approve' ? !reviewReference.trim() : !reviewReason.trim())}>{reviewingId !== null ? '提交中…' : '确认提交'}</button></footer></form></div>}
    {autoTradingOpen && <div className="admin-modal-backdrop" role="presentation"><form ref={modalRef} className="admin-modal admin-danger-modal" tabIndex={-1} onSubmit={submitAutoTrading} aria-modal="true" role="dialog" aria-labelledby="auto-trading-title"><header><h2 id="auto-trading-title">用户实盘服务</h2><button className="icon-button" type="button" aria-label="关闭用户实盘服务窗口" onClick={() => setAutoTradingOpen(false)}>×</button></header><p>这是高风险管理动作，不是券商交易操作。请输入密码后才能提交；服务端还会验证固定确认语。</p><fieldset><legend>目标状态</legend><label><input type="radio" name="auto-trading" checked={!autoTradingEnabled} onChange={() => setAutoTradingEnabled(false)} />暂停用户实盘服务</label><label><input type="radio" name="auto-trading" checked={autoTradingEnabled} onChange={() => setAutoTradingEnabled(true)} />恢复用户实盘服务</label></fieldset><label>管理员密码<input type="password" name="admin-password" value={autoTradingPassword} onChange={(event) => setAutoTradingPassword(event.target.value)} autoComplete="current-password" required /></label><footer><button className="button secondary" type="button" disabled={autoTradingBusy} onClick={() => setAutoTradingOpen(false)}>取消</button><button className="button danger" type="submit" disabled={!autoTradingPassword || autoTradingBusy}>{autoTradingBusy ? '正在提交…' : autoTradingEnabled ? '确认恢复服务' : '确认暂停服务'}</button></footer></form></div>}
  </div>
}

function AdminState({ label }: { label: string }) {
  return <div className="admin-empty" role="status"><span>{label}</span></div>
}
