import { ArrowLeft, Download, FileCheck2, LoaderCircle, RefreshCw, RotateCcw, Square } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  backtestApi,
  BacktestApiError,
  classifyBacktestError,
  type BacktestArtifact,
  type BacktestJob,
} from '../api/backtests'
import { TruthState, WorkflowStatusPill, WorkflowStatusRail } from '../components/intelligence/IntelligencePrimitives'
import { WORKFLOW_STATUS_COPY } from '../components/intelligence/workflowStatus'
import '../styles/intelligence.css'

type LoadState = 'loading' | 'ready' | 'missing' | 'forbidden' | 'error'

function formatTime(value: string | null) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.valueOf()) ? '—' : new Intl.DateTimeFormat('zh-HK', { dateStyle: 'medium', timeStyle: 'medium', timeZone: 'Asia/Hong_Kong' }).format(parsed)
}

export function WorkflowTaskPage() {
  const { taskId = '' } = useParams()
  const [job, setJob] = useState<BacktestJob | null>(null)
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoadState('loading')
    setError('')
    try {
      const next = await backtestApi.getJob(taskId, signal)
      setJob(next)
      setLoadState('ready')
    } catch (caught) {
      if (signal?.aborted) return
      const kind = caught instanceof BacktestApiError ? classifyBacktestError(caught.status, 'item') : 'error'
      setJob(null)
      setLoadState(kind === 'missing' ? 'missing' : kind === 'forbidden' || kind === 'unauthorized' ? 'forbidden' : 'error')
      setError(caught instanceof Error ? caught.message : '任务读取失败。')
    }
  }, [taskId])

  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load])

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return
    const timer = window.setInterval(() => void load(), 10_000)
    return () => window.clearInterval(timer)
  }, [job, load])

  async function cancelTask() {
    if (!job || busy) return
    setBusy('cancel')
    setError('')
    try {
      setJob(await backtestApi.cancelJob(job.id))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '取消任务失败。')
    } finally {
      setBusy('')
    }
  }

  async function downloadArtifact(artifact: BacktestArtifact) {
    if (!job || busy) return
    setBusy(artifact.artifactKey)
    setError('')
    try {
      const blob = await backtestApi.downloadArtifact(job.id, artifact)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = artifact.artifactKey
      link.click()
      URL.revokeObjectURL(url)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '制品下载失败。')
    } finally {
      setBusy('')
    }
  }

  const canCancel = job?.status === 'queued' || job?.status === 'running'
  return <div className="intelligence-page workflow-page">
    <header className="intelligence-page-header">
      <div><span>AI WORKFLOW / REAL TASK</span><h1>Workflow 任务详情</h1><p>只展示服务端返回的任务、状态、时间与已验证制品，不生成模拟日志或伪进度。</p></div>
      <Link className="intelligence-back-link" to="/lab"><ArrowLeft />返回实验室</Link>
    </header>

    {loadState === 'loading' && <section className="intelligence-panel workflow-loading" role="status"><LoaderCircle /><strong>正在读取真实任务</strong><span>任务服务返回前不会补写状态。</span></section>}
    {loadState === 'missing' && <TruthState tone="warning" title="任务不存在" detail="任务服务没有返回该任务；请检查任务 ID 或从实验室重新进入。" action={<Link className="intelligence-inline-action" to="/lab">打开实验室</Link>} />}
    {loadState === 'forbidden' && <TruthState tone="error" title="没有任务访问权限" detail="当前账户不能读取这个任务，页面没有展示任何任务内容。" />}
    {loadState === 'error' && <TruthState tone="error" title="任务读取失败" detail={error || '任务服务暂时不可用。'} action={<button className="intelligence-inline-action" type="button" onClick={() => void load()}><RotateCcw />重新读取</button>} />}

    {loadState === 'ready' && job && <div className="workflow-layout">
      <section className="intelligence-panel workflow-main">
        <header className="workflow-title-row">
          <div><span>{job.jobType}</span><h2>{job.id}</h2></div>
          <WorkflowStatusPill status={job.status} />
        </header>
        <WorkflowStatusRail status={job.status} />
        <section className="workflow-facts" aria-label="任务事实">
          <div><span>当前状态</span><strong>{WORKFLOW_STATUS_COPY[job.status]}</strong></div>
          <div><span>公开阶段</span><strong>{job.progressStage}</strong></div>
          <div><span>执行进度</span><strong>{job.progress === null ? '—' : `${Math.round(job.progress * 100)}%`}</strong></div>
          <div><span>Attempt</span><strong>{job.attemptCount} / {job.maxAttempts}</strong></div>
        </section>
        <section className="workflow-timeline" aria-labelledby="workflow-timeline-title">
          <header className="intelligence-section-heading"><div><span>PUBLIC TIMELINE</span><h2 id="workflow-timeline-title">真实任务时间</h2></div></header>
          <ol>
            <li><i /><div><strong>任务已创建</strong><time>{formatTime(job.createdAt)}</time></div></li>
            <li><i /><div><strong>最近状态更新</strong><time>{formatTime(job.updatedAt)}</time></div></li>
            {job.completedAt && <li><i /><div><strong>任务进入终态</strong><time>{formatTime(job.completedAt)}</time></div></li>}
          </ol>
        </section>
        {error && <p className="workflow-inline-error" role="alert">{error}</p>}
        <footer className="workflow-actions">
          <button type="button" onClick={() => void load()} disabled={Boolean(busy)}><RefreshCw />刷新任务</button>
          {canCancel && <button className="is-danger" type="button" onClick={() => void cancelTask()} disabled={Boolean(busy) || job.cancelRequested}><Square />{job.cancelRequested ? '取消请求已提交' : busy === 'cancel' ? '正在取消' : '取消任务'}</button>}
        </footer>
      </section>

      <aside className="workflow-inspector">
        <section className="intelligence-panel">
          <header className="intelligence-section-heading"><div><span>INPUT BINDING</span><h2>冻结输入</h2></div></header>
          <dl className="workflow-detail-list">
            <div><dt>评估日期</dt><dd>{job.manifest.evaluation_date}</dd></div>
            <div><dt>数据截止</dt><dd>{job.manifest.dataset_end}</dd></div>
            <div><dt>代码包 SHA-256</dt><dd>{job.manifest.code_bundle_sha256}</dd></div>
            <div><dt>Manifest SHA-256</dt><dd>{job.manifestSha256 ?? '—'}</dd></div>
          </dl>
        </section>
        <section className="intelligence-panel">
          <header className="intelligence-section-heading"><div><span>VERIFIED ARTIFACTS</span><h2>已验证制品</h2></div><FileCheck2 /></header>
          {job.artifacts.length ? <div className="workflow-artifacts">{job.artifacts.map((artifact) => <article key={artifact.artifactKey}><div><strong>{artifact.artifactKey}</strong><small>{artifact.sha256}</small></div><button type="button" aria-label={`下载 ${artifact.artifactKey}`} onClick={() => void downloadArtifact(artifact)} disabled={Boolean(busy)}>{busy === artifact.artifactKey ? <LoaderCircle /> : <Download />}</button></article>)}</div> : <TruthState title="暂无已验证制品" detail="只有成功任务且服务端返回完整哈希证明后，才会出现下载项。" />}
        </section>
      </aside>
    </div>}
  </div>
}
