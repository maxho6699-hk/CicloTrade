import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpFromLine,
  BadgeDollarSign,
  CheckCircle2,
  CircleDollarSign,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  WalletCards,
  XCircle,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  cancelPersonalPaperStockOrder,
  createPersonalPaperSeason,
  fetchPersonalPaperAccount,
  issuePersonalPaperQuote,
  submitPersonalPaperStockOrder,
  type PersonalPaperAccount,
  type PersonalPaperOrder,
  type PersonalPaperOrderRequest,
  type PersonalPaperOrderType,
  type PersonalPaperQuoteProof,
  type PersonalPaperSide,
} from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { useLocale } from '../i18n/useLocale'

const SEASON_STORAGE_KEY = 'ciclotrade.personalPaper.activeSeason.v1'
const SIDE_OPTIONS: PersonalPaperSide[] = ['BUY', 'SELL', 'SHORT', 'COVER']
const ORDER_TYPE_OPTIONS: PersonalPaperOrderType[] = ['MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT']

const COPY = {
  'zh-Hans': {
    kicker: 'PERSONAL PAPER · USD 10,000', title: '个人模拟交易',
    description: '独立于 CicloTrade 官方模拟和券商实盘。每笔订单都由你确认，绝不会自动提交到真实账户。',
    personal: '个人模拟', official: '官方模拟', live: '券商实盘', active: '当前页面', isolated: '完全隔离', disconnected: '未连接',
    startTitle: '开启你的 USD 10,000 赛季', startBody: '第一赛季本金固定且历史不可覆盖。这里只使用个人模拟账本，不会连接券商。', start: '创建个人模拟账户',
    loading: '正在读取个人模拟账户', retry: '重新读取', unavailable: '个人模拟服务暂时不可用',
    cash: '现金', buyingPower: '可用购买力', equity: '总权益', pnl: '未实现盈亏', reserved: '已冻结资金', marketValue: '持仓市值',
    ticket: '股票订单票据', step1: '1 · 设置', step2: '2 · 核验', step3: '3 · 确认',
    symbol: '美股代码', side: '方向', orderType: '订单类型', quantity: '数量（整数股）', limit: '限价', stop: '止损触发价',
    quote: '取得可执行报价证明', quoteReady: '已取得服务端实时报价证明', quoteWarning: '报价证明有时效；修改票据后必须重新核验。',
    confirm: '确认提交个人模拟订单', submitting: '正在提交…', refresh: '刷新账户',
    positions: '个人模拟持仓', noPositions: '当前没有个人模拟持仓。', orders: '本次会话订单', noOrders: '本次会话尚未提交订单。', cancel: '撤销挂单',
    fill: '已成交', pending: '待成交', cancelled: '已撤销', replayed: '已安全返回同一订单结果，没有重复下单。',
    risk: '模拟不等于无风险', riskBody: '卖出和回补必须有对应持仓；购买力、实时报价、账户版本和订单字段由服务端强制校验。',
  },
  'zh-Hant': {
    kicker: 'PERSONAL PAPER · USD 10,000', title: '個人模擬交易',
    description: '獨立於 CicloTrade 官方模擬和券商實盤。每筆訂單都由你確認，絕不會自動提交到真實帳戶。',
    personal: '個人模擬', official: '官方模擬', live: '券商實盤', active: '目前頁面', isolated: '完全隔離', disconnected: '未連接',
    startTitle: '開啟你的 USD 10,000 賽季', startBody: '第一賽季本金固定且歷史不可覆蓋。這裡只使用個人模擬帳本，不會連接券商。', start: '建立個人模擬帳戶',
    loading: '正在讀取個人模擬帳戶', retry: '重新讀取', unavailable: '個人模擬服務暫時不可用',
    cash: '現金', buyingPower: '可用購買力', equity: '總權益', pnl: '未實現盈虧', reserved: '已凍結資金', marketValue: '持倉市值',
    ticket: '股票訂單票據', step1: '1 · 設定', step2: '2 · 核驗', step3: '3 · 確認',
    symbol: '美股代碼', side: '方向', orderType: '訂單類型', quantity: '數量（整數股）', limit: '限價', stop: '停損觸發價',
    quote: '取得可執行報價證明', quoteReady: '已取得服務端即時報價證明', quoteWarning: '報價證明有時效；修改票據後必須重新核驗。',
    confirm: '確認提交個人模擬訂單', submitting: '正在提交…', refresh: '重新整理帳戶',
    positions: '個人模擬持倉', noPositions: '目前沒有個人模擬持倉。', orders: '本次工作階段訂單', noOrders: '本次工作階段尚未提交訂單。', cancel: '撤銷掛單',
    fill: '已成交', pending: '待成交', cancelled: '已撤銷', replayed: '已安全傳回同一訂單結果，沒有重複下單。',
    risk: '模擬不等於無風險', riskBody: '賣出和回補必須有對應持倉；購買力、即時報價、帳戶版本和訂單欄位由服務端強制校驗。',
  },
} as const

