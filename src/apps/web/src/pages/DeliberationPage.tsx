import { ArrowRight, BookOpenCheck, ChartCandlestick, FileQuestion, LoaderCircle, RefreshCw, RotateCcw, Scale, ShieldAlert, Square } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { classifyDeliberationError, deliberationApi, type DeliberationBinding, type DeliberationReadiness, type DeliberationResult, type DeliberationSeatName } from '../api/deliberation'
import { workflowApi, type WorkflowTask } from '../api/workflows'
import { CicloCore } from '../components/paper/CicloCore'
import { EvidenceStrength, TruthState } from '../components/intelligence/IntelligencePrimitives'
import '../styles/intelligence.css'

const SEATS: Array<{ key: DeliberationSeatName; label: string; Icon: typeof BookOpenCheck }> = [
  { key: 'market_structure', label: '市场结构研究员', Icon: ChartCandlestick },
  { key: 'fundamentals', label: '基本面研究员', Icon: BookOpenCheck },
  { key: 'news_macro', label: '新闻宏观研究员', Icon: Scale },
  { key: 'risk', label: '风险研究员', Icon: ShieldAlert },
]

type PageState = { kind: 'loading' } | { kind: 'missing'; detail: string } | { kind: 'error'; detail: string } | { kind: 'ready'; data: DeliberationReadiness | DeliberationResult; workflow: WorkflowTask | null }
const STATUS_LABEL: Record<string, string> = { queued: 'queued · 已排队', running: 'running · 执行中', succeeded: 'succeeded · 已完成', partial: 'partial · 部分资料', failed: 'failed · 失败', cancelled: 'cancelled · 已取消', blocked: 'blocked · 已阻断', timed_out: 'timed_out · 已超时' }

