import {
  Activity,
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  BookOpenCheck,
  ChartCandlestick,
  Check,
  ChevronDown,
  CircleDot,
  Clock3,
  Download,
  FileOutput,
  FileQuestion,
  FileText,
  Filter,
  Home,
  LayoutGrid,
  LoaderCircle,
  Network,
  RefreshCw,
  RotateCcw,
  Scale,
  Search,
  ShieldAlert,
  Sparkles,
  Square,
  UserRound,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { classifyDeliberationError, deliberationApi, type DeliberationBinding, type DeliberationReadiness, type DeliberationResult, type DeliberationSeatName } from '../api/deliberation'
import { workflowApi, type WorkflowEvent, type WorkflowTask } from '../api/workflows'
import { EvidenceStrength } from '../components/intelligence/IntelligencePrimitives'
import { CicloCore } from '../components/paper/CicloCore'
import { useCicloTier } from '../api/use-ciclo-tier'
import '../styles/intelligence.css'
import '../styles/deliberation.css'

const SEATS: Array<{ key: DeliberationSeatName; label: string; short: string; node: string; Icon: LucideIcon }> = [
  { key: 'market_structure', label: '市场结构研究员', short: '结构', node: 'MARKET', Icon: ChartCandlestick },
  { key: 'fundamentals', label: '基本面研究员', short: '基本面', node: 'FINANCE', Icon: BookOpenCheck },
  { key: 'news_macro', label: '新闻宏观研究员', short: '宏观', node: 'NEWS', Icon: Scale },
  { key: 'risk', label: '风险研究员', short: '风险', node: 'RISK', Icon: ShieldAlert },
]

type PageState = { kind: 'loading' } | { kind: 'missing'; detail: string } | { kind: 'error'; detail: string } | { kind: 'ready'; data: DeliberationReadiness | DeliberationResult; workflow: WorkflowTask | null }
type Tone = 'success' | 'danger' | 'warning' | 'info' | 'muted'

const STATUS_LABEL: Record<string, string> = {
  queued: '已排队',
  running: '执行中',
  succeeded: '已完成',
  partial: '部分资料',
  failed: '失败',
  cancelled: '已取消',
  blocked: '已阻断',
  timed_out: '已超时',
}

function isResult(value: DeliberationReadiness | DeliberationResult): value is DeliberationResult { return 'seats' in value }

function statusTone(status: string | null | undefined): Tone {
  if (status === 'succeeded' || status === 'ready') return 'success'
  if (status === 'failed' || status === 'blocked' || status === 'timed_out' || status === 'invalidated') return 'danger'
  if (status === 'partial' || status === 'cancelled' || status === 'missing') return 'warning'
  if (status === 'queued' || status === 'running' || status === 'loading') return 'info'
  return 'muted'
}

function seatState(data: DeliberationReadiness | DeliberationResult, key: DeliberationSeatName) {
  if (isResult(data)) return data.invalidated_reason ? 'invalidated' : data.seats[key]?.status ?? (data.missing.includes(key) ? 'missing' : 'blocked')
  if (data.status === 'blocked') return 'blocked'
  return data.missing.includes(key) ? 'missing' : 'ready'
}

function seatDetail(data: DeliberationReadiness | DeliberationResult, key: DeliberationSeatName) {
  if (!isResult(data)) return data.missing.includes(key) ? '服务端尚未提供此席位资料' : `${STATUS_LABEL[data.status] ?? data.status}，等待真实结果`
  const seat = data.seats[key]
  if (!seat) return '服务端没有返回此席位'
  if (seat.invalidated_reason) return `证据失效：${seat.invalidated_reason}`
  if (seat.missing.length) return `缺少：${seat.missing.join('、')}`
  if (seat.support_strength === null || seat.counter_evidence_strength === null) return '席位已绑定来源，但强度尚未计算'
  return `支持 ${formatScore(seat.support_strength)} · 反向 ${formatScore(seat.counter_evidence_strength)} · 覆盖 ${formatCoverage(seat.coverage)}`
}

function formatTime(value: string | null | undefined) {
  if (!value) return '时间未提供'
  const parsed = new Date(value)
  return Number.isNaN(parsed.valueOf()) ? '时间格式不可识别' : new Intl.DateTimeFormat('zh-HK', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Hong_Kong' }).format(parsed)
}

function formatScore(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? new Intl.NumberFormat('zh-HK', { maximumFractionDigits: 1 }).format(value) : '暂无数据'
}

function formatCoverage(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)}%` : '暂无数据'
}

function materialText(value: unknown, missingCopy: string) {
  if (value === null || value === undefined) return missingCopy
  if (typeof value === 'string') return value.trim() || missingCopy
  try { return JSON.stringify(value, null, 2) } catch { return '资料格式无法显示' }
}

function eventKind(event: WorkflowEvent): 'normal' | 'risk' | 'avatar' | 'document' {
  if (event.status === 'partial' || ['failed', 'blocked', 'timed_out', 'cancelled'].includes(event.status) || /risk|warning/i.test(event.event_type)) return 'risk'
  if (event.status === 'succeeded' || /result|artifact|document|draft/i.test(event.event_type)) return 'document'
  if (event.status === 'running') return 'avatar'
  return 'normal'
}

function EventIcon({ kind }: { kind: ReturnType<typeof eventKind> }) {
  if (kind === 'risk') return <AlertTriangle aria-hidden="true" />
  if (kind === 'avatar') return <UserRound aria-hidden="true" />
  if (kind === 'document') return <FileText aria-hidden="true" />
  return <CircleDot aria-hidden="true" />
}

function StateNotice({ kind, title, detail }: { kind: 'loading' | 'missing' | 'error' | 'empty'; title: string; detail: string }) {
  const Icon = kind === 'loading' ? LoaderCircle : kind === 'error' ? AlertTriangle : kind === 'empty' ? FileQuestion : Clock3
  return <div className="deliberation-state" data-kind={kind} role={kind === 'error' ? 'alert' : 'status'}>
    <Icon aria-hidden="true" />
    <div><strong>{title}</strong><p>{detail}</p></div>
  </div>
}

function DirectionalAnimal({ side }: { side: 'bull' | 'bear' }) {
  return <span className={`deliberation-animal-frame is-${side}`} aria-hidden="true">
    <img
      className="deliberation-animal-mark"
      src={`/assets/robot/${side}.png`}
      alt=""
      width="128"
      height="128"
      loading="eager"
      decoding="async"
    />
  </span>
}

function DirectionalTrend({ values, side }: { values: number[]; side: 'bull' | 'bear' }) {
  if (values.length < 2) return <div className="deliberation-pk-trend-empty" role="img" aria-label={`${side === 'bull' ? '多方观点' : '空方观点'}趋势数据不足`}><Activity aria-hidden="true" /><span>趋势数据不足</span></div>
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = Math.max(max - min, 0.0001)
  const points = values.map((value, index) => `${4 + (index / Math.max(values.length - 1, 1)) * 92},${32 - ((value - min) / range) * 25}`).join(' ')
  const gradientId = `deliberation-${side}-trend-fill`
  return <div className="deliberation-pk-trend" role="img" aria-label={`${side === 'bull' ? '多方观点' : '空方观点'}四席位真实强度趋势`}>
    <svg viewBox="0 0 100 36" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="currentColor" stopOpacity=".34" /><stop offset="1" stopColor="currentColor" stopOpacity="0" /></linearGradient></defs><path className="trend-grid" d="M4 10H96M4 22H96M4 34H96" /><polygon points={`4,34 ${points} 96,34`} fill={`url(#${gradientId})`} /><polyline points={points} /></svg>
  </div>
}

export function DeliberationPage() {
  const cicloTier = useCicloTier()
  const [searchParams, setSearchParams] = useSearchParams()
  const market = searchParams.get('market')?.toUpperCase() ?? ''
  const symbol = searchParams.get('symbol')?.toUpperCase() ?? ''
  const [state, setState] = useState<PageState>({ kind: 'loading' })
  const [busy, setBusy] = useState<'create' | 'cancel' | 'retry' | ''>('')
  const [actionError, setActionError] = useState('')
  const [selectedSeat, setSelectedSeat] = useState<DeliberationSeatName | null>(null)
  const [refreshNonce, setRefreshNonce] = useState(0)
  const binding = useMemo<DeliberationBinding | null>(() => {
    const sourceEventId = searchParams.get('source_event_id')
    const sourceEventSha256 = searchParams.get('source_event_sha256')
    const version = Number(searchParams.get('source_event_version'))
    if (!market || !symbol || !sourceEventId || !/^[0-9a-f]{64}$/.test(sourceEventSha256 ?? '') || !Number.isSafeInteger(version) || version < 1) return null
    return { market, symbol, timeframe: searchParams.get('timeframe') || '1d', question: searchParams.get('question') || '资料审阅', source_event_id: sourceEventId, source_event_version: version, source_event_sha256: sourceEventSha256 as string }
  }, [market, searchParams, symbol])

  useEffect(() => {
    const controller = new AbortController()
    setActionError('')
    if (!binding) {
      setState({ kind: 'missing', detail: '缺少可核验的股票与 source event 绑定；页面不会生成审议任务或演示结果。' })
      return () => controller.abort()
    }
    const deliberationId = searchParams.get('deliberation_id')
    setState({ kind: 'loading' })
    void (async () => {
      try {
        const data = deliberationId ? await deliberationApi.get(deliberationId, controller.signal) : await deliberationApi.readiness(binding, controller.signal)
        let workflow: WorkflowTask | null = null
        if (isResult(data) && data.task_public_id) {
          try { workflow = await workflowApi.get(data.task_public_id, controller.signal) } catch { workflow = null }
        }
        if (!controller.signal.aborted) setState({ kind: 'ready', data, workflow })
      } catch (caught) {
        if (controller.signal.aborted) return
        const status = caught instanceof Error && 'status' in caught ? Number((caught as { status?: number }).status) : 0
        const kind = classifyDeliberationError(status, 'read')
        setState(kind === 'missing'
          ? { kind: 'missing', detail: '服务端没有返回这次审议；不会用占位结果替代。' }
          : { kind: 'error', detail: kind === 'forbidden' ? '当前账户没有读取多智能体审议的权限。' : caught instanceof Error ? caught.message : '审议服务读取失败。' })
      }
    })()
    return () => controller.abort()
  }, [binding, refreshNonce, searchParams])

  const data = state.kind === 'ready' ? state.data : null
  const result = data && isResult(data) ? data : null
  const readiness = data && !isResult(data) ? data : null
  const currentStatus = state.kind === 'loading' ? '正在同步' : state.kind === 'error' ? '读取失败' : state.kind === 'missing' ? '资料未绑定' : STATUS_LABEL[data?.status ?? ''] ?? '状态未提供'
  const currentTone = state.kind === 'loading' ? 'info' : state.kind === 'error' ? 'danger' : state.kind === 'missing' ? 'warning' : statusTone(data?.status)
  const coreState = !data || data.status === 'blocked' || data.status === 'failed' || data.status === 'cancelled' || data.status === 'timed_out' || Boolean(result?.invalidated_reason) ? 'locked' : data.status === 'queued' || data.status === 'running' ? 'processing' : 'neutral'
  const evidenceStatus = result?.invalidated_reason ? 'invalidated' : result?.status === 'succeeded' ? 'ready' : result?.status === 'partial' ? 'partial' : null
  const authoritativeEvidence = evidenceStatus === 'ready'
    && [result?.support_strength, result?.counter_evidence_strength, result?.coverage].every((value) => typeof value === 'number' && Number.isFinite(value))
    && Boolean(result?.method_version.trim())
    && [result?.observed_at, result?.available_at, result?.as_of, result?.calculated_at].every((value) => value !== null && value !== undefined && Number.isFinite(Date.parse(value)))

  async function createDeliberation() {
    if (!binding || !readiness || !readiness.ready || busy) return
    setBusy('create')
    setActionError('')
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
    setBusy(action)
    setActionError('')
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

  function exportDeliberationRecord() {
    if (!data) { setActionError('当前没有可导出的真实审议记录。'); return }
    setActionError('')
    try {
      const payload = JSON.stringify({ exported_at: new Date().toISOString(), deliberation: data, workflow: state.kind === 'ready' ? state.workflow : null }, null, 2)
      const url = URL.createObjectURL(new Blob([payload], { type: 'application/json;charset=utf-8' }))
      const link = document.createElement('a')
      link.href = url
      link.download = `ciclotrade-deliberation-${(symbol || 'unbound').replace(/[^A-Z0-9._-]/g, '')}-${new Date().toISOString().slice(0, 10)}.json`
      document.body.append(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch {
      setActionError('浏览器无法导出审议记录，请检查下载权限后重试。')
    }
  }

  const canCancel = Boolean(result?.deliberation_public_id && (result.status === 'queued' || result.status === 'running'))
  const canRetry = Boolean(result?.deliberation_public_id && ['partial', 'succeeded', 'failed', 'cancelled', 'blocked', 'timed_out'].includes(result.status))
  const researchLink = symbol ? `/research?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}` : '/discover?returnTo=deliberation'
  const researchLinkLabel = symbol ? '返回股票研究' : '前往发现页选择股票'
  const paperLink = result ? `/paper?market=${encodeURIComponent(result.market)}&symbol=${encodeURIComponent(result.symbol)}&source=deliberation&deliberation_id=${encodeURIComponent(result.deliberation_public_id ?? '')}` : '/paper'
  const seatTimestamp = result?.calculated_at ?? result?.available_at ?? null
  const flowTones: Tone[] = data?.status === 'succeeded'
    ? ['success', 'success', 'success']
    : data?.status === 'running'
      ? ['success', 'info', 'muted']
      : data?.status === 'queued'
        ? ['info', 'muted', 'muted']
        : data?.status === 'partial'
          ? ['success', 'warning', 'muted']
          : data?.status === 'failed' || data?.status === 'blocked' || data?.status === 'timed_out'
            ? ['warning', 'danger', 'muted']
            : readiness?.ready
              ? ['info', 'muted', 'muted']
              : ['muted', 'muted', 'muted']
  const counterEvidenceRows = result ? [
    ...SEATS.map(({ key, label }) => ({
      key,
      source: key.toUpperCase(),
      label,
      summary: result.seats[key].counter_evidence_strength === null
        ? seatDetail(result, key)
        : `反向强度 ${formatScore(result.seats[key].counter_evidence_strength)} · 权重 ${(result.seats[key].weight_bps / 100).toFixed(1)}%`,
      tone: statusTone(seatState(result, key)),
      time: result.calculated_at,
    })),
    {
      key: 'aggregate',
      source: 'AGGREGATE',
      label: '综合反向证据',
      summary: result.counter_evidence_strength === null ? '服务端尚未形成综合反向证据强度' : `综合强度 ${formatScore(result.counter_evidence_strength)} · 覆盖 ${formatCoverage(result.coverage)}`,
      tone: statusTone(result.invalidated_reason ? 'invalidated' : result.status),
      time: result.calculated_at,
    },
  ] : []
  const bullTrend = result ? SEATS.map(({ key }) => result.seats[key].support_strength).filter((value): value is number => typeof value === 'number' && Number.isFinite(value)) : []
  const bearTrend = result ? SEATS.map(({ key }) => result.seats[key].counter_evidence_strength).filter((value): value is number => typeof value === 'number' && Number.isFinite(value)) : []
  const analysisRows = [
    ...(result ? SEATS.map(({ key, label }) => {
      const seat = result.seats[key]
      const rawState = seatState(result, key)
      return {
        key: `seat-${key}`,
        time: seatTimestamp,
        source: label,
        role: key.toUpperCase(),
        type: '席位观点',
        summary: seatDetail(result, key),
        score: seat.support_strength === null && seat.counter_evidence_strength === null ? '暂无数据' : `支持 ${formatScore(seat.support_strength)} / 反向 ${formatScore(seat.counter_evidence_strength)}`,
        status: rawState === 'ready' ? '已就绪' : rawState === 'invalidated' ? '证据失效' : rawState === 'missing' ? '资料不足' : STATUS_LABEL[rawState] ?? rawState,
        tone: statusTone(rawState),
      }
    }) : []),
    ...(state.kind === 'ready' ? state.workflow?.events.map((event) => {
      const kind = eventKind(event)
      return {
        key: `event-${event.seq}-${event.created_at}`,
        time: event.created_at,
        source: event.event_type,
        role: 'WORKFLOW',
        type: kind === 'risk' ? '风险记录' : kind === 'document' ? '结论制品' : kind === 'avatar' ? '审议进程' : '系统记录',
        summary: `审议事件 #${event.seq} · ${STATUS_LABEL[event.status] ?? event.status}`,
        score: '未评分',
        status: STATUS_LABEL[event.status] ?? event.status,
        tone: statusTone(event.status),
      }
    }) ?? [] : []),
  ]

  return <div className="intelligence-page deliberation-page">
    <header className="deliberation-local-topbar">
      <div className="deliberation-view-status" data-tone={currentTone}>
        <i aria-hidden="true" />
        <div><span>观点管理</span><h1>多空观点对照</h1></div>
        <strong>{currentStatus}</strong>
      </div>
      <div className="deliberation-top-actions">
        <Link className="deliberation-icon-button" to="/today" aria-label="返回今日总览" title="返回今日总览"><Home /></Link>
        <button className="deliberation-export-button" type="button" onClick={exportDeliberationRecord} disabled={!data}><Download />导出</button>
      </div>
    </header>

    <p className="deliberation-compliance-banner" role="note"><FileQuestion aria-hidden="true" /><strong>研究免责声明</strong><span>仅供研究/教育参考，不构成投资建议、交易邀约或收益承诺；市场可能损失部分或全部本金；数据/模型/AI可能延迟、遗漏、错误或偏差。</span><Link className="deliberation-legal-link" to="/legal">完整免责声明与法律条款</Link></p>

    {state.kind === 'error' && <StateNotice kind="error" title="审议页面读取失败" detail={state.detail} />}
    {state.kind === 'missing' && <StateNotice kind="missing" title="审议资料未绑定" detail={state.detail} />}

    <div className="deliberation-grid">
      <aside className="deliberation-researchers" aria-labelledby="researcher-seats-title">
        <div className="deliberation-task-picker">
          <button type="button" onClick={() => setRefreshNonce((value) => value + 1)} disabled={state.kind === 'loading'}>
            {state.kind === 'loading' ? <LoaderCircle /> : <Sparkles />}
            <span><small>当前任务</small><strong>{symbol ? `${symbol} 策略审议` : '尚未选择股票任务'}</strong></span>
          </button>
          <Link to={researchLink} aria-label="搜索并选择股票研究任务" title="搜索股票"><Search /></Link>
        </div>

        <header className="deliberation-section-title">
          <div><span>RESEARCH DESK</span><h2 id="researcher-seats-title">研究员席</h2></div>
          <strong>{result ? `${SEATS.filter(({ key }) => result.seats[key].status === 'ready').length} / 4` : '等待结果'}</strong>
        </header>

        <div className="deliberation-seat-list">
          {SEATS.map(({ key, label, short, Icon }) => {
            const seat = result?.seats[key]
            const rawState = data ? seatState(data, key) : state.kind === 'loading' ? 'loading' : 'missing'
            const tone = statusTone(rawState)
            const open = selectedSeat === key
            return <article className="deliberation-seat" data-state={tone} key={key}>
              <div className="deliberation-seat-main">
                <span className="deliberation-seat-avatar"><Icon aria-hidden="true" /><small>{short}</small></span>
                <div className="deliberation-seat-copy">
                  <div><strong>{label}</strong><span className="deliberation-seat-status" data-tone={tone}><i />{rawState === 'ready' ? '在线' : rawState === 'loading' ? '同步中' : rawState === 'invalidated' ? '证据失效' : rawState === 'missing' ? '资料不足' : STATUS_LABEL[rawState] ?? rawState}</span></div>
                  <p>{data ? seatDetail(data, key) : state.kind === 'loading' ? '正在读取真实席位资料' : '当前没有可核验的席位输出'}</p>
                  <time>{seatTimestamp ? formatTime(seatTimestamp) : data ? `${STATUS_LABEL[data.status] ?? data.status} · 时间未提供` : '尚未取得更新时间'}</time>
                </div>
              </div>
              <button type="button" onClick={() => setSelectedSeat(open ? null : key)} disabled={!seat} aria-expanded={open}>
                {open ? '收起观点' : '查看观点'}<ArrowRight />
              </button>
              {open && seat && <div className="deliberation-seat-detail">
                <div><span>来源</span><pre>{materialText(seat.source, '服务端未提供来源资料')}</pre></div>
                <div><span>引用</span><pre>{materialText(seat.citation, '服务端未提供引用资料')}</pre></div>
              </div>}
            </article>
          })}
        </div>

        <article className="deliberation-side-operation is-draft">
          <span><FileOutput /></span>
          <div><strong>生成模拟研究草稿</strong><p>把真实审议结果带入个人模拟研究；不会提交订单。</p></div>
          {result ? <Link className="deliberation-secondary-action" to={paperLink}><FileText />生成模拟研究草稿</Link> : <button className="deliberation-secondary-action" type="button" disabled><FileText />等待审议结果</button>}
        </article>
      </aside>

      <section className="deliberation-center" aria-labelledby="deliberation-core-title">
        <section className="deliberation-core">
          <header className="deliberation-center-heading">
            <div><span>DIRECTIONAL RESEARCH CORE</span><h2 id="deliberation-core-title">观点证据中枢</h2></div>
            <span className="deliberation-status-pill" data-tone={currentTone}><i />{currentStatus}</span>
          </header>

          <div className="deliberation-robot-stage" data-tier={cicloTier}>
            <div className="deliberation-robot-halo" aria-hidden="true"><i /><i /><i /></div>
            {SEATS.map(({ key, node, Icon }, index) => {
              const rawState = data ? seatState(data, key) : state.kind === 'loading' ? 'loading' : 'missing'
              return <div className={`deliberation-capability-node node-${index + 1}`} data-tone={statusTone(rawState)} key={key}>
                <Icon aria-hidden="true" /><span>{node}</span><i aria-hidden="true" />
              </div>
            })}
            <CicloCore label={`Ciclo 审议中枢 · ${currentStatus}`} state={coreState} tier={cicloTier} />
            <div className="deliberation-robot-base" aria-hidden="true"><span /><i /></div>
          </div>

          <div className="deliberation-flow-panel">
            <div className="deliberation-flow-nodes">
              {[
                { label: '观点分析', detail: result ? '四席位来源已进入独立审阅' : '等待四席位真实资料', Icon: Activity },
                { label: '风险复评', detail: result?.invalidated_reason ? '证据快照已失效' : result?.missing.length ? `${result.missing.length} 个席位仍缺资料` : result ? '风险席位已绑定' : '尚未进入风险复评', Icon: ShieldAlert },
                { label: '生成草稿', detail: result?.status === 'succeeded' ? '可生成模拟研究草稿' : '不会在资料不足时生成结论', Icon: FileOutput },
              ].map(({ label, detail, Icon }, index) => <div className="deliberation-flow-fragment" key={label}>
                <article data-tone={flowTones[index]}>
                  <span><Icon aria-hidden="true" /></span>
                  <div><strong>{label}</strong><small>{detail}</small></div>
                  {flowTones[index] === 'success' ? <Check aria-hidden="true" /> : <i aria-hidden="true" />}
                </article>
                {index < 2 && <div className="deliberation-flow-arrow" aria-hidden="true"><ArrowRight /><span /></div>}
              </div>)}
            </div>
            <div className="deliberation-flow-result">
              <ArrowDown aria-hidden="true" />
              <span><strong>倾向性研判</strong><small>{result ? result.invalidated_reason ? '证据失效，暂不形成研判' : result.missing.length ? `${result.missing.length} 个席位资料不完整` : '多空证据已汇总，等待人工复核' : '等待真实审议结果'}</small></span>
              <em>{result ? result.invalidated_reason ? '已失效' : result.missing.length ? `${result.missing.length} 项缺口` : '待复核' : '未形成'}</em>
            </div>
          </div>

          {state.kind === 'loading' && <StateNotice kind="loading" title="正在读取真实 readiness" detail="审议中枢保持锁定，直到服务端返回账户可见的任务状态。" />}
          {readiness && !readiness.ready && <StateNotice kind="missing" title="当前 readiness 未通过" detail={readiness.reason || '服务端尚未提供创建审议所需的完整证据快照。'} />}
          {result?.invalidated_reason && <StateNotice kind="error" title="证据快照已失效" detail={`${result.invalidated_reason}；失效证据不会升级为综合结论。`} />}
          {data && !result?.invalidated_reason && <p className="deliberation-boundary"><FileQuestion />观点—证据审议链仅展示服务端绑定事实与数据分析，不补写胜率、买卖结论或订单指令。</p>}

          <div className="deliberation-runtime-actions">
            {readiness?.ready && <button className="deliberation-secondary-action" type="button" onClick={() => void createDeliberation()} disabled={busy !== ''}><RefreshCw />{busy === 'create' ? '正在发起审议' : '发起多智能体审议'}</button>}
            {canCancel && <button className="deliberation-secondary-action" type="button" onClick={() => void updateDeliberation('cancel')} disabled={busy !== ''}><Square />{busy === 'cancel' ? '正在取消' : '取消审议'}</button>}
            {canRetry && <button className="deliberation-secondary-action" type="button" onClick={() => void updateDeliberation('retry')} disabled={busy !== ''}><RotateCcw />{busy === 'retry' ? '正在重试' : '重新发起'}</button>}
            <Link className="deliberation-text-link" to={researchLink}>{researchLinkLabel} <ArrowRight /></Link>
          </div>
          {actionError && <p className="deliberation-action-error" role="alert">{actionError}</p>}
        </section>

        <section className="deliberation-timeline" aria-labelledby="deliberation-timeline-title">
          <header className="deliberation-section-title">
            <div><span>REAL DELIBERATION TIMELINE</span><h2 id="deliberation-timeline-title">审议记录</h2></div>
            {result?.task_public_id && <Link className="deliberation-text-link" to={`/workflow/${encodeURIComponent(result.task_public_id)}`}>查看真实 Workflow <ArrowRight /></Link>}
          </header>
          {state.kind === 'loading' && <StateNotice kind="loading" title="正在读取真实审议任务" detail="公开事件到达后会按服务端时间顺序显示。" />}
          {state.kind === 'error' && <StateNotice kind="error" title="审议任务读取失败" detail={state.detail} />}
          {state.kind === 'missing' && <StateNotice kind="missing" title="暂无真实审议节点" detail={state.detail} />}
          {state.kind === 'ready' && (state.workflow?.events.length ? <ol>
            {state.workflow.events.map((event) => {
              const kind = eventKind(event)
              return <li data-kind={kind} key={`${event.seq}-${event.created_at}`}>
                <span className="deliberation-timeline-node"><EventIcon kind={kind} /></span>
                <div><small>{event.event_type}</small><strong>{STATUS_LABEL[event.status] ?? event.status}</strong><time>{formatTime(event.created_at)}</time></div>
              </li>
            })}
          </ol> : <StateNotice kind="empty" title="审议服务未返回公开事件" detail={data?.status === 'blocked' ? '服务端已阻断这次审议，因此没有可展示的公开事件。' : '当前任务没有 queued、running、partial、succeeded、failed、cancelled、blocked 或 timed_out 事件。'} />)}
        </section>

      </section>

      <aside className="deliberation-evidence" aria-labelledby="directional-evidence-title">
        <header className="deliberation-section-title">
          <div><span>DIRECTIONAL EVIDENCE</span><h2 id="directional-evidence-title">倾向性研判</h2></div>
          <div className="deliberation-evidence-tools" aria-label="证据视图工具">
            <span title="筛选视图"><Filter /></span><span title="网格布局"><LayoutGrid /></span>
            <button type="button" aria-label="刷新真实证据" title="刷新真实证据" onClick={() => setRefreshNonce((value) => value + 1)} disabled={state.kind === 'loading'}><RefreshCw /></button>
          </div>
        </header>

        <section className="deliberation-evidence-card">
          <div className="deliberation-evidence-map" aria-label="四席位证据汇聚图">
            <div className="deliberation-source-stack">
              {SEATS.map(({ key, node, Icon }) => {
                const rawState = data ? seatState(data, key) : state.kind === 'loading' ? 'loading' : 'missing'
                return <span data-tone={statusTone(rawState)} key={key}><Icon /><strong>{node}</strong><i /></span>
              })}
            </div>
            <div className="deliberation-evidence-beam" aria-hidden="true"><i /><Network /><i /></div>
            <div className="deliberation-pk-arena">
              <article className="deliberation-pk-card is-bull">
                <header><DirectionalAnimal side="bull" /><div><strong>多方观点</strong></div></header>
                <div className="deliberation-pk-score"><span>多方证据强度</span><strong>{formatScore(result?.support_strength)}</strong></div>
                <DirectionalTrend values={bullTrend} side="bull" />
              </article>
              <div className="deliberation-pk-vs" aria-label="多空观点对照"><span>对照</span><small>{authoritativeEvidence ? '真实证据' : '等待数据'}</small></div>
              <article className="deliberation-pk-card is-bear">
                <header><div><strong>空方观点</strong></div><DirectionalAnimal side="bear" /></header>
                <div className="deliberation-pk-score"><span>空方证据强度</span><strong>{formatScore(result?.counter_evidence_strength)}</strong></div>
                <DirectionalTrend values={bearTrend} side="bear" />
              </article>
            </div>
          </div>

          <div className="deliberation-evidence-facts">
            <span><i className="is-blue" /><small>覆盖率</small><strong>{formatCoverage(result?.coverage)}</strong></span>
            <span><i className="is-green" /><small>方法版本</small><strong>{result?.method_version || '版本未提供'}</strong></span>
            <span><i className={result?.invalidated_reason ? 'is-red' : 'is-yellow'} /><small>证据状态</small><strong>{result?.invalidated_reason ? '已失效' : result ? STATUS_LABEL[result.status] ?? result.status : '等待结果'}</strong></span>
          </div>

          {authoritativeEvidence ? <div className="deliberation-strengths">
            <EvidenceStrength label="支持证据强度" value={result?.support_strength ?? null} status={evidenceStatus} coverage={result?.coverage ?? null} methodVersion={result?.method_version ?? null} observedAt={result?.observed_at ?? null} availableAt={result?.available_at ?? null} asOf={result?.as_of ?? null} calculatedAt={result?.calculated_at ?? null} tone="support" />
            <EvidenceStrength label="反向证据强度" value={result?.counter_evidence_strength ?? null} status={evidenceStatus} coverage={result?.coverage ?? null} methodVersion={result?.method_version ?? null} observedAt={result?.observed_at ?? null} availableAt={result?.available_at ?? null} asOf={result?.as_of ?? null} calculatedAt={result?.calculated_at ?? null} tone="counter" />
          </div> : <StateNotice kind={state.kind === 'loading' ? 'loading' : result ? 'missing' : state.kind === 'error' ? 'error' : 'empty'} title={state.kind === 'loading' ? '正在汇聚多空证据' : '倾向性研判：暂无数据'} detail={result?.invalidated_reason || '当前缺少完整的强度、覆盖率、方法版本或时间字段；页面不会留下空白，也不会补写估算值。'} />}

          <div className="deliberation-counter-prompt">
            <AlertTriangle />
            <div><strong>反向证据 / 分歧</strong><p>{result?.invalidated_reason || (result?.missing.length ? `${result.missing.length} 个研究席位仍有资料缺口，草稿保持锁定。` : result ? '当前没有席位缺项；仍需人工复核风险与未知项。' : '等待真实审议结果后显示反向证据提示。')}</p></div>
            <span>{result ? `${result.missing.length} 风险项` : '等待'}</span>
          </div>
        </section>

        <section className="deliberation-counter-list">
          <header><div><span>COUNTER EVIDENCE</span><h2>反向证据列表</h2></div><strong>{result ? `${counterEvidenceRows.filter((row) => row.tone === 'danger' || row.tone === 'warning').length} 项需复核` : '等待结果'}</strong></header>
          {counterEvidenceRows.length ? <ol>
            {counterEvidenceRows.map((row) => <li key={row.key}>
              <time>{formatTime(row.time)}</time>
              <span>{row.source}</span>
              <div><strong>{row.label}</strong><p>{row.summary}</p></div>
              <i data-tone={row.tone} aria-label={row.tone === 'danger' ? '风险' : row.tone === 'warning' ? '警告' : row.tone === 'success' ? '正常' : '处理中'} />
            </li>)}
          </ol> : <StateNotice kind={state.kind === 'loading' ? 'loading' : state.kind === 'error' ? 'error' : 'empty'} title={state.kind === 'loading' ? '正在读取反向证据' : '反向证据：暂无数据'} detail={state.kind === 'error' ? state.detail : '暂无数据：服务端尚未返回可按日期、来源和状态展示的反向证据。'} />}
        </section>

        <details className="deliberation-risk-review">
          <summary><span><ShieldAlert />风险审阅记录</span><strong>{result?.invalidated_reason ? '证据失效' : result?.missing.length ? `${result.missing.length} 项缺口` : result ? '可展开复核' : '等待结果'}</strong><ChevronDown /></summary>
          <div>
            {result ? <>
              <p><span>审议状态</span><strong>{STATUS_LABEL[result.status] ?? result.status}</strong></p>
              <p><span>缺失席位</span><strong>{result.missing.length ? result.missing.map((key) => SEATS.find((seat) => seat.key === key)?.label ?? key).join('、') : '服务端未记录席位缺项'}</strong></p>
              <p><span>失效原因</span><strong>{result.invalidated_reason || '服务端未记录失效原因'}</strong></p>
              <p><span>Workflow</span><strong>{state.kind === 'ready' && state.workflow ? `${STATUS_LABEL[state.workflow.status] ?? state.workflow.status} · ${state.workflow.events.length} 个公开事件` : result.task_public_id ? '任务详情暂未取得' : '服务端未绑定 Workflow 任务'}</strong></p>
            </> : <p><span>风险记录</span><strong>当前没有真实审议结果可供复核</strong></p>}
          </div>
        </details>

        <article className="deliberation-side-operation is-export">
          <span><Download /></span>
          <div><strong>导出审议记录</strong><p>导出真实审议与 Workflow 数据，便于留档复核。</p></div>
          <button className="deliberation-gradient-action" type="button" onClick={exportDeliberationRecord} disabled={!data}><Download />导出审议记录</button>
        </article>
      </aside>
    </div>

    <section className="deliberation-analysis" aria-labelledby="deliberation-analysis-title">
      <header className="deliberation-section-title">
        <div><span>ANALYSIS HISTORY</span><h2 id="deliberation-analysis-title">分析观点</h2></div>
        <strong>{analysisRows.length ? `${analysisRows.length} 条真实记录` : '等待真实数据'}</strong>
      </header>
      {analysisRows.length ? <div className="deliberation-analysis-table"><table>
        <thead><tr><th>时间</th><th>来源 / 角色</th><th>观点类型</th><th>文本摘要</th><th>评分</th><th>状态</th></tr></thead>
        <tbody>{analysisRows.map((row) => <tr key={row.key}>
          <td><time>{formatTime(row.time)}</time></td>
          <td><span className="deliberation-role-tag">{row.role}</span><strong>{row.source}</strong></td>
          <td>{row.type}</td>
          <td><p>{row.summary}</p></td>
          <td><code>{row.score}</code></td>
          <td><span className="deliberation-analysis-status" data-tone={row.tone}><i />{row.status}</span></td>
        </tr>)}</tbody>
      </table></div> : <StateNotice kind={state.kind === 'loading' ? 'loading' : state.kind === 'error' ? 'error' : 'empty'} title={state.kind === 'loading' ? '正在读取分析观点' : '暂无真实分析观点'} detail={state.kind === 'error' ? state.detail : '服务端尚未返回席位观点或公开审议记录；页面不会填入示例观点。'} />}
    </section>
  </div>
}
