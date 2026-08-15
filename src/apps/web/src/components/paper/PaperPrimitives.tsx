import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Clock3,
  FileCheck2,
  Gauge,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  XCircle,
} from 'lucide-react'
import type {
  PersonalPaperAccount,
  PersonalPaperOrderType,
  PersonalPaperQuoteProof,
  PersonalPaperRiskCheck,
  PersonalPaperRiskDataState,
  PersonalPaperRiskProof,
  PersonalPaperRiskProofRequest,
  PersonalPaperSide,
} from '../../api/client'
import { formatPersonalPaperRiskCheck, parsePersonalPaperRiskCheck } from '../../api/client'

export type PaperLocale = 'zh-Hans' | 'zh-Hant'

export interface PaperDraftValues {
  symbol: string
  side: PersonalPaperSide
  orderType: PersonalPaperOrderType
  quantity: string
  limitPrice: string
  stopPrice: string
  timeInForce: 'DAY'
  sourceKind: PersonalPaperRiskProofRequest['source_context']['kind']
  sourceReference: string | null
}

interface PaperDraftCardProps {
  locale: PaperLocale
  draft: PaperDraftValues
  disabled: boolean
  valid: boolean
  quote: PersonalPaperQuoteProof | null
  riskProof: PersonalPaperRiskProof | null
  busy: '' | 'quote' | 'risk' | 'submit' | 'refresh' | 'cancel'
  onChange: (next: PaperDraftValues) => void
  onQuote: () => void
  onRisk: () => void
}

const SIDES: PersonalPaperSide[] = ['BUY', 'SELL', 'SHORT', 'COVER']
const ORDER_TYPES: PersonalPaperOrderType[] = ['MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT']

function sideOptionLabel(value: PersonalPaperSide, locale: PaperLocale): string {
  const labels = locale === 'zh-Hant'
    ? { BUY: '買入', SELL: '賣出', SHORT: '放空', COVER: '回補' }
    : { BUY: '买入', SELL: '卖出', SHORT: '做空', COVER: '回补' }
  return labels[value]
}

function orderTypeOptionLabel(value: PersonalPaperOrderType, locale: PaperLocale): string {
  const labels = locale === 'zh-Hant'
    ? { MARKET: '市價', LIMIT: '限價', STOP: '停損觸發', STOP_LIMIT: '停損限價' }
    : { MARKET: '市价', LIMIT: '限价', STOP: '止损触发', STOP_LIMIT: '止损限价' }
  return labels[value]
}

function EvidenceMark() {
  return <svg className="paper-evidence-mark" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.7 13 4.6v6.8L8 14.3 3 11.4V4.6Z" /><path d="M10.7 5.4A3.35 3.35 0 1 0 10.7 10.6" /></svg>
}

function paperText(locale: PaperLocale) {
  return locale === 'zh-Hant' ? {
    workflow: '草稿 → 報價 → 風險證明 → 確認', draft: '訂單草稿', symbol: '股票代碼', side: '方向',
    orderType: '訂單類型', quantity: '數量（整數股）', limit: '限價', stop: '停損觸發價', tif: '有效期',
    source: '草稿來源', manual: '手動輸入', recommendation: '研究建議', chart: '圖表帶入', screener: '選股器帶入',
    manualize: '改為手動輸入', quote: '取得報價證明', quoteReady: '報價證明已取得', risk: '產生風險證明',
    riskReady: '風險證明已產生', working: '核驗中…', invalid: '請完成有效的股票代碼、數量與價格。',
  } : {
    workflow: '草稿 → 报价 → 风险证明 → 确认', draft: '订单草稿', symbol: '股票代码', side: '方向',
    orderType: '订单类型', quantity: '数量（整数股）', limit: '限价', stop: '止损触发价', tif: '有效期',
    source: '草稿来源', manual: '手动输入', recommendation: '研究建议', chart: '图表带入', screener: '选股器带入',
    manualize: '改为手动输入', quote: '取得报价证明', quoteReady: '报价证明已取得', risk: '生成风险证明',
    riskReady: '风险证明已生成', working: '核验中…', invalid: '请完成有效的股票代码、数量与价格。',
  }
}

