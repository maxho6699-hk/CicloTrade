import {
  AlertTriangle,
  Ban,
  BarChart3,
  CheckCircle2,
  Clock3,
  Download,
  FileCheck2,
  LoaderCircle,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  Square,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  BacktestApiError,
  backtestApi,
  classifyBacktestError,
  type BacktestArtifact,
  type BacktestErrorKind,
  type BacktestJob,
  type BacktestStatus,
} from '../../api/backtests.ts'
import { localizeText } from '../../i18n/runtime.ts'
import { useLocale } from '../../i18n/useLocale.ts'
import { SelectField } from '../ui/SelectField.tsx'
import '../../styles/lab-backtests.css'

interface BacktestWorkspaceProps {
  authenticated: boolean
  maxBacktestYears: number
  symbol: string
  timeframe: string
  lookback: string
  commission: string
  slippage: string
  profitTarget: string
  draftNotice: string
  onSymbolChange: (value: string) => void
  onLookbackChange: (value: string) => void
  onCommissionChange: (value: string) => void
  onSlippageChange: (value: string) => void
  onProfitTargetChange: (value: string) => void
}

type QueuePhase = 'loading' | 'ready' | 'signed-out' | BacktestErrorKind

const statusCopy: Record<BacktestStatus, { label: string; detail: string }> = {
  queued: { label: '排队中', detail: '任务已登记，等待冻结输入和可用计算资源。' },
  running: { label: '运行中', detail: 'Worker 正在执行真实阶段；页面只显示公开进度。' },
  succeeded: { label: '已完成', detail: '任务已完成，只有哈希验证通过的输出可下载。' },
  failed: { label: '执行失败', detail: '本次 attempt 未生成成功制品，可核对输入后重新创建任务。' },
  cancelled: { label: '已取消', detail: '任务已停止，不会把临时输出标记为成功制品。' },
  blocked: { label: '已被替代', detail: '该任务不再代表当前版本，只保留只读审计记录。' },
}

const stageLabel: Record<BacktestJob['progressStage'], string> = {
  queued: '等待领取',
  loading: '读取冻结输入',
  executing: '执行策略',
  finalizing: '封存与核验',
}

const metricLabels: Record<string, string> = {
  total_return_pct: '总收益（回测）',
  max_drawdown_pct: '最大回撤',
  trade_count: '交易次数',
  win_rate_pct: '胜率',
  oos_return_pct: 'OOS 收益',
  walk_forward_score: 'Walk-forward 分数',
  cost_adjusted_return_pct: '计费后收益',
}

function displayTime(value: string | null, formatLocale: string) {
  if (!value) return localizeText('未提供')
  const parsed = new Date(value)
  return Number.isNaN(parsed.valueOf())
    ? value
    : parsed.toLocaleString(formatLocale, { hour12: false, dateStyle: 'medium', timeStyle: 'short' })
}

function displayMetric(key: string, value: string | number | boolean | null, formatLocale: string) {
  if (value === null) return localizeText('未提供')
  if (typeof value === 'boolean') return localizeText(value ? '是' : '否')
  if (typeof value === 'number') {
    const formatted = new Intl.NumberFormat(formatLocale, { maximumFractionDigits: 4 }).format(value)
    return key.endsWith('_pct') ? `${formatted}%` : formatted
  }
  return localizeText(value)
}

function QueueState({ phase, retry }: { phase: QueuePhase; retry: () => void }) {
  const states: Partial<Record<QueuePhase, { icon: typeof LockKeyhole; title: string; detail: string }>> = {
    loading: { icon: LoaderCircle, title: '正在读取真实任务', detail: '正在向回测队列核对当前账户的任务状态。' },
    'signed-out': { icon: LockKeyhole, title: '登录后查看真实任务', detail: '演示模式不会请求、生成或展示任何私人回测任务。' },
    locked: { icon: LockKeyhole, title: '回测队列当前未启用', detail: '服务端功能开关尚未开放；页面不会显示演示任务或伪造结果。' },
    unauthorized: { icon: LockKeyhole, title: '登录状态已失效', detail: '重新登录后才能读取你的回测任务。' },
    forbidden: { icon: Ban, title: '当前账户无权执行此操作', detail: '历史和限制仍会保留；权限以服务端拒绝为准。' },
    missing: { icon: AlertTriangle, title: '任务已经不存在', detail: '资源可能被替代或不属于当前账户，请重新读取列表。' },
    conflict: { icon: AlertTriangle, title: '任务状态已变化', detail: '服务端拒绝了过期操作，请读取最新状态。' },
    limited: { icon: Clock3, title: '当前队列额度已满', detail: '保留现有任务，稍后再创建新的任务。' },
    error: { icon: AlertTriangle, title: '暂时无法读取任务', detail: '已保留本页策略参数，可稍后重试真实队列。' },
  }
  const state = states[phase]
  if (!state) return null
  const Icon = state.icon
  return (
    <div className={`lab-queue-state is-${phase}`} role={phase === 'error' ? 'alert' : 'status'}>
      <Icon className={phase === 'loading' ? 'is-spinning' : ''} size={22} aria-hidden="true" />
      <span><strong>{state.title}</strong><small>{state.detail}</small></span>
      {['error', 'missing', 'conflict'].includes(phase) && (
        <button className="button secondary" type="button" onClick={retry}>
          <RefreshCw size={15} /> 重新读取
        </button>
      )}
    </div>
  )
}

