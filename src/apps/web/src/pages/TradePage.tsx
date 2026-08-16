import {
  ArrowRight,
  AlertTriangle,
  Building2,
  Cable,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  FileCheck2,
  HelpCircle,
  LockKeyhole,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Unplug,
  WalletCards,
} from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import type { BrokerCatalogEntry } from '../api/client'
import { autoLiveApi, validAutoLivePauseResult, type AutoLiveConfirmationInput, type AutoLiveGate, type AutoLiveMandate, type AutoLivePauseResult, type AutoLiveSnapshot } from '../api/autoLive'
import { brokerAccessApi, type BrokerAccessApplication, type BrokerAccessReadiness, type BrokerProvider, isBrokerAccessRejection } from '../api/brokerAccess'
import { useWorkspace } from '../api/workspace-context'
import { PageHeader } from '../components/PageHeader'
import { createSessionIdempotencyRegistry } from '../domain/sessionIdempotency'

const usMarketDetail: {
  label: string
  currency: string
  execution: string
  shorting: string
} = {
  label: '美股',
  currency: 'USD',
  execution: '首期范围只覆盖美股券商接入准备；实盘能力必须通过服务端 mandate 与独立门控，当前账户未获得可执行授权。',
  shorting: '未来是否可做空仍由券商保证金、可借券和账户权限决定，CicloTrade 不伪造可借券状态。',
}

const capabilityLabels: Record<BrokerCatalogEntry['capabilities'][number], string> = {
  market_data: '平台侧行情',
  us_stock_limit_orders: '受限美股限价单后端',
}

const BROKER_ACCESS_PENDING_KEY = 'ciclotrade.brokerAccessPending'
interface BrokerAccessPendingIntent { provider: BrokerProvider; requestReason: string; idempotencyKey: string }
function readPendingIntent(): BrokerAccessPendingIntent | null {
  try {
    const value: unknown = JSON.parse(window.sessionStorage.getItem(BROKER_ACCESS_PENDING_KEY) ?? 'null')
    if (!value || typeof value !== 'object') return null
    const candidate = value as Partial<BrokerAccessPendingIntent>
    return typeof candidate.provider === 'string' && typeof candidate.requestReason === 'string' && typeof candidate.idempotencyKey === 'string'
      ? { provider: candidate.provider as BrokerProvider, requestReason: candidate.requestReason, idempotencyKey: candidate.idempotencyKey }
      : null
  } catch { return null }
}
function brokerAccessIntent(provider: BrokerProvider, requestReason: string, existing: BrokerAccessPendingIntent | null): BrokerAccessPendingIntent {
  return existing?.provider === provider && existing.requestReason === requestReason
    ? existing
    : { provider, requestReason, idempotencyKey: `broker-${crypto.randomUUID()}` }
}

function displayMandateState(state: AutoLiveMandate['state']): string {
  return { draft: '草稿', pending_confirmation: '待精确确认', active: '已激活', paused: '已暂停', blocked: '被门控阻断', expired: '已过期', revoked: '已撤销' }[state]
}

function latestReceiptEpoch(snapshot: AutoLiveSnapshot, mandatePublicId: string): number {
  const startEpochs = snapshot.start_receipts.filter((item) => item.mandate_public_id === mandatePublicId && typeof item.fencing_epoch === 'number').map((item) => item.fencing_epoch as number)
  const pauseEpochs = snapshot.pause_receipts.flatMap((item) => item.receipt.target_details ?? []).filter((item) => item.target_public_id === mandatePublicId).map((item) => item.fencing_epoch)
  return Math.max(0, ...startEpochs, ...pauseEpochs)
}

type AutoLiveReadState = 'idle' | 'loading' | 'fresh' | 'stale'

