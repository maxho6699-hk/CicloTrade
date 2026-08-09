import { CheckCircle2, Clock3, Crown, FileCheck2, ShieldCheck, Upload } from 'lucide-react'
import { useEffect, useState } from 'react'
import { BrowserApiError, createMembershipOrder, fetchMembershipPaymentQr, submitMembershipProof } from '../api/client'
import { useWorkspace } from '../api/workspace-context'
import { PageHeader } from '../components/PageHeader'
import { WorkspaceState } from '../components/WorkspaceState'
import { getFormatLocale, localizeText } from '../i18n/runtime'
import { useLocale } from '../i18n/useLocale'

const plans = [
  { name: '免费会员', key: 'free', planValue: '免费版', prices: { month: 0, quarter: 0, year: 0 }, note: '先完成第一次策略研究', features: ['基础策略', '1 条价格预警', '近 1 年回测', '延迟行情'] },
  { name: '标准会员', key: 'standard', planValue: '标准版', prices: { month: 298, quarter: 850, year: 2980 }, note: '完整策略研究与 3 年回测', features: ['全部 8 种策略', '10 条组合预警', '近 3 年回测', '网页正式建议'] },
  { name: '高级会员', key: 'advanced', planValue: '高级版', prices: { month: 698, quarter: 1980, year: 6980 }, note: '正股即时建议与深度研究', features: ['即时正股 Telegram', '期权链研究', '近 10 年回测', '市场玄学'], recommended: true },
  { name: '专业会员', key: 'professional', planValue: '专业版', prices: { month: 2980, quarter: 8500, year: 29800 }, note: '期权、API 与专业报告', features: ['正股与期权 Telegram', '专业 API', '专业报告', '多券商账户'] },
  { name: '定制会员', key: 'custom', planValue: '定制版', prices: { month: 30000, quarter: 30000, year: 30000 }, note: '专属实施与私有化方案', features: ['专业版全部权益', '专属实施支持', '不限券商账户', '私有部署规划'] },
] as const

const cycleLabel = { month: '月付', quarter: '季付', year: '年付' } as const
const paymentMethodLabels = {
  fps: 'FPS',
  alipay: '支付宝',
  wechat: '微信支付',
  paypal: 'PayPal（历史）',
  paddle: 'Paddle（历史）',
} as const
type PaymentMethod = 'fps' | 'alipay' | 'wechat'
type ProofOrder = { orderNo: string; method: PaymentMethod; instructions: string; hasQr: boolean }
type OrderNotice =
  | { kind: 'created'; orderNo: string; currency: string; amount: string }
  | { kind: 'refresh-failed'; orderNo: string }
  | { kind: 'proof-submitted'; orderNo: string }
  | { kind: 'plain'; text: string }
const manualPaymentMethods = new Set<PaymentMethod>(['fps', 'alipay', 'wechat'])

function orderNoticeText(notice: OrderNotice | null, locale: 'zh-Hant' | 'zh-Hans') {
  if (!notice) return ''
  if (notice.kind === 'plain') return locale === 'zh-Hant' ? localizeText(notice.text) : notice.text
  if (notice.kind === 'created') return locale === 'zh-Hant'
    ? `訂單 ${notice.orderNo} 已建立 · ${notice.currency} ${notice.amount} · 請在本頁上傳付款截圖，財務核對到帳後開通`
    : `订单 ${notice.orderNo} 已建立 · ${notice.currency} ${notice.amount} · 请在本页上传付款截图，财务核对到账后开通`
  if (notice.kind === 'refresh-failed') return locale === 'zh-Hant'
    ? `訂單 ${notice.orderNo} 已建立；列表重新整理失敗，請稍後重試。`
    : `订单 ${notice.orderNo} 已建立；列表刷新失败，请稍后重试。`
  return locale === 'zh-Hant'
    ? `訂單 ${notice.orderNo} 的付款憑證已提交，等待財務人工核對。`
    : `订单 ${notice.orderNo} 的付款凭证已提交，等待财务人工核对。`
}