export function BacktestWorkspace(props: BacktestWorkspaceProps) {
  const { formatLocale } = useLocale()
  const [jobs, setJobs] = useState<BacktestJob[]>([])
  const [selected, setSelected] = useState<BacktestJob | null>(null)
  const [phase, setPhase] = useState<QueuePhase>(props.authenticated ? 'loading' : 'signed-out')
  const [notice, setNotice] = useState('')
  const [cancelBusy, setCancelBusy] = useState(false)
  const [downloadBusy, setDownloadBusy] = useState<string | null>(null)
  const hasJobs = useRef(false)
  const lookbackYears = Number.parseInt(props.lookback, 10)

  const activeJobs = useMemo(
    () => jobs.some((job) => job.status === 'queued' || job.status === 'running'),
    [jobs],
  )

  const updateJob = useCallback((job: BacktestJob) => {
    setJobs((current) => current.map((item) => item.id === job.id ? job : item))
    setSelected((current) => current?.id === job.id ? job : current)
  }, [])

  const loadJobs = useCallback(async (signal?: AbortSignal) => {
    if (!props.authenticated) {
      setPhase('signed-out')
      setJobs([])
      setSelected(null)
      return
    }
    try {
      const items = await backtestApi.listJobs(signal)
      hasJobs.current = items.length > 0
      setJobs(items)
      setSelected((current) => items.find((item) => item.id === current?.id) ?? items[0] ?? null)
      setPhase('ready')
    } catch (error) {
      if (signal?.aborted) return
      const status = error instanceof BacktestApiError ? error.status : 0
      if (hasJobs.current) {
        setPhase('ready')
        setNotice(localizeText('最新状态读取失败；继续显示上次已确认的任务，稍后自动重试。'))
      } else {
        setPhase(classifyBacktestError(status, 'list'))
        setNotice(error instanceof Error ? localizeText(error.message) : localizeText('回测队列暂时不可用。'))
      }
    }
  }, [props.authenticated])

  useEffect(() => {
    const controller = new AbortController()
    setPhase(props.authenticated ? 'loading' : 'signed-out')
    void loadJobs(controller.signal)
    return () => controller.abort()
  }, [loadJobs, props.authenticated])

  useEffect(() => {
    if (!props.authenticated || !activeJobs) return
    const timer = window.setInterval(() => void loadJobs(), 8_000)
    return () => window.clearInterval(timer)
  }, [activeJobs, loadJobs, props.authenticated])

  const chooseJob = async (job: BacktestJob) => {
    setSelected(job)
    try {
      const detail = await backtestApi.getJob(job.id)
      updateJob(detail)
      setPhase('ready')
    } catch (error) {
      const status = error instanceof BacktestApiError ? error.status : 0
      if (jobs.length === 0) setPhase(classifyBacktestError(status, 'item'))
      setNotice(error instanceof Error ? localizeText(error.message) : localizeText('无法读取任务详情。'))
    }
  }

  const cancel = async () => {
    if (!selected || cancelBusy || !['queued', 'running'].includes(selected.status)) return
    setCancelBusy(true)
    setNotice('')
    try {
      const job = await backtestApi.cancelJob(selected.id)
      updateJob(job)
      setNotice(localizeText(job.status === 'cancelled' ? '任务已经取消。' : '取消请求已登记，运行中的阶段会安全停止。'))
    } catch (error) {
      const status = error instanceof BacktestApiError ? error.status : 0
      if (jobs.length === 0) setPhase(classifyBacktestError(status, 'write'))
      setNotice(error instanceof Error ? localizeText(error.message) : localizeText('取消状态未确认，请重新读取。'))
    } finally {
      setCancelBusy(false)
    }
  }

  const download = async (artifact: BacktestArtifact) => {
    if (!selected || downloadBusy) return
    setDownloadBusy(artifact.artifactKey)
    setNotice('')
    try {
      const blob = await backtestApi.downloadArtifact(selected.id, artifact)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = artifact.artifactKey
      document.body.append(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      setNotice(localizeText(`已核对并下载 ${artifact.artifactKey}。`))
    } catch (error) {
      const status = error instanceof BacktestApiError ? error.status : 0
      if (jobs.length === 0) setPhase(classifyBacktestError(status, 'artifact'))
      setNotice(error instanceof Error ? localizeText(error.message) : localizeText('制品下载失败。'))
    } finally {
      setDownloadBusy(null)
    }
  }

  return (
    <div className="lab-backtest-workbench">
      <div className="lab-config-zone">
        <div className="backtest-form">
          <label>
            股票
            <input value={props.symbol} onChange={(event) => props.onSymbolChange(event.target.value.toUpperCase())} />
          </label>
          <SelectField label="样本期" value={props.lookback} onValueChange={props.onLookbackChange} options={[1, 3, 5, 10].map((years) => ({ value: `${years} 年`, label: `${years} 年`, disabled: props.maxBacktestYears < years }))} />
          <label>
            手续费（%）
            <input inputMode="decimal" value={props.commission} onChange={(event) => props.onCommissionChange(event.target.value)} />
          </label>
          <label>
            滑点（%）
            <input inputMode="decimal" value={props.slippage} onChange={(event) => props.onSlippageChange(event.target.value)} />
          </label>
        </div>
        <div className="parameter-grid">
          <label>RSI 周期<input type="number" defaultValue="14" min="2" max="100" /></label>
          <label>均线周期<input type="number" defaultValue="50" min="5" max="300" /></label>
          <label>单笔风险（%）<input type="number" defaultValue="1" min="0.1" max="5" step="0.1" /></label>
          <SelectField label="分批止盈" value={props.profitTarget} onValueChange={props.onProfitTargetChange} options={['1.5R', '2R', '3R'].map((value) => ({ value, label: value }))} />
        </div>
        <section className="lab-submit-gate" aria-labelledby="lab-submit-gate-title">
          <LockKeyhole size={19} aria-hidden="true" />
          <span>
            <strong id="lab-submit-gate-title">新任务等待服务端冻结数据入口</strong>
            <small>系统尚未开放把所选股票数据冻结成可核验快照的准备步骤。本页不会伪造 SHA-256，也不会创建永远停在排队中的任务。</small>
          </span>
          <button className="button primary" type="button" disabled aria-describedby="lab-submit-gate-reason">
            <BarChart3 size={15} /> 等待数据快照准备
          </button>
          <p id="lab-submit-gate-reason">历史任务、真实状态、取消请求和已验证制品下载已连接服务端回测队列。</p>
        </section>
        <div className="lab-run-row">
          <button className="button secondary" type="button" onClick={() => void loadJobs()} disabled={!props.authenticated || phase === 'loading'}>
            <RefreshCw size={15} className={phase === 'loading' ? 'is-spinning' : ''} /> 重新读取任务
          </button>
          <span>
            {props.draftNotice || (props.maxBacktestYears && lookbackYears <= props.maxBacktestYears
              ? `当前草稿：${props.symbol} · ${props.lookback} ${props.timeframe} · 手续费 ${props.commission}% · 滑点 ${props.slippage}%。`
              : '当前方案未开放新的回测参数；历史任务仍可只读。')}
          </span>
        </div>
      </div>

      <aside className="lab-result-zone" aria-label="真实回测任务与结果">
        <header className="lab-queue-heading">
          <span><strong>真实任务队列</strong><small>最多读取当前账户最近 50 项</small></span>
          <ShieldCheck size={18} aria-label="服务端所有者隔离" />
        </header>
        {phase !== 'ready' ? <QueueState phase={phase} retry={() => void loadJobs()} /> : jobs.length === 0 ? (
          <div className="lab-queue-state is-empty" role="status">
            <Clock3 size={22} /><span><strong>暂无真实回测任务</strong><small>数据快照准备入口开放后，可从当前策略草稿创建第一项任务。</small></span>
          </div>
        ) : (
          <div className="lab-job-list" role="list" aria-label="回测任务列表">
            {jobs.map((job) => (
              <button className={selected?.id === job.id ? 'is-selected' : ''} type="button" role="listitem" key={job.id} onClick={() => void chooseJob(job)}>
                <span className={`lab-job-status is-${job.status}`}><i />{statusCopy[job.status].label}</span>
                <strong>{job.manifest.parameters?.symbol || job.jobType}</strong>
                <small>{displayTime(job.updatedAt, formatLocale)} · attempt {job.attemptCount}/{job.maxAttempts}</small>
              </button>
            ))}
          </div>
        )}

        {selected && (
          <section className="lab-job-detail" aria-labelledby="lab-job-detail-title">
            <header>
              <span>
                <small>{selected.jobType}</small>
                <strong id="lab-job-detail-title">{statusCopy[selected.status].label}</strong>
              </span>
              {(selected.status === 'queued' || selected.status === 'running') && (
                <button className="button danger" type="button" onClick={() => void cancel()} disabled={cancelBusy || selected.cancelRequested} aria-busy={cancelBusy}>
                  {cancelBusy ? <LoaderCircle className="is-spinning" size={15} /> : <Square size={14} />}
                  {selected.cancelRequested ? '取消处理中' : '取消任务'}
                </button>
              )}
            </header>
            <p>{statusCopy[selected.status].detail}</p>
            <dl className="lab-job-facts">
              <div><dt>任务 ID</dt><dd>{selected.id}</dd></div>
              <div><dt>公开阶段</dt><dd>{stageLabel[selected.progressStage]}</dd></div>
              <div><dt>数据截止</dt><dd>{selected.manifest.dataset_end}</dd></div>
              <div><dt>代码版本</dt><dd>{selected.manifest.code_bundle_sha256}</dd></div>
              <div><dt>更新时间</dt><dd>{displayTime(selected.updatedAt, formatLocale)}</dd></div>
              <div><dt>完成时间</dt><dd>{displayTime(selected.completedAt, formatLocale)}</dd></div>
            </dl>
            {selected.status === 'running' && selected.progress !== null && (
              <div className="lab-real-progress" role="progressbar" aria-label="服务端真实回测进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(selected.progress * 100)}>
                <span><strong>{stageLabel[selected.progressStage]}</strong><b>{Math.round(selected.progress * 100)}%</b></span>
                <i><em style={{ width: `${selected.progress * 100}%` }} /></i>
              </div>
            )}
            {selected.evidence && Object.keys(selected.evidence.metrics).length > 0 && (
              <section className="lab-result-metrics" aria-label="服务端回测指标">
                {Object.entries(selected.evidence.metrics).map(([key, value]) => (
                  <div key={key}><span>{localizeText(metricLabels[key] || key)}</span><strong>{displayMetric(key, value, formatLocale)}</strong></div>
                ))}
              </section>
            )}
            {selected.evidence?.limitations.length ? (
              <ul className="lab-result-limitations">
                {selected.evidence.limitations.map((item) => <li key={item}><AlertTriangle size={14} />{localizeText(item)}</li>)}
              </ul>
            ) : null}
            {selected.artifacts.length > 0 && (
              <section className="lab-artifacts" aria-labelledby="lab-artifacts-title">
                <header><FileCheck2 size={16} /><strong id="lab-artifacts-title">已验证制品</strong></header>
                {selected.artifacts.map((artifact) => (
                  <article key={artifact.artifactKey}>
                    <CheckCircle2 size={16} aria-label="哈希已验证" />
                    <span><strong>{artifact.artifactKey}</strong><small>SHA-256 · {artifact.sha256}</small></span>
                    <button className="button secondary" type="button" onClick={() => void download(artifact)} disabled={downloadBusy !== null} aria-busy={downloadBusy === artifact.artifactKey}>
                      {downloadBusy === artifact.artifactKey ? <LoaderCircle className="is-spinning" size={15} /> : <Download size={15} />}
                      下载
                    </button>
                  </article>
                ))}
              </section>
            )}
          </section>
        )}
        {notice && <p className="lab-queue-notice" role="status" aria-live="polite">{notice}</p>}
        <p className="lab-disclaimer">页面不显示 lease、heartbeat、fencing token 或内部 Worker 日志；没有服务端结果时不生成收益、回撤、OOS/WF 或压力测试数字。</p>
      </aside>
    </div>
  )
}