function AutoLiveControlPanel({ authenticated, onSnapshotState }: { authenticated: boolean; onSnapshotState: (snapshot: AutoLiveSnapshot | null, state: AutoLiveReadState) => void }) {
  const [snapshot, setSnapshot] = useState<AutoLiveSnapshot | null>(null)
  const [snapshotState, setSnapshotState] = useState<AutoLiveReadState>(authenticated ? 'loading' : 'idle')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [confirmation, setConfirmation] = useState<AutoLiveConfirmationInput | null>(null)
  const [gateResults, setGateResults] = useState<Record<string, { all_ok: boolean; gates: AutoLiveGate[] }>>({})
  const [lastPause, setLastPause] = useState<AutoLivePauseResult | null>(null)
  const [revokeReason] = useState('用户主动撤销自动实盘授权')
  const [mandateDraft, setMandateDraft] = useState({ broker_account_public_id: '', strategy_version: '', risk_version: '', capital_limit_minor: '', frequency_limit: '', valid_from: '', valid_until: '' })
  const idempotency = useRef(createSessionIdempotencyRegistry('ciclotrade.autoLivePending.v1'))

  const refresh = useCallback(async () => {
    if (!authenticated) { setSnapshot(null); setSnapshotState('idle'); onSnapshotState(null, 'idle'); return }
    setSnapshotState('loading'); onSnapshotState(null, 'loading')
    try { const next = await autoLiveApi.snapshot(); setSnapshot(next); setSnapshotState('fresh'); onSnapshotState(next, 'fresh') } catch { setSnapshotState('stale'); onSnapshotState(null, 'stale') }
  }, [authenticated, onSnapshotState])
  useEffect(() => { void refresh() }, [refresh])

  const run = async (key: string, action: () => Promise<unknown>, success: string) => {
    setBusy(key); setNotice('')
    try {
      const result = await action()
      if (result && typeof result === 'object' && !Array.isArray(result)) {
        const candidate = result as { all_ok?: unknown; gates?: unknown; mandate_public_id?: unknown }
        if (typeof candidate.mandate_public_id === 'string' && typeof candidate.all_ok === 'boolean' && Array.isArray(candidate.gates) && candidate.gates.every((item) => item && typeof item === 'object' && typeof (item as { name?: unknown }).name === 'string' && typeof (item as { ok?: unknown }).ok === 'boolean' && typeof (item as { reason?: unknown }).reason === 'string')) {
          const mandatePublicId = candidate.mandate_public_id
          const allOk = candidate.all_ok
          const gates = candidate.gates as AutoLiveGate[]
          setGateResults((current) => ({ ...current, [mandatePublicId]: { all_ok: allOk, gates } }))
        }
      }
      if (validAutoLivePauseResult(result)) setLastPause(result)
      setNotice(success); await refresh()
    } catch (error) { setNotice(error instanceof Error ? error.message : '自动实盘请求未确认，请刷新收据。') }
    finally { setBusy('') }
  }

  const requestConfirmation = (mandate: AutoLiveMandate) => void run(`confirm-request:${mandate.public_id}`, async () => {
    const result = await autoLiveApi.requestConfirmation(mandate.public_id)
    if (typeof result.confirmation_phrase === 'string' && typeof result.confirmation_snapshot_sha256 === 'string') setConfirmation({ mandate_public_id: mandate.public_id, confirmation_phrase: '', snapshot_sha256: result.confirmation_snapshot_sha256 })
  }, '服务端已生成精确确认内容；请完成审阅后输入完整短语。')

  const confirmMandate = (mandate: AutoLiveMandate) => {
    if (!confirmation || confirmation.mandate_public_id !== mandate.public_id || confirmation.confirmation_phrase.trim() !== `ACTIVATE ${mandate.public_id}`) { setNotice(`请精确输入：ACTIVATE ${mandate.public_id}`); return }
    void run(`confirm:${mandate.public_id}`, () => autoLiveApi.confirm(mandate.public_id, confirmation), 'mandate 已确认；运行前仍需通过全部独立门控。').then(() => setConfirmation(null))
  }

  const createMandate = (event: FormEvent) => {
    event.preventDefault()
    const values = mandateDraft
    const toIso = (input: string) => { const parsed = new Date(input); return Number.isNaN(parsed.getTime()) ? input : parsed.toISOString() }
    const scope = 'create-mandate'
    const request = { broker_account_public_id: values.broker_account_public_id, strategy_version: values.strategy_version, risk_version: values.risk_version, capital_limit_minor: Number(values.capital_limit_minor), frequency_limit: Number(values.frequency_limit), valid_from: toIso(values.valid_from), valid_until: toIso(values.valid_until) }
    const fingerprint = JSON.stringify(request)
    void run('create', async () => { const result = await autoLiveApi.createMandate(request, idempotency.current.key(scope, fingerprint)); idempotency.current.clear(scope, fingerprint); return result }, 'mandate 草稿已创建，请审阅服务端快照。')
  }

  if (!authenticated) return <section className="data-panel auto-live-panel" aria-labelledby="auto-live-title"><header className="panel-heading"><div><span>AUTO-LIVE / LOCKED</span><h2 id="auto-live-title">自动实盘控制</h2></div><LockKeyhole size={20} /></header><p className="admin-panel-note">登录后才能读取服务端 mandate、门控与运行收据。当前不显示任何连接或运行状态。</p></section>

  return <section className="data-panel auto-live-panel" aria-labelledby="auto-live-title">
    <header className="panel-heading"><div><span>AUTO-LIVE / SERVER-BOUND CONTROL</span><h2 id="auto-live-title">自动实盘控制</h2></div><button className="button tertiary" type="button" onClick={() => void refresh()} disabled={busy !== ''}><RefreshCw size={15} />重新读取</button></header>
    <p className="admin-panel-note">仅服务端返回的 broker public ref 可创建 mandate。券商授权、Telegram、策略/风险合同、数据健康和 kill-switch 是独立必要门；自然语言 AI 永远不能下单。</p>
    {snapshotState === 'stale' && <p className="auto-live-notice risk" role="alert">自动实盘接口暂不可用；最近快照已标记过期。创建、确认、恢复与启动已锁定，暂停和撤销仍保留。</p>}
    {notice && <p className="auto-live-notice" role="status">{notice}</p>}
    {snapshot && <>
      <div className="auto-live-summary"><span><strong>{snapshot.mandates.length}</strong> 个 mandate</span><span><strong>{snapshot.runtime_projections.length}</strong> 个 runtime projection</span><span><strong>{snapshot.start_receipts.length + snapshot.pause_receipts.length}</strong> 条控制收据</span><span><strong>{snapshot.order_receipts.length}</strong> 条订单收据</span></div>
      {snapshot.broker_accounts.length > 0 && <div className="auto-live-broker-controls"><header><strong>券商级安全控制</strong><small>仅对服务端返回的 broker public ref 操作；未返回的券商保持 locked/disconnected。</small></header>{snapshot.broker_accounts.map((broker) => <div className="auto-live-broker-row" key={broker.public_id}><span><strong>{broker.provider}</strong><small>{broker.status} · {broker.public_id}</small></span><button className="button danger" type="button" onClick={() => { const scope = `pause-broker-${broker.public_id}`; const request = { scope: 'broker' as const, broker_account_public_id: broker.public_id }; const fingerprint = JSON.stringify(request); void run(`pause-broker:${broker.public_id}`, async () => { const result = await autoLiveApi.pause(request, idempotency.current.key(scope, fingerprint)); idempotency.current.clear(scope, fingerprint); return result }, '券商级暂停收据已返回；partial 仅代表部分目标确认') }} disabled={busy !== ''}><Pause size={15} />暂停此券商</button></div>)}</div>}
      {lastPause && <div className={`auto-live-pause-receipt ${lastPause.status === 'partial' ? 'is-partial' : ''}`} role="status"><strong>最近暂停收据：{lastPause.status}</strong><span>{lastPause.confirmed}/{lastPause.total} 个目标已确认</span>{lastPause.unconfirmed.length > 0 && <small>未确认：{lastPause.unconfirmed.join('、')}</small>}</div>}
      {snapshot.order_receipts.length > 0 && <section className="auto-live-order-receipts" aria-label="订单收据"><header><strong>订单收据</strong><small>SUBMISSION_UNKNOWN 与 runtime 状态分离展示</small></header>{snapshot.order_receipts.map((receipt) => <div key={receipt.public_id}><span>{receipt.client_order_id}</span><strong className={receipt.submission_state === 'submission_unknown' ? 'risk' : ''}>{receipt.submission_state}</strong><small>{receipt.observed_at}</small></div>)}</section>}
      {snapshot.broker_accounts.length > 0 && <form className="auto-live-mandate-form" onSubmit={createMandate}><label>服务端 broker ref<select value={mandateDraft.broker_account_public_id} onChange={(event) => setMandateDraft((current) => ({ ...current, broker_account_public_id: event.target.value }))}><option value="">选择已返回的 broker ref</option>{snapshot.broker_accounts.map((broker) => <option value={broker.public_id} key={broker.public_id}>{broker.provider} · {broker.status} · {broker.public_id}</option>)}</select></label><label>策略版本<input value={mandateDraft.strategy_version} onChange={(event) => setMandateDraft((current) => ({ ...current, strategy_version: event.target.value }))} placeholder="服务端批准版本" required /></label><label>风险版本<input value={mandateDraft.risk_version} onChange={(event) => setMandateDraft((current) => ({ ...current, risk_version: event.target.value }))} placeholder="服务端批准版本" required /></label><label>资本上限（minor）<input type="number" min="1" value={mandateDraft.capital_limit_minor} onChange={(event) => setMandateDraft((current) => ({ ...current, capital_limit_minor: event.target.value }))} required /></label><label>频率上限<input type="number" min="1" value={mandateDraft.frequency_limit} onChange={(event) => setMandateDraft((current) => ({ ...current, frequency_limit: event.target.value }))} required /></label><label>有效开始<input type="datetime-local" value={mandateDraft.valid_from} onChange={(event) => setMandateDraft((current) => ({ ...current, valid_from: event.target.value }))} required /></label><label>有效结束<input type="datetime-local" value={mandateDraft.valid_until} onChange={(event) => setMandateDraft((current) => ({ ...current, valid_until: event.target.value }))} required /></label><button className="button primary" type="submit" disabled={busy !== '' || snapshotState !== 'fresh' || !mandateDraft.broker_account_public_id}><Play size={15} />创建 mandate 草稿</button></form>}
      {!snapshot.broker_accounts.length && <p className="auto-live-locked-note"><LockKeyhole size={15} />服务端尚未返回可用 broker public ref；五家券商仍显示为 locked/disconnected，不能创建 mandate。</p>}
      <div className="auto-live-mandates">{snapshot.mandates.length ? snapshot.mandates.map((mandate) => {
        const runtime = snapshot.runtime_projections.find((item) => item.mandate_public_id === mandate.public_id)
        const heartbeat = snapshot.heartbeat_projections.find((item) => item.mandate_public_id === mandate.public_id)
        const epoch = latestReceiptEpoch(snapshot, mandate.public_id)
        const gateResult = gateResults[mandate.public_id]
        return <article className="auto-live-mandate" key={mandate.public_id}><header><div><strong>{mandate.public_id}</strong><small>{mandate.strategy_version} · {mandate.risk_version}</small></div><span className={`auto-live-state state-${mandate.state}`}>{displayMandateState(mandate.state)}</span></header><dl><div><dt>券商 ref</dt><dd>{mandate.broker_account_public_id ?? '未返回'}</dd></div><div><dt>资本 / 频率</dt><dd>{mandate.capital_limit_minor} / {mandate.frequency_limit}</dd></div><div><dt>runtime / heartbeat</dt><dd>{runtime?.state ?? '未提供'} / {heartbeat?.heartbeat_state ?? '未提供'}</dd></div><div><dt>runtime error</dt><dd>{runtime?.last_error_code ?? '无'}</dd></div><div><dt>fencing epoch</dt><dd>{epoch} · 收据推导</dd></div></dl>{gateResult && <div className="auto-live-gates"><strong>{gateResult.all_ok ? '所有门控通过' : '门控未全部通过'}</strong>{gateResult.gates.map((gate) => <span className={gate.ok ? 'ok' : 'blocked'} key={gate.name}>{gate.name} · {gate.ok ? '通过' : '阻断'} · {gate.reason}</span>)}</div>}<div className="auto-live-actions">{mandate.state === 'pending_confirmation' ? <><input aria-label={`确认 ${mandate.public_id}`} value={confirmation?.mandate_public_id === mandate.public_id ? confirmation.confirmation_phrase : ''} onChange={(event) => setConfirmation({ mandate_public_id: mandate.public_id, snapshot_sha256: confirmation?.snapshot_sha256 ?? mandate.snapshot_sha256, confirmation_phrase: event.target.value })} placeholder={`ACTIVATE ${mandate.public_id}`} /><button className="button primary" type="button" onClick={() => confirmMandate(mandate)} disabled={busy !== '' || snapshotState !== 'fresh'}><CheckCircle2 size={15} />精确确认</button></> : (mandate.state === 'draft' || mandate.state === 'paused' || mandate.state === 'blocked') && <button className="button primary" type="button" onClick={() => mandate.state === 'draft' ? requestConfirmation(mandate) : void run(`resume:${mandate.public_id}`, () => autoLiveApi.resume(mandate.public_id), '恢复请求已提交；请按服务端返回继续确认')} disabled={busy !== '' || snapshotState !== 'fresh'}><RotateCcw size={15} />{mandate.state === 'draft' ? '审阅并确认' : '请求恢复'}</button>}{mandate.state === 'active' && !['starting', 'running'].includes(runtime?.state ?? '') && <button className="button primary" type="button" onClick={() => { const scope = `start-${mandate.public_id}`; const request = { expected_fencing_epoch: epoch }; const fingerprint = JSON.stringify({ mandate_public_id: mandate.public_id, ...request }); void run(`start:${mandate.public_id}`, async () => { const result = await autoLiveApi.start(mandate.public_id, request, idempotency.current.key(scope, fingerprint)); idempotency.current.clear(scope, fingerprint); return result }, '启动请求已提交；等待 runtime 收据') }} disabled={busy !== '' || snapshotState !== 'fresh'}><Play size={15} />幂等启动</button>}{mandate.state === 'active' && ['starting', 'running'].includes(runtime?.state ?? '') && <span className="auto-live-locked-note">runtime {runtime?.state}；已隐藏重复启动</span>}{!['expired', 'revoked'].includes(mandate.state) && <><button className="button danger" type="button" onClick={() => { const scope = `pause-${mandate.public_id}`; const request = { scope: 'mandate' as const, mandate_public_id: mandate.public_id }; const fingerprint = JSON.stringify(request); void run(`pause:${mandate.public_id}`, async () => { const result = await autoLiveApi.pause(request, idempotency.current.key(scope, fingerprint)); idempotency.current.clear(scope, fingerprint); return result }, '暂停收据已返回；partial 不代表全部暂停') }} disabled={busy !== ''}><Pause size={15} />暂停 mandate</button><button className="button tertiary" type="button" onClick={() => void run(`revoke:${mandate.public_id}`, () => autoLiveApi.revoke(mandate.public_id, revokeReason), 'mandate 已撤销')} disabled={busy !== ''}>撤销</button></>}</div></article>
      }) : <p className="auto-live-locked-note">暂无服务端 mandate；没有真实连接或运行对象可供宣称。</p>}</div>
    </>}
    <footer className="auto-live-boundary"><AlertTriangle size={15} /><span>自然语言 AI 只能提供结构化解释与草稿；只有通过全部门控、精确确认和幂等启动的 AutoExecution 引擎才可执行。</span></footer>
  </section>
}

