import { ArrowLeft, LoaderCircle, RefreshCw, RotateCcw, Square } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { classifyWorkflowError, workflowApi, type WorkflowTask } from '../api/workflows'
import { TruthState, WorkflowStatusPill, WorkflowStatusRail } from '../components/intelligence/IntelligencePrimitives'
import { WORKFLOW_STATUS_COPY } from '../components/intelligence/workflowStatus'
import '../styles/intelligence.css'

type LoadState = 'loading' | 'ready' | 'missing' | 'forbidden' | 'error'
const TERMINAL = new Set<WorkflowTask['status']>(['partial', 'succeeded', 'failed', 'cancelled', 'blocked', 'timed_out'])
function formatTime(value: string | null) { if (!value) return '—'; const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? '—' : new Intl.DateTimeFormat('zh-HK', { dateStyle: 'medium', timeStyle: 'medium', timeZone: 'Asia/Hong_Kong' }).format(parsed) }
function displayJson(value: unknown) { if (value === null || value === undefined) return '—'; try { return JSON.stringify(value, null, 2) } catch { return '—' } }
function sourceHash(task: WorkflowTask) { const deliberation = task.deliberation; if (deliberation && typeof deliberation === 'object' && !Array.isArray(deliberation) && typeof deliberation.source_event_sha256 === 'string') return deliberation.source_event_sha256; return task.source_sha256 }

