import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpFromLine,
  BadgeDollarSign,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  LoaderCircle,
  ReceiptText,
  ShieldCheck,
  WalletCards,
  XCircle,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  BrowserApiError,
  cancelPersonalPaperStockOrder,
  createPersonalPaperSeason,
  fetchPersonalPaperAccount,
  issuePersonalPaperQuote,
  issuePersonalPaperRiskProof,
  submitPersonalPaperStockOrder,
  type PersonalPaperAccount,
  type PersonalPaperOrder,
  type PersonalPaperOrderRequest,
  type PersonalPaperOrderResult,
  type PersonalPaperRiskProof,
  type PersonalPaperRiskProofRequest,
  type PersonalPaperSide,
} from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { CicloCore, type CicloCoreState } from '../components/paper/CicloCore'
import {
  DataHealth,
  DecisionSummary,
  OrderPreview,
  PaperDraftCard,
  PaperRefreshButton,
  RiskCheckList,
  type PaperDraftValues,
} from '../components/paper/PaperPrimitives'
import { useLocale } from '../i18n/useLocale'

const SEASON_STORAGE_KEY = 'ciclotrade.personalPaper.activeSeason.v1'
type BusyState = '' | 'quote' | 'risk' | 'submit' | 'refresh' | 'cancel'
type SubmitState = 'idle' | 'unknown' | 'success'

const COPY = {
  'zh-Hans': {
    kicker: 'PERSONAL PAPER · USD 10,000', title: '个人模拟交易',
    description: '独立于 CicloTrade 官方模拟和券商实盘。每笔股票订单都由你确认，绝不会自动提交到真实账户。',
    personal: '个人模拟', official: '官方模拟', live: '券商实盘', active: '当前页面', isolated: '完全隔离', disconnected: '未连接',
    startTitle: '开启你的 USD 10,000 赛季', startBody: '第一赛季本金固定且历史不可覆盖。这里只使用个人模拟账本，不会连接券商。', start: '创建个人模拟账户',
    loading: '正在读取个人模拟账户', retry: '重新读取', unavailable: '个人模拟服务暂时不可用',
    cash: '现金', buyingPower: '可用购买力', equity: '总权益', pnl: '未实现盈亏', reserved: '已冻结资金', marketValue: '持仓市值',
    tasks: '账户待办', accountReady: 'USD 10,000 账户已就绪', draftReady: '股票订单草稿有效', quoteReady: '报价证明已取得', riskReady: '风险证明已取得', awaiting: '待完成',
    confirm: '确认提交个人模拟订单', submitting: '正在提交…', confirmBlocked: '风险证明拒绝提交', confirmExpired: '风险证明已过期', confirmWaiting: '完成风险证明后确认', refresh: '刷新账户',
    positions: '个人模拟持仓', noPositions: '当前没有个人模拟持仓。', orders: '本次会话订单', noOrders: '本次会话尚未提交订单。', cancel: '撤销挂单',
    fill: '已成交', pending: '待成交', cancelled: '已撤销', replayed: '已安全返回同一订单结果，没有重复下单。',
    risk: '持仓风险', riskBody: '个人模拟仍会经历购买力不足、集中度、回撤、事件跳空与流动性风险。', longPositions: '多头股票', shortPositions: '空头股票',
    coreTitle: 'Ciclo Core 循环中枢', coreBody: '只负责整理草稿、报价和风险证明；最终提交能力只属于你。',
    receipt: '个人模拟订单收据', replay: '幂等重放', direct: '首次返回', unknownTitle: '提交结果暂时未知', unknownBody: '不要修改草稿或重复创建订单。使用相同幂等键、报价和风险证明核对原提交结果。', verify: '核对提交结果', requestIdentity: '原提交身份',
    shortRisk: 'SHORT 的理论最大亏损无限；服务端可以拒绝风险证明。',
  },
  'zh-Hant': {
    kicker: 'PERSONAL PAPER · USD 10,000', title: '個人模擬交易',
    description: '獨立於 CicloTrade 官方模擬和券商實盤。每筆股票訂單都由你確認，絕不會自動提交到真實帳戶。',
    personal: '個人模擬', official: '官方模擬', live: '券商實盤', active: '目前頁面', isolated: '完全隔離', disconnected: '未連接',
    startTitle: '開啟你的 USD 10,000 賽季', startBody: '第一賽季本金固定且歷史不可覆蓋。這裡只使用個人模擬帳本，不會連接券商。', start: '建立個人模擬帳戶',
    loading: '正在讀取個人模擬帳戶', retry: '重新讀取', unavailable: '個人模擬服務暫時不可用',
    cash: '現金', buyingPower: '可用購買力', equity: '總權益', pnl: '未實現盈虧', reserved: '已凍結資金', marketValue: '持倉市值',
    tasks: '帳戶待辦', accountReady: 'USD 10,000 帳戶已就緒', draftReady: '股票訂單草稿有效', quoteReady: '報價證明已取得', riskReady: '風險證明已取得', awaiting: '待完成',
    confirm: '確認提交個人模擬訂單', submitting: '正在提交…', confirmBlocked: '風險證明拒絕提交', confirmExpired: '風險證明已過期', confirmWaiting: '完成風險證明後確認', refresh: '重新整理帳戶',
    positions: '個人模擬持倉', noPositions: '目前沒有個人模擬持倉。', orders: '本次工作階段訂單', noOrders: '本次工作階段尚未提交訂單。', cancel: '撤銷掛單',
    fill: '已成交', pending: '待成交', cancelled: '已撤銷', replayed: '已安全傳回同一訂單結果，沒有重複下單。',
    risk: '持倉風險', riskBody: '個人模擬仍會經歷購買力不足、集中度、回撤、事件跳空與流動性風險。', longPositions: '多頭股票', shortPositions: '空頭股票',
    coreTitle: 'Ciclo Core 循環中樞', coreBody: '只負責整理草稿、報價和風險證明；最終提交能力只屬於你。',
    receipt: '個人模擬訂單收據', replay: '冪等重放', direct: '首次傳回', unknownTitle: '提交結果暫時未知', unknownBody: '不要修改草稿或重複建立訂單。使用相同冪等鍵、報價和風險證明核對原提交結果。', verify: '核對提交結果', requestIdentity: '原提交身分',
    shortRisk: 'SHORT 的理論最大虧損無限；服務端可以拒絕風險證明。',
  },
} as const

