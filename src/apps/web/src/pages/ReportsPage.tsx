import { BarChart3, Download, FlaskConical, ShieldCheck, TrendingUp } from 'lucide-react'
import { useState } from 'react'
import { useWorkspace } from '../api/workspace-context'
import { PageHeader } from '../components/PageHeader'
import { WorkspaceState } from '../components/WorkspaceState'
import { modelReports, reportReturns } from '../data/workspace'
import { getFormatLocale } from '../i18n/runtime'

export function ReportsPage() {
  const workspace = useWorkspace()
  const [view, setView] = useState('组合表现')
  const snapshots = workspace.data?.performance.items ?? []
  const authenticated = workspace.mode === 'authenticated'
  const demoMode = workspace.mode === 'demo' || workspace.mode === 'offline'
  const realReturns = snapshots.map((item) => item.total_pnl)
  const values = authenticated ? realReturns : demoMode ? reportReturns : []
  const maximum = Math.max(...values.map((value) => Math.abs(value)), 1)
  const first = snapshots[0]
  const latest = snapshots.at(-1)
  const returnPct = first && latest && first.initial_cash ? latest.total_pnl / first.initial_cash * 100 : demoMode ? 12.84 : null
  let peak = Number.NEGATIVE_INFINITY
  const maxDrawdown = snapshots.reduce((maximumDrawdown, item) => {
    peak = Math.max(peak, item.total_equity)
    return peak > 0 ? Math.max(maximumDrawdown, (peak - item.total_equity) / peak * 100) : maximumDrawdown
  }, 0)
  const exportReport = () => {
    const usingSnapshots = authenticated
    if (usingSnapshots && snapshots.length === 0) return
    const header = usingSnapshots ? 'captured_at,currency,cash,market_value,realized_pnl,unrealized_pnl,total_equity,total_pnl\n' : 'model,version,state,sample_size,win_rate,max_drawdown,stress_expectancy,stability\n'
    const rows = usingSnapshots ? snapshots.map((item) => [item.captured_at, item.currency, item.cash, item.market_value, item.realized_pnl, item.unrealized_pnl, item.total_equity, item.total_pnl].join(',')).join('\n') : modelReports.map((model) => [model.name, model.version, model.state, model.sampleSize, model.winRate, model.maxDrawdown, model.stressExpectancy, model.stability].join(',')).join('\n')
    const url = URL.createObjectURL(new Blob([header, rows], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'ciclotrade-model-report.csv'
    link.click()
    URL.revokeObjectURL(url)
  }
  return (
    <div className="page operations-page">
      <PageHeader kicker="REPORTS / EVIDENCE" title="报告中心" description="收益、回撤、样本量和模型版本同时展示，避免只看一个漂亮的胜率。" />
      <WorkspaceState empty={workspace.mode === 'authenticated' && snapshots.length === 0} emptyText="量化账本还没有权益快照；报告页不会用演示收益替代真实账户报告。" />
      <div className="toolbar-row"><div className="segmented-control report-tabs">{['组合表现', '策略回测', '模型版本'].map((item) => <button className={view === item ? 'active' : ''} type="button" key={item} onClick={() => setView(item)}>{item}</button>)}</div><button className="button secondary" type="button" disabled={authenticated && snapshots.length === 0} onClick={exportReport}><Download size={16} /> 导出报告</button></div>

      <section className="metric-grid report-metrics">
        <article><span>累计收益</span><strong className={returnPct === null ? '' : returnPct >= 0 ? 'positive-text' : 'negative-text'}>{returnPct === null ? '暂无记录' : `${returnPct >= 0 ? '+' : ''}${returnPct.toFixed(2)}%`}</strong><small>{snapshots.length ? '来自历史权益快照' : demoMode ? '界面演示数据' : '等待权益快照'}</small></article>
        <article><span>最大回撤</span><strong className={snapshots.length || demoMode ? 'negative-text' : ''}>{snapshots.length ? `−${maxDrawdown.toFixed(2)}%` : demoMode ? '−6.42%' : '暂无记录'}</strong><small>{snapshots.length ? '按已记录快照计算' : demoMode ? '界面演示数据' : '至少需要两条权益快照'}</small></article>
        <article><span>记录快照</span><strong>{snapshots.length || (demoMode ? 16 : 0)}</strong><small>{workspace.data?.performance.mark_source ?? (demoMode ? '界面演示' : '暂无来源')}</small></article>
        <article><span>最新总权益</span><strong>{latest ? `${latest.currency} ${latest.total_equity.toLocaleString(getFormatLocale())}` : demoMode ? '演示 42,860' : '暂无记录'}</strong><small>{latest ? new Date(latest.captured_at).toLocaleString(getFormatLocale(), { hour12: false }) : demoMode ? '未连接真实账户' : '等待权益快照'}</small></article>
      </section>

      <section className="report-layout">
        <article className="data-panel performance-chart">
          <header className="panel-heading"><div><span>PERFORMANCE CURVE</span><h2>{view}</h2></div><span className="status-chip official"><ShieldCheck size={14} /> {snapshots.length ? '历史权益快照' : demoMode ? '界面预览' : '等待数据'}</span></header>
          {values.length ? <div className="bar-chart" role="img" aria-label="组合表现趋势图">{values.map((value, index) => <i key={`${value}-${index}`} style={{ height: `${20 + Math.abs(value) / maximum * 72}%` }}><span>{Number(value).toFixed(0)}</span></i>)}</div> : <div className="inline-empty">当前没有可绘制的真实权益快照。</div>}
          <footer><span>2025-05</span><span>2026-08</span></footer>
        </article>
        <aside className="data-panel evidence-summary">
          <header className="panel-heading"><div><span>EVIDENCE QUALITY</span><h2>证据质量</h2></div><BarChart3 size={20} /></header>
          <dl><div><dt>快照覆盖</dt><dd>{snapshots.length || (demoMode ? '演示' : 0)} 条</dd></div><div><dt>行情标记</dt><dd>{snapshots.length ? workspace.data?.performance.fresh_marks ? '新鲜' : '历史' : '未记录'}</dd></div><div><dt>快照来源</dt><dd>{workspace.data?.performance.mark_source ?? (demoMode ? '界面演示' : '未记录')}</dd></div><div><dt>自动发布</dt><dd>禁止</dd></div><div><dt>数据泄漏检查</dt><dd>{snapshots.length ? '按发布门槛复核' : '暂无报告'}</dd></div></dl>
        </aside>
      </section>

      <section className="data-panel">
        <header className="panel-heading"><div><span>MODEL GOVERNANCE PREVIEW</span><h2>模型治理规则预览</h2></div><span className="status-chip research"><FlaskConical size={14} /> 不代表线上模型实绩</span></header>
        {demoMode ? <div className="responsive-table"><table><thead><tr><th>模型</th><th>状态</th><th>样本</th><th>胜率</th><th>最大回撤</th><th>压力期望</th><th>稳定性</th></tr></thead><tbody>{modelReports.map((model) => <tr key={model.version}><td><strong>{model.name}</strong><small>{model.version}</small></td><td><span className={`model-state ${model.state}`}>{model.state === 'active' ? '正式运行' : model.state === 'shadow' ? '影子验证' : '已阻止'}</span></td><td>{model.sampleSize}</td><td>{model.winRate}%</td><td>{model.maxDrawdown}%</td><td>{model.stressExpectancy.toFixed(2)}</td><td><span className="stability-value"><TrendingUp size={14} /> {model.stability}%</span></td></tr>)}</tbody></table></div> : <div className="inline-empty">真实模型注册表尚未开放到报告接口；登录状态不会显示演示模型成绩。</div>}
      </section>
    </div>
  )
}