function money(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(value)
}

function idempotencyKey(): string {
  return `paper-${typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`
}

function caughtPersonalPaperMessage(error: unknown): string {
  return error instanceof Error ? error.message : '个人模拟服务暂时不可用。'
}

const PERSONAL_PAPER_ERROR_HANT: Readonly<Record<string, string>> = {
  '服务暂时不可用。': '服務暫時不可用。',
  '个人模拟服务暂时不可用。': '個人模擬服務暫時不可用。',
  '实时报价暂时不可用。': '即時報價暫時不可用。',
  '实时美股报价不可用。': '即時美股報價不可用。',
  '当前没有可执行的实时美股报价。': '目前沒有可執行的即時美股報價。',
  '报价证明无效。': '報價證明無效。',
  '报价证明用户无效。': '報價證明使用者無效。',
  '报价证明数据库无效。': '報價證明資料庫無效。',
  '报价证明密钥必须是至少 32 字节的独立密钥。': '報價證明金鑰必須是至少 32 位元組的獨立金鑰。',
  '报价证明只允许有效的美股代码。': '報價證明只允許有效的美股代碼。',
  '报价时间无效或已过期。': '報價時間無效或已過期。',
  '报价证明有效期无效。': '報價證明有效期無效。',
  '报价证明随机标识无效。': '報價證明隨機識別碼無效。',
  '报价证明需要有效的个人模拟账户。': '報價證明需要有效的個人模擬帳戶。',
  '报价证明随机标识重复。': '報價證明隨機識別碼重複。',
  '报价证明账户无法验证。': '報價證明帳戶無法驗證。',
  '报价证明事务无效。': '報價證明交易無效。',
  '报价证明账户无效。': '報價證明帳戶無效。',
  '报价证明不可用，订单未被接受。': '報價證明不可用，訂單未被接受。',
  '报价证明缺失、过期或不匹配。': '報價證明缺失、過期或不相符。',
  '报价请求字段无效。': '報價請求欄位無效。',
  '报价请求只允许有效的美股代码。': '報價請求只允許有效的美股代碼。',
  '登录身份无效。': '登入身分無效。',
  'user_id 无效。': 'user_id 無效。',
  '请求包含不可序列化或非有限数值。': '請求包含不可序列化或非有限數值。',
  '首批个人模拟只支持整数股。': '首批個人模擬只支援整數股。',
  'quantity 超出范围。': 'quantity 超出範圍。',
  '订单必须为对象。': '訂單必須為物件。',
  '订单字段不完整或包含未知字段。': '訂單欄位不完整或包含未知欄位。',
  '首批个人模拟仅允许有效美股代码。': '首批個人模擬僅允許有效美股代碼。',
  'side 或 order_type 无效。': 'side 或 order_type 無效。',
  '首批个人模拟仅支持 DAY。': '首批個人模擬僅支援 DAY。',
  'account_version 无效。': 'account_version 無效。',
  '订单价格字段与 order_type 不匹配。': '訂單價格欄位與 order_type 不相符。',
  'source_context 无效。': 'source_context 無效。',
  '订单名义金额无效。': '訂單名義金額無效。',
  '撤单字段不完整或包含未知字段。': '撤單欄位不完整或包含未知欄位。',
  'season_id 或 order_id 无效。': 'season_id 或 order_id 無效。',
  '服务时间必须包含时区。': '服務時間必須包含時區。',
  '个人模拟赛季响应格式无效。': '個人模擬賽季回應格式無效。',
  '个人模拟赛季无效。': '個人模擬賽季無效。',
  '个人模拟账户响应格式无效。': '個人模擬帳戶回應格式無效。',
  '个人模拟报价响应格式无效。': '個人模擬報價回應格式無效。',
  '个人模拟订单响应格式无效。': '個人模擬訂單回應格式無效。',
  '个人模拟撤单响应格式无效。': '個人模擬撤單回應格式無效。',
  '个人模拟赛季不存在或不属于当前用户。': '個人模擬賽季不存在或不屬於目前使用者。',
  '个人模拟赛季已经结束。': '個人模擬賽季已經結束。',
  '个人模拟账本余额不平，请联系支持。': '個人模擬帳本餘額不平，請聯絡支援。',
  '账户已变化，请刷新后再撤单。': '帳戶已變更，請重新整理後再撤單。',
  '账户已变化，请刷新后确认订单。': '帳戶已變更，請重新整理後確認訂單。',
  '订单不存在或不属于当前用户。': '訂單不存在或不屬於目前使用者。',
  '只有未成交的挂单可以撤销。': '只有未成交的掛單可以撤銷。',
  '挂单保留记录缺失，无法安全撤单。': '掛單保留記錄缺失，無法安全撤單。',
  '同一幂等键不能提交不同订单。': '同一冪等鍵不能提交不同訂單。',
  '空头仓位必须先使用 COVER 平仓。': '空頭持倉必須先使用 COVER 平倉。',
  '多头仓位必须先使用 SELL 平仓。': '多頭持倉必須先使用 SELL 平倉。',
  'SELL 不得超过可用多头持仓。': 'SELL 不得超過可用多頭持倉。',
  'COVER 不得超过可用空头持仓。': 'COVER 不得超過可用空頭持倉。',
  '可用购买力不足。': '可用購買力不足。',
}