function money(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(value)
}

function idempotencyKey(): string {
  return `paper-${typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`
}

function caughtMessage(error: unknown): string {
  return error instanceof Error ? error.message : '个人模拟服务暂时不可用。'
}

function validSourceReference(value: string | null): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)
}

const ERROR_HANT: Readonly<Record<string, string>> = {
  '服务暂时不可用。': '服務暫時不可用。', '个人模拟服务暂时不可用。': '個人模擬服務暫時不可用。',
  '个人模拟风险证明请求无效。': '個人模擬風險證明請求無效。', '个人模拟风险证明响应格式无效。': '個人模擬風險證明回應格式無效。',
  '个人模拟风险证明不可用。': '個人模擬風險證明不可用。', '风险证明已过期。': '風險證明已過期。',
  '风险证明拒绝订单。': '風險證明拒絕訂單。', '实时报价暂时不可用。': '即時報價暫時不可用。',
  '当前没有可执行的实时美股报价。': '目前沒有可執行的即時美股報價。', '报价证明无效。': '報價證明無效。',
  '报价证明缺失、过期或不匹配。': '報價證明缺失、過期或不相符。', '账户已变化，请刷新后确认订单。': '帳戶已變更，請重新整理後確認訂單。',
  '可用购买力不足。': '可用購買力不足。', 'SELL 不得超过可用多头持仓。': 'SELL 不得超過可用多頭持倉。',
  'COVER 不得超过可用空头持仓。': 'COVER 不得超過可用空頭持倉。', '同一幂等键不能提交不同订单。': '同一冪等鍵不能提交不同訂單。',
}