export function PaperDraftCard({ locale, draft, disabled, valid, quote, riskProof, busy, onChange, onQuote, onRisk }: PaperDraftCardProps) {
  const copy = paperText(locale)
  const needsLimit = draft.orderType === 'LIMIT' || draft.orderType === 'STOP_LIMIT'
  const needsStop = draft.orderType === 'STOP' || draft.orderType === 'STOP_LIMIT'
  const symbolValid = /^[A-Z][A-Z0-9.-]{0,15}$/.test(draft.symbol)
  const quantityValid = /^\d+$/.test(draft.quantity) && Number(draft.quantity) > 0
  const limitValid = !needsLimit || (Number.isFinite(Number(draft.limitPrice)) && Number(draft.limitPrice) > 0)
  const stopValid = !needsStop || (Number.isFinite(Number(draft.stopPrice)) && Number(draft.stopPrice) > 0)
  const validationId = 'paper-draft-validation'
  const sourceLabel = copy[draft.sourceKind]
  const patch = (next: Partial<PaperDraftValues>) => onChange({ ...draft, ...next })

  return <section className="paper-draft-card" aria-labelledby="paper-draft-title">
    <header>
      <div><small>{copy.workflow}</small><h2 id="paper-draft-title">{copy.draft}</h2></div>
      <span className="paper-source-chip"><EvidenceMark />{copy.source} · {sourceLabel}</span>
    </header>
    <div className="paper-draft-grid">
      <label><span>{copy.symbol}</span><input name="personal-paper-symbol" autoComplete="off" value={draft.symbol} maxLength={16} autoCapitalize="characters" disabled={disabled} aria-invalid={!symbolValid} aria-describedby={!symbolValid ? validationId : undefined} onChange={(event) => patch({ symbol: event.target.value.trim().toUpperCase() })} /></label>
      <label><span>{copy.side}</span><select name="personal-paper-side" autoComplete="off" value={draft.side} disabled={disabled} onChange={(event) => patch({ side: event.target.value as PersonalPaperSide })}>{SIDES.map((value) => <option key={value} value={value}>{sideOptionLabel(value, locale)}</option>)}</select></label>
      <label><span>{copy.orderType}</span><select name="personal-paper-order-type" autoComplete="off" value={draft.orderType} disabled={disabled} onChange={(event) => patch({ orderType: event.target.value as PersonalPaperOrderType })}>{ORDER_TYPES.map((value) => <option key={value} value={value}>{orderTypeOptionLabel(value, locale)}</option>)}</select></label>
      <label><span>{copy.quantity}</span><input name="personal-paper-quantity" autoComplete="off" type="number" inputMode="numeric" min="1" step="1" value={draft.quantity} disabled={disabled} aria-invalid={!quantityValid} aria-describedby={!quantityValid ? validationId : undefined} onChange={(event) => patch({ quantity: event.target.value })} /></label>
      {needsLimit && <label><span>{copy.limit}</span><input name="personal-paper-limit-price" autoComplete="off" type="number" inputMode="decimal" min="0.01" step="0.01" value={draft.limitPrice} disabled={disabled} aria-invalid={!limitValid} aria-describedby={!limitValid ? validationId : undefined} onChange={(event) => patch({ limitPrice: event.target.value })} /></label>}
      {needsStop && <label><span>{copy.stop}</span><input name="personal-paper-stop-price" autoComplete="off" type="number" inputMode="decimal" min="0.01" step="0.01" value={draft.stopPrice} disabled={disabled} aria-invalid={!stopValid} aria-describedby={!stopValid ? validationId : undefined} onChange={(event) => patch({ stopPrice: event.target.value })} /></label>}
      <label><span>{copy.tif}</span><select name="personal-paper-time-in-force" value={draft.timeInForce} disabled={disabled} onChange={() => patch({ timeInForce: 'DAY' })}><option value="DAY">{locale === 'zh-Hant' ? '當日有效' : '当日有效'}</option></select></label>
    </div>
    {draft.sourceKind !== 'manual' && <div className="paper-source-context"><span title={draft.sourceReference ?? undefined}>{sourceLabel}{draft.sourceReference ? ` · ${draft.sourceReference}` : ''}</span><button type="button" disabled={disabled} onClick={() => patch({ sourceKind: 'manual', sourceReference: null })}>{copy.manualize}</button></div>}
    {!valid && <p id={validationId} className="paper-inline-note" role="alert" aria-live="polite"><AlertTriangle size={15} />{copy.invalid}</p>}
    <div className="paper-proof-actions">
      <button className={`paper-proof-action ${quote ? 'is-ready' : ''}`} type="button" disabled={!valid || disabled} onClick={onQuote}>
        {quote ? <CheckCircle2 size={18} /> : <CircleDashed size={18} />}<span><strong>{busy === 'quote' ? copy.working : quote ? copy.quoteReady : copy.quote}</strong><small>{quote?.quote_id ?? 'QUOTE REQUIRED'}</small></span>
      </button>
      <button className={`paper-proof-action ${riskProof ? 'is-ready' : ''}`} type="button" disabled={!quote || disabled} onClick={onRisk}>
        {riskProof ? <ShieldCheck size={18} /> : <ShieldAlert size={18} />}<span><strong>{busy === 'risk' ? copy.working : riskProof ? copy.riskReady : copy.risk}</strong><small>{riskProof?.id ?? 'RISK PROOF REQUIRED'}</small></span>
      </button>
    </div>
  </section>
}