function PaymentProofPanel({ order, onSubmitted }: { order: ProofOrder; onSubmitted: () => void }) {
  const { locale } = useLocale()
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState('')
  const [claimId, setClaimId] = useState<number | null>(null)
  const [submitted, setSubmitted] = useState(false)
  const [qrUrl, setQrUrl] = useState('')
  const [qrError, setQrError] = useState('')

  useEffect(() => {
    let active = true
    let objectUrl = ''
    if (!order.hasQr) {
      setQrUrl('')
      setQrError('')
      return () => undefined
    }
    void fetchMembershipPaymentQr(order.orderNo).then((blob) => {
      if (!active) return
      objectUrl = URL.createObjectURL(blob)
      setQrUrl(objectUrl)
      setQrError('')
    }).catch((caught) => {
      if (active) setQrError(caught instanceof BrowserApiError ? caught.message : '收款二维码暂时不可用。')
    })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [order.hasQr, order.orderNo])

  async function uploadProof() {
    if (!file || submitted) return
    try {
      const claim = await submitMembershipProof(order.orderNo, file)
      setSubmitted(true)
      setClaimId(claim.claim_id)
      setError('')
      onSubmitted()
    } catch (caught) {
      setError(caught instanceof BrowserApiError ? caught.message : '付款凭证提交失败')
    }
  }

  return (
    <section className="data-panel payment-proof-panel">
      <header className="panel-heading">
        <div><span>PAYMENT PROOF</span><h2>提交付款凭证</h2></div>
        <FileCheck2 size={20} />
      </header>
      <div className="payment-proof-body">
        <div className="payment-proof-order"><strong>{order.orderNo}</strong><span>{paymentMethodLabels[order.method]} · 全部人工对账</span></div>
        <div className="payment-instructions">
          <strong>收款资料</strong>
          {order.instructions && <p data-no-localize>{order.instructions}</p>}
          {order.hasQr && <div className="payment-qr-frame">{qrUrl ? <img src={qrUrl} alt={`${paymentMethodLabels[order.method]} 收款二维码`} /> : <span>{qrError || '正在读取收款二维码…'}</span>}</div>}
          {!order.instructions && !order.hasQr && <p>收款资料尚未配置，请联系客服。</p>}
          <small>请使用与订单金额一致的付款凭证；凭证只用于财务人工核对，不会自动开通会员。</small>
        </div>
        <label className="proof-upload-field">
          <span><Upload size={16} /> 选择付款截图</span>
          <input type="file" accept="image/jpeg,image/png,image/webp" disabled={submitted} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setError('') }} />
          <small>{file?.name ?? '支持 JPG、PNG、WebP，最大 4 MB'}</small>
        </label>
        <button className="button primary wide" type="button" disabled={!file || submitted} onClick={uploadProof}>
          {submitted ? '已提交，等待核对' : '上传并提交人工审核'}
        </button>
        <p className="form-status" role="status">{claimId !== null ? (locale === 'zh-Hant' ? `付款憑證已提交，申報 #${claimId} 等待財務人工核對。` : `付款凭证已提交，申报 #${claimId} 等待财务人工核对。`) : locale === 'zh-Hant' ? localizeText(error) : error}</p>
      </div>
    </section>
  )
}

