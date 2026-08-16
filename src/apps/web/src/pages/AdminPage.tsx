import { AlertTriangle, Banknote, BarChart3, CheckCircle2, CircleAlert, CreditCard, Database, Fingerprint, Gauge, Gift, HandCoins, LoaderCircle, LockKeyhole, RefreshCw, ShieldAlert, TicketCheck, UsersRound } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  BrowserApiError,
  createAdminReferralCoupon,
  confirmAdminReferralWithdrawalPaid,
  fetchAdminAudit,
  fetchAdminBrokers,
  fetchAdminComputeEvidenceHistory,
  fetchAdminComputeEvidenceLatest,
  fetchAdminComputeEvidenceStatus,
  fetchAdminManualClaims,
  fetchAdminReferralAnalytics,
  fetchAdminReferralCoupons,
  fetchAdminReferralPolicy,
  fetchAdminOverview,
  fetchAdminReferralWithdrawals,
  fetchAdminUsers,
  pauseAdminReferralCoupon,
  reviewAdminReferralWithdrawal,
  reviewAdminManualClaim,
  updateAdminReferralPolicy,
  updateAdminUserAutoTrading,
  type AdminAuditEntry,
  type AdminBrokerAccount,
  type AdminComputeEvidenceHistory,
  type AdminComputeEvidenceLatest,
  type AdminComputeEvidenceStatus,
  type AdminManualClaim,
  type AdminOverview,
  type AdminReferralAnalytics,
  type AdminReferralAnalyticsFilter,
  type AdminReferralCoupon,
  type AdminReferralPolicy,
  type AdminReferralPolicyValue,
  type AdminReferralWithdrawal,
  type AdminUser,
} from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { brokerAccessApi, type AdminBrokerAccessApplication } from '../api/brokerAccess'

type AdminData = {
  overview: AdminOverview
  users: AdminUser[]
  claims: AdminManualClaim[]
  withdrawals: AdminReferralWithdrawal[]
  policy: AdminReferralPolicy | null
  coupons: AdminReferralCoupon[]
  analytics: AdminReferralAnalytics | null
  brokers: AdminBrokerAccount[]
  audit: AdminAuditEntry[]
  evidenceStatus: AdminComputeEvidenceStatus
  evidenceLatest: AdminComputeEvidenceLatest
  evidenceHistory: AdminComputeEvidenceHistory
}

const emptyEvidenceAuthority = { publication_ceiling: 'shadow', research_only: true, actionable: false, user_visible: false } as const
const emptyData: AdminData = {
  overview: {}, users: [], claims: [], withdrawals: [], policy: null, coupons: [], analytics: null, brokers: [], audit: [],
  evidenceStatus: { ...emptyEvidenceAuthority, available: false, counts: { quarantine: 0, shadow: 0 }, last_received_at: null },
  evidenceLatest: { ...emptyEvidenceAuthority, available: false, evidence: null },
  evidenceHistory: { ...emptyEvidenceAuthority, available: false, limit: 20, items: [] },
}

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

function shortHash(value: unknown) {
  const text = typeof value === 'string' ? value.trim() : ''
  return text.length >= 16 ? `${text.slice(0, 8)}…${text.slice(-6)}` : text || '未提供'
}

type CouponDraft = Omit<AdminReferralCoupon, 'coupon_id' | 'version' | 'created_at' | 'updated_at'>
const couponDraft = (): CouponDraft => ({ code: '', campaign_name: '', discount_type: 'percent', discount_value: 1000, max_discount_minor: null, min_spend_minor: 0, total_use_limit: 100, per_user_limit: 1, applicable_plans: ['标准版'], applicable_cycles: ['monthly'], starts_at: '', expires_at: '', enabled: true })
const policyFields: Array<[keyof Omit<AdminReferralPolicyValue, 'bonus_tiers' | 'bonus_enabled' | 'withdrawal_paused' | 'commission_rate_bps' | 'referral_discount_bps'>, string, number]> = [['minimum_final_amount_minor', '最低实付（HKD 分）', 1], ['commission_cap_minor', '首单佣金上限（HKD 分）', 1], ['hold_days', '冻结天数', 0], ['withdrawal_min_minor', '最低提现（HKD 分）', 1], ['withdrawal_max_minor', '最高提现（HKD 分）', 1], ['withdrawal_daily_limit', '日申请上限', 1], ['withdrawal_monthly_limit', '月申请上限', 1], ['withdrawal_open_limit', '待处理上限', 1], ['withdrawal_cooldown_days', '冷却天数', 0], ['automatic_payout_review_threshold_minor', '自动复核阈值（HKD 分）', 1]]

function withdrawalStatusLabel(status: AdminReferralWithdrawal['status']) {
  return {
    submitted: '待审核',
    approved: '待付款',
    rejected: '已拒绝',
    paid: '已付款',
    system_cancelled: '系统取消',
  }[status]
}
function formatMinor(value: number) { return `HKD ${(value / 100).toLocaleString('zh-HK', { minimumFractionDigits: 2 })}` }