function isResult(value: DeliberationReadiness | DeliberationResult): value is DeliberationResult { return 'seats' in value }
function seatState(data: DeliberationReadiness | DeliberationResult, key: DeliberationSeatName) {
  if (isResult(data)) return data.invalidated_reason ? 'invalidated' : data.seats[key]?.status ?? (data.missing.includes(key) ? 'missing' : 'blocked')
  if (data.status === 'blocked') return 'blocked'
  return data.missing.includes(key) ? 'missing' : 'ready'
}
function seatDetail(data: DeliberationReadiness | DeliberationResult, key: DeliberationSeatName) {
  if (!isResult(data)) return data.missing.includes(key) ? 'missing · 服务端尚未提供此席位资料' : `${STATUS_LABEL[data.status] ?? data.status} · 等待真实结果`
  const seat = data.seats[key]
  if (!seat) return 'missing · 服务端没有返回此席位'
  if (seat.invalidated_reason) return `invalidated · ${seat.invalidated_reason}`
  if (seat.missing.length) return `missing · ${seat.missing.join('、')}`
  return `${seat.status} · source/citation 已绑定`
}
function formatTime(value: string | null | undefined) { if (!value) return '—'; const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? '—' : new Intl.DateTimeFormat('zh-HK', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Hong_Kong' }).format(parsed) }

export function DeliberationPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const market = searchParams.get('market')?.toUpperCase() || '—'
  const symbol = searchParams.get('symbol')?.toUpperCase() || '—'
  const [state, setState] = useState<PageState>({ kind: 'loading' })
  const [busy, setBusy] = useState<'create' | 'cancel' | 'retry' | ''>('')
  const [actionError, setActionError] = useState('')
  const binding = useMemo<DeliberationBinding | null>(() => {
    const sourceEventId = searchParams.get('source_event_id')
    const sourceEventSha256 = searchParams.get('source_event_sha256')
    const version = Number(searchParams.get('source_event_version'))
    if (market === '—' || symbol === '—' || !sourceEventId || !/^[0-9a-f]{64}$/.test(sourceEventSha256 ?? '') || !Number.isSafeInteger(version) || version < 1) return null
    return { market, symbol, timeframe: searchParams.get('timeframe') || '1d', question: searchParams.get('question') || '资料审阅', source_event_id: sourceEventId, source_event_version: version, source_event_sha256: sourceEventSha256 as string }
  }, [market, searchParams, symbol])

  useEffect(() => {
    const controller = new AbortController()
    setActionError('')
    if (!binding) { setState({ kind: 'missing', detail: '缺少可核验的股票与 source event 绑定；页面不会生成审议任务或演示结果。' }); return () => controller.abort() }
    const deliberationId = searchParams.get('deliberation_id')
    setState({ kind: 'loading' })
    void (async () => {
      try {
        const data = deliberationId ? await deliberationApi.get(deliberationId, controller.signal) : await deliberationApi.readiness(binding, controller.signal)
        let workflow: WorkflowTask | null = null
        if (isResult(data) && data.task_public_id) { try { workflow = await workflowApi.get(data.task_public_id, controller.signal) } catch { workflow = null } }
        if (!controller.signal.aborted) setState({ kind: 'ready', data, workflow })
      } catch (caught) {
        if (controller.signal.aborted) return
        const status = caught instanceof Error && 'status' in caught ? Number((caught as { status?: number }).status) : 0
        const kind = classifyDeliberationError(status, 'read')
        setState(kind === 'missing' ? { kind: 'missing', detail: '服务端没有返回这次审议；不会用占位结果替代。' } : { kind: 'error', detail: kind === 'forbidden' ? '当前账户没有读取多智能体审议的权限。' : caught instanceof Error ? caught.message : '审议服务读取失败。' })
      }
    })()
    return () => controller.abort()
  }, [binding, searchParams])

  const data = state.kind === 'ready' ? state.data : null
  const result = data && isResult(data) ? data : null
  const readiness = data && !isResult(data) ? data : null
  const coreState = !data || data.status === 'blocked' || data.status === 'failed' || data.status === 'cancelled' || data.status === 'timed_out' || Boolean(result?.invalidated_reason) ? 'locked' : data.status === 'queued' || data.status === 'running' ? 'processing' : 'neutral'
  const evidenceStatus = result?.invalidated_reason ? 'invalidated' : result?.status === 'succeeded' ? 'ready' : result?.status === 'partial' ? 'partial' : null

  async function createDeliberation() {
    if (!binding || !readiness || !readiness.ready || busy) return
    setBusy('create'); setActionError('')
    try {
      const created = await deliberationApi.create(binding)
      if (!created.deliberation_public_id) throw new Error('审议服务没有返回 deliberation_id，页面不会伪装创建成功。')
      const next = new URLSearchParams(searchParams)
      next.set('deliberation_id', created.deliberation_public_id)
      setSearchParams(next, { replace: true })
      setState({ kind: 'ready', data: created, workflow: created.task_public_id ? await workflowApi.get(created.task_public_id).catch(() => null) : null })
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : '审议创建失败。')
    } finally { setBusy('') }
  }

  async function updateDeliberation(action: 'cancel' | 'retry') {
    if (!result?.deliberation_public_id || busy) return
    setBusy(action); setActionError('')
    try {
      const next = action === 'cancel' ? await deliberationApi.cancel(result.deliberation_public_id) : await deliberationApi.retry(result.deliberation_public_id)
      const params = new URLSearchParams(searchParams)
      if (!next.deliberation_public_id) throw new Error('审议服务没有返回 deliberation_id，页面不会伪装操作成功。')
      params.set('deliberation_id', next.deliberation_public_id)
      setSearchParams(params, { replace: true })
      setState({ kind: 'ready', data: next, workflow: next.task_public_id ? await workflowApi.get(next.task_public_id).catch(() => null) : null })
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : `${action === 'cancel' ? '取消' : '重试'}审议失败。`)
    } finally { setBusy('') }
  }

  const canCancel = Boolean(result?.deliberation_public_id && (result.status === 'queued' || result.status === 'running'))
  const canRetry = Boolean(result?.deliberation_public_id && ['partial', 'succeeded', 'failed', 'cancelled', 'blocked', 'timed_out'].includes(result.status))
  const researchLink = symbol !== '—' ? `/research?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}` : '/research'
  const missingAction = <Link className="intelligence-inline-action" to={researchLink}>返回股票研究，选择有来源的量化事件 <ArrowRight /></Link>

  return <div className="intelligence-page deliberation-page">
    <header className="intelligence-page-header"><div><span>MULTI-AGENT EVIDENCE REVIEW</span><h1>牛熊多智能体审议</h1><p>四类研究职责只对真实资料进行整理；牛熊双强度不是胜率、概率或买卖结论。</p></div><div className="deliberation-stock-context"><span>市场</span><strong>{market}</strong><span>股票</span><strong>{symbol}</strong></div></header>
    <div className="deliberation-grid">
      <section className="deliberation-researchers" aria-labelledby="researcher-seats-title"><header className="intelligence-section-heading"><div><span>FOUR RESEARCH SEATS</span><h2 id="researcher-seats-title">四大研究员席位</h2></div></header>{SEATS.map(({ key, label, Icon }) => { const seat = result?.seats[key]; return <article className="deliberation-seat" data-state={data ? seatState(data, key) : 'missing'} key={key}><Icon /><div><span>{label}</span><strong>{seat?.support_strength ?? '—'}</strong><small>{data ? seatDetail(data, key) : 'missing · 暂无真实任务输出'}</small></div></article> })}</section>
      <section className="intelligence-panel deliberation-core" aria-labelledby="deliberation-core-title"><div className="deliberation-core-orbit" aria-hidden="true"><i /><i /><i /></div><CicloCore label={`Ciclo 审议中枢 · ${data ? STATUS_LABEL[data.status] ?? data.status : 'missing'}`} state={coreState} /><span>CICLO DELIBERATION CORE</span><h2 id="deliberation-core-title">{data ? `审议中枢 · ${STATUS_LABEL[data.status] ?? data.status}` : '审议中枢等待资料'}</h2><p>{result?.invalidated_reason ? `证据快照已失效：${result.invalidated_reason}` : '页面只展示服务端绑定的审议事实，不补写综合结论、分歧、辩论内容或分数。'}</p><TruthState title="综合结论：—" detail={result?.invalidated_reason ? 'invalidated · 失效证据不会升级为结论。' : data ? `${STATUS_LABEL[data.status] ?? data.status} · 服务端未返回综合结论字段。` : state.kind === 'loading' ? '正在读取真实 readiness' : state.kind === 'error' || state.kind === 'missing' ? state.detail : 'missing · 缺少可核验的多智能体审议结果。'} tone={state.kind === 'error' ? 'error' : result?.invalidated_reason || data?.status === 'blocked' ? 'warning' : 'neutral'} action={state.kind === 'missing' ? missingAction : undefined} />{readiness && <div className="deliberation-actions">{readiness.ready ? <button className="button primary" type="button" onClick={() => void createDeliberation()} disabled={busy !== ''}><RefreshCw />{busy === 'create' ? '正在发起审议' : '发起多智能体审议'}</button> : <span className="intelligence-boundary-note">当前 readiness 未通过，服务端不会创建审议任务。</span>}{actionError && <p className="workflow-inline-error" role="alert">{actionError}</p>}</div>}{result && <div className="deliberation-actions">{canCancel && <button className="button secondary" type="button" onClick={() => void updateDeliberation('cancel')} disabled={busy !== ''}><Square />{busy === 'cancel' ? '正在取消' : '取消审议'}</button>}{canRetry && <button className="button secondary" type="button" onClick={() => void updateDeliberation('retry')} disabled={busy !== ''}><RotateCcw />{busy === 'retry' ? '正在重试' : '重新发起'}</button>}{actionError && <p className="workflow-inline-error" role="alert">{actionError}</p>}</div>}<Link className="intelligence-inline-action" to={researchLink}>返回股票研究 <ArrowRight /></Link></section>
      <aside className="deliberation-evidence" aria-labelledby="directional-evidence-title"><header className="intelligence-section-heading"><div><span>DIRECTIONAL EVIDENCE</span><h2 id="directional-evidence-title">牛熊独立证据强度</h2></div></header><div className="directional-emblems" aria-hidden="true"><span className="is-bull">牛</span><span className="is-bear">熊</span></div><EvidenceStrength label="支持证据强度" value={result?.support_strength ?? null} status={evidenceStatus} coverage={result?.coverage ?? null} methodVersion={result?.method_version ?? null} observedAt={result?.observed_at ?? null} availableAt={result?.available_at ?? null} asOf={result?.as_of ?? null} calculatedAt={result?.calculated_at ?? null} tone="support" /><EvidenceStrength label="反向证据强度" value={result?.counter_evidence_strength ?? null} status={evidenceStatus} coverage={result?.coverage ?? null} methodVersion={result?.method_version ?? null} observedAt={result?.observed_at ?? null} availableAt={result?.available_at ?? null} asOf={result?.as_of ?? null} calculatedAt={result?.calculated_at ?? null} tone="counter" /><div className="deliberation-evidence-groups"><article><strong>支持证据</strong><span>{result?.support_strength ?? '—'}</span><small>{result ? result.status : 'missing'}</small></article><article><strong>反向证据</strong><span>{result?.counter_evidence_strength ?? '—'}</span><small>{result ? result.status : 'missing'}</small></article><article><strong>分歧 / 风险 / 未知</strong><span>{result?.missing.length ?? '—'}</span><small>{result?.invalidated_reason ? 'invalidated' : result ? `${result.missing.length} 个席位缺资料` : 'missing'}</small></article></div><p className="intelligence-boundary-note"><FileQuestion />分数必须由服务端绑定 method_version、证据快照、覆盖率与四个时间字段；前端不补算。</p></aside>
      <section className="intelligence-panel deliberation-timeline" aria-labelledby="deliberation-timeline-title"><header className="intelligence-section-heading"><div><span>REAL DELIBERATION TIMELINE</span><h2 id="deliberation-timeline-title">审议任务时间轴</h2></div>{result?.task_public_id && <Link className="intelligence-inline-action" to={`/workflow/${encodeURIComponent(result.task_public_id)}`}>查看真实 Workflow <ArrowRight /></Link>}</header>{state.kind === 'loading' && <p className="workflow-loading" role="status"><LoaderCircle />正在读取真实审议任务</p>}{state.kind !== 'loading' && state.kind !== 'ready' && <TruthState title={state.kind === 'error' ? '审议任务读取失败' : '暂无真实审议节点'} detail={state.detail} tone={state.kind === 'error' ? 'error' : 'warning'} />}{state.kind === 'ready' && (state.workflow?.events.length ? <ol>{state.workflow.events.map((event) => <li key={`${event.seq}-${event.created_at}`}><i /><div><strong>{STATUS_LABEL[event.status] ?? event.event_type}</strong><time>{formatTime(event.created_at)}</time></div></li>)}</ol> : <TruthState title="暂无公开事件" detail={data?.status === 'blocked' ? 'blocked · 服务端阻断了审议，未返回可展示的公开事件。' : '审议服务没有返回公开 queued、running、partial、succeeded、failed、cancelled、blocked 或 timed_out 节点。'} />)}</section>
    </div>
  </div>
}
