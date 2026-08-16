import {
  Archive, ArrowRight, Bot, CheckCircle2, CircleSlash2, FileSearch, LoaderCircle,
  LockKeyhole, MessageSquareText, Plus, RefreshCw, RotateCcw, Send, ShieldCheck,
  ShieldX, Square, TimerOff, XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CicloCore } from '../components/paper/CicloCore'
import {
  AI_TASK_STATUSES, AiWorkspaceApiError, aiWorkspaceApi, classifyAiWorkspaceError,
  readAiWorkspaceStructuredMessage, type AiTaskStatus, type AiWorkspaceReadiness,
  type AiWorkspaceSession, type AiWorkspaceSessionSummary, type AiWorkspaceTask,
  type AiWorkspaceTaskEvent, type AiWorkspaceTaskResult, type AiWorkspaceStructuredAnswer,
} from '../api/aiWorkspace.ts'
import '../styles/intelligence.css'
import '../styles/ai-workspace.css'

type PageState = 'loading' | 'ready' | 'empty' | 'forbidden' | 'error'
type BusyAction = '' | 'create' | 'archive' | 'send' | 'cancel'

const STATUS_COPY: Record<AiTaskStatus, string> = {
  queued: '排队中', running: '执行中', partial: '部分完成', succeeded: '已完成',
  failed: '失败', cancelled: '已取消', blocked: '已阻断', timed_out: '已超时',
}
const STATUS_ICONS: Record<AiTaskStatus, typeof CheckCircle2> = {
  queued: LoaderCircle, running: LoaderCircle, partial: RefreshCw, succeeded: CheckCircle2,
  failed: XCircle, cancelled: CircleSlash2, blocked: ShieldX, timed_out: TimerOff,
}
const RESPONSE_SECTIONS: Array<{ key: 'support' | 'counter' | 'risks' | 'next_steps'; label: string }> = [
  { key: 'support', label: '支持证据' }, { key: 'counter', label: '反向证据' },
  { key: 'risks', label: '风险与失效' }, { key: 'next_steps', label: '下一步' },
]