export function AdminPage() {
  const [data, setData] = useState<AdminData>(emptyData)
  const [loading, setLoading] = useState(true)
  const [evidenceLoading, setEvidenceLoading] = useState(true)
  const [evidenceError, setEvidenceError] = useState('')
  const [evidenceUpdatedAt, setEvidenceUpdatedAt] = useState<string | null>(null)
  const evidenceUpdatedAtRef = useRef<string | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [reviewingId, setReviewingId] = useState<number | null>(null)
  const [reviewMode, setReviewMode] = useState<{ id: number; decision: 'approve' | 'reject' } | null>(null)
  const [reviewReason, setReviewReason] = useState('')
  const [reviewReference, setReviewReference] = useState('')
  const [reviewPassword, setReviewPassword] = useState('')
  const [withdrawalMode, setWithdrawalMode] = useState<{ item: AdminReferralWithdrawal; action: 'approve' | 'reject' | 'paid' } | null>(null)
  const [withdrawalReason, setWithdrawalReason] = useState('')
  const [withdrawalPassword, setWithdrawalPassword] = useState('')
  const [withdrawalPayoutMethod, setWithdrawalPayoutMethod] = useState<'fps' | 'bank' | 'other'>('fps')
  const [withdrawalReference, setWithdrawalReference] = useState('')
  const [withdrawalBusy, setWithdrawalBusy] = useState(false)
  const [withdrawalActionKey, setWithdrawalActionKey] = useState('')
  const [policyDraft, setPolicyDraft] = useState<AdminReferralPolicyValue | null>(null)
  const [policyPassword, setPolicyPassword] = useState('')
  const [policyBusy, setPolicyBusy] = useState(false)
  const [policyKey, setPolicyKey] = useState('')
  const [coupon, setCoupon] = useState<CouponDraft>(couponDraft)
  const [couponPassword, setCouponPassword] = useState('')
  const [couponBusy, setCouponBusy] = useState(false)
  const [couponKey, setCouponKey] = useState('')
  const [couponPausePassword, setCouponPausePassword] = useState('')
  const [couponPauseTarget, setCouponPauseTarget] = useState<AdminReferralCoupon | null>(null)
  const [couponPauseBusy, setCouponPauseBusy] = useState(false)
  const [couponPauseKey, setCouponPauseKey] = useState('')
  const [analyticsFilter, setAnalyticsFilter] = useState<AdminReferralAnalyticsFilter>({ promotion_type: 'all' })
  const [autoTradingOpen, setAutoTradingOpen] = useState(false)
  const [autoTradingEnabled, setAutoTradingEnabled] = useState(true)
  const [autoTradingPassword, setAutoTradingPassword] = useState('')
  const [autoTradingBusy, setAutoTradingBusy] = useState(false)
  const [brokerApplications, setBrokerApplications] = useState<AdminBrokerAccessApplication[]>([])
  const [brokerReviewBusy, setBrokerReviewBusy] = useState<string | null>(null)
  const modalRef = useRef<HTMLFormElement | null>(null)
  const modalTriggerRef = useRef<HTMLButtonElement | null>(null)
  const modalBusyRef = useRef(false)
  const noticeRef = useRef<HTMLParagraphElement | null>(null)
  const focusNoticeAfterLoadRef = useRef(false)

  modalBusyRef.current = reviewingId !== null || withdrawalBusy || couponPauseBusy || autoTradingBusy

  const activeModal = reviewMode
    ? `review-${reviewMode.id}-${reviewMode.decision}`
    : withdrawalMode
      ? `withdrawal-${withdrawalMode.item.withdrawal_id}-${withdrawalMode.action}`
      : couponPauseTarget ? `coupon-pause-${couponPauseTarget.coupon_id}`
        : autoTradingOpen ? 'auto-trading' : ''

  const metrics = useMemo(() => [
    { label: '活跃账户', value: data.overview.active_users, total: data.overview.users, Icon: UsersRound, tone: 'healthy' },
    { label: '待人工审核', value: data.overview.pending_orders, total: Math.max(1, data.claims.length), Icon: CreditCard, tone: 'warning' },
    { label: '订阅账户', value: data.overview.subscribers, total: data.overview.users, Icon: Gauge, tone: 'gold' },
    { label: '24h 严重风险', value: data.overview.critical_risk, total: Math.max(1, numberValue(data.overview.critical_risk)), Icon: AlertTriangle, tone: 'risk' },
  ], [data.claims.length, data.overview])

  const load = useCallback(async (filter: AdminReferralAnalyticsFilter = { promotion_type: 'all' }) => {
    setLoading(true); setError('')
    try {
      const [overview, users, claims, withdrawals, policy, coupons, analytics, brokers, audit, brokerAccess] = await Promise.all([
        fetchAdminOverview(), fetchAdminUsers(), fetchAdminManualClaims(), fetchAdminReferralWithdrawals('all'), fetchAdminReferralPolicy(), fetchAdminReferralCoupons(), fetchAdminReferralAnalytics(filter), fetchAdminBrokers(), fetchAdminAudit(), brokerAccessApi.adminList('submitted'),
      ])
      setData((current) => ({ ...current, overview, users, claims, withdrawals, policy, coupons, analytics, brokers, audit }))
      setBrokerApplications(brokerAccess)
      setPolicyDraft(policy.policy)
    } catch (caught) {
      setError(errorMessage(caught, '管理数据暂时不可用。未显示任何推断数据。'))
      setData((current) => ({ ...current, overview: {}, users: [], claims: [], withdrawals: [], policy: null, coupons: [], analytics: null, brokers: [], audit: [] }))
      setBrokerApplications([])
    } finally { setLoading(false) }
  }, [])

  async function reviewBrokerApplication(item: AdminBrokerAccessApplication, decision: 'approved' | 'rejected') {
    const reason = window.prompt(decision === 'approved' ? '请输入资格核验说明' : '请输入拒绝原因', '')?.trim()
    if (!reason) return
    setBrokerReviewBusy(item.id)
    try {
      await brokerAccessApi.review(item.id, decision, reason)
      setBrokerApplications((current) => current.filter((candidate) => candidate.id !== item.id))
      setNotice('券商资格审核已写入服务端；未创建券商账户，也未启用执行。')
    } catch (caught) { setError(errorMessage(caught, '券商资格审核未确认，请刷新队列。')) }
    finally { setBrokerReviewBusy(null) }
  }

  const refreshEvidence = useCallback(async () => {
    setEvidenceLoading(true); setEvidenceError('')
    try {
      const [evidenceStatus, evidenceLatest, evidenceHistory] = await Promise.all([
        fetchAdminComputeEvidenceStatus(), fetchAdminComputeEvidenceLatest(), fetchAdminComputeEvidenceHistory(20),
      ])
      setData((current) => ({ ...current, evidenceStatus, evidenceLatest, evidenceHistory }))
      const refreshedAt = new Date().toISOString()
      evidenceUpdatedAtRef.current = refreshedAt
      setEvidenceUpdatedAt(refreshedAt)
    } catch (caught) {
      const message = errorMessage(caught, '隔离研究收据暂时不可用。')
      const lastSuccessfulRefresh = evidenceUpdatedAtRef.current
      setEvidenceError(lastSuccessfulRefresh ? `${message} 当前保留上次成功刷新的只读快照（${displayDate(lastSuccessfulRefresh)}）。` : message)
    } finally { setEvidenceLoading(false) }
  }, [])

  useEffect(() => { void load(); void refreshEvidence() }, [load, refreshEvidence])

  useEffect(() => {
    if (!loading && notice && focusNoticeAfterLoadRef.current) {
      focusNoticeAfterLoadRef.current = false
      noticeRef.current?.focus()
    }
  }, [loading, notice])

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
        else if (activeModal.startsWith('withdrawal-')) setWithdrawalMode(null)
        else if (activeModal.startsWith('coupon-pause-')) { setCouponPauseTarget(null); setCouponPausePassword(''); setCouponPauseKey('') }
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
      focusNoticeAfterLoadRef.current = true
      setReviewMode(null); setReviewReason(''); setReviewReference(''); setReviewPassword('')
      await load()
    } catch (caught) { setError(errorMessage(caught, '付款凭证审核未完成。')) } finally { setReviewingId(null) }
  }

  function openWithdrawalAction(
    item: AdminReferralWithdrawal,
    action: 'approve' | 'reject' | 'paid',
    trigger: HTMLButtonElement,
  ) {
    modalTriggerRef.current = trigger
    setWithdrawalMode({ item, action })
    setWithdrawalReason('')
    setWithdrawalPassword('')
    setWithdrawalPayoutMethod('fps')
    setWithdrawalReference('')
    setWithdrawalActionKey(crypto.randomUUID())
  }

  function closeWithdrawalAction() {
    if (withdrawalBusy) return
    setWithdrawalMode(null)
    setWithdrawalReason('')
    setWithdrawalPassword('')
    setWithdrawalReference('')
    setWithdrawalActionKey('')
  }

  async function submitWithdrawalAction(event: FormEvent) {
    event.preventDefault()
    if (!withdrawalMode || withdrawalBusy || !withdrawalPassword || !withdrawalActionKey) return
    if (withdrawalMode.action === 'reject' && !withdrawalReason.trim()) return
    if (withdrawalMode.action === 'paid' && !withdrawalReference.trim()) return
    setWithdrawalBusy(true); setError(''); setNotice('')
    try {
      if (withdrawalMode.action === 'paid') {
        await confirmAdminReferralWithdrawalPaid(
          withdrawalMode.item.withdrawal_id,
          {
            password: withdrawalPassword,
            payout_method: withdrawalPayoutMethod,
            payout_reference: withdrawalReference.trim(),
          },
          withdrawalActionKey,
        )
      } else {
        await reviewAdminReferralWithdrawal(
          withdrawalMode.item.withdrawal_id,
          {
            decision: withdrawalMode.action,
            password: withdrawalPassword,
            ...(withdrawalReason.trim() ? { reason: withdrawalReason.trim() } : {}),
          },
          withdrawalActionKey,
        )
      }
      const actionLabel = withdrawalMode.action === 'approve' ? '批准' : withdrawalMode.action === 'reject' ? '拒绝' : '确认付款'
      setNotice(`推广提现 ${withdrawalMode.item.withdrawal_id} 已${actionLabel}。`)
      focusNoticeAfterLoadRef.current = true
      setWithdrawalMode(null)
      setWithdrawalReason('')
      setWithdrawalPassword('')
      setWithdrawalReference('')
      setWithdrawalActionKey('')
      await load()
    } catch (caught) {
      setError(errorMessage(caught, '推广提现操作未完成；可使用同一请求安全重试。'))
    } finally { setWithdrawalBusy(false) }
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

  async function savePolicy(event: FormEvent) {
    event.preventDefault()
    if (!data.policy || !policyDraft || !policyPassword || policyBusy) return
    setPolicyBusy(true); setError(''); setNotice('')
    try {
      await updateAdminReferralPolicy(data.policy.version, policyDraft, policyPassword, policyKey || crypto.randomUUID())
      setPolicyPassword(''); setPolicyKey(''); setNotice('推广政策已保存，下一笔订单会冻结新版本。'); focusNoticeAfterLoadRef.current = true; await load()
    } catch (caught) { setError(errorMessage(caught, '推广政策未保存；版本冲突时请刷新后重试。')) } finally { setPolicyBusy(false) }
  }

  async function createCoupon(event: FormEvent) {
    event.preventDefault()
    if (!couponPassword || !coupon.starts_at || !coupon.expires_at || couponBusy) return
    const startsAt = new Date(coupon.starts_at); const expiresAt = new Date(coupon.expires_at)
    if (!Number.isFinite(startsAt.valueOf()) || !Number.isFinite(expiresAt.valueOf()) || expiresAt <= startsAt) { setError('优惠码期限无效。'); return }
    setCouponBusy(true); setError(''); setNotice('')
    try {
      await createAdminReferralCoupon({ ...coupon, code: coupon.code.trim().toUpperCase(), campaign_name: coupon.campaign_name.trim(), starts_at: startsAt.toISOString(), expires_at: expiresAt.toISOString() }, couponPassword, couponKey || crypto.randomUUID())
      setCoupon(couponDraft()); setCouponPassword(''); setCouponKey(''); setNotice('优惠码已创建。'); focusNoticeAfterLoadRef.current = true; await load()
    } catch (caught) { setError(errorMessage(caught, '优惠码未创建；可使用同一请求安全重试。')) } finally { setCouponBusy(false) }
  }

  function pauseCoupon(item: AdminReferralCoupon) {
    if (couponBusy) return
    modalTriggerRef.current = document.activeElement instanceof HTMLButtonElement ? document.activeElement : null
    setCouponPauseTarget(item)
    setCouponPauseKey(crypto.randomUUID())
  }

  function closeCouponPause() {
    if (couponPauseBusy) return
    setCouponPauseTarget(null)
    setCouponPausePassword('')
    setCouponPauseKey('')
  }

  async function confirmCouponPause(event: FormEvent) {
    event.preventDefault()
    if (!couponPauseTarget || !couponPausePassword || !couponPauseKey || couponPauseBusy) return
    setCouponPauseBusy(true); setError(''); setNotice('')
    try {
      await pauseAdminReferralCoupon(couponPauseTarget.coupon_id, couponPauseTarget.version, couponPausePassword, couponPauseKey)
      const code = couponPauseTarget.code
      setCouponPauseTarget(null); setCouponPausePassword(''); setCouponPauseKey(''); setNotice(`优惠码 ${code} 已暂停。`); focusNoticeAfterLoadRef.current = true; await load()
    } catch (caught) { setError(errorMessage(caught, '优惠码未暂停；响应不确定时可使用同一请求安全重试。')) } finally { setCouponPauseBusy(false) }
  }

  function updatePolicyNumber(key: typeof policyFields[number][0], value: string) {
    if (!policyDraft) return
    setPolicyDraft({ ...policyDraft, [key]: Number(value) })
  }

  async function applyAnalytics(event: FormEvent) {
    event.preventDefault()
    const normalized = { ...analyticsFilter }
    for (const key of ['started_at', 'ended_at'] as const) {
      if (!normalized[key]) continue
      const moment = new Date(normalized[key])
      if (!Number.isFinite(moment.valueOf())) { setError('归因筛选时间无效。'); return }
      normalized[key] = moment.toISOString()
    }
    if (normalized.started_at && normalized.ended_at && normalized.ended_at < normalized.started_at) { setError('归因筛选结束时间必须晚于开始时间。'); return }
    setError('')
    await load(normalized)
  }

  return <div className="page operations-page admin-page">
    <PageHeader kicker="SUPER ADMIN / HUMAN REVIEW" title="超级管理" description="审核账户、人工付款与受控服务状态。所有操作保留审计记录；不可用数据不会被替换为演示值。" />
    <div className="admin-workspace">
      <aside className="admin-local-nav" aria-label="管理员运营导航"><header><span>OPERATIONS</span><strong>管理工作区</strong><small>按运营任务快速定位</small></header><nav><a href="#admin-overview"><Gauge /><span>运营概览</span></a><a href="#admin-payouts"><HandCoins /><span>审核与提现</span></a><a href="#admin-evidence"><Database /><span>研究收据</span></a><a href="#admin-growth"><Gift /><span>推广与政策</span></a><a href="#admin-accounts"><UsersRound /><span>账户与审计</span></a></nav><footer><ShieldAlert /><span>高风险动作始终要求密码复核与服务端校验。</span></footer></aside>
      <main className="admin-workspace-content">
    <section className="admin-boundary" id="admin-overview" aria-label="管理台边界"><ShieldAlert size={18} /><span><strong>受控管理台</strong> 影子候选仅供研究，待人工审核；官方模拟不执行券商下单。</span></section>
    {error && <p className="form-error" role="alert"><CircleAlert size={16} />{error}</p>}
    {notice && <p className="admin-notice" role="status" aria-live="polite" tabIndex={-1} ref={noticeRef}><CheckCircle2 size={16} />{notice}</p>}
    <div className="admin-toolbar"><span role="status" aria-live="polite">{loading ? <><LoaderCircle className="spin" size={16} />正在读取管理数据…</> : '数据按当前管理员权限读取'}</span><button className="button secondary" type="button" disabled={loading} onClick={() => void load()}><RefreshCw size={16} />刷新</button><button className="button danger" type="button" onClick={(event) => { modalTriggerRef.current = event.currentTarget; setAutoTradingOpen(true) }}><AlertTriangle size={16} />用户实盘服务</button></div>
    <section className="admin-metric-grid" aria-label="运营概览">
      {metrics.map(({ label, value, total, Icon, tone }) => {
        const percentage = Math.min(100, Math.round(numberValue(value) / Math.max(1, numberValue(total)) * 100))
        return <article className={`admin-metric admin-metric-${tone}`} key={label}><Icon size={18} /><div className="admin-ring" style={{ '--admin-ring-value': percentage } as React.CSSProperties}><strong>{countLabel(value)}</strong><small>{percentage}%</small></div><span>{label}</span></article>
      })}
    </section>
    <section className="data-panel admin-panel admin-withdrawals" id="admin-payouts" aria-labelledby="admin-withdrawals-title">
      <header className="panel-heading">
        <div><span>REFERRAL PAYOUT / DUAL CONTROL</span><h2 id="admin-withdrawals-title">推广提现队列</h2></div>
        <HandCoins size={20} />
      </header>
      <p className="admin-panel-note">批准与付款确认必须由不同管理员完成；每次提交都使用持久幂等回执，响应不确定时可安全重试。</p>
      {loading ? <AdminState label="正在读取推广提现队列…" /> : data.withdrawals.length ? <div className="responsive-table"><table>
        <thead><tr><th>申请</th><th>推广账户</th><th>金额</th><th>状态</th><th>时间</th><th>操作</th></tr></thead>
        <tbody>{data.withdrawals.map((item) => <tr key={item.withdrawal_id}>
          <td><strong>{item.withdrawal_id}</strong><small>{item.user_reference}</small></td>
          <td>{item.user_masked}</td>
          <td><strong>HKD {(item.amount_minor / 100).toLocaleString('zh-HK', { minimumFractionDigits: 2 })}</strong></td>
          <td><span className={`admin-state ${item.status}`}>{withdrawalStatusLabel(item.status)}</span></td>
          <td>{displayDate(item.submitted_at)}</td>
          <td>{item.status === 'submitted' ? <span className="admin-row-actions">
            <button type="button" onClick={(event) => openWithdrawalAction(item, 'approve', event.currentTarget)}>批准</button>
            <button type="button" onClick={(event) => openWithdrawalAction(item, 'reject', event.currentTarget)}>拒绝</button>
          </span> : item.status === 'approved' ? <span className="admin-row-actions">
            <button type="button" onClick={(event) => openWithdrawalAction(item, 'paid', event.currentTarget)}><Banknote size={14} />确认付款</button>
            <button type="button" onClick={(event) => openWithdrawalAction(item, 'reject', event.currentTarget)}>拒绝</button>
          </span> : <span className="table-muted">已完成</span>}</td>
        </tr>)}</tbody>
      </table></div> : <AdminState label="当前没有推广提现申请。" />}
    </section>
    <section className="admin-evidence" id="admin-evidence" aria-labelledby="compute-evidence-title">
      <header>
        <div className="admin-evidence-title"><span><LockKeyhole size={18} /></span><div><small>COMPUTE EVIDENCE / QUARANTINE</small><h2 id="compute-evidence-title">策略研究收据隔离区</h2><p>只读查看已回传的研究证据；始终不可执行、不可推送、不可对用户显示。</p>{evidenceUpdatedAt && <small className="admin-evidence-refreshed">上次成功刷新：{displayDate(evidenceUpdatedAt)}</small>}</div></div>
        <button className="button secondary" type="button" disabled={evidenceLoading} onClick={() => void refreshEvidence()}><RefreshCw className={evidenceLoading ? 'spin' : ''} size={16} />{evidenceLoading ? '刷新中…' : '刷新收据'}</button>
      </header>
      {evidenceError && <p className="admin-evidence-error" role="alert"><CircleAlert size={15} />{evidenceError}</p>}
      {evidenceLoading ? <AdminState label="正在读取隔离研究收据…" /> : <>
        <div className="admin-evidence-metrics">
          <article><Database size={17} /><span>收据总数</span><strong>{countLabel(data.evidenceStatus.counts.quarantine + data.evidenceStatus.counts.shadow)}</strong></article>
          <article><ShieldAlert size={17} /><span>隔离 / 影子</span><strong>{countLabel(data.evidenceStatus.counts.quarantine)} / {countLabel(data.evidenceStatus.counts.shadow)}</strong></article>
          <article><Gauge size={17} /><span>最新候选</span><strong>{data.evidenceLatest.evidence?.candidate_id ?? '尚无收据'}</strong></article>
          <article><Fingerprint size={17} /><span>最近接收</span><strong>{displayDate(data.evidenceStatus.last_received_at)}</strong></article>
        </div>
        {data.evidenceLatest.evidence ? <div className="admin-evidence-latest">
          <div><span className={`admin-state ${data.evidenceLatest.evidence.publication_state === 'quarantine' ? 'pending' : 'healthy'}`}>{data.evidenceLatest.evidence.publication_state === 'quarantine' ? '隔离' : '影子'}</span><small>最新候选</small><strong>{data.evidenceLatest.evidence.candidate_id}</strong><p>{data.evidenceLatest.evidence.symbols.join(' · ')} · {data.evidenceLatest.evidence.candidate_version}</p></div>
          <dl><div><dt>候选状态</dt><dd>{data.evidenceLatest.evidence.candidate_status}</dd></div><div><dt>完成时间</dt><dd>{displayDate(data.evidenceLatest.evidence.completed_at)}</dd></div><div><dt>Manifest</dt><dd title={data.evidenceLatest.evidence.manifest_sha256}>{shortHash(data.evidenceLatest.evidence.manifest_sha256)}</dd></div><div><dt>Result</dt><dd title={data.evidenceLatest.evidence.result_sha256}>{shortHash(data.evidenceLatest.evidence.result_sha256)}</dd></div></dl>
        </div> : <AdminState label="尚未收到可显示的隔离研究收据。" />}
        {data.evidenceHistory.items.length > 0 && <div className="admin-evidence-history"><header><span>最近收据</span><small>最多显示 20 条</small></header><div className="responsive-table"><table><thead><tr><th>候选</th><th>状态</th><th>接收时间</th><th>Package</th></tr></thead><tbody>{data.evidenceHistory.items.map((item) => <tr key={`${item.package_sha256}:${item.received_at}`}><td><strong>{item.candidate_id}</strong><small>{item.candidate_version}</small></td><td><span className={`admin-state ${item.publication_state === 'quarantine' ? 'pending' : 'healthy'}`}>{item.publication_state === 'quarantine' ? '隔离' : '影子'}</span></td><td>{displayDate(item.received_at)}</td><td><code title={item.package_sha256}>{shortHash(item.package_sha256)}</code></td></tr>)}</tbody></table></div></div>}
      </>}
    </section>
    <section className="admin-promotion-grid" id="admin-growth" aria-label="推广政策、优惠码与归因">
      <section className="data-panel admin-policy-details"><header className="panel-heading"><div><span>POLICY LIMITS / SERVER VALIDATED</span><h2>提现与首单限制</h2></div><LockKeyhole size={20} /></header>{loading || !policyDraft ? <AdminState label="正在读取限制…" /> : <><p className="admin-panel-note">首单佣金固定 10%（1000 bps），推荐折扣固定 5%（500 bps）。修改限额后，请在“推广政策与奖金阶梯”输入密码并统一保存。</p><div className="admin-policy-fields">{policyFields.map(([key, label, minimum]) => <label key={key}>{label}<input name={`policy-${key}`} autoComplete="off" inputMode="numeric" type="number" min={minimum} value={policyDraft[key]} onChange={(event) => updatePolicyNumber(key, event.target.value)} /></label>)}</div><label className="admin-switch"><input name="policy-withdrawal-paused" type="checkbox" checked={policyDraft.withdrawal_paused} onChange={(event) => setPolicyDraft({ ...policyDraft, withdrawal_paused: event.target.checked })} /><span className="admin-switch-track" aria-hidden="true"><i /></span><span>暂停提现申请</span></label></>}</section>
      <form className="data-panel admin-analytics-controls" onSubmit={applyAnalytics}><header className="panel-heading"><div><span>ANALYTICS FILTERS / SERVER</span><h2>归因筛选</h2></div><BarChart3 size={20} /></header><label>优惠码<input name="analytics-coupon" autoComplete="off" value={analyticsFilter.coupon_code ?? ''} onChange={(event) => setAnalyticsFilter({ ...analyticsFilter, coupon_code: event.target.value })} /></label><label>活动<input name="analytics-campaign" autoComplete="off" value={analyticsFilter.campaign ?? ''} onChange={(event) => setAnalyticsFilter({ ...analyticsFilter, campaign: event.target.value })} /></label><label>状态<select name="analytics-status" value={analyticsFilter.status ?? ''} onChange={(event) => setAnalyticsFilter({ ...analyticsFilter, status: event.target.value as AdminReferralAnalyticsFilter['status'] })}><option value="">全部</option><option value="paid">已支付</option><option value="refunded">已退款</option><option value="pending">待处理</option><option value="cancelled">已取消</option><option value="failed">失败</option></select></label><label>归因<select name="analytics-type" value={analyticsFilter.promotion_type ?? 'all'} onChange={(event) => setAnalyticsFilter({ ...analyticsFilter, promotion_type: event.target.value as AdminReferralAnalyticsFilter['promotion_type'] })}><option value="all">全部</option><option value="coupon_only">仅优惠码</option><option value="referral_only">仅推荐</option><option value="stacked">叠加</option><option value="none">无归因</option></select></label><label>开始时间<input name="analytics-start" autoComplete="off" type="datetime-local" value={analyticsFilter.started_at ?? ''} onChange={(event) => setAnalyticsFilter({ ...analyticsFilter, started_at: event.target.value })} /></label><label>结束时间<input name="analytics-end" autoComplete="off" type="datetime-local" value={analyticsFilter.ended_at ?? ''} onChange={(event) => setAnalyticsFilter({ ...analyticsFilter, ended_at: event.target.value })} /></label><footer><button className="button secondary" type="button" onClick={() => { const reset = { promotion_type: 'all' as const }; setAnalyticsFilter(reset); void load(reset) }}>重置</button><button className="button primary" type="submit" disabled={loading}>应用筛选</button></footer></form>
      <form className="data-panel admin-promotion-policy" onSubmit={savePolicy}><header className="panel-heading"><div><span>REFERRAL POLICY / VERSIONED</span><h2>推广政策与奖金阶梯</h2></div><Gift size={20} /></header>{loading || !policyDraft || !data.policy ? <AdminState label="正在读取推广政策…" /> : <><p className="admin-panel-note">版本 {data.policy.version} · 金额均为 HKD 分；服务端会再次验证并冻结到新订单。</p><label className="admin-switch"><input name="policy-bonus-enabled" type="checkbox" checked={policyDraft.bonus_enabled} onChange={(event) => setPolicyDraft({ ...policyDraft, bonus_enabled: event.target.checked })} /><span className="admin-switch-track" aria-hidden="true"><i /></span><span>启用推荐人数固定奖金</span></label><div className="admin-tier-editor">{policyDraft.bonus_tiers.map((tier, index) => <div key={index}><label>推荐人数<input name={`bonus-count-${index}`} autoComplete="off" inputMode="numeric" type="number" min="1" value={tier.qualified_count} onChange={(event) => setPolicyDraft({ ...policyDraft, bonus_tiers: policyDraft.bonus_tiers.map((item, itemIndex) => itemIndex === index ? { ...item, qualified_count: Number(event.target.value) } : item) })} /></label><label>累计奖金（分）<input name={`bonus-amount-${index}`} autoComplete="off" inputMode="numeric" type="number" min="1" value={tier.cumulative_amount_minor} onChange={(event) => setPolicyDraft({ ...policyDraft, bonus_tiers: policyDraft.bonus_tiers.map((item, itemIndex) => itemIndex === index ? { ...item, cumulative_amount_minor: Number(event.target.value) } : item) })} /></label><button type="button" disabled={policyDraft.bonus_tiers.length === 1} onClick={() => setPolicyDraft({ ...policyDraft, bonus_tiers: policyDraft.bonus_tiers.filter((_, itemIndex) => itemIndex !== index) })}>移除</button></div>)}</div><div className="admin-tier-actions"><button type="button" disabled={policyDraft.bonus_tiers.length >= 10} onClick={() => setPolicyDraft({ ...policyDraft, bonus_tiers: [...policyDraft.bonus_tiers, { qualified_count: (policyDraft.bonus_tiers.at(-1)?.qualified_count ?? 0) + 1, cumulative_amount_minor: (policyDraft.bonus_tiers.at(-1)?.cumulative_amount_minor ?? 0) + 1000 }] })}>添加阶梯</button><span>跨阶仅结算累计目标的差额。</span></div><label>管理员密码<input name="referral-policy-password" aria-label="推广政策管理员密码" type="password" value={policyPassword} onChange={(event) => { setPolicyPassword(event.target.value); if (!policyKey) setPolicyKey(crypto.randomUUID()) }} autoComplete="current-password" required /></label><button className="button primary" disabled={policyBusy || !policyPassword} type="submit">{policyBusy ? '保存中…' : '保存推广政策'}</button></>}</form>
      <section className="data-panel admin-promotion-analytics"><header className="panel-heading"><div><span>ATTRIBUTION / SERVER TOTALS</span><h2>推广归因仪表盘</h2></div><BarChart3 size={20} /></header>{loading || !data.analytics ? <AdminState label="正在读取归因统计…" /> : <><div className="admin-attribution-metrics">{[['订单', data.analytics.summary.orders], ['客户', data.analytics.summary.customers], ['收入', formatMinor(data.analytics.summary.net_revenue_minor)], ['折扣', formatMinor(data.analytics.summary.coupon_cost_minor + data.analytics.summary.referral_cost_minor)], ['佣金', formatMinor(data.analytics.summary.commission_cost_minor)], ['奖金', formatMinor(data.analytics.summary.bonus_cost_minor)], ['退款', formatMinor(data.analytics.summary.refund_or_chargeback_minor)], ['叠加订单', data.analytics.summary.stacked_orders]].map(([label, value]) => <div key={String(label)}><small>{label}</small><strong>{value}</strong></div>)}</div>{data.analytics.items.length ? <><p className="admin-panel-note">当前筛选返回 {data.analytics.items.length} 笔订单；明细最多显示前 50 笔，汇总金额全部来自服务端。</p><div className="responsive-table admin-attribution-table"><table><thead><tr><th>订单 / 归因</th><th>收入</th><th>折扣</th><th>佣金 / 奖金</th><th>退款</th></tr></thead><tbody>{data.analytics.items.slice(0, 50).map((item) => <tr key={item.order_id}><td><strong>{item.order_id}</strong><small>{item.promotion_type} · {item.coupon_code ?? '无优惠码'}</small></td><td>{formatMinor(item.net_revenue_minor)}</td><td>{formatMinor(item.discount_cost_minor)}</td><td>{formatMinor(item.commission_cost_minor + item.bonus_cost_minor)}</td><td>{formatMinor(item.refund_or_chargeback_minor)}</td></tr>)}</tbody></table></div></> : <AdminState label="当前筛选没有符合条件的推广订单。请调整优惠码、活动、日期或归因类型。" />}</>}</section>
    </section>
    <section className="data-panel admin-coupons" id="admin-coupons"><header className="panel-heading"><div><span>COUPONS / PASSWORD REAUTH</span><h2>优惠码管理</h2></div><TicketCheck size={20} /></header><p className="admin-panel-note">每笔订单只接受 1 张优惠码；优惠码先计算，再计算推荐新客 95 折。百分比最高 15%，固定优惠最高 HKD 1,000。</p><form className="admin-coupon-form" onSubmit={createCoupon}><label>代码<input name="coupon-code" autoComplete="off" spellCheck={false} value={coupon.code} maxLength={64} required onChange={(event) => setCoupon({ ...coupon, code: event.target.value })} /></label><label>活动名称<input name="coupon-campaign" autoComplete="off" value={coupon.campaign_name} maxLength={120} required onChange={(event) => setCoupon({ ...coupon, campaign_name: event.target.value })} /></label><label>类型<select name="coupon-type" value={coupon.discount_type} onChange={(event) => setCoupon({ ...coupon, discount_type: event.target.value as CouponDraft['discount_type'] })}><option value="percent">百分比（bps）</option><option value="fixed_hkd">固定 HKD 分</option></select></label><label>优惠值<input name="coupon-value" autoComplete="off" inputMode="numeric" type="number" min="1" max={coupon.discount_type === 'percent' ? 1500 : 100000} value={coupon.discount_value} onChange={(event) => setCoupon({ ...coupon, discount_value: Number(event.target.value) })} /></label><label>最高优惠（HKD 分，可留空）<input name="coupon-cap" autoComplete="off" inputMode="numeric" type="number" min="1" max="100000" value={coupon.max_discount_minor ?? ''} onChange={(event) => setCoupon({ ...coupon, max_discount_minor: event.target.value ? Number(event.target.value) : null })} /></label><label>最低消费（HKD 分）<input name="coupon-min-spend" autoComplete="off" inputMode="numeric" type="number" min="0" value={coupon.min_spend_minor} onChange={(event) => setCoupon({ ...coupon, min_spend_minor: Number(event.target.value) })} /></label><label>总使用上限<input name="coupon-total-limit" autoComplete="off" inputMode="numeric" type="number" min="1" value={coupon.total_use_limit} onChange={(event) => setCoupon({ ...coupon, total_use_limit: Number(event.target.value) })} /></label><label>单用户上限<input name="coupon-user-limit" autoComplete="off" inputMode="numeric" type="number" min="1" value={coupon.per_user_limit} onChange={(event) => setCoupon({ ...coupon, per_user_limit: Number(event.target.value) })} /></label><label>开始<input name="coupon-start" autoComplete="off" type="datetime-local" required value={coupon.starts_at} onChange={(event) => setCoupon({ ...coupon, starts_at: event.target.value })} /></label><label>结束<input name="coupon-end" autoComplete="off" type="datetime-local" required value={coupon.expires_at} onChange={(event) => setCoupon({ ...coupon, expires_at: event.target.value })} /></label><label className="admin-switch"><input name="coupon-enabled" type="checkbox" checked={coupon.enabled} onChange={(event) => setCoupon({ ...coupon, enabled: event.target.checked })} /><span className="admin-switch-track" aria-hidden="true"><i /></span><span>创建后立即启用</span></label><fieldset><legend>适用方案</legend>{(['标准版', '高级版'] as const).map((plan) => <label key={plan}><input name={`coupon-plan-${plan === '标准版' ? 'standard' : 'premium'}`} type="checkbox" checked={coupon.applicable_plans.includes(plan)} onChange={(event) => setCoupon({ ...coupon, applicable_plans: event.target.checked ? [...coupon.applicable_plans, plan] : coupon.applicable_plans.filter((item) => item !== plan) })} />{plan}</label>)}</fieldset><fieldset><legend>适用周期</legend>{(['monthly', 'quarterly', 'yearly'] as const).map((cycle) => <label key={cycle}><input name={`coupon-cycle-${cycle}`} type="checkbox" checked={coupon.applicable_cycles.includes(cycle)} onChange={(event) => setCoupon({ ...coupon, applicable_cycles: event.target.checked ? [...coupon.applicable_cycles, cycle] : coupon.applicable_cycles.filter((item) => item !== cycle) })} />{cycle}</label>)}</fieldset><label>管理员密码<input name="coupon-admin-password" aria-label="优惠码管理员密码" type="password" value={couponPassword} onChange={(event) => { setCouponPassword(event.target.value); if (!couponKey) setCouponKey(crypto.randomUUID()) }} autoComplete="current-password" required /></label><button className="button primary" disabled={couponBusy || !couponPassword || !coupon.applicable_plans.length || !coupon.applicable_cycles.length} type="submit">{couponBusy ? '创建中…' : '创建优惠码'}</button></form><div className="admin-coupon-pause"><label>暂停操作密码<input name="coupon-pause-password" aria-label="暂停优惠码管理员密码" type="password" value={couponPausePassword} onChange={(event) => setCouponPausePassword(event.target.value)} autoComplete="current-password" /></label><span>暂停会要求当前版本一致；已领取订单不会由前端改写。</span></div>{loading ? <AdminState label="正在读取优惠码…" /> : data.coupons.length ? <div className="responsive-table"><table><thead><tr><th>代码</th><th>期限</th><th>限制</th><th>方案 / 周期</th><th>状态</th><th>操作</th></tr></thead><tbody>{data.coupons.map((item) => <tr key={item.coupon_id}><td><strong>{item.code}</strong><small>{item.campaign_name} · v{item.version}</small></td><td>{displayDate(item.starts_at)}<small>{displayDate(item.expires_at)}</small></td><td>{item.discount_type === 'percent' ? `${item.discount_value / 100}%` : formatMinor(item.discount_value)}<small>最低 {formatMinor(item.min_spend_minor)} · 总量 {item.total_use_limit} · 每人 {item.per_user_limit}</small></td><td>{item.applicable_plans.join(' / ')}<small>{item.applicable_cycles.join(' / ')}</small></td><td><span className={`admin-state ${item.enabled ? 'healthy' : 'system_cancelled'}`}>{item.enabled ? '启用' : '已暂停'}</span></td><td>{item.enabled ? <button type="button" disabled={couponBusy || !couponPausePassword} onClick={() => void pauseCoupon(item)}>暂停</button> : '—'}</td></tr>)}</tbody></table></div> : <AdminState label="暂无优惠码；可在上方创建。" />}</section>
    <div className="admin-grid" id="admin-accounts">
      <section className="data-panel admin-panel admin-claims"><header className="panel-heading"><div><span>PAYMENT REVIEW</span><h2>人工付款凭证</h2></div><CreditCard size={20} /></header><p className="admin-panel-note">只有“待人工审核”的凭证可提交决定；批准与拒绝均写入审计记录。</p>{loading ? <AdminState label="正在读取付款凭证…" /> : data.claims.length ? <div className="responsive-table"><table><thead><tr><th>凭证</th><th>账户</th><th>金额</th><th>状态</th><th>操作</th></tr></thead><tbody>{data.claims.map((claim) => <tr key={claim.id}><td><strong>#{claim.id}</strong><small>{claim.order_no}</small></td><td>{claim.user_email ?? '未提供'}</td><td>{typeof claim.amount === 'number' ? `${claim.currency ?? ''} ${claim.amount.toLocaleString('zh-HK')}` : '未提供'}</td><td><span className={`admin-state ${claim.status === 'submitted' ? 'pending' : claim.status}`}>{claim.status === 'submitted' ? '待人工审核' : claim.status}</span></td><td>{claim.status === 'submitted' ? <span className="admin-row-actions"><button type="button" onClick={(event) => { modalTriggerRef.current = event.currentTarget; setReviewMode({ id: claim.id, decision: 'approve' }) }}>批准</button><button type="button" onClick={(event) => { modalTriggerRef.current = event.currentTarget; setReviewMode({ id: claim.id, decision: 'reject' }) }}>拒绝</button></span> : '已处理'}</td></tr>)}</tbody></table></div> : <AdminState label="没有待处理的人工付款凭证。" />}</section>
      <section className="data-panel admin-panel"><header className="panel-heading"><div><span>BROKER BOUNDARY</span><h2>券商连接</h2></div><ShieldAlert size={20} /></header><p className="admin-panel-note">账户标识始终掩码；本页不提供连接、交易或生产激活控制。</p>{loading ? <AdminState label="正在读取券商状态…" /> : data.brokers.length ? <ul className="admin-broker-list">{data.brokers.map((broker, index) => <li key={String(broker.id ?? index)}><span><strong>{broker.broker ?? '券商'}</strong><small>{maskBrokerAccount(broker.account_masked)}</small></span><span className="admin-state neutral">{broker.status ?? '状态未提供'}</span></li>)}</ul> : <AdminState label="没有可显示的券商连接记录。" />}</section>
      <section className="data-panel admin-panel admin-broker-access"><header className="panel-heading"><div><span>BROKER ELIGIBILITY / HUMAN REVIEW</span><h2>券商资格审核</h2></div><ShieldAlert size={20} /></header><p className="admin-panel-note">仅审核服务端资格申请；批准不会创建券商账户、启用执行、发送 Telegram，也不是全局 kill-switch。</p>{loading ? <AdminState label="正在读取资格申请…" /> : brokerApplications.length ? <ul className="admin-broker-list">{brokerApplications.map((item) => <li key={item.id}><span><strong>{item.provider}</strong><small>{item.user_display_name} · {item.id}</small></span><span className="admin-row-actions"><button type="button" disabled={brokerReviewBusy === item.id} onClick={() => void reviewBrokerApplication(item, 'approved')}>批准资格</button><button type="button" className="danger" disabled={brokerReviewBusy === item.id} onClick={() => void reviewBrokerApplication(item, 'rejected')}>拒绝</button></span></li>)}</ul> : <AdminState label="暂无待审核券商资格申请。" />}</section>
      <section className="data-panel admin-panel"><header className="panel-heading"><div><span>ACCOUNT SCOPE</span><h2>账户概况</h2></div><UsersRound size={20} /></header>{loading ? <AdminState label="正在读取账户概况…" /> : data.users.length ? <ul className="admin-user-list">{data.users.slice(0, 6).map((user) => <li key={user.id}><span><strong>{user.display_name}</strong><small>{user.email}</small></span><span className={`admin-state ${user.is_active ? 'healthy' : 'risk'}`}>{user.is_active ? '活跃' : '已停用'}</span></li>)}</ul> : <AdminState label="没有可显示的账户记录。" />}</section>
      <section className="data-panel admin-panel"><header className="panel-heading"><div><span>AUDIT TRAIL</span><h2>最近审计记录</h2></div><ShieldAlert size={20} /></header>{loading ? <AdminState label="正在读取审计记录…" /> : data.audit.length ? <ol className="admin-audit-list">{data.audit.slice(0, 6).map((item, index) => <li key={String(item.id ?? index)}><strong>{item.action_type ?? '管理操作'}</strong><span>{item.actor_display ?? '管理员'} · {displayDate(item.created_at)}</span></li>)}</ol> : <AdminState label="没有可显示的审计记录。" />}</section>
    </div>
      </main>
    </div>
    {withdrawalMode && <div className="admin-modal-backdrop" role="presentation"><form ref={modalRef} className="admin-modal" tabIndex={-1} onSubmit={submitWithdrawalAction} aria-modal="true" role="dialog" aria-labelledby="withdrawal-action-title">
      <header><h2 id="withdrawal-action-title">{withdrawalMode.action === 'approve' ? '批准推广提现' : withdrawalMode.action === 'reject' ? '拒绝推广提现' : '确认推广提现付款'}</h2><button className="icon-button" type="button" aria-label="关闭推广提现窗口" onClick={closeWithdrawalAction}>×</button></header>
      <p><strong>{withdrawalMode.item.withdrawal_id}</strong> · HKD {(withdrawalMode.item.amount_minor / 100).toLocaleString('zh-HK', { minimumFractionDigits: 2 })}。服务端会重新验证权限、自审限制和双人复核。</p>
      {withdrawalMode.action === 'paid' ? <>
        <label>付款方式<select name="withdrawal-payout-method" value={withdrawalPayoutMethod} onChange={(event) => setWithdrawalPayoutMethod(event.target.value as 'fps' | 'bank' | 'other')}><option value="fps">FPS</option><option value="bank">银行转账</option><option value="other">其他已核验方式</option></select></label>
        <label>付款参考编号<input name="withdrawal-payout-reference" value={withdrawalReference} onChange={(event) => setWithdrawalReference(event.target.value)} required maxLength={160} autoComplete="off" /></label>
      </> : <label>{withdrawalMode.action === 'reject' ? '拒绝原因' : '审核说明（高金额提款必填）'}<textarea name="withdrawal-review-reason" value={withdrawalReason} onChange={(event) => setWithdrawalReason(event.target.value)} required={withdrawalMode.action === 'reject'} maxLength={500} rows={3} autoComplete="off" /></label>}
      <label>管理员密码<input type="password" name="withdrawal-admin-password" value={withdrawalPassword} onChange={(event) => setWithdrawalPassword(event.target.value)} autoComplete="current-password" required /></label>
      <footer><button className="button secondary" type="button" disabled={withdrawalBusy} onClick={closeWithdrawalAction}>取消</button><button className={`button ${withdrawalMode.action === 'reject' ? 'danger' : 'primary'}`} type="submit" disabled={withdrawalBusy || !withdrawalPassword || (withdrawalMode.action === 'reject' && !withdrawalReason.trim()) || (withdrawalMode.action === 'paid' && !withdrawalReference.trim())}>{withdrawalBusy ? '提交中…' : withdrawalMode.action === 'approve' ? '确认批准' : withdrawalMode.action === 'reject' ? '确认拒绝' : '确认已付款'}</button></footer>
    </form></div>}
    {couponPauseTarget && <div className="admin-modal-backdrop" role="presentation"><form ref={modalRef} className="admin-modal admin-danger-modal" tabIndex={-1} onSubmit={confirmCouponPause} aria-modal="true" role="dialog" aria-labelledby="coupon-pause-title">
      <header><h2 id="coupon-pause-title">确认暂停优惠码</h2><button className="icon-button" type="button" aria-label="关闭暂停优惠码窗口" onClick={closeCouponPause}>×</button></header>
      <p><strong>{couponPauseTarget.code}</strong> 将立即停止接受新订单；已领取订单不会由前端改写。</p>
      <label>管理员密码<input name="coupon-pause-confirm-password" aria-label="暂停优惠码管理员密码" type="password" value={couponPausePassword} onChange={(event) => setCouponPausePassword(event.target.value)} autoComplete="current-password" required /></label>
      <footer><button className="button secondary" type="button" disabled={couponPauseBusy} onClick={closeCouponPause}>取消</button><button className="button danger" type="submit" disabled={couponPauseBusy || !couponPausePassword}>{couponPauseBusy ? '暂停中…' : '确认暂停'}</button></footer>
    </form></div>}
    {reviewMode && <div className="admin-modal-backdrop" role="presentation"><form ref={modalRef} className="admin-modal" tabIndex={-1} onSubmit={submitReview} aria-modal="true" role="dialog" aria-labelledby="claim-review-title"><header><h2 id="claim-review-title">{reviewMode.decision === 'approve' ? '批准付款凭证' : '拒绝付款凭证'}</h2><button className="icon-button" type="button" aria-label="关闭审核窗口" onClick={() => setReviewMode(null)}>×</button></header><p>此决定会提交当前超级管理员密码，由服务端原子审核并写入审计记录。请只依据已核验的付款资料操作。</p>{reviewMode.decision === 'approve' ? <label>结算参考号<input name="claim-review-reference" value={reviewReference} onChange={(event) => setReviewReference(event.target.value)} required maxLength={160} autoComplete="off" /></label> : <label>拒绝原因<input name="claim-review-reason" value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} required maxLength={500} autoComplete="off" /></label>}<label>管理员密码<input type="password" name="claim-review-password" value={reviewPassword} onChange={(event) => setReviewPassword(event.target.value)} autoComplete="current-password" required /></label><footer><button className="button secondary" type="button" disabled={reviewingId !== null} onClick={() => setReviewMode(null)}>取消</button><button className={`button ${reviewMode.decision === 'approve' ? 'primary' : 'danger'}`} type="submit" disabled={reviewingId !== null || !reviewPassword || (reviewMode.decision === 'approve' ? !reviewReference.trim() : !reviewReason.trim())}>{reviewingId !== null ? '提交中…' : '确认提交'}</button></footer></form></div>}
    {autoTradingOpen && <div className="admin-modal-backdrop" role="presentation"><form ref={modalRef} className="admin-modal admin-danger-modal" tabIndex={-1} onSubmit={submitAutoTrading} aria-modal="true" role="dialog" aria-labelledby="auto-trading-title"><header><h2 id="auto-trading-title">用户实盘服务</h2><button className="icon-button" type="button" aria-label="关闭用户实盘服务窗口" onClick={() => setAutoTradingOpen(false)}>×</button></header><p>这是高风险管理动作，不是券商交易操作。请输入密码后才能提交；服务端还会验证固定确认语。</p><fieldset><legend>目标状态</legend><label><input type="radio" name="auto-trading" checked={!autoTradingEnabled} onChange={() => setAutoTradingEnabled(false)} />暂停用户实盘服务</label><label><input type="radio" name="auto-trading" checked={autoTradingEnabled} onChange={() => setAutoTradingEnabled(true)} />恢复用户实盘服务</label></fieldset><label>管理员密码<input type="password" name="admin-password" value={autoTradingPassword} onChange={(event) => setAutoTradingPassword(event.target.value)} autoComplete="current-password" required /></label><footer><button className="button secondary" type="button" disabled={autoTradingBusy} onClick={() => setAutoTradingOpen(false)}>取消</button><button className="button danger" type="submit" disabled={!autoTradingPassword || autoTradingBusy}>{autoTradingBusy ? '正在提交…' : autoTradingEnabled ? '确认恢复服务' : '确认暂停服务'}</button></footer></form></div>}
  </div>
}

function AdminState({ label }: { label: string }) {
  return <div className="admin-empty" role="status"><span>{label}</span></div>
}