function stateLabel(state: PersonalPaperRiskDataState, locale: PaperLocale) {
  const labels = locale === 'zh-Hant'
    ? { fresh: '即時', partial: '部分資料', stale: '已過期', missing: '缺資料' }
    : { fresh: '实时', partial: '部分数据', stale: '已过期', missing: '缺数据' }
  return labels[state]
}

interface DataHealthProps {
  locale: PaperLocale
  account: PersonalPaperAccount
  riskProof: PersonalPaperRiskProof | null
  expired: boolean
}

export function DataHealth({ locale, account, riskProof, expired }: DataHealthProps) {
  const title = locale === 'zh-Hant' ? '資料健康' : '数据健康'
  const accountLabel = locale === 'zh-Hant' ? '帳戶快照' : '账户快照'
  const quoteLabel = locale === 'zh-Hant' ? '報價狀態' : '报价状态'
  const riskLabel = locale === 'zh-Hant' ? '風險證明' : '风险证明'
  const computedLabel = locale === 'zh-Hant' ? '帳本計算' : '账本计算'
  const marksLabel = locale === 'zh-Hant' ? '持倉報價' : '持仓报价'
  const riskState = expired ? 'stale' : riskProof?.data_state ?? 'missing'
  const quoteState: PersonalPaperRiskDataState = account.quote_state === 'fresh' ? 'fresh' : account.quote_state === 'delayed' ? 'partial' : account.quote_state
  const dateLocale = locale === 'zh-Hant' ? 'zh-TW' : 'zh-CN'

  return <section className="paper-data-health" aria-labelledby="paper-data-health-title">
    <header><div><Gauge size={17} /><h2 id="paper-data-health-title">{title}</h2></div><span data-state={riskState}>{stateLabel(riskState, locale)}</span></header>
    <dl>
      <div><dt>{accountLabel}</dt><dd>{new Date(account.as_of).toLocaleString(dateLocale)}</dd></div>
      <div><dt>{quoteLabel}</dt><dd data-state={quoteState}>{stateLabel(quoteState, locale)}</dd></div>
      <div><dt>{riskLabel}</dt><dd data-state={riskState}>{stateLabel(riskState, locale)}</dd></div>
      {riskProof && <><div><dt>{computedLabel}</dt><dd>{new Date(riskProof.computed_at).toLocaleString(dateLocale)}</dd></div><div><dt>{marksLabel}</dt><dd>{new Date(riskProof.marks_as_of).toLocaleString(dateLocale)}</dd></div></>}
    </dl>
  </section>
}

function checkIcon(check: PersonalPaperRiskCheck) {
  if (check.status === 'pass') return <CheckCircle2 size={17} />
  if (check.status === 'fail') return <XCircle size={17} />
  if (check.status === 'warn') return <AlertTriangle size={17} />
  return <CircleDashed size={17} />
}

function money(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(value)
}

function quoteMoneyMinor(value: number): string {
  return money(value / 100)
}

