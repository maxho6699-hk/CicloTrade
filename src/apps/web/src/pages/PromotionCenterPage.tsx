import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowUpRight, BadgeDollarSign, Check, CircleAlert, Copy, FileClock, Gift, Landmark, LoaderCircle, LockKeyhole, QrCode, RefreshCw, RotateCcw, ShieldCheck, Wallet, WalletCards } from 'lucide-react'
import { referralApi, type ReferralPortal, type ReferralTimelineType, type ReferralWithdrawalStatus } from '../api/promotion'
import { PageHeader } from '../components/PageHeader'
import { useLocale } from '../i18n/useLocale'
import { localizeText } from '../i18n/runtime'
import { withdrawalIdempotencyKey } from '../domain/referralWithdrawal'
import { BrowserApiError } from '../api/client'

type ViewState = 'ready' | 'loading' | 'error' | 'forbidden' | 'rejected' | 'disabled'
type CopyTarget = 'link' | 'code' | `referral:${string}` | null
const WITHDRAWAL_IDEMPOTENCY_STORAGE_KEY = 'ciclotrade.referralWithdrawalPending'

const formatMinor = (value: number, locale: string) => new Intl.NumberFormat(locale, { style: 'currency', currency: 'HKD' }).format(value / 100)
const formatBps = (value: number) => `${(value / 100).toFixed(2)}%`
const hkt = (value: string | null, locale: string) => value ? new Intl.DateTimeFormat(locale, { month: '2-digit', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Hong_Kong' }).format(new Date(value)) : '—'
const funnelRows = (funnel: ReferralPortal['funnel']): Array<[string, number]> => [['访问', funnel.visits_30d], ['注册', funnel.registrations_30d], ['首个有效付费客户', funnel.settled_referrals_30d]]
const withdrawalLabel = (value: ReferralWithdrawalStatus) => localizeText(({ submitted: '待审核', approved: '已批准', rejected: '已拒绝', paid: '已付款', system_cancelled: '已取消' })[value])
const eventLabel = (value: ReferralTimelineType) => localizeText(({ registration: '完成注册', commission_pending: '佣金进入冻结期', commission_withdrawable: '佣金可提现', clawback: '佣金回退', bonus_pending: '人数奖金已入账', bonus_clawback: '人数奖金被追回', withdrawal_submitted: '已提交提现', withdrawal_approved: '提现已批准', withdrawal_rejected: '提现被拒绝', withdrawal_paid: '已人工付款', withdrawal_cancelled: '提现已取消' })[value])
const commissionLabel = (value: ReferralPortal['commissions'][number]['commission_type']) => localizeText(({ initial_purchase: '首次购买', renewal: '续费', upgrade: '升级' })[value])
const commissionStatusLabel = (value: ReferralPortal['commissions'][number]['status']) => localizeText(({ pending: '冻结中', withdrawable: '可提现', partially_clawed_back: '部分回退', clawed_back: '已回退' })[value])
const timelineTone = (value: ReferralTimelineType) => value === 'withdrawal_paid' || value === 'withdrawal_approved' || value === 'commission_withdrawable' ? 'success' : value === 'withdrawal_rejected' || value === 'withdrawal_cancelled' || value === 'clawback' || value === 'bonus_clawback' ? 'danger' : 'pending'
const bonusStatusLabel = (value: ReferralPortal['program']['bonus_progress']['status']) => localizeText(({ not_qualified: '尚未达到奖金门槛', pending: '奖金冻结中', earned: '奖金已获得', partially_clawed_back: '奖金部分追回', clawed_back: '奖金已追回' })[value])

function storedWithdrawalRequest(): { amountMinor: number; key: string } | null {
  try {
    const value: unknown = JSON.parse(window.sessionStorage.getItem(WITHDRAWAL_IDEMPOTENCY_STORAGE_KEY) ?? 'null')
    if (!value || typeof value !== 'object' || !('amountMinor' in value) || !('key' in value)) return null
    const amountMinor = Number(value.amountMinor)
    const key = String(value.key)
    return Number.isSafeInteger(amountMinor) && amountMinor > 0 && /^[A-Za-z0-9_-]{8,128}$/.test(key) ? { amountMinor, key } : null
  } catch { return null }
}
function StatePanel({ state, retry }: { state: Exclude<ViewState, 'ready'>; retry: () => void }) {
  const content = state === 'loading'
    ? [LoaderCircle, '正在读取推广资料', '只展示服务端返回的归因、结算和审计字段。'] as const
    : state === 'disabled'
      ? [Landmark, '推广计划暂未开放', '推广入口会保留；计划开放后可在这里查看真实邀请、佣金和提现资料。'] as const
      : state === 'forbidden'
        ? [LockKeyhole, '当前账户无推广权限', '不会显示其他用户的推广或结算资料。'] as const
        : state === 'rejected'
          ? [CircleAlert, '请求被服务端拒绝', '这是确定的 4xx 业务结果；请按页面提示修正条件后再试。'] as const
        : [CircleAlert, '推广资料暂时无法读取', '保留空白，不以旧资料、日期或金额推算替代。'] as const
  const Icon = content[0]
  return <section className={`promotion-state ${state}`} role={state === 'error' || state === 'forbidden' ? 'alert' : 'status'}><Icon /><strong>{localizeText(content[1])}</strong><span>{localizeText(content[2])}</span>{state === 'error' && <button className="button secondary" type="button" onClick={retry}><RefreshCw size={16} />{localizeText('重新读取')}</button>}</section>
}
export function PromotionCenterPage() {
  const { formatLocale } = useLocale()
  const [state, setState] = useState<ViewState>('loading')
  const [portal, setPortal] = useState<ReferralPortal | null>(null)
  const [copied, setCopied] = useState<CopyTarget>(null)
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [submitBusy, setSubmitBusy] = useState(false)
  const pendingWithdrawal = useRef<{ amountMinor: number; key: string } | null>(storedWithdrawalRequest())

  const load = useCallback(() => {
    const controller = new AbortController()
    setState('loading')
    void referralApi.loadPortal(controller.signal).then((result) => {
      setPortal(result)
      setState(result.program.enabled ? 'ready' : 'disabled')
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return
      const status = typeof error === 'object' && error !== null && 'status' in error ? Number(error.status) : 0
      setPortal(null)
      setState(status === 403 ? 'forbidden' : status >= 400 && status < 500 ? 'rejected' : 'error')
    })
    return () => controller.abort()
  }, [])

  useEffect(() => load(), [load])

  const copy = async (kind: Exclude<CopyTarget, null>, value: string) => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(kind)
      window.setTimeout(() => setCopied(null), 1800)
    } catch {
      setNote(localizeText('浏览器未允许复制；请手动选择内容。'))
    }
  }

  const amountMinor = useMemo(() => Math.round(Number(amount) * 100), [amount])
  const changeAmount = (value: string) => {
    setAmount(value)
    setNote('')
  }
  const withdraw = async () => {
    if (!portal) return
    if (portal.program.withdrawal_paused) {
      setNote(localizeText('提现计划目前暂停，请等待后台恢复。'))
      return
    }
    if (!Number.isSafeInteger(amountMinor) || amountMinor < portal.program.minimum_withdrawal_minor) {
      setNote(`${localizeText('申请金额不得低于')} ${formatMinor(portal.program.minimum_withdrawal_minor, formatLocale)}。`)
      return
    }
    if (amountMinor > portal.balances.withdrawable_minor) {
      setNote(localizeText('申请金额超过服务端返回的可提现余额。'))
      return
    }
    if (amountMinor > portal.program.maximum_withdrawal_minor) {
      setNote(`${localizeText('单笔申请不得超过')} ${formatMinor(portal.program.maximum_withdrawal_minor, formatLocale)}。`)
      return
    }
    const request = withdrawalIdempotencyKey(amountMinor, pendingWithdrawal.current)
    pendingWithdrawal.current = request
    try { window.sessionStorage.setItem(WITHDRAWAL_IDEMPOTENCY_STORAGE_KEY, JSON.stringify(request)) } catch { /* session storage may be disabled */ }
    setSubmitBusy(true)
    setNote('')
    try {
      const result = await referralApi.requestWithdrawal({ amount_minor: amountMinor, currency: portal.program.currency }, request.key)
      pendingWithdrawal.current = null
      try { window.sessionStorage.removeItem(WITHDRAWAL_IDEMPOTENCY_STORAGE_KEY) } catch { /* session storage may be disabled */ }
      setAmount('')
      setNote(`${localizeText('申请')} ${result.withdrawal.withdrawal_id} ${localizeText('已提交；最终状态以服务端审核为准。')}`)
      void referralApi.loadPortal().then((next) => { setPortal(next); setState(next.program.enabled ? 'ready' : 'disabled') }).catch(() => undefined)
    } catch (error) {
      setNote(error instanceof BrowserApiError && error.status >= 400 && error.status < 500
        ? error.message
        : localizeText('提现申请状态未确认；本次申请编号已保留，刷新后再次提交相同金额仍会安全复用。'))
    } finally {
      setSubmitBusy(false)
    }
  }

  const balanceCards = portal ? [
    ['earned_total_minor', '累计佣金', FileClock, `${localizeText('回退累计')} ${formatMinor(portal.balances.clawed_back_total_minor, formatLocale)}`],
    ['withdrawable_minor', '可提现', Wallet, localizeText('可提交提现申请')],
    ['pending_minor', '冻结佣金', ShieldCheck, `${portal.program.hold_days} ${localizeText('日冻结期')}`],
    ['reserved_minor', '提现审核中', Landmark, localizeText('已为申请预留')],
    ['paid_minor', '已付款', Check, localizeText('管理员已确认付款')],
    ['debt_minor', '负余额', CircleAlert, localizeText('后续佣金将优先抵扣')],
  ] as const : []
  const bonusNextTier = portal?.program.bonus_tiers.find((tier) => tier.qualified_count > portal.program.bonus_progress.qualified_count) ?? null
  const bonusProgressPercent = portal && bonusNextTier
    ? Math.min(100, Math.round(portal.program.bonus_progress.qualified_count / bonusNextTier.qualified_count * 100))
    : portal?.program.bonus_progress.qualified_count ? 100 : 0

  return <div className="page promotion-page"><div className="promotion-title-row"><PageHeader kicker="PARTNER / SETTLEMENT" title="推广中心" description={portal ? `有效推荐新客首个已支付会员订单享 ${(10000 - portal.program.referral_discount_bps) / 100} 折；推荐人按最终实付获得 ${portal.program.referrer_commission_bps / 100}% 佣金，续费与升级为 0%。` : "查看推广转化、真实佣金、奖金、提现状态与审计记录。"} /><div className="promotion-preview-badge"><ShieldCheck size={15} /><span><strong>{localizeText('服务端核对字段')}</strong><small>{localizeText('金额按 HKD minor units，时间固定为香港时区；不保存访客、收款账户或付款参考。')}</small></span></div></div><p className="promotion-copy-receipt" role="status" aria-live="polite" aria-atomic="true">{copied && localizeText('已复制到剪贴板。')}</p>
    {state !== 'ready' || !portal ? <StatePanel state={state === 'ready' ? 'loading' : state} retry={load} /> : <>
      <section className="promotion-balance-grid" aria-label={localizeText('推广资产总览')}>{balanceCards.map(([key, label, Icon, detail]) => <article className={`promotion-balance-card ${key}`} key={key}><header><span>{localizeText(label)}</span><Icon size={17} /></header><strong>{formatMinor(portal.balances[key], formatLocale)}</strong><small>{detail}</small></article>)}</section>
      <section className="promotion-bonus-panel" aria-labelledby="promotion-bonus-title"><header><div><span>BONUS / MONTHLY TIER</span><h2 id="promotion-bonus-title">{localizeText('推广人数奖金')}</h2><p>{localizeText('按当月有效首单人数累计；跨档只补差额，退款会按账本追回。')}</p></div><Gift size={20} /></header>{portal.program.bonus_enabled ? <div className="promotion-bonus-body"><section className="promotion-bonus-progress"><div><span>{localizeText('本期有效人数')}</span><strong>{portal.program.bonus_progress.qualified_count.toLocaleString(formatLocale)}</strong><small>{portal.program.bonus_progress.period_key}</small></div><div className="promotion-bonus-track" aria-label={localizeText('奖金进度')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={bonusProgressPercent} role="progressbar"><i style={{ width: `${bonusProgressPercent}%` }} /></div><p>{bonusNextTier ? `${localizeText('距离下一档还差')} ${Math.max(0, bonusNextTier.qualified_count - portal.program.bonus_progress.qualified_count)} ${localizeText('人')} · ${localizeText('累计奖金')} ${formatMinor(bonusNextTier.cumulative_amount_minor, formatLocale)}` : localizeText('已达到当前最高奖金档。')}</p></section><ol className="promotion-bonus-tiers">{portal.program.bonus_tiers.map((tier) => { const reached = portal.program.bonus_progress.qualified_count >= tier.qualified_count; return <li className={reached ? 'reached' : ''} key={tier.qualified_count}><span><Check size={14} />{tier.qualified_count} {localizeText('人')}</span><strong>{formatMinor(tier.cumulative_amount_minor, formatLocale)}</strong></li> })}</ol><dl className="promotion-bonus-ledger"><div><dt><BadgeDollarSign size={14} />{localizeText('累计获得')}</dt><dd>{formatMinor(portal.program.bonus_progress.earned_amount_minor, formatLocale)}</dd></div><div><dt><RotateCcw size={14} />{localizeText('退款追回')}</dt><dd>{formatMinor(portal.program.bonus_progress.clawed_back_minor, formatLocale)}</dd></div><div><dt>{localizeText('净奖金')}</dt><dd>{formatMinor(portal.program.bonus_progress.net_amount_minor, formatLocale)}</dd></div><div><dt>{localizeText('当前状态')}</dt><dd><span className={`promotion-status ${portal.program.bonus_progress.status}`}>{bonusStatusLabel(portal.program.bonus_progress.status)}</span></dd></div></dl></div> : <div className="promotion-bonus-disabled"><LockKeyhole size={18} /><span><strong>{localizeText('人数奖金当前暂停')}</strong><small>{localizeText('推荐首单佣金与历史账本不受影响；恢复后以新的后台策略版本为准。')}</small></span></div>}</section>
      <section className="promotion-insight-grid"><article className="promotion-conversion-panel"><header><span>CONVERSION / SERVER BASIS</span><strong>{localizeText('转化质量')}</strong><small>{localizeText('分母与比率由服务端窗口统计返回。')}</small></header><div className="promotion-conversion-rates"><div><span>{localizeText('注册转化')}</span><strong>{formatBps(portal.funnel.registration_rate_bps)}</strong><small>{localizeText('30日访问 → 注册')}</small></div><div><span>{localizeText('结算转化')}</span><strong>{formatBps(portal.funnel.settlement_rate_bps)}</strong><small>{localizeText('30日注册 → 有效结算')}</small></div></div></article><article className="promotion-funnel-panel"><header><span>ATTRIBUTION / FUNNEL</span><strong>{localizeText('访问 → 注册 → 结算')}</strong><small>{localizeText('不由前端反推归因或转化率。')}</small></header><ol>{funnelRows(portal.funnel).map(([label, count], index) => <li key={label}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{localizeText(label)}</strong><small>{localizeText('30日服务端统计')}</small></div><b>{count.toLocaleString(formatLocale)}</b><i style={{ width: `${count / Math.max(portal.funnel.visits_30d, 1) * 100}%` }} /></li>)}</ol></article></section>
      <section className="promotion-analytics-grid"><section className="promotion-trend-panel"><header><div><span>WINDOW / HKT SETTLEMENT</span><strong>{localizeText('7 / 30 / 90 趋势矩阵')}</strong></div><small>{localizeText('没有日序列时，直接展示服务端窗口 KPI，不构造时间线。')}</small></header><div className="promotion-kpi-matrix">{portal.trends.windows.map((item) => <div key={item.days}><strong>{item.days}{localizeText('日')}</strong><dl><div><dt>{localizeText('访问')}</dt><dd>{item.visits.toLocaleString(formatLocale)}</dd></div><div><dt>{localizeText('注册')}</dt><dd>{item.registrations.toLocaleString(formatLocale)}</dd></div><div><dt>{localizeText('结算订单')}</dt><dd>{item.settled_orders.toLocaleString(formatLocale)}</dd></div><div><dt>{localizeText('已得佣金')}</dt><dd>{formatMinor(item.earned_amount_minor, formatLocale)}</dd></div></dl></div>)}</div><footer><span>{localizeText('按香港结算时间窗口')}</span><span>{localizeText('金额/计数来自服务端')}</span></footer></section></section>
      <section className="promotion-asset-grid"><article className="promotion-assets-panel"><header><div><span>REFERRAL ASSETS</span><strong>{localizeText('专属链接与邀请码')}</strong></div><ShieldCheck size={20} /></header><div className="promotion-copy-row"><span><small>{localizeText('专属链接')}</small><b>{portal.invite.invite_link}</b></span><button type="button" onClick={() => void copy('link', portal.invite.invite_link)} aria-label={localizeText('复制专属链接')}>{copied === 'link' ? <Check size={17} /> : <Copy size={17} />}<em>{localizeText(copied === 'link' ? '已复制' : '复制')}</em></button></div><div className="promotion-code-row"><span><small>{localizeText('邀请码')}</small><b>{portal.invite.invite_code}</b></span><button type="button" onClick={() => void copy('code', portal.invite.invite_code)} aria-label={localizeText('复制邀请码')}>{copied === 'code' ? <Check size={17} /> : <Copy size={17} />}</button></div></article><article className="promotion-qr-panel"><header><QrCode size={17} /><span><strong>{localizeText('邀请链接')}</strong><small>{localizeText('在接入可扫描 QR 编码器前仅显示可复制的链接，不生成伪二维码。')}</small></span></header><a className="promotion-link-proof" href={portal.invite.invite_link} target="_blank" rel="noreferrer">{localizeText('打开邀请链接')} <ArrowUpRight size={15} /></a></article></section>
      <section className="promotion-records-grid"><article className="promotion-table-panel"><header><div><span>REFERRALS</span><strong>{localizeText('邀请用户')}</strong></div></header><div className="promotion-record-list">{portal.referrals.length ? portal.referrals.map((referral) => <article className="promotion-record" key={referral.referral_id}><div><button className="promotion-id-copy" type="button" aria-label={localizeText('复制邀请记录 ID')} onClick={() => void copy(`referral:${referral.referral_id}`, referral.referral_id)}><span>{referral.referral_id}</span>{copied === `referral:${referral.referral_id}` ? <Check size={13} /> : <Copy size={13} />}</button><small>{referral.user_masked}</small></div><dl><div><dt>{localizeText('加入时间')}</dt><dd>{hkt(referral.joined_at, formatLocale)}</dd></div><div><dt>{localizeText('有效首单')}</dt><dd>{referral.settled_orders}</dd></div><div><dt>{localizeText('最近结算')}</dt><dd>{hkt(referral.last_settled_at, formatLocale)}</dd></div></dl></article>) : <p className="promotion-empty-cell">{localizeText('暂无可核对的邀请用户记录。')}</p>}</div><header className="promotion-subtable-heading"><div><span>COMMISSIONS / FIRST PAID ORDER</span><strong>{localizeText('首单佣金流水')}</strong></div></header><div className="promotion-record-list">{portal.commissions.length ? portal.commissions.map((commission) => <article className="promotion-record promotion-commission-record" key={commission.commission_id}><div><strong>{commission.commission_id}</strong><small>{localizeText('订单 ID')} · {commission.recharge_id}</small><span className={`promotion-status ${commission.status}`}>{commissionStatusLabel(commission.status)}</span></div><dl><div><dt>{localizeText('类型')}</dt><dd>{commissionLabel(commission.commission_type)}</dd></div><div><dt>{localizeText('订单金额')}</dt><dd>{formatMinor(commission.gross_amount_minor, formatLocale)}</dd></div><div><dt>{localizeText('佣金比例')}</dt><dd>{formatBps(commission.rate_bps)}</dd></div><div><dt>{localizeText('原始佣金')}</dt><dd>{formatMinor(commission.earned_amount_minor, formatLocale)}</dd></div><div><dt>{localizeText('佣金回退')}</dt><dd>{formatMinor(commission.clawed_back_minor, formatLocale)}</dd></div><div><dt>{localizeText('净佣金')}</dt><dd>{formatMinor(commission.net_amount_minor, formatLocale)}</dd></div><div><dt>{localizeText('结算时间')}</dt><dd>{hkt(commission.settled_at, formatLocale)}</dd></div><div><dt>{localizeText('可提现时间')}</dt><dd>{hkt(commission.available_at, formatLocale)}</dd></div></dl></article>) : <p className="promotion-empty-cell">{localizeText('暂无可核对的首单佣金流水。')}</p>}</div></article><article className="promotion-withdrawal-panel"><header><span>WITHDRAWAL / IDEMPOTENT</span><strong>{localizeText('提现申请')}</strong><small>{localizeText('不收集银行账号；管理员人工付款后更新状态。')}</small></header><label><span>{localizeText('申请金额 · HKD（最低')} {formatMinor(portal.program.minimum_withdrawal_minor, formatLocale)}）</span><input aria-label={localizeText('申请金额')} autoComplete="off" inputMode="decimal" min={portal.program.minimum_withdrawal_minor / 100} name="withdrawal_amount" step="0.01" type="number" value={amount} onChange={(event) => changeAmount(event.target.value)} /></label><button className="button primary wide" disabled={submitBusy || portal.program.withdrawal_paused || portal.balances.withdrawable_minor < portal.program.minimum_withdrawal_minor} type="button" onClick={() => void withdraw()}><ArrowUpRight size={16} />{submitBusy ? localizeText('正在提交…') : localizeText('提交提现申请')}</button>{note && <p className="promotion-form-note" role="status">{note}</p>}<section className="promotion-withdrawal-history"><h3>{localizeText('提现记录')}</h3>{portal.withdrawals.length ? portal.withdrawals.map((withdrawal) => <article key={withdrawal.withdrawal_id}><div><strong>{withdrawal.withdrawal_id}</strong><span className={`promotion-status withdrawal-${withdrawal.status}`}>{withdrawalLabel(withdrawal.status)}</span></div><b>{formatMinor(withdrawal.amount_minor, formatLocale)}</b><small>{hkt(withdrawal.submitted_at, formatLocale)}</small>{withdrawal.rejection_reason && <p>{withdrawal.rejection_reason}</p>}</article>) : <p className="promotion-empty-cell">{localizeText('暂无提现记录。')}</p>}</section><div className="promotion-timeline"><h3>{localizeText('审计时间线')}</h3>{portal.timeline.length ? portal.timeline.map((event) => <div key={event.event_id}><i className={timelineTone(event.event_type)} /><span><strong>{eventLabel(event.event_type)}</strong><small>{hkt(event.occurred_at, formatLocale)} · <b>{event.public_reference}</b></small></span>{timelineTone(event.event_type) === 'success' && <Check size={15} />}</div>) : <p className="promotion-empty-cell">{localizeText('暂无审计记录。')}</p>}</div></article></section>
      <footer className="promotion-policy-strip"><WalletCards size={16} /><span>{localizeText('佣金冻结、退款追回和提现状态均以平台资金账本为准。')}</span></footer>
    </>}</div>
}