export function MembershipPage() {
  const { locale } = useLocale()
  const workspace = useWorkspace()
  const [cycle, setCycle] = useState<'month' | 'quarter' | 'year'>('year')
  const [selectedPlan, setSelectedPlan] = useState('')
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>('fps')
  const [termsAccepted, setTermsAccepted] = useState(false)
  const [orderStatus, setOrderStatus] = useState<OrderNotice | null>(null)
  const [showOrders, setShowOrders] = useState(false)
  const [proofOrder, setProofOrder] = useState<ProofOrder | null>(null)
  const paymentAvailability = workspace.data?.membership.payment_methods

  function resetOrderStatus() {
    setOrderStatus(null)
  }

  return (
    <div className="page operations-page membership-page">
      <PageHeader kicker="MEMBERSHIP / ONE-TIME" title="会员与账单" description="一次付款获得固定有效期，到期不会自动扣款，也不会绑定自动续费。" />
      <WorkspaceState />
      <section className="current-plan-band data-panel"><span className="membership-emblem"><Crown size={25} /></span><div><span>CURRENT PLAN</span><h2>{workspace.user?.plan_display_name ?? '演示模式'}</h2><p><Clock3 size={15} /> {workspace.user?.subscription_expire ? `有效期至 ${workspace.user.subscription_expire.slice(0, 10)}` : workspace.user ? '长期有效或未设置到期日' : '登录后查看真实有效期'}</p></div><span className={`status-chip ${workspace.user ? 'official' : 'research'}`}><ShieldCheck size={14} /> {workspace.user ? '权益正常' : '演示数据'}</span><button className="button secondary" type="button" aria-expanded={showOrders} onClick={() => setShowOrders(!showOrders)}>{showOrders ? '收起订单' : '查看订单'}</button></section>
      {showOrders && <section className="data-panel membership-orders"><header className="panel-heading"><div><span>ORDER HISTORY</span><h2>一次性购买记录</h2></div><span className="status-chip official">绝不自动续费</span></header>{workspace.data?.membership.orders.length ? <div className="responsive-table"><table><thead><tr><th>订单</th><th>方案</th><th>周期</th><th>金额</th><th>方式</th><th>凭证</th><th>状态</th><th>建立时间</th><th>操作</th></tr></thead><tbody>{workspace.data.membership.orders.map((order) => { const method = manualPaymentMethods.has(order.pay_method as PaymentMethod) ? order.pay_method as PaymentMethod : null; const canSubmit = order.status === 'pending' && order.proof_status !== 'submitted' && method; return <tr key={order.order_no}><td><strong>{order.order_no}</strong></td><td>{order.plan_type}</td><td>{order.billing_cycle}</td><td>{order.currency} {Number(order.amount).toLocaleString(getFormatLocale())}</td><td>{paymentMethodLabels[order.pay_method]}</td><td>{order.proof_status === 'submitted' ? '审核中' : order.proof_status === 'approved' ? '已核对' : order.proof_status === 'rejected' ? '需重新提交' : '未提交'}</td><td><span className={`model-state ${order.status === 'PAID' ? 'active' : 'shadow'}`}>{order.status}</span></td><td>{new Date(order.created_at).toLocaleString(getFormatLocale(), { hour12: false })}</td><td>{canSubmit && method ? <button className="button tertiary" type="button" onClick={() => setProofOrder({ orderNo: order.order_no, method, instructions: order.payment_instructions ?? '', hasQr: order.payment_qr_available === true })}>提交凭证</button> : order.proof_status === 'submitted' ? <span className="status-chip research">等待审核</span> : <span className="table-muted">--</span>}</td></tr> })}</tbody></table></div> : <div className="inline-empty">当前账户还没有会员订单。</div>}</section>}
      <div className="billing-cycle-bar"><span>购买时长</span><div className="segmented-control">{Object.entries(cycleLabel).map(([key, label]) => <button className={cycle === key ? 'active' : ''} type="button" onClick={() => { setCycle(key as typeof cycle); resetOrderStatus() }} key={key}>{label}</button>)}</div><small>所有方案均为一次性付款</small></div>
      <div className="membership-grid">{plans.map((plan) => <article className={`membership-card ${plan.key === 'advanced' ? 'recommended' : ''}`} key={plan.key}>{plan.key === 'advanced' && <span className="recommended-label">推荐方案</span>}<header><span>{plan.name}</span><strong>{plan.key === 'free' ? '免费' : plan.key === 'custom' ? 'HKD 30,000 起' : `HKD ${plan.prices[cycle].toLocaleString(getFormatLocale())}`}</strong><small>{plan.key === 'custom' ? '按项目报价' : cycleLabel[cycle]}</small><p>{plan.note}</p></header><ul>{plan.features.map((feature) => <li key={feature}><CheckCircle2 size={16} /> {feature}</li>)}</ul><button className={selectedPlan === plan.key ? 'button secondary wide' : 'button primary wide'} type="button" disabled={plan.key === 'free'} onClick={() => { setSelectedPlan(plan.key); resetOrderStatus() }}>{plan.key === 'free' ? '当前免费权益' : selectedPlan === plan.key ? '已选择' : plan.key === 'custom' ? '选择定制方案' : '选择并查看付款方式'}</button><footer>不会自动续费 · 到期需主动购买</footer></article>)}</div>
      {selectedPlan && <section className="checkout-panel data-panel"><header className="panel-heading"><div><span>ORDER CONFIRMATION</span><h2>确认一次性会员订单</h2></div><ShieldCheck size={20} /></header><div className="checkout-body"><dl><div><dt>方案</dt><dd>{plans.find((plan) => plan.key === selectedPlan)?.name}</dd></div><div><dt>时长</dt><dd>{selectedPlan === 'custom' ? '项目制' : cycleLabel[cycle]}</dd></div><div><dt>金额</dt><dd>HKD {plans.find((plan) => plan.key === selectedPlan)?.prices[cycle].toLocaleString(getFormatLocale())}</dd></div><div><dt>续费方式</dt><dd>到期停止，不自动扣款</dd></div></dl><div><span>付款方式 · 全部人工对账</span><div className="segmented-control">{([['fps', 'FPS'], ['alipay', '支付宝'], ['wechat', '微信支付']] as const).map(([key, label]) => <button className={paymentMethod === key ? 'active' : ''} type="button" key={key} disabled={!paymentAvailability?.[key]?.available} title={paymentAvailability?.[key]?.available ? label : `${label}尚未配置`} onClick={() => { setPaymentMethod(key); resetOrderStatus() }}>{label}</button>)}</div></div><label className="terms-check"><input type="checkbox" checked={termsAccepted} onChange={(event) => setTermsAccepted(event.target.checked)} /><span>我已阅读并同意用户协议、风险披露与不退款政策，确认这是一次性购买。</span></label><button className="button primary wide" type="button" disabled={!termsAccepted || !paymentAvailability?.[paymentMethod]?.available} onClick={async () => {
        if (workspace.mode !== 'authenticated') { setOrderStatus({ kind: 'plain', text: '请先登录后建立会员订单' }); return }
        const plan = plans.find((item) => item.key === selectedPlan)
        if (!plan) return
        try {
          const order = await createMembershipOrder({ plan: plan.planValue, cycle: selectedPlan === 'custom' ? 'project' : { month: 'monthly', quarter: 'quarterly', year: 'yearly' }[cycle], method: paymentMethod, terms_accepted: termsAccepted }, crypto.randomUUID())
          setProofOrder({ orderNo: order.order_no, method: paymentMethod, instructions: order.payment_instructions, hasQr: order.payment_qr_available })
          setOrderStatus({ kind: 'created', orderNo: order.order_no, currency: order.currency, amount: order.amount.toLocaleString(getFormatLocale()) })
          setShowOrders(true)
          try { await workspace.refresh() } catch { setOrderStatus({ kind: 'refresh-failed', orderNo: order.order_no }) }
        } catch (caught) { setOrderStatus({ kind: 'plain', text: caught instanceof BrowserApiError ? caught.message : '会员订单建立失败' }) }
      }}>建立待付款订单</button><p className="form-status" role="status">{orderNoticeText(orderStatus, locale)}</p></div></section>}
      {proofOrder && <PaymentProofPanel order={proofOrder} onSubmitted={() => { setOrderStatus({ kind: 'proof-submitted', orderNo: proofOrder.orderNo }); void workspace.refresh().catch(() => undefined) }} />}
    </div>
  )
}