export function WorkflowTaskPage() {
  const { taskId = '' } = useParams()
  const [task, setTask] = useState<WorkflowTask | null>(null)
  const [tasks, setTasks] = useState<WorkflowTask[]>([])
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoadState('loading'); setError('')
    try {
      if (!taskId) { setTask(null); setTasks(await workflowApi.list(signal)); setLoadState('ready'); return }
      const next = await workflowApi.get(taskId, signal); setTask(next); setTasks([]); setLoadState('ready')
    }
    catch (caught) { if (signal?.aborted) return; const status = caught instanceof Error && 'status' in caught ? Number((caught as { status?: number }).status) : 0; const kind = classifyWorkflowError(status, 'read'); setTask(null); setLoadState(kind === 'missing' ? 'missing' : kind === 'forbidden' ? 'forbidden' : 'error'); setError(caught instanceof Error ? caught.message : 'Workflow 任务读取失败。') }
  }, [taskId])

  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load])
  useEffect(() => { if (!task || !['queued', 'running'].includes(task.status)) return; const timer = window.setInterval(() => void load(), 10_000); return () => window.clearInterval(timer) }, [task, load])

  async function cancelTask() { if (!task || busy || TERMINAL.has(task.status)) return; setBusy('cancel'); setError(''); try { setTask(await workflowApi.cancel(task.task_public_id)) } catch (caught) { setError(caught instanceof Error ? caught.message : '取消任务失败。') } finally { setBusy('') } }
  async function retryTask() { if (!task || busy || !TERMINAL.has(task.status)) return; setBusy('retry'); setError(''); try { const created = await workflowApi.retry(task.task_public_id); setTask(await workflowApi.get(created.task_public_id)) } catch (caught) { setError(caught instanceof Error ? caught.message : '重试任务失败。') } finally { setBusy('') } }

  const canCancel = Boolean(task && ['queued', 'running'].includes(task.status) && !task.cancel_requested)
  const canRetry = Boolean(task && TERMINAL.has(task.status))
  return <div className="intelligence-page workflow-page">
    <header className="intelligence-page-header"><div><span>WORKFLOW REGISTRY / PUBLIC TASK</span><h1>Workflow 任务详情</h1><p>只展示服务端公开任务、context、result、哈希绑定与公开事件；不展示账户归属、原始 provenance 或任意制品 URL。</p></div><Link className="intelligence-back-link" to="/lab"><ArrowLeft />返回实验室</Link></header>
    {loadState === 'loading' && <section className="intelligence-panel workflow-loading" role="status"><LoaderCircle /><strong>正在读取真实 Workflow 任务</strong><span>任务服务返回前不会补写状态。</span></section>}
    {loadState === 'missing' && <TruthState tone="warning" title="任务不存在" detail="Workflow Registry 没有返回该任务；请检查任务 ID 或从任务入口重新进入。" action={<Link className="intelligence-inline-action" to="/lab">打开实验室</Link>} />}
    {loadState === 'forbidden' && <TruthState tone="error" title="没有任务访问权限" detail="当前账户不能读取这个任务，页面没有展示任何任务内容。" />}
    {loadState === 'error' && <TruthState tone="error" title="任务读取失败" detail={error || 'Workflow 服务暂时不可用。'} action={<button className="intelligence-inline-action" type="button" onClick={() => void load()}><RotateCcw />重新读取</button>} />}
    {loadState === 'ready' && !taskId && <section className="intelligence-panel workflow-main" aria-labelledby="workflow-index-title"><header className="workflow-title-row"><div><span>WORKFLOW REGISTRY / PUBLIC TASKS</span><h2 id="workflow-index-title">当前 Workflow 任务</h2></div><button type="button" onClick={() => void load()} disabled={Boolean(busy)}><RefreshCw />刷新列表</button></header>{tasks.length ? <div className="workflow-artifacts">{tasks.map((item) => <Link className="workflow-index-link" to={`/workflow/${encodeURIComponent(item.task_public_id)}`} key={item.task_public_id}><article><div><strong>{item.source_kind} · {item.task_public_id}</strong><small>{item.source_public_id} · {WORKFLOW_STATUS_COPY[item.status]} · {formatTime(item.updated_at)}</small></div></article></Link>)}</div> : <TruthState title="暂无 Workflow 任务" detail="Workflow Registry 尚未返回当前账户可见的真实任务。不会展示演示记录。" />}</section>}
    {loadState === 'ready' && task && <div className="workflow-layout">
      <section className="intelligence-panel workflow-main">
        <header className="workflow-title-row"><div><span>{task.source_kind}</span><h2>{task.task_public_id}</h2><small>来源记录：{task.source_public_id}</small></div><WorkflowStatusPill status={task.status} /></header>
        <WorkflowStatusRail status={task.status} />
        <section className="workflow-facts" aria-label="任务事实"><div><span>当前状态</span><strong>{WORKFLOW_STATUS_COPY[task.status]}</strong></div><div><span>Attempt</span><strong>{task.attempt}</strong></div><div><span>创建时间</span><strong>{formatTime(task.created_at)}</strong></div><div><span>更新时间</span><strong>{formatTime(task.updated_at)}</strong></div></section>
        <section className="workflow-timeline" aria-labelledby="workflow-timeline-title"><header className="intelligence-section-heading"><div><span>PUBLIC EVENTS</span><h2 id="workflow-timeline-title">公开任务事件</h2></div></header>{task.events.length ? <ol>{task.events.map((event) => <li key={`${event.seq}-${event.created_at}`}><i /><div><strong>{WORKFLOW_STATUS_COPY[event.status]} · {event.event_type}</strong><time>{formatTime(event.created_at)}</time></div></li>)}</ol> : <TruthState title="暂无公开事件" detail="Workflow Registry 尚未返回可展示的公开事件。" />}</section>
        {error && <p className="workflow-inline-error" role="alert">{error}</p>}
        <footer className="workflow-actions"><button type="button" onClick={() => void load()} disabled={Boolean(busy)}><RefreshCw />刷新任务</button>{canCancel && <button className="is-danger" type="button" onClick={() => void cancelTask()} disabled={Boolean(busy)}><Square />{busy === 'cancel' ? '正在取消' : '取消任务'}</button>}{canRetry && <button type="button" onClick={() => void retryTask()} disabled={Boolean(busy)}><RotateCcw />{busy === 'retry' ? '正在重试' : '重试任务'}</button>}</footer>
      </section>
      <aside className="workflow-inspector">
        <section className="intelligence-panel"><header className="intelligence-section-heading"><div><span>SAFE CONTEXT</span><h2>任务上下文</h2></div></header><pre className="workflow-json" aria-label="安全任务上下文">{displayJson(task.context)}</pre></section>
        <section className="intelligence-panel"><header className="intelligence-section-heading"><div><span>SAFE RESULT</span><h2>任务结果</h2></div></header>{task.result === null ? <TruthState title="暂无任务结果" detail="只有服务端返回 result 后才会展示；不会用演示结果替代。" /> : <pre className="workflow-json" aria-label="安全任务结果">{displayJson(task.result)}</pre>}</section>
        <section className="intelligence-panel"><header className="intelligence-section-heading"><div><span>HASH BINDINGS</span><h2>来源与完整性</h2></div></header><dl className="workflow-detail-list"><div><dt>Source SHA-256</dt><dd>{sourceHash(task) ?? '—'}</dd></div><div><dt>Context SHA-256</dt><dd>{task.context_sha256}</dd></div><div><dt>Provenance SHA-256</dt><dd>{task.provenance_sha256}</dd></div><div><dt>Result SHA-256</dt><dd>{task.result_sha256 ?? '—'}</dd></div></dl></section>
      </aside>
    </div>}
  </div>
}