export function RiskCheckList({ locale, proof }: { locale: PaperLocale; proof: PersonalPaperRiskProof | null }) {
  const title = locale === 'zh-Hant' ? '風險核對清單' : '风险核对清单'
  const empty = locale === 'zh-Hant' ? '取得報價後產生服務端風險證明。' : '取得报价后生成服务端风险证明。'
  return <section className="paper-risk-checks" aria-labelledby="paper-risk-checks-title">
    <header><ShieldCheck size={17} /><h2 id="paper-risk-checks-title">{title}</h2><span>{proof?.checks.length ?? 0}/7</span></header>
    {!proof ? <p>{empty}</p> : <div className="paper-risk-check-list">{proof.checks.map((check) => {
      const formatted = formatPersonalPaperRiskCheck(check, locale)
      return <article key={check.code} data-status={check.status}>
        {checkIcon(check)}<div><strong>{check.title}</strong><small>{check.detail}</small></div><span>{formatted.value}{formatted.limit ? <small>{formatted.limit}</small> : null}</span>
      </article>
    })}</div>}
  </section>
}

interface OrderPreviewProps {
  locale: PaperLocale
  request: PaperDraftValues
  quote: PersonalPaperQuoteProof | null
  proof: PersonalPaperRiskProof | null
}

export function OrderPreview({ locale, request, quote, proof }: OrderPreviewProps) {
  const hant = locale === 'zh-Hant'
  const unavailable = hant ? '不可用（需重新取得）' : '不可用（需重新获取）'
  const buyingPowerCheck = proof?.checks.find((check) => check.code === 'buying_power')
  const buyingPower = buyingPowerCheck ? parsePersonalPaperRiskCheck(buyingPowerCheck) : null
  const buyingValue = buyingPower?.code === 'buying_power' ? buyingPower.value : null
  const estimatedFunds = buyingValue ? money(buyingValue.required) : unavailable
  const buyingImpact = buyingValue ? `需要 ${money(buyingValue.required)} · 当前可用 ${money(buyingValue.available)}` : unavailable
  const sideLabel = ({ BUY: hant ? '買入' : '买入', SELL: hant ? '賣出' : '卖出', SHORT: hant ? '放空' : '做空', COVER: hant ? '回補' : '回补' })[request.side]
  const orderTypeLabel = ({ MARKET: hant ? '市價' : '市价', LIMIT: hant ? '限價' : '限价', STOP: hant ? '停損觸發' : '止损触发', STOP_LIMIT: hant ? '停損限價' : '止损限价' })[request.orderType]
  const priceCondition = request.orderType === 'MARKET'
    ? (hant ? '市價' : '市价')
    : request.orderType === 'LIMIT'
      ? `${hant ? '限價' : '限价'} ${request.limitPrice || unavailable}`
      : request.orderType === 'STOP'
        ? `${hant ? '停損觸發' : '止损触发'} ${request.stopPrice || unavailable}`
        : `${hant ? '停損觸發' : '止损触发'} ${request.stopPrice || unavailable} · ${hant ? '限價' : '限价'} ${request.limitPrice || unavailable}`
  const markTime = proof ? new Date(proof.marks_as_of).toLocaleString(hant ? 'zh-TW' : 'zh-CN') : unavailable
  const expiresAt = proof ? new Date(proof.expires_at).toLocaleString(hant ? 'zh-TW' : 'zh-CN') : unavailable
  const quoteTime = quote ? new Date(quote.quote_at).toLocaleString(hant ? 'zh-TW' : 'zh-CN') : unavailable
  const quoteExpiresAt = quote ? new Date(quote.expires_at).toLocaleString(hant ? 'zh-TW' : 'zh-CN') : unavailable
  const quoteSession = quote?.session ?? '未提供'
  const title = hant ? '訂單預覽' : '订单预览'
  return <section className="paper-order-preview" aria-labelledby="paper-order-preview-title">
    <header><span>ORDER PREVIEW</span><h2 id="paper-order-preview-title">{title}</h2></header>
    <dl>
      <div><dt>股票</dt><dd>{request.symbol || unavailable}</dd></div>
      <div><dt>{hant ? '方向 / 數量' : '方向 / 数量'}</dt><dd>{sideLabel} · {request.quantity || unavailable}</dd></div>
      <div><dt>{hant ? '訂單類型 / 價格條件' : '订单类型 / 价格条件'}</dt><dd>{orderTypeLabel} · {priceCondition}</dd></div>
      <div><dt>{hant ? '帳本標記時間 / 風險證明到期' : '账本标记时间 / 风险证明到期'}</dt><dd>{markTime} · {expiresAt}</dd></div>
      <div><dt>{hant ? '買 / 賣 / 最新報價' : '买 / 卖 / 最新报价'}</dt><dd>{quote ? `${quoteMoneyMinor(quote.bid_minor)} · ${quoteMoneyMinor(quote.ask_minor)} · ${quoteMoneyMinor(quote.last_minor)}` : unavailable}</dd></div>
      <div><dt>{hant ? '報價時間 / 報價到期' : '报价时间 / 报价到期'}</dt><dd>{quoteTime} · {quoteExpiresAt}</dd></div>
      <div><dt>{hant ? '新鮮度 / 來源 / 時段' : '新鲜度 / 来源 / 时段'}</dt><dd>{quote ? `${quote.freshness} · ${quote.source} · ${quoteSession}` : unavailable}</dd></div>
      <div><dt>{hant ? '預計資金佔用（含費用）' : '预计资金占用（含费用）'}</dt><dd>{estimatedFunds}</dd></div>
      <div><dt>{hant ? '費用 / 購買力影響' : '费用 / 购买力影响'}</dt><dd>{hant ? '費用未單列' : '费用未单列'} · {buyingImpact}</dd></div>
      <div><dt>{hant ? '報價證明' : '报价证明'}</dt><dd>{quote ? `${quote.market} · ${quote.symbol} · ${quote.quote_id}` : unavailable}</dd></div>
    </dl>
  </section>
}