function formatTime(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? '—' : new Intl.DateTimeFormat('zh-HK', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Hong_Kong' }).format(date)
}
function errorMessage(error: unknown, fallback: string) { return error instanceof Error ? error.message : fallback }
function textOf(value: unknown): string { return Array.isArray(value) ? value.join('；') : typeof value === 'string' ? value : '暂无可公开内容。' }

function StatusPill({ status }: { status: AiTaskStatus }) {
  const Icon = STATUS_ICONS[status]
  return <span className={`ai-task-status is-${status}`}><Icon aria-hidden="true" />{STATUS_COPY[status]}</span>
}

function SessionRow({ session, selected, onSelect }: { session: AiWorkspaceSessionSummary; selected: boolean; onSelect: () => void }) {
  return <button className={`ai-session-row${selected ? ' is-selected' : ''}`} type="button" aria-current={selected ? 'page' : undefined} onClick={onSelect}>
    <span className="ai-session-row-mark" aria-hidden="true"><MessageSquareText /></span>
    <span className="ai-session-row-copy"><strong>{session.title || '新研究会话'}</strong><small>{formatTime(session.created_at)}</small></span>
    <span className={`ai-session-state is-${session.status}`}>{session.status === 'archived' ? '已归档' : '进行中'}</span>
  </button>
}

function AnswerCard({ structured, createdAt, title = 'Ciclo AI 研究回执' }: { structured: AiWorkspaceStructuredAnswer; createdAt: string; title?: string }) {
  return <section className="ai-answer-card">
    <header className="ai-answer-header"><div><span>STRUCTURED ANSWER</span><h2>{title}</h2></div><span className="ai-answer-proof"><ShieldCheck />已绑定服务端版本</span></header>
    <article className="ai-answer-conclusion"><span>结论</span><p>{textOf(structured.conclusion.text)}</p></article>
    <div className="ai-answer-citations"><span>引用与时间</span><div>{structured.citations.map((citation) => <code key={citation}>{citation}</code>)}</div><small>引用 ID 由服务端签发；回答不展示内部思维链或供应商凭据。</small></div>
    <div className="ai-answer-sections">{RESPONSE_SECTIONS.map(({ key, label }) => <article key={key}><span>{label}</span><p>{textOf(structured[key].text)}</p></article>)}</div>
    {structured.tool_calls?.some((call) => call.name === 'create_paper_draft') && <div className="ai-draft-boundary"><FileSearch /><div><strong>仅生成个人模拟草稿</strong><p>草稿需要你在个人模拟页面继续核对；AI 不提交订单，也不启用自动实盘。</p></div><Link to="/paper">打开个人模拟 <ArrowRight /></Link></div>}
    <footer className="ai-answer-footer">回答时间 {formatTime(createdAt)} · provider 版本已核验</footer>
  </section>
}

function TaskReceipt({ task, events, onCancel, busy }: { task: AiWorkspaceTask; events: AiWorkspaceTaskEvent[]; onCancel: () => void; busy: boolean }) {
  const canCancel = task.status === 'queued' || task.status === 'running' || task.status === 'partial'
  return <section className="ai-task-card" aria-labelledby="ai-task-title">
    <header className="ai-task-header"><div><span>PUBLIC TASK RECEIPT</span><h2 id="ai-task-title">任务公开回执</h2></div><StatusPill status={task.status} /></header>
    <dl className="ai-task-facts"><div><dt>任务 ID</dt><dd>{task.public_id}</dd></div><div><dt>创建时间</dt><dd>{formatTime(task.created_at)}</dd></div><div><dt>更新时间</dt><dd>{formatTime(task.updated_at)}</dd></div><div><dt>错误码</dt><dd>{task.error_code ?? '—'}</dd></div></dl>
    {(task.blocked_reason || task.error_code) && <div className={`ai-task-notice is-${task.status}`} role="status"><LockKeyhole /><div><strong>{task.status === 'blocked' ? '服务不可用，任务已公开阻断' : `任务${STATUS_COPY[task.status]}`}</strong><p>{task.blocked_reason ?? task.error_code ?? '服务端没有返回更多说明。'}；没有生成回答，也没有伪造执行轨迹。</p></div></div>}
    <div className="ai-task-events"><div className="ai-subheading"><span>PUBLIC EVENTS</span><strong>公开事件</strong></div>{events.length ? <ol>{events.map((event) => <li key={`${event.seq}-${event.created_at}`}><i /><div><strong>{STATUS_COPY[event.status]}</strong><time>{formatTime(event.created_at)}</time></div></li>)}</ol> : <p className="ai-muted">服务端尚未返回公开事件。</p>}</div>
    {canCancel && <footer className="ai-task-actions"><button className="ai-button danger" type="button" onClick={onCancel} disabled={busy}><Square />{busy ? '正在取消' : '取消任务'}</button></footer>}
  </section>
}

function SessionMessages({ session }: { session: AiWorkspaceSession }) {
  return <div className="ai-message-list" aria-live="polite">
    {session.messages.length === 0 && <div className="ai-message-empty"><Bot /><p>这是一个空白研究会话。你可以先问一只股票的事实、风险或比较依据。</p></div>}
    {session.messages.map((message) => {
      const structured = message.role === 'assistant' ? readAiWorkspaceStructuredMessage(message.content) : null
      if (structured) return <article className="ai-history-answer" key={message.public_id}><span className="ai-message-role">Ciclo AI · 结构化回答</span><AnswerCard structured={structured} createdAt={message.created_at} /><small className="ai-history-public-id">消息 {message.public_id}</small></article>
      return <article className={`ai-message is-${message.role}`} key={message.public_id}><span className="ai-message-role">{message.role === 'user' ? '你' : message.role === 'assistant' ? 'Ciclo AI' : '系统'}</span><p>{textOf(message.content.text)}</p><time>{formatTime(message.created_at)}</time></article>
    })}
  </div>
}

export function AIWorkspacePage() {
  const [readiness, setReadiness] = useState<AiWorkspaceReadiness | null>(null)
  const [sessions, setSessions] = useState<AiWorkspaceSessionSummary[]>([])
  const [session, setSession] = useState<AiWorkspaceSession | null>(null)
  const [taskResult, setTaskResult] = useState<AiWorkspaceTaskResult | null>(null)
  const [events, setEvents] = useState<AiWorkspaceTaskEvent[]>([])
  const [pageState, setPageState] = useState<PageState>('loading')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<BusyAction>('')
  const [showCreate, setShowCreate] = useState(false)
  const [title, setTitle] = useState('')
  const [symbol, setSymbol] = useState('')
  const [question, setQuestion] = useState('')
  const [message, setMessage] = useState('')

  const loadSessions = useCallback(async (signal?: AbortSignal) => { const next = await aiWorkspaceApi.listSessions(signal); setSessions(next); return next }, [])
  const loadSession = useCallback(async (sessionId: string, signal?: AbortSignal) => { const next = await aiWorkspaceApi.getSession(sessionId, signal); setSession(next); setTaskResult(null); setEvents([]) }, [])
  const loadWorkspace = useCallback(async (signal?: AbortSignal) => {
    setPageState('loading'); setError('')
    try {
      const [nextReadiness, nextSessions] = await Promise.all([aiWorkspaceApi.readiness(signal), loadSessions(signal)])
      setReadiness(nextReadiness); setPageState(nextSessions.length ? 'ready' : 'empty')
      if (nextSessions.length) await loadSession(session?.public_id ?? nextSessions[0].public_id, signal)
    } catch (caught) {
      if (signal?.aborted) return
      const kind = caught instanceof AiWorkspaceApiError ? classifyAiWorkspaceError(caught.status) : 'error'
      setPageState(kind === 'forbidden' || kind === 'unauthorized' ? 'forbidden' : 'error'); setError(errorMessage(caught, 'AI 工作台暂时无法读取。'))
    }
  }, [loadSession, loadSessions, session?.public_id])
  useEffect(() => { const controller = new AbortController(); void loadWorkspace(controller.signal); return () => controller.abort() }, [loadWorkspace])

  const loadTask = useCallback(async (taskId: string, signal?: AbortSignal) => {
    const [task, nextEvents] = await Promise.all([aiWorkspaceApi.getTask(taskId, signal), aiWorkspaceApi.listTaskEvents(taskId, signal)])
    setTaskResult((current) => current ? { ...current, task } : { task, assistant: null, blocked: task.status === 'blocked' }); setEvents(nextEvents); return task
  }, [])
  useEffect(() => {
    const task = taskResult?.task
    if (!task || !['queued', 'running', 'partial'].includes(task.status)) return
    const timer = window.setInterval(() => void loadTask(task.public_id).catch((caught) => setError(errorMessage(caught, '任务状态读取失败。'))), 3000)
    return () => window.clearInterval(timer)
  }, [loadTask, taskResult?.task])

  const handleSelect = async (sessionId: string) => { if (busy) return; setError(''); try { await loadSession(sessionId) } catch (caught) { setError(errorMessage(caught, '会话详情读取失败。')) } }
  const handleCreate = async () => {
    if (busy) return
    setBusy('create'); setError('')
    try {
      const created = await aiWorkspaceApi.createSession({ title: title.trim() || undefined, route: '/ai', symbol: symbol.trim().toUpperCase() || undefined, question: question.trim() || undefined }, `ai-session-${crypto.randomUUID()}`)
      setSessions((current) => [created, ...current]); setSession(created); setShowCreate(false); setTitle(''); setSymbol(''); setQuestion(''); setPageState('ready')
    } catch (caught) { setError(errorMessage(caught, 'AI 会话创建失败。')) } finally { setBusy('') }
  }
  const handleArchive = async () => {
    if (!session || busy || session.status === 'archived') return
    setBusy('archive'); setError('')
    try { const archived = await aiWorkspaceApi.archiveSession(session.public_id, `ai-archive-${crypto.randomUUID()}`); setSession(archived); setSessions((current) => current.map((item) => item.public_id === archived.public_id ? archived : item)) } catch (caught) { setError(errorMessage(caught, 'AI 会话归档失败。')) } finally { setBusy('') }
  }
  const handleSend = async () => {
    if (!session || session.status === 'archived' || busy || !message.trim()) return
    setBusy('send'); setError('')
    try { const result = await aiWorkspaceApi.sendMessage(session.public_id, message.trim(), `ai-message-${crypto.randomUUID()}`); setTaskResult(result); setMessage(''); setEvents(await aiWorkspaceApi.listTaskEvents(result.task.public_id)); setSession(await aiWorkspaceApi.getSession(session.public_id)) } catch (caught) { setError(errorMessage(caught, 'AI 消息发送失败。')) } finally { setBusy('') }
  }
  const handleCancel = async () => {
    if (!taskResult || busy) return
    setBusy('cancel'); setError('')
    try { const task = await aiWorkspaceApi.cancelTask(taskResult.task.public_id, `ai-cancel-${crypto.randomUUID()}`); setTaskResult({ ...taskResult, task }); setEvents(await aiWorkspaceApi.listTaskEvents(task.public_id)) } catch (caught) { setError(errorMessage(caught, 'AI 任务取消失败。')) } finally { setBusy('') }
  }
  const statusSummary = useMemo(() => readiness?.ready ? `provider ${readiness.provider_version} · model ${readiness.model}` : readiness ? `缺少 ${readiness.missing.join('、')}` : '正在读取 readiness', [readiness])

  return <div className="intelligence-page ai-workspace-page">
    <header className="intelligence-page-header ai-workspace-header"><div><span>GLOBAL AI / BOUNDED CONTEXT</span><h1>Ciclo AI 工作台</h1><p>AI 负责解释、比较与生成安全草稿；你负责审阅股票证据并作出最终决定。</p></div><span className={`intelligence-status ${readiness?.ready ? 'is-success' : 'is-warning'}`}><i />{readiness?.ready ? 'ready' : 'unavailable'}</span></header>
    {pageState === 'loading' && <section className="intelligence-panel ai-page-state" role="status"><LoaderCircle /><strong>正在读取 AI 工作台</strong><span>会话、provider readiness 与公开任务会一起核验。</span></section>}
    {pageState === 'forbidden' && <section className="intelligence-panel ai-page-state"><ShieldX /><strong>没有 AI 工作台访问权限</strong><span>当前账户不能读取会话、任务或来源内容。</span></section>}
    {pageState === 'error' && <section className="intelligence-panel ai-page-state"><XCircle /><strong>AI 工作台读取失败</strong><span>{error || '服务暂时不可用。'}</span><button className="ai-button" type="button" onClick={() => void loadWorkspace()}><RotateCcw />重新读取</button></section>}
    {(pageState === 'ready' || pageState === 'empty') && <div className="ai-workspace-shell">
      <aside className="intelligence-panel ai-session-sidebar" aria-label="AI 会话列表">
        <header className="ai-sidebar-header"><div><span>CONVERSATIONS</span><h2>研究会话</h2></div><button className="ai-icon-button" type="button" aria-label="新建 AI 研究会话" title="新建 AI 研究会话" onClick={() => setShowCreate((value) => !value)}><Plus /></button></header>
        {showCreate && <div className="ai-create-form"><label htmlFor="ai-session-title">会话标题</label><input id="ai-session-title" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：财报后股票研究" /><label htmlFor="ai-session-symbol">股票代码（可选）</label><input id="ai-session-symbol" value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="例如：AAPL" autoCapitalize="characters" /><label htmlFor="ai-session-question">研究问题（可选）</label><textarea id="ai-session-question" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="告诉 AI 你想核验什么" /><div><button className="ai-button primary" type="button" onClick={() => void handleCreate()} disabled={busy === 'create'}>{busy === 'create' ? <LoaderCircle /> : <Plus />}创建会话</button><button className="ai-button" type="button" onClick={() => setShowCreate(false)} disabled={Boolean(busy)}>取消</button></div></div>}
        {sessions.length ? <div className="ai-session-list">{sessions.map((item) => <SessionRow key={item.public_id} session={item} selected={item.public_id === session?.public_id} onSelect={() => void handleSelect(item.public_id)} />)}</div> : <div className="ai-sidebar-empty"><MessageSquareText /><strong>还没有研究会话</strong><span>新建一个会话，开始核验股票事实。</span><button className="ai-button primary" type="button" onClick={() => setShowCreate(true)}><Plus />新建会话</button></div>}
        <div className="ai-sidebar-foot"><span>{statusSummary}</span><Link to="/research">先看股票研究 <ArrowRight /></Link></div>
      </aside>
      <main className="ai-workspace-main">
        {!session && <section className="intelligence-panel ai-core-stage"><div className="ai-core-visual"><CicloCore label="Ciclo AI 工作台" state={readiness?.ready ? 'processing' : 'locked'} /></div><div className="ai-core-copy"><span className="ai-core-kicker"><Bot /> CICLO RESEARCH CORE</span><h2>{readiness?.ready ? '从一只股票开始研究' : 'AI 服务暂不可用'}</h2><p>{readiness?.ready ? '创建会话后，AI 只会读取服务端授权的研究上下文，并返回可核验的结构化回答。' : '当前没有可验证的 AI provider；不会生成占位回答、不会伪造回答，也不会展示虚假的执行轨迹。'}</p></div><div className="ai-readiness-note"><LockKeyhole /><div><strong>{readiness?.ready ? '服务 readiness 已通过' : '输入暂时锁定'}</strong><p>{readiness?.ready ? '可创建会话并提交研究问题。' : `服务端尚未返回完整 provider、模型版本与合同证明${readiness?.missing.length ? `（缺少：${readiness.missing.join('、')}）` : ''}。`}</p></div></div></section>}
        {session && <section className="intelligence-panel ai-conversation-panel"><header className="ai-conversation-header"><div><span>{session.status === 'archived' ? 'ARCHIVED SESSION' : 'ACTIVE SESSION'}</span><h2>{session.title}</h2><small>{session.public_id} · 创建于 {formatTime(session.created_at)}</small></div><div className="ai-conversation-actions"><button className="ai-button" type="button" onClick={() => void handleSelect(session.public_id)} disabled={Boolean(busy)}><RefreshCw />刷新</button><button className="ai-button danger" type="button" onClick={() => void handleArchive()} disabled={Boolean(busy) || session.status === 'archived'}><Archive />{session.status === 'archived' ? '已归档' : '归档'}</button></div></header><div className="ai-context-strip"><span>股票上下文由服务端读取</span><strong>{session.context_snapshot_public_id ? 'context snapshot 已绑定' : '尚未绑定 context snapshot'}</strong></div><SessionMessages session={session} />{taskResult && <TaskReceipt task={taskResult.task} events={events} onCancel={() => void handleCancel()} busy={busy === 'cancel'} />}{taskResult?.assistant && !session.messages.some((item) => item.role === 'assistant' && readAiWorkspaceStructuredMessage(item.content)) && <AnswerCard structured={taskResult.assistant.structured} createdAt={taskResult.assistant.created_at} />}{error && <p className="ai-inline-error" role="alert">{error}</p>}<div className="ai-composer"><label htmlFor="ai-message">向 Ciclo AI 提问</label><div><textarea id="ai-message" value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') void handleSend() }} disabled={session.status === 'archived' || !readiness?.ready || Boolean(busy)} placeholder={session.status === 'archived' ? '已归档的会话不能继续写入' : readiness?.ready ? '例如：这只股票当前最需要核验的反向证据是什么？' : 'AI 服务 readiness 未通过'} /><button className="ai-button primary send-button" type="button" onClick={() => void handleSend()} disabled={session.status === 'archived' || !readiness?.ready || Boolean(busy) || !message.trim()}>{busy === 'send' ? <LoaderCircle /> : <Send />}发送问题</button></div><p><ShieldCheck />只生成研究回答或个人模拟草稿；不提交订单，不启用自动实盘。</p></div></section>}
      </main>
      <aside className="ai-workspace-inspector" aria-label="AI 工作台说明"><section className="intelligence-panel"><header className="intelligence-section-heading"><div><span>RESPONSE CONTRACT</span><h2>结构化回答顺序</h2></div></header><ol className="response-contract-list"><li><span>01</span><strong>结论</strong></li><li><span>02</span><strong>引用与时间</strong></li><li><span>03</span><strong>支持证据</strong></li><li><span>04</span><strong>反向证据</strong></li><li><span>05</span><strong>风险与失效</strong></li><li><span>06</span><strong>下一步</strong></li></ol></section><section className="intelligence-panel"><header className="intelligence-section-heading"><div><span>CAPABILITY BOUNDARY</span><h2>允许的协助范围</h2></div></header><div className="ai-capability-list"><article><FileSearch /><div><strong>研究解释</strong><p>整理真实行情、引用、支持与反向证据。</p></div></article><article><MessageSquareText /><div><strong>安全草稿</strong><p>只能生成个人模拟草稿，不提交订单。</p></div></article><article><ShieldCheck /><div><strong>风险边界</strong><p>显示来源、时间、数据状态、风险和失效条件。</p></div></article></div><p className="intelligence-boundary-note"><ShieldCheck />自然语言 AI 永久没有订单提交、付款审批、权益审批或自动实盘启用权限。</p></section><section className="intelligence-panel ai-status-legend"><header className="intelligence-section-heading"><div><span>PUBLIC STATUS</span><h2>任务状态</h2></div></header><div>{AI_TASK_STATUSES.map((status) => <span key={status}><i className={`is-${status}`} />{STATUS_COPY[status]}</span>)}</div></section></aside>
    </div>}
  </div>
}