export function TradePage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const workspace = useWorkspace()
  const symbol = searchParams.get('symbol')?.toUpperCase()
  const eventId = searchParams.get('event_id')
  const authenticated = workspace.mode === 'authenticated'
  const brokerCatalog = useMemo(() => workspace.data?.membership.brokerage.capability_catalog ?? [], [workspace.data?.membership.brokerage.capability_catalog])
  const [applications, setApplications] = useState<BrokerAccessApplication[]>([])
  const [brokerReadiness, setBrokerReadiness] = useState<BrokerAccessReadiness | null>(null)
  const [readinessLoading, setReadinessLoading] = useState(authenticated)
  const [selectedProvider, setSelectedProvider] = useState<BrokerProvider | ''>('')
  const [requestReason, setRequestReason] = useState('')
  const [requestState, setRequestState] = useState<string | null>(null)
  const [loadingApplications, setLoadingApplications] = useState(false)
  const [applicationLoadError, setApplicationLoadError] = useState(false)
  const [autoLiveBrokerSummary, setAutoLiveBrokerSummary] = useState<{ count: number | null; state: AutoLiveReadState }>({ count: null, state: authenticated ? 'loading' : 'idle' })
  const pendingIntent = useRef<BrokerAccessPendingIntent | null>(readPendingIntent())
  const availableProviders = useMemo(
    () => brokerReadiness?.can_apply ? brokerCatalog.filter((broker) => brokerReadiness.providers.includes(broker.key as BrokerProvider)) : [],
    [brokerCatalog, brokerReadiness],
  )
  const receiveAutoLiveSnapshotState = useCallback((snapshot: AutoLiveSnapshot | null, state: AutoLiveReadState) => {
    setAutoLiveBrokerSummary((current) => ({ count: snapshot ? snapshot.broker_accounts.length : current.count, state }))
  }, [])

  const loadApplications = useCallback(async () => {
    if (!authenticated) return
    setLoadingApplications(true)
    setApplicationLoadError(false)
    try { setApplications(await brokerAccessApi.list()) } catch { setApplicationLoadError(true) }
    finally { setLoadingApplications(false) }
  }, [authenticated])

  const loadBrokerReadiness = useCallback(async () => {
    if (!authenticated) { setBrokerReadiness(null); setReadinessLoading(false); return }
    setReadinessLoading(true)
    try { setBrokerReadiness(await brokerAccessApi.readiness()) } catch { setBrokerReadiness(null) }
    finally { setReadinessLoading(false) }
  }, [authenticated])

  useEffect(() => { void loadApplications(); void loadBrokerReadiness() }, [loadApplications, loadBrokerReadiness])

  async function submitAccessRequest(event: FormEvent) {
    event.preventDefault()
    if (!selectedProvider) return
    setRequestState(null)
    try {
      const intent = brokerAccessIntent(selectedProvider, requestReason.trim(), pendingIntent.current)
      pendingIntent.current = intent
      try { window.sessionStorage.setItem(BROKER_ACCESS_PENDING_KEY, JSON.stringify(intent)) } catch { /* session storage may be disabled */ }
      const result = await brokerAccessApi.create(intent.provider, intent.requestReason || null, intent.idempotencyKey)
      pendingIntent.current = null
      try { window.sessionStorage.removeItem(BROKER_ACCESS_PENDING_KEY) } catch { /* session storage may be disabled */ }
      setApplications((current) => [result.application, ...current.filter((item) => item.id !== result.application.id)])
      setRequestReason('')
      setRequestState(result.replayed ? '已恢复上次相同申请。' : '申请已提交，等待人工审核。')
    } catch (error) {
      if (isBrokerAccessRejection(error)) {
        pendingIntent.current = null
        try { window.sessionStorage.removeItem(BROKER_ACCESS_PENDING_KEY) } catch { /* session storage may be disabled */ }
        setRequestState((error as Error).message)
      } else setRequestState('网络响应未确认；相同申请正文会复用原请求编号安全重试。')
    }
  }

  async function withdrawAccessRequest(item: BrokerAccessApplication) {
    if (item.status !== 'submitted') return
    setRequestState(null)
    try {
      const updated = await brokerAccessApi.withdraw(item.id)
      setApplications((current) => current.map((candidate) => candidate.id === updated.id ? updated : candidate))
      setRequestState('资格申请已撤回。')
    } catch (error) {
      setRequestState(isBrokerAccessRejection(error) ? (error as Error).message : '撤回结果未确认，请刷新资格历史。')
    }
  }

  return (
    <div className="page operations-page brokerage-page">
      <PageHeader kicker="BROKERAGE / LIVE SERVICE" title="券商实盘连接" description="CicloTrade 提供实盘连接服务。会员订阅与券商连接是两条独立流程；只有你主动连接并授权个人券商后，系统才可能进入真实执行。" />

      <section className="brokerage-status-band" aria-label="券商连接状态">
        <span className="brokerage-status-icon"><Unplug size={21} /></span>
        <div><span>当前账户</span><strong>{authenticated ? workspace.user?.display_name : '尚未登录'} · {authenticated ? autoLiveBrokerSummary.state === 'loading' ? '券商状态读取中' : autoLiveBrokerSummary.state === 'stale' ? autoLiveBrokerSummary.count === null ? '券商状态读取失败' : `${autoLiveBrokerSummary.count} 个最近记录 · 状态已过期` : autoLiveBrokerSummary.count && autoLiveBrokerSummary.count > 0 ? `${autoLiveBrokerSummary.count} 个服务端受控券商记录` : '未返回已连接券商' : '未连接券商'}</strong><small>网页不显示或保存券商凭据；连接与运行状态只采用服务端受控快照。</small></div>
        <span className="status-chip research"><ShieldCheck size={14} /> 五家目录 · 服务端授权独立</span>
        <button className="button primary" type="button" disabled><LockKeyhole size={16} /> 暂未开放绑定</button>
      </section>

      <AutoLiveControlPanel authenticated={authenticated} onSnapshotState={receiveAutoLiveSnapshotState} />

      {authenticated && <section className="data-panel brokerage-access-panel" aria-labelledby="broker-access-title">
        <header className="panel-heading"><div><span>ELIGIBILITY / HUMAN REVIEW</span><h2 id="broker-access-title">券商资格申请</h2></div><FileCheck2 size={20} /></header>
        <p className="admin-panel-note">申请只记录资格审核，不创建券商账户、不启用执行，也不会发送 Telegram。连接可用前不会显示“已连接”或“运行中”。</p>
        {readinessLoading ? <p className="admin-panel-note">正在核对会员与 Telegram 资格…</p> : availableProviders.length ? <form className="brokerage-access-form" onSubmit={submitAccessRequest}><label>券商<select value={selectedProvider} onChange={(event) => setSelectedProvider(event.target.value as BrokerProvider)}><option value="">选择券商</option>{availableProviders.map((broker) => <option value={broker.key} key={broker.key}>{broker.display_name}</option>)}</select></label><label>申请原因（可选）<textarea value={requestReason} maxLength={500} onChange={(event) => setRequestReason(event.target.value)} rows={2} /></label><button className="button primary" type="submit" disabled={!selectedProvider}>提交资格申请</button></form> : <p className="admin-panel-note" role={brokerReadiness ? 'status' : 'alert'}>{brokerReadiness?.reason ?? '资格准备状态暂时无法读取；申请入口已安全锁定。'}</p>}
        {requestState && <p role="status" className="admin-panel-note">{requestState}</p>}
        {loadingApplications ? <p className="admin-panel-note">正在读取资格历史…</p> : applicationLoadError ? <p className="admin-panel-note" role="alert">资格历史暂时无法读取，未将失败当作空历史。<button className="button tertiary" type="button" onClick={() => void loadApplications()}>重新读取</button></p> : applications.length ? <ul className="brokerage-access-history">{applications.map((item) => <li key={item.id}><span><strong>{item.provider}</strong><small>{item.id} · {item.created_at}</small></span><span className={`admin-state ${item.status === 'approved' ? 'healthy' : item.status === 'rejected' ? 'risk' : 'pending'}`}>{item.status}</span>{item.status === 'submitted' && <button className="button tertiary" type="button" onClick={() => void withdrawAccessRequest(item)}>撤回申请</button>}</li>)}</ul> : <p className="admin-panel-note">暂无资格申请历史。</p>}
      </section>}

      {(symbol || eventId) && <section className="brokerage-context-note"><FileCheck2 size={17} /><span><strong>你从一条研究或验证记录来到这里</strong><small>{symbol ? `${symbol} · ` : ''}{eventId ? `事件 QE-${eventId} · ` : ''}本页不会把它转换成模拟订单或自动发送到券商。</small></span><button className="button tertiary" type="button" onClick={() => navigate('/portfolio')}>查看模拟验证结果</button></section>}

      <section className="brokerage-market-panel data-panel">
        <header className="panel-heading"><div><span>MARKET CAPABILITY</span><h2>市场与账户能力</h2></div><Building2 size={20} /></header>
        <div className="brokerage-market-controls"><strong>美股首发范围</strong><span>{usMarketDetail.currency} 独立账户视图</span></div>
        <div className="brokerage-capability-grid">
          <article><CircleDollarSign size={18} /><span><strong>{usMarketDetail.label}执行范围</strong><small>{usMarketDetail.execution}</small></span></article>
          <article><ShieldCheck size={18} /><span><strong>做空规则</strong><small>{usMarketDetail.shorting}</small></span></article>
          <article><LockKeyhole size={18} /><span><strong>权限来源</strong><small>会员只决定研究、数据、提醒与回测权益；真实交易权限来自你的券商账户。</small></span></article>
        </div>
      </section>

      <section className="brokerage-catalog-panel data-panel" aria-labelledby="broker-catalog-title">
        <header className="panel-heading"><div><span>US BROKER LAUNCH CATALOG</span><h2 id="broker-catalog-title">首期美股券商列表</h2></div><Building2 size={20} /></header>
        <div className="brokerage-catalog-summary">
          <span><ShieldCheck size={16} /> 逐家显示 source-bound catalog / connection</span>
          <small>A 股券商及其他候补平台全部后置。会员资格也不会自动开通任何券商连接。</small>
        </div>
        <div className="brokerage-catalog-grid">
          {brokerCatalog.length ? brokerCatalog.map((broker) => (
            <article className={`brokerage-provider-card status-${broker.status}`} key={broker.key}>
              <header>
                <span className="brokerage-provider-mark" aria-hidden="true">{broker.display_name.slice(0, 1)}</span>
                <div><strong>{broker.display_name}</strong><small>美股首发范围</small></div>
                <span className="brokerage-provider-status"><Clock3 size={13} />{broker.status_label}</span>
              </header>
              <p>{broker.availability_detail}</p>
              <div className="brokerage-provider-capabilities">
                {broker.capabilities.length
                  ? broker.capabilities.map((capability) => <span key={capability}>{capabilityLabels[capability]}</span>)
                  : <span>尚无可公开能力</span>}
              </div>
              <footer><LockKeyhole size={14} /><span>{broker.key === 'tiger' ? '有限执行边界；仍需服务端授权，未宣称已连接' : '未开放绑定；locked/disconnected'}</span></footer>
            </article>
          )) : (
            <div className="brokerage-catalog-empty"><Unplug size={20} /><span>券商目录暂未取得，请稍后刷新。页面不会用演示状态代替真实接入能力。</span></div>
          )}
        </div>
      </section>

      <section className="brokerage-workflow">
        <article className="data-panel brokerage-steps"><header className="panel-heading"><div><span>CONNECTION WORKFLOW</span><h2>接入流程</h2></div><Cable size={20} /></header><ol><li><span>01</span><div><strong>选择个人券商账户</strong><small>确认市场、币种、保证金账户和 API/终端支持范围。</small></div></li><li><span>02</span><div><strong>由你主动授权连接</strong><small>凭据不通过会员付款自动获得，也不会从本机应用静默读取。</small></div></li><li><span>03</span><div><strong>先完成权限与风险核对</strong><small>检查账户状态、订单权限、可借券、数量、价格和最大风险。</small></div></li><li><span>04</span><div><strong>明确确认后才进入真实执行</strong><small>正式接入后仍需逐笔确认或使用你明确配置的受控规则。</small></div></li></ol></article>

        <aside className="data-panel brokerage-boundary"><header className="panel-heading"><div><span>WHAT YOU CAN DO NOW</span><h2>当前可用入口</h2></div><CheckCircle2 size={20} /></header><div className="brokerage-link-list"><button type="button" onClick={() => navigate('/portfolio')}><WalletCards size={17} /><span><strong>CicloTrade模拟持仓及建议</strong><small>只读查看官方模拟记录、当前持仓与已平仓结果。</small></span><ArrowRight size={16} /></button><button type="button" onClick={() => navigate('/reports')}><FileCheck2 size={17} /><span><strong>CicloTrade模拟验证结果</strong><small>查看报告、假设、数据来源和验证状态。</small></span><ArrowRight size={16} /></button><button type="button" onClick={() => navigate('/help')}><HelpCircle size={17} /><span><strong>实盘接入服务</strong><small>确认支持范围、接入方式、时间与风险边界。</small></span><ArrowRight size={16} /></button></div></aside>
      </section>
    </div>
  )
}