function localizeError(message: string, locale: keyof typeof COPY): string {
  return locale === 'zh-Hant' ? ERROR_HANT[message] ?? message : message
}

function statusLabel(status: PersonalPaperOrder['status'], copy: typeof COPY[keyof typeof COPY]) {
  return status === 'FILLED' ? copy.fill : status === 'PENDING' ? copy.pending : copy.cancelled
}

function orderSideLabel(side: PersonalPaperOrder['side'], locale: keyof typeof COPY): string {
  const labels = locale === 'zh-Hant'
    ? { BUY: '買入', SELL: '賣出', SHORT: '放空', COVER: '回補' }
    : { BUY: '买入', SELL: '卖出', SHORT: '做空', COVER: '回补' }
  return labels[side]
}

function orderTypeLabel(orderType: PersonalPaperOrder['order_type'], locale: keyof typeof COPY): string {
  const labels = locale === 'zh-Hant'
    ? { MARKET: '市價', LIMIT: '限價', STOP: '停損觸發', STOP_LIMIT: '停損限價' }
    : { MARKET: '市价', LIMIT: '限价', STOP: '止损触发', STOP_LIMIT: '止损限价' }
  return labels[orderType]
}

export function PersonalPaperPage() {
  const { locale } = useLocale()
  const copy = COPY[locale]
  const [searchParams] = useSearchParams()
  const initialSymbol = (searchParams.get('symbol') ?? '').toUpperCase()
  const sourceParam = searchParams.get('source')
  const sourceKind: PaperDraftValues['sourceKind'] = ['recommendation', 'chart', 'screener'].includes(sourceParam ?? '') ? sourceParam as PaperDraftValues['sourceKind'] : 'manual'
  const referenceParam = searchParams.get('reference')
  const initialReference = validSourceReference(referenceParam) ? referenceParam : /^[A-Z][A-Z0-9.-]{0,15}$/.test(initialSymbol) ? initialSymbol : null
  const [account, setAccount] = useState<PersonalPaperAccount | null>(null)
  const [seasonId, setSeasonId] = useState(() => window.localStorage.getItem(SEASON_STORAGE_KEY) ?? '')
  const [loading, setLoading] = useState(Boolean(seasonId))
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState<PaperDraftValues>({
    symbol: /^[A-Z][A-Z0-9.-]{0,15}$/.test(initialSymbol) ? initialSymbol : 'AAPL',
    side: ['BUY', 'SELL', 'SHORT', 'COVER'].includes(searchParams.get('side')?.toUpperCase() ?? '') ? searchParams.get('side')?.toUpperCase() as PersonalPaperSide : 'BUY',
    orderType: 'MARKET', quantity: '1', limitPrice: '', stopPrice: '', timeInForce: 'DAY', sourceKind,
    sourceReference: sourceKind === 'manual' ? null : initialReference,
  })
  const [quote, setQuote] = useState<Awaited<ReturnType<typeof issuePersonalPaperQuote>> | null>(null)
  const [riskProof, setRiskProof] = useState<PersonalPaperRiskProof | null>(null)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState<BusyState>('')
  const [feedback, setFeedback] = useState('')
  const [orders, setOrders] = useState<PersonalPaperOrder[]>([])
  const [receipt, setReceipt] = useState<PersonalPaperOrderResult | null>(null)
  const [pendingRequest, setPendingRequest] = useState<PersonalPaperOrderRequest | null>(null)
  const [submitState, setSubmitState] = useState<SubmitState>('idle')
  const [, setClock] = useState(() => Date.now())

  const resetProofs = () => {
    setQuote(null); setRiskProof(null); setKey(''); setFeedback(''); setReceipt(null); setPendingRequest(null); setSubmitState('idle')
  }
  const updateDraft = (next: PaperDraftValues) => { setDraft(next); resetProofs() }

  const acceptAccount = (next: PersonalPaperAccount) => {
    if (account && account.account_version !== next.account_version) resetProofs()
    setAccount(next)
  }

  const loadAccount = async (id: string, busyState: 'refresh' | '' = '') => {
    if (!id) return
    if (busyState) setBusy(busyState)
    setLoading(!account); setError('')
    try { acceptAccount(await fetchPersonalPaperAccount(id)) }
    catch (caught) {
      const message = caughtMessage(caught); setError(message)
      if (/不存在|不属于|404/.test(message)) { window.localStorage.removeItem(SEASON_STORAGE_KEY); setSeasonId(''); setAccount(null) }
    } finally { setLoading(false); setBusy('') }
  }

  useEffect(() => {
    if (!seasonId) return
    let active = true
    setLoading(true); setError('')
    void fetchPersonalPaperAccount(seasonId).then((payload) => { if (active) setAccount(payload) }).catch((caught) => {
      if (!active) return
      const message = caughtMessage(caught); setError(message)
      if (/不存在|不属于|404/.test(message)) { window.localStorage.removeItem(SEASON_STORAGE_KEY); setSeasonId(''); setAccount(null) }
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [seasonId])

  useEffect(() => {
    if (!riskProof) return
    const delay = Math.max(0, Date.parse(riskProof.expires_at) - Date.now() + 25)
    const timer = window.setTimeout(() => setClock(Date.now()), delay)
    return () => window.clearTimeout(timer)
  }, [riskProof])

  const needsLimit = draft.orderType === 'LIMIT' || draft.orderType === 'STOP_LIMIT'
  const needsStop = draft.orderType === 'STOP' || draft.orderType === 'STOP_LIMIT'
  const validQuantity = /^\d+$/.test(draft.quantity) && Number(draft.quantity) > 0
  const validLimit = !needsLimit || (Number.isFinite(Number(draft.limitPrice)) && Number(draft.limitPrice) > 0)
  const validStop = !needsStop || (Number.isFinite(Number(draft.stopPrice)) && Number(draft.stopPrice) > 0)
  const draftValid = /^[A-Z][A-Z0-9.-]{0,15}$/.test(draft.symbol) && validQuantity && validLimit && validStop
  const proofExpired = Boolean(riskProof && Date.parse(riskProof.expires_at) <= Date.now())
  const proofPermitsSubmit = Boolean(riskProof && !proofExpired && (riskProof.decision === 'allow' || riskProof.decision === 'review'))
  const workflowLocked = Boolean(busy) || submitState === 'unknown'
  const positions = account?.positions ?? []
  const pnlClass = (account?.unrealized_pnl ?? 0) > 0 ? 'positive' : (account?.unrealized_pnl ?? 0) < 0 ? 'negative' : ''
  const longCount = positions.filter((position) => position.quantity > 0).length
  const shortCount = positions.filter((position) => position.quantity < 0).length

  const start = async () => {
    setStarting(true); setError('')
    try { const season = await createPersonalPaperSeason(); window.localStorage.setItem(SEASON_STORAGE_KEY, season.id); setSeasonId(season.id); await loadAccount(season.id) }
    catch (caught) { setError(caughtMessage(caught)) } finally { setStarting(false) }
  }

  const riskRequest = (): PersonalPaperRiskProofRequest | null => account && quote ? {
    season_id: account.season.id, market: 'US', symbol: draft.symbol, side: draft.side, order_type: draft.orderType,
    quantity: Number(draft.quantity), limit_price: needsLimit ? Number(draft.limitPrice) : null,
    stop_price: needsStop ? Number(draft.stopPrice) : null, time_in_force: draft.timeInForce, quote_id: quote.quote_id,
    account_version: account.account_version, source_context: { kind: draft.sourceKind, reference_id: draft.sourceReference },
  } : null

  const requestQuote = async () => {
    if (!draftValid || workflowLocked) return
    setBusy('quote'); setError(''); setFeedback(''); setRiskProof(null); setKey(''); setReceipt(null)
    try { setQuote(await issuePersonalPaperQuote(draft.symbol)) }
    catch (caught) { setQuote(null); setError(caughtMessage(caught)) } finally { setBusy('') }
  }

  const requestRisk = async () => {
    const requestPayload = riskRequest()
    if (!requestPayload || workflowLocked) return
    setBusy('risk'); setError(''); setFeedback(''); setReceipt(null)
    try { setRiskProof(await issuePersonalPaperRiskProof(requestPayload)); setKey(idempotencyKey()) }
    catch (caught) { setRiskProof(null); setKey(''); setError(caughtMessage(caught)) } finally { setBusy('') }
  }

  const handleSubmitResult = (result: PersonalPaperOrderResult) => {
    acceptAccount(result.account)
    setOrders((current) => [result.order, ...current.filter((item) => item.id !== result.order.id)])
    setReceipt(result); setFeedback(result.replayed ? copy.replayed : `${result.order.symbol} · ${statusLabel(result.order.status, copy)}`)
    setQuote(null); setRiskProof(null); setKey(''); setPendingRequest(null); setSubmitState('success')
  }

  const attemptSubmit = async (payload: PersonalPaperOrderRequest) => {
    setBusy('submit'); setError(''); setFeedback('')
    try { handleSubmitResult(await submitPersonalPaperStockOrder(payload)) }
    catch (caught) {
      if (caught instanceof BrowserApiError && caught.status >= 400 && caught.status < 500) {
        setError(caught.message); setRiskProof(null); setKey(''); setPendingRequest(null); setSubmitState('idle')
        if (caught.status === 409 || /账户已变化|报价证明缺失、过期或不匹配|风险证明已过期/.test(caught.message)) setQuote(null)
        if (caught.status === 409 && account) {
          try { acceptAccount(await fetchPersonalPaperAccount(account.season.id)) } catch { /* Preserve the authoritative submit rejection. */ }
        }
      } else {
        // Keep the same idempotency key, quote, risk proof, account version, source and original request until the result is known.
        setPendingRequest(payload); setSubmitState('unknown')
      }
    } finally { setBusy('') }
  }

  const submit = async () => {
    if (!account || !quote || !riskProof || !key || !draftValid || !proofPermitsSubmit || workflowLocked) return
    const payload: PersonalPaperOrderRequest = { ...riskRequest()!, idempotency_key: key, risk_proof_id: riskProof.id }
    setPendingRequest(payload)
    await attemptSubmit(payload)
  }

  const retryUnknown = async () => { if (pendingRequest && !busy) await attemptSubmit(pendingRequest) }

  const cancel = async (order: PersonalPaperOrder) => {
    if (!account || workflowLocked) return
    setBusy('cancel'); setError('')
    try { const result = await cancelPersonalPaperStockOrder({ season_id: account.season.id, order_id: order.id, account_version: account.account_version }); acceptAccount(result.account); setOrders((current) => current.map((item) => item.id === result.order.id ? result.order : item)) }
    catch (caught) { setError(caughtMessage(caught)) } finally { setBusy('') }
  }

  const metrics = useMemo(() => account ? [
    [copy.cash, account.cash], [copy.buyingPower, account.buying_power], [copy.equity, account.total_equity],
    [copy.pnl, account.unrealized_pnl], [copy.reserved, account.reserved_cash], [copy.marketValue, account.market_value],
  ] as const : [], [account, copy])
  const tasks = account ? [
    [copy.accountReady, true], [copy.draftReady, draftValid], [copy.quoteReady, Boolean(quote)], [copy.riskReady, Boolean(riskProof && !proofExpired)],
  ] as const : []
  const coreState: CicloCoreState = busy === 'quote' || busy === 'risk' || busy === 'submit' ? 'processing' : riskProof?.decision === 'reject' || proofExpired ? 'locked' : account?.quote_state === 'missing' ? 'offline' : 'neutral'
  const coreStateLabel: Record<CicloCoreState, string> = locale === 'zh-Hant'
    ? { neutral: '待命', processing: '核驗中', locked: '已鎖定', offline: '離線' }
    : { neutral: '待命', processing: '核验中', locked: '已锁定', offline: '离线' }
  const confirmLabel = busy === 'submit' ? copy.submitting : riskProof?.decision === 'reject' ? copy.confirmBlocked : proofExpired ? copy.confirmExpired : !proofPermitsSubmit ? copy.confirmWaiting : copy.confirm

  return <div className="page personal-paper-page">
    <PageHeader kicker={copy.kicker} title={copy.title} description={copy.description} />
    <section className="paper-boundary" aria-label={locale === 'zh-Hant' ? '帳戶邊界' : '账户边界'}>
      <span className="active"><WalletCards size={16} />{copy.personal}<strong>{copy.active}</strong></span>
      <span><ShieldCheck size={16} />{copy.official}<strong>{copy.isolated}</strong></span>
      <span><CircleDollarSign size={16} />{copy.live}<strong>{copy.disconnected}</strong></span>
    </section>
    {error && <div className="paper-alert error" role="alert"><AlertTriangle size={18} /><span>{localizeError(error, locale)}</span>{seasonId && <button type="button" onClick={() => void loadAccount(seasonId)}>{copy.retry}</button>}</div>}
    {loading && <div className="paper-state" role="status"><LoaderCircle className="spin" /><strong>{copy.loading}</strong></div>}
    {!loading && !account && <section className="paper-onboarding"><BadgeDollarSign size={30} /><div><h2>{copy.startTitle}</h2><p>{copy.startBody}</p></div><button className="button primary" type="button" disabled={starting} onClick={() => void start()}>{starting ? copy.loading : copy.start}</button></section>}
    {account && <div className="paper-console">
      <aside className="paper-account-rail" aria-label={locale === 'zh-Hant' ? '帳戶與訂單' : '账户与订单'}>
        <section className="paper-tasks"><header><h2>{copy.tasks}</h2><PaperRefreshButton label={copy.refresh} busy={busy === 'refresh'} disabled={workflowLocked} onClick={() => void loadAccount(account.season.id, 'refresh')} /></header><ol>{tasks.map(([label, done], index) => <li key={label} data-complete={done}><span>{done ? <CheckCircle2 size={16} /> : <span>{index + 1}</span>}</span><strong>{label}</strong><small>{done ? 'READY' : copy.awaiting}</small></li>)}</ol></section>
        <section className="paper-metrics" aria-label={locale === 'zh-Hant' ? '個人模擬帳戶摘要' : '个人模拟账户摘要'}>{metrics.map(([label, value]) => <article key={label}><span>{label}</span><strong className={label === copy.pnl ? pnlClass : ''}>{money(value)}</strong></article>)}</section>
        <OrderList copy={copy} locale={locale} orders={orders} busy={workflowLocked} onCancel={cancel} />
      </aside>

      <section className="paper-flow" aria-labelledby="paper-draft-title">
        <PaperDraftCard locale={locale} draft={draft} disabled={workflowLocked} valid={draftValid} quote={quote} riskProof={riskProof} busy={busy} onChange={updateDraft} onQuote={() => void requestQuote()} onRisk={() => void requestRisk()} />
        {draft.side === 'SHORT' && <div className="paper-short-risk" role="note"><AlertTriangle size={17} />{copy.shortRisk}</div>}
        <OrderPreview locale={locale} request={draft} quote={quote} proof={riskProof} />
        <DecisionSummary locale={locale} proof={riskProof} side={draft.side} expired={proofExpired} />
        <RiskCheckList locale={locale} proof={riskProof} />
        <button className="button primary paper-submit" type="button" disabled={!proofPermitsSubmit || !draftValid || workflowLocked} onClick={() => void submit()}>{confirmLabel}</button>
        {submitState === 'unknown' && pendingRequest && <section className="paper-unknown" role="alert"><Clock3 size={21} /><div><h2>{copy.unknownTitle}</h2><p>{copy.unknownBody}</p><small>{copy.requestIdentity} · {pendingRequest.idempotency_key}</small></div><button className="button secondary" type="button" disabled={Boolean(busy)} onClick={() => void retryUnknown()}>{busy === 'submit' ? copy.submitting : copy.verify}</button></section>}
        {receipt && <section className="paper-receipt" role="status"><ReceiptText size={21} /><div><small>{receipt.replayed ? copy.replay : copy.direct}</small><h2>{copy.receipt}</h2><p>{receipt.order.symbol} · {orderSideLabel(receipt.order.side, locale)} · {receipt.order.quantity} · {statusLabel(receipt.order.status, copy)}</p><code>{receipt.order.id}</code></div><CheckCircle2 size={24} /></section>}
        {feedback && <p className="paper-feedback" role="status"><CheckCircle2 size={16} />{feedback}</p>}
      </section>

      <aside className="paper-risk-rail" aria-label={locale === 'zh-Hant' ? '風險與資料健康' : '风险与数据健康'}>
        <section className="paper-core-panel"><div><small>CICLO RISK CORE</small><h2>{copy.coreTitle}</h2><p>{copy.coreBody}</p></div><CicloCore label={`${copy.coreTitle} · ${coreStateLabel[coreState]}`} state={coreState} /></section>
        <section className="paper-position-risk"><header><ShieldCheck size={18} /><h2>{copy.risk}</h2></header><p>{copy.riskBody}</p><dl><div><dt>{copy.longPositions}</dt><dd className="positive">{longCount}</dd></div><div><dt>{copy.shortPositions}</dt><dd className="negative">{shortCount}</dd></div></dl></section>
        <DataHealth locale={locale} account={account} riskProof={riskProof} expired={proofExpired} />
        <PositionList copy={copy} positions={positions} />
      </aside>
    </div>}
  </div>
}

function PositionList({ copy, positions }: { copy: typeof COPY[keyof typeof COPY]; positions: PersonalPaperAccount['positions'] }) {
  return <section className="paper-list paper-positions"><header><h2>{copy.positions}</h2><span>{positions.length}</span></header>{positions.length ? positions.map((position) => <article key={position.symbol}><span><strong>{position.symbol}</strong><small>US · STOCK</small></span><strong className={position.quantity > 0 ? 'positive' : 'negative'}>{position.quantity > 0 ? <ArrowUpFromLine size={15} /> : <ArrowDownToLine size={15} />}{position.quantity}</strong></article>) : <p>{copy.noPositions}</p>}</section>
}

function OrderList({ copy, locale, orders, busy, onCancel }: { copy: typeof COPY[keyof typeof COPY]; locale: keyof typeof COPY; orders: PersonalPaperOrder[]; busy: boolean; onCancel: (order: PersonalPaperOrder) => void }) {
  return <section className="paper-list paper-orders"><header><h2>{copy.orders}</h2><span>{orders.length}</span></header>{orders.length ? orders.map((order) => <article key={order.id}><span><strong>{order.symbol} · {orderSideLabel(order.side, locale)}</strong><small>{orderTypeLabel(order.order_type, locale)} · {order.quantity}</small></span><span className={`paper-order-state ${order.status.toLowerCase()}`}>{statusLabel(order.status, copy)}</span>{order.status === 'PENDING' && <button className="icon-button danger" type="button" aria-label={copy.cancel} disabled={busy} onClick={() => onCancel(order)}><XCircle size={16} /></button>}</article>) : <p>{copy.noOrders}</p>}</section>
}