function localizePersonalPaperError(message: string, locale: keyof typeof COPY): string {
  return locale === 'zh-Hant' ? PERSONAL_PAPER_ERROR_HANT[message] ?? message : message
}

function statusLabel(status: PersonalPaperOrder['status'], copy: typeof COPY['zh-Hans'] | typeof COPY['zh-Hant']) {
  return status === 'FILLED' ? copy.fill : status === 'PENDING' ? copy.pending : copy.cancelled
}

export function PersonalPaperPage() {
  const { locale } = useLocale()
  const copy = COPY[locale]
  const [searchParams] = useSearchParams()
  const initialSymbol = (searchParams.get('symbol') ?? '').toUpperCase()
  const initialSide = searchParams.get('side')?.toUpperCase()
  const [account, setAccount] = useState<PersonalPaperAccount | null>(null)
  const [seasonId, setSeasonId] = useState(() => window.localStorage.getItem(SEASON_STORAGE_KEY) ?? '')
  const [loading, setLoading] = useState(Boolean(seasonId))
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [symbol, setSymbol] = useState(/^[A-Z][A-Z0-9.-]{0,15}$/.test(initialSymbol) ? initialSymbol : 'AAPL')
  const [side, setSide] = useState<PersonalPaperSide>(SIDE_OPTIONS.includes(initialSide as PersonalPaperSide) ? initialSide as PersonalPaperSide : 'BUY')
  const [orderType, setOrderType] = useState<PersonalPaperOrderType>('MARKET')
  const [quantity, setQuantity] = useState('1')
  const [limitPrice, setLimitPrice] = useState('')
  const [stopPrice, setStopPrice] = useState('')
  const [quote, setQuote] = useState<PersonalPaperQuoteProof | null>(null)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState<'quote' | 'submit' | 'refresh' | 'cancel' | ''>('')
  const [feedback, setFeedback] = useState('')
  const [orders, setOrders] = useState<PersonalPaperOrder[]>([])

  const loadAccount = async (id: string, busyState: 'refresh' | '' = '') => {
    if (!id) return
    if (busyState) setBusy(busyState)
    setLoading(!account)
    setError('')
    try {
      setAccount(await fetchPersonalPaperAccount(id))
    } catch (caught) {
      const message = caughtPersonalPaperMessage(caught)
      setError(message)
      if (/不存在|不属于|404/.test(message)) {
        window.localStorage.removeItem(SEASON_STORAGE_KEY)
        setSeasonId('')
        setAccount(null)
      }
    } finally {
      setLoading(false)
      setBusy('')
    }
  }

  useEffect(() => {
    if (!seasonId) return
    let active = true
    setLoading(true)
    setError('')
    void fetchPersonalPaperAccount(seasonId).then((payload) => {
      if (active) setAccount(payload)
    }).catch((caught) => {
      if (!active) return
      const message = caughtPersonalPaperMessage(caught)
      setError(message)
      if (/不存在|不属于|404/.test(message)) {
        window.localStorage.removeItem(SEASON_STORAGE_KEY)
        setSeasonId('')
        setAccount(null)
      }
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [seasonId])

  const resetProof = () => { setQuote(null); setKey(''); setFeedback('') }
  const validQuantity = /^\d+$/.test(quantity) && Number(quantity) > 0
  const needsLimit = orderType === 'LIMIT' || orderType === 'STOP_LIMIT'
  const needsStop = orderType === 'STOP' || orderType === 'STOP_LIMIT'
  const validLimit = !needsLimit || (Number.isFinite(Number(limitPrice)) && Number(limitPrice) > 0)
  const validStop = !needsStop || (Number.isFinite(Number(stopPrice)) && Number(stopPrice) > 0)
  const ticketValid = /^[A-Z][A-Z0-9.-]{0,15}$/.test(symbol) && validQuantity && validLimit && validStop
  const pnlClass = (account?.unrealized_pnl ?? 0) > 0 ? 'positive' : (account?.unrealized_pnl ?? 0) < 0 ? 'negative' : ''
  const positions = account?.positions ?? []

  const start = async () => {
    setStarting(true); setError('')
    try {
      const season = await createPersonalPaperSeason()
      window.localStorage.setItem(SEASON_STORAGE_KEY, season.id)
      setSeasonId(season.id)
      await loadAccount(season.id)
    } catch (caught) { setError(caughtPersonalPaperMessage(caught)) } finally { setStarting(false) }
  }

  const requestQuote = async () => {
    if (!ticketValid || busy) return
    setBusy('quote'); setError(''); setFeedback('')
    try {
      const proof = await issuePersonalPaperQuote(symbol)
      setQuote(proof)
      setKey(idempotencyKey())
    } catch (caught) { setQuote(null); setKey(''); setError(caughtPersonalPaperMessage(caught)) } finally { setBusy('') }
  }

  const submit = async () => {
    if (!account || !quote || !key || !ticketValid || busy) return
    setBusy('submit'); setError(''); setFeedback('')
    const payload: PersonalPaperOrderRequest = {
      idempotency_key: key,
      season_id: account.season.id,
      market: 'US', symbol, side, order_type: orderType, quantity: Number(quantity),
      limit_price: needsLimit ? Number(limitPrice) : null,
      stop_price: needsStop ? Number(stopPrice) : null,
      time_in_force: 'DAY', quote_id: quote.quote_id,
      account_version: account.account_version,
      source_context: { kind: 'manual', reference_id: null },
    }
    try {
      const result = await submitPersonalPaperStockOrder(payload)
      setAccount(result.account)
      setOrders((current) => [result.order, ...current.filter((item) => item.id !== result.order.id)])
      setFeedback(result.replayed ? copy.replayed : `${result.order.symbol} · ${statusLabel(result.order.status, copy)}`)
      setQuote(null); setKey('')
    } catch (caught) {
      // Keep the same idempotency key and exact payload so an unknown delivery can be retried safely.
      setError(caughtPersonalPaperMessage(caught))
    } finally { setBusy('') }
  }

  const cancel = async (order: PersonalPaperOrder) => {
    if (!account || busy) return
    setBusy('cancel'); setError('')
    try {
      const result = await cancelPersonalPaperStockOrder({ season_id: account.season.id, order_id: order.id, account_version: account.account_version })
      setAccount(result.account)
      setOrders((current) => current.map((item) => item.id === result.order.id ? result.order : item))
    } catch (caught) { setError(caughtPersonalPaperMessage(caught)) } finally { setBusy('') }
  }

  const metrics = useMemo(() => account ? [
    [copy.cash, account.cash], [copy.buyingPower, account.buying_power], [copy.equity, account.total_equity],
    [copy.pnl, account.unrealized_pnl], [copy.reserved, account.reserved_cash], [copy.marketValue, account.market_value],
  ] as const : [], [account, copy])

  return <div className="page personal-paper-page">
    <PageHeader kicker={copy.kicker} title={copy.title} description={copy.description} />
    <section className="paper-boundary" aria-label="账户边界">
      <span className="active"><WalletCards size={16} />{copy.personal}<strong>{copy.active}</strong></span>
      <span><ShieldCheck size={16} />{copy.official}<strong>{copy.isolated}</strong></span>
      <span><CircleDollarSign size={16} />{copy.live}<strong>{copy.disconnected}</strong></span>
    </section>
    {error && <div className="paper-alert error" role="alert"><AlertTriangle size={18} /><span>{localizePersonalPaperError(error, locale)}</span>{seasonId && <button type="button" onClick={() => void loadAccount(seasonId)}>{copy.retry}</button>}</div>}
    {loading && <div className="paper-state" role="status"><LoaderCircle className="spin" /><strong>{copy.loading}</strong></div>}
    {!loading && !account && <section className="paper-onboarding">
      <BadgeDollarSign size={28} /><div><h2>{copy.startTitle}</h2><p>{copy.startBody}</p></div>
      <button className="button primary" type="button" disabled={starting} onClick={() => void start()}>{starting ? copy.loading : copy.start}</button>
    </section>}
    {account && <>
      <section className="paper-metrics" aria-label="个人模拟账户摘要">
        {metrics.map(([label, value]) => <article key={label}><span>{label}</span><strong className={label === copy.pnl ? pnlClass : ''}>{money(value)}</strong></article>)}
      </section>
      <div className="paper-workspace">
        <section className="paper-ticket" aria-labelledby="paper-ticket-title">
          <header><div><small>{copy.step1} → {copy.step2} → {copy.step3}</small><h2 id="paper-ticket-title">{copy.ticket}</h2></div><button className="icon-button" type="button" aria-label={copy.refresh} disabled={Boolean(busy)} onClick={() => void loadAccount(account.season.id, 'refresh')}><RefreshCw size={17} className={busy === 'refresh' ? 'spin' : ''} /></button></header>
          <div className="paper-ticket-grid">
            <label><span>{copy.symbol}</span><input name="personal-paper-symbol" autoComplete="off" value={symbol} maxLength={16} autoCapitalize="characters" onChange={(event) => { setSymbol(event.target.value.trim().toUpperCase()); resetProof() }} /></label>
            <label><span>{copy.side}</span><select name="personal-paper-side" autoComplete="off" value={side} onChange={(event) => { setSide(event.target.value as PersonalPaperSide); resetProof() }}>{SIDE_OPTIONS.map((value) => <option key={value}>{value}</option>)}</select></label>
            <label><span>{copy.orderType}</span><select name="personal-paper-order-type" autoComplete="off" value={orderType} onChange={(event) => { setOrderType(event.target.value as PersonalPaperOrderType); resetProof() }}>{ORDER_TYPE_OPTIONS.map((value) => <option key={value}>{value}</option>)}</select></label>
            <label><span>{copy.quantity}</span><input name="personal-paper-quantity" autoComplete="off" type="number" inputMode="numeric" min="1" step="1" value={quantity} onChange={(event) => { setQuantity(event.target.value); resetProof() }} /></label>
            {needsLimit && <label><span>{copy.limit}</span><input name="personal-paper-limit-price" autoComplete="off" type="number" inputMode="decimal" min="0.01" step="0.01" value={limitPrice} onChange={(event) => { setLimitPrice(event.target.value); resetProof() }} /></label>}
            {needsStop && <label><span>{copy.stop}</span><input name="personal-paper-stop-price" autoComplete="off" type="number" inputMode="decimal" min="0.01" step="0.01" value={stopPrice} onChange={(event) => { setStopPrice(event.target.value); resetProof() }} /></label>}
          </div>
          <div className={`paper-proof ${quote ? 'ready' : ''}`}>
            {quote ? <CheckCircle2 size={18} /> : <ShieldCheck size={18} />}
            <div><strong>{quote ? copy.quoteReady : copy.step2}</strong><small>{copy.quoteWarning}</small></div>
            <button className="button secondary" type="button" disabled={!ticketValid || Boolean(busy)} onClick={() => void requestQuote()}>{busy === 'quote' ? copy.loading : copy.quote}</button>
          </div>
          <button className="button primary paper-submit" type="button" disabled={!quote || !ticketValid || Boolean(busy)} onClick={() => void submit()}>{busy === 'submit' ? copy.submitting : copy.confirm}</button>
          {feedback && <p className="paper-feedback" role="status"><CheckCircle2 size={16} />{feedback}</p>}
        </section>
        <aside className="paper-risk"><ShieldCheck size={19} /><div><strong>{copy.risk}</strong><p>{copy.riskBody}</p></div></aside>
      </div>
      <section className="paper-ledger-grid">
        <div className="paper-list"><header><h2>{copy.positions}</h2><span>{positions.length}</span></header>{positions.length ? positions.map((position) => <article key={position.symbol}><span><strong>{position.symbol}</strong><small>US · STOCK</small></span><strong className={position.quantity > 0 ? 'positive' : 'negative'}>{position.quantity > 0 ? <ArrowUpFromLine size={15} /> : <ArrowDownToLine size={15} />}{position.quantity}</strong></article>) : <p>{copy.noPositions}</p>}</div>
        <div className="paper-list"><header><h2>{copy.orders}</h2><span>{orders.length}</span></header>{orders.length ? orders.map((order) => <article key={order.id}><span><strong>{order.symbol} · {order.side}</strong><small>{order.order_type} · {order.quantity}</small></span><span className={`paper-order-state ${order.status.toLowerCase()}`}>{statusLabel(order.status, copy)}</span>{order.status === 'PENDING' && <button className="icon-button danger" type="button" aria-label={copy.cancel} disabled={Boolean(busy)} onClick={() => void cancel(order)}><XCircle size={16} /></button>}</article>) : <p>{copy.noOrders}</p>}</div>
      </section>
    </>}
  </div>
}