interface DecisionSummaryProps {
  locale: PaperLocale
  proof: PersonalPaperRiskProof | null
  side: PersonalPaperSide
  expired: boolean
}

export function DecisionSummary({ locale, proof, side, expired }: DecisionSummaryProps) {
  const isHant = locale === 'zh-Hant'
  const title = isHant ? '確認前決策摘要' : '确认前决策摘要'
  const noProof = isHant ? '尚未產生風險證明，不能確認提交。' : '尚未生成风险证明，不能确认提交。'
  const fingerprint = isHant ? '證據指紋' : '证据指纹'
  const decision = !proof ? 'unknown' : expired ? 'expired' : proof.decision
  const decisionText = isHant
    ? { allow: '允許確認', review: '檢視後可確認', reject: '拒絕提交', expired: '證明已過期', unknown: '等待證明' }
    : { allow: '允许确认', review: '复核后可确认', reject: '拒绝提交', expired: '证明已过期', unknown: '等待证明' }
  const maxLoss = side === 'SHORT'
    ? (isHant ? '理論最大虧損：無限' : '理论最大亏损：无限')
    : (() => {
      const check = proof?.checks.find((item) => item.code === 'max_loss')
      return check ? formatPersonalPaperRiskCheck(check, locale).value : '—'
    })()

  return <section className="paper-decision-summary" data-decision={decision} aria-labelledby="paper-decision-title">
    <header><FileCheck2 size={18} /><div><small>DECISION SUMMARY</small><h2 id="paper-decision-title">{title}</h2></div><strong>{decisionText[decision]}</strong></header>
    {!proof ? <p>{noProof}</p> : <>
      <dl><div><dt>{isHant ? '風險等級' : '风险等级'}</dt><dd>{proof.risk_level}</dd></div><div><dt>{isHant ? '最大虧損' : '最大亏损'}</dt><dd>{maxLoss}</dd></div><div><dt>{isHant ? '有效期限' : '有效期限'}</dt><dd><Clock3 size={14} />{new Date(proof.expires_at).toLocaleTimeString(isHant ? 'zh-TW' : 'zh-CN')}</dd></div></dl>
      {proof.blocking_reasons.length > 0 && <ul className="paper-blocking-reasons">{proof.blocking_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
      {proof.warnings.length > 0 && <ul className="paper-warning-reasons">{proof.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
      <div className="paper-proof-fingerprint"><span>{fingerprint}</span><code title={proof.proof_sha256}>{proof.proof_sha256.slice(0, 12)}…</code></div>
    </>}
  </section>
}

export function PaperRefreshButton({ label, busy, disabled = false, onClick }: { label: string; busy: boolean; disabled?: boolean; onClick: () => void }) {
  return <button className="icon-button paper-refresh-button" type="button" aria-label={label} disabled={busy || disabled} onClick={onClick}><RefreshCw size={17} className={busy ? 'spin' : ''} /></button>
}
