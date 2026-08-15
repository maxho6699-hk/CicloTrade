import { BarChart3, Download, FlaskConical, ShieldCheck, TrendingUp } from 'lucide-react'
import { type CSSProperties } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import { PageHeader } from '../components/PageHeader'
import { StrategyResearch97Panel } from '../components/StrategyResearch97Panel'
import { WorkspaceState } from '../components/WorkspaceState'
import { SystemCycleResearchPanel } from '../components/SystemCycleResearchPanel'
import { modelReports, reportReturns } from '../data/workspace'
import { getFormatLocale } from '../i18n/runtime'
import { useLocale } from '../i18n/useLocale'
import { displayDataSource } from '../domain/dataSourcePresentation'

const reportViews = ['CicloTrade模拟验证结果', '系统模型验证', '模型版本', '影子策略研究'] as const
type ReportView = typeof reportViews[number]
type ResearchScope = 'stable' | 'expanded'

export function ReportsPage() {
  const workspace = useWorkspace()
  const { locale } = useLocale()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedView = searchParams.get('view') as ReportView | null
  const view: ReportView = requestedView && reportViews.includes(requestedView) ? requestedView : reportViews[0]
  const researchScope: ResearchScope = searchParams.get('research_scope') === 'expanded' ? 'expanded' : 'stable'
  const setView = (next: ReportView) => {
    const params = new URLSearchParams(searchParams)
    if (next === reportViews[0]) params.delete('view')
    else params.set('view', next)
    if (next !== '影子策略研究') params.delete('research_scope')
    setSearchParams(params, { replace: true })
  }
  const setResearchScope = (next: ResearchScope) => {
    const params = new URLSearchParams(searchParams)
    params.set('view', '影子策略研究')
    if (next === 'stable') params.delete('research_scope')
    else params.set('research_scope', next)
    setSearchParams(params, { replace: true })
  }
  const snapshots = workspace.data?.performance.items ?? []
  const authenticated = workspace.mode === 'authenticated'
  const demoMode = workspace.mode === 'demo' || workspace.mode === 'offline'
  const accountView = view === 'CicloTrade模拟验证结果'
  const modelView = view === '系统模型验证'
  const versionView = view === '模型版本'
  const researchView = view === '影子策略研究'
  const researchViewLabel = locale === 'zh-Hant' ? '影子策略研究' : '影子策略研究'
  const realReturns = snapshots.map((item) => item.total_pnl)
  const values = accountView ? (authenticated ? realReturns : demoMode ? reportReturns : []) : []
  const maximum = Math.max(...values.map((value) => Math.abs(value)), 1)
  const first = snapshots[0]
  const latest = snapshots.at(-1)
  const returnPct = accountView ? (first && latest && first.initial_cash ? latest.total_pnl / first.initial_cash * 100 : demoMode ? 12.84 : null) : null
  let peak = Number.NEGATIVE_INFINITY
  const maxDrawdown = snapshots.reduce((maximumDrawdown, item) => {
    peak = Math.max(peak, item.total_equity)
    return peak > 0 ? Math.max(maximumDrawdown, (peak - item.total_equity) / peak * 100) : maximumDrawdown
  }, 0)
  const canExport = accountView ? (authenticated ? snapshots.length > 0 : demoMode) : !researchView && demoMode
  const exportReport = () => {
    if (researchView) return
    const usingSnapshots = authenticated && accountView
    if (usingSnapshots && snapshots.length === 0) return
    const reportScope = accountView ? (usingSnapshots ? 'ciclotrade_system_validation' : 'demo_validation_preview') : modelView ? 'model_validation_preview' : 'model_registry_preview'
    const header = usingSnapshots ? 'report_scope,captured_at,currency,cash,market_value,realized_pnl,unrealized_pnl,total_equity,total_pnl\n' : 'report_scope,model,version,state,sample_size,win_rate,max_drawdown,stress_expectancy,stability\n'
    const rows = usingSnapshots ? snapshots.map((item) => [reportScope, item.captured_at, item.currency, item.cash, item.market_value, item.realized_pnl, item.unrealized_pnl, item.total_equity, item.total_pnl].join(',')).join('\n') : modelReports.map((model) => [reportScope, model.name, model.version, model.state, model.sampleSize, model.winRate, model.maxDrawdown, model.stressExpectancy, model.stability].join(',')).join('\n')
    const url = URL.createObjectURL(new Blob([header, rows], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = usingSnapshots ? 'ciclotrade-validation-report.csv' : `${modelView ? 'ciclotrade-model-validation' : 'ciclotrade-model-registry'}-preview.csv`
    link.click()
    URL.revokeObjectURL(url)
  }
  return (
    <div className="page operations-page">
      <PageHeader kicker="REPORTS / EVIDENCE" title="报告中心" description="验证收益、回撤、样本量和模型版本按口径分开查看。系统验证快照不是你的券商收益，历史结果也不保证未来。" />
      <WorkspaceState empty={accountView && workspace.mode === 'authenticated' && snapshots.length === 0} emptyText="系统量化账本还没有权益快照；报告页不会用演示收益替代真实验证记录。" />
      <div className="toolbar-row"><div className="segmented-control report-tabs">{reportViews.map((item) => <button className={`${view === item ? 'active' : ''}${item === '影子策略研究' ? ' system-cycle-research-tab' : ''}`} type="button" key={item} onClick={() => setView(item)}>{item === '影子策略研究' ? researchViewLabel : item}</button>)}</div>{!researchView && <button className="button secondary" type="button" disabled={!canExport} onClick={exportReport}><Download size={16} /> 导出当前口径</button>}</div>

      {accountView && <>
      <section className="metric-grid report-metrics">
        <article><span>系统模拟验证收益</span><strong className={returnPct === null ? '' : returnPct >= 0 ? 'positive-text' : 'negative-text'}>{returnPct === null ? '暂无记录' : `${returnPct >= 0 ? '+' : ''}${returnPct.toFixed(2)}%`}</strong><small>{snapshots.length ? 'CicloTrade 系统验证快照，不是个人券商收益' : demoMode ? '界面演示数据' : '等待验证快照'}</small></article>
        <article><span>最大回撤</span><strong className={snapshots.length ? 'negative-text' : ''}>{snapshots.length ? `−${maxDrawdown.toFixed(2)}%` : demoMode ? '界面预览' : '暂无记录'}</strong><small>{snapshots.length ? '按已记录权益快照计算' : '正式数据未接入'}</small></article>
        <article><span>记录快照</span><strong>{snapshots.length || (demoMode ? 16 : 0)}</strong><small>{displayDataSource(workspace.data?.performance.mark_source, demoMode ? '界面演示' : '暂无来源')}</small></article>
        <article><span>系统验证总权益</span><strong>{latest ? `${latest.currency} ${latest.total_equity.toLocaleString(getFormatLocale())}` : demoMode ? '界面预览' : '暂无记录'}</strong><small>{latest ? new Date(latest.captured_at).toLocaleString(getFormatLocale(), { hour12: false }) : '没有跨口径替代数据'}</small></article>
      </section>

      <section className="report-layout">
        <article className="data-panel performance-chart">
          <header className="panel-heading"><div><span>PERFORMANCE CURVE / {view}</span><h2>{accountView ? '官方模拟账户验证记录' : modelView ? '系统模型验证口径' : '模型版本登记'}</h2></div><span className={`status-chip ${snapshots.length && accountView ? 'official' : 'research'}`}><ShieldCheck size={14} /> {snapshots.length && accountView ? '系统验证快照' : demoMode ? '界面预览' : '等待数据'}</span></header>
          {values.length ? <div className="bar-chart" role="img" aria-label="系统模拟验证盈亏趋势图，零轴以上为正，以下为负">{values.map((value, index) => <i className={value >= 0 ? 'positive' : 'negative'} key={`${value}-${index}`} style={{ '--bar-size': `${Math.max(4, Math.abs(value) / maximum * 44)}%` } as CSSProperties}><span>{Number(value).toFixed(0)}</span></i>)}</div> : <div className="inline-empty">当前没有可绘制的真实权益快照。</div>}
          <footer><span>{first?.captured_at.slice(0, 10) ?? '起点未记录'}</span><span>{latest?.captured_at.slice(0, 10) ?? '终点未记录'}</span></footer>
        </article>
        <aside className="data-panel evidence-summary">
          <header className="panel-heading"><div><span>EVIDENCE QUALITY</span><h2>证据质量</h2></div><BarChart3 size={20} /></header>
          <dl><div><dt>报告口径</dt><dd>CicloTrade 系统模拟验证</dd></div><div><dt>快照覆盖</dt><dd>{snapshots.length || (demoMode ? '演示' : 0)} 条</dd></div><div><dt>行情标记</dt><dd>{snapshots.length ? workspace.data?.performance.fresh_marks ? '新鲜' : '历史' : '未记录'}</dd></div><div><dt>快照来源</dt><dd>{displayDataSource(workspace.data?.performance.mark_source, demoMode ? '界面演示' : '未记录')}</dd></div><div><dt>个人收益</dt><dd>不提供</dd></div><div><dt>自动发布</dt><dd>禁止</dd></div><div><dt>数据泄漏检查</dt><dd>{snapshots.length ? '按发布门槛复核' : '暂无报告'}</dd></div></dl>
        </aside>
      </section>

      <section className="data-panel report-assumptions">
        <header className="panel-heading"><div><span>REPORT CONDITIONS</span><h2>这份结果用了什么条件</h2></div><ShieldCheck size={20} /></header>
        <dl><div><dt>样本区间</dt><dd>{first && latest ? `${first.captured_at.slice(0, 10)} 至 ${latest.captured_at.slice(0, 10)}` : '未记录'}</dd></div><div><dt>手续费</dt><dd>当前权益接口未单独列出</dd></div><div><dt>滑点</dt><dd>当前权益接口未单独列出</dd></div><div><dt>税费</dt><dd>当前权益接口未说明是否包含</dd></div><div><dt>未来数据检查</dt><dd>正式发布前必须通过；本页不自行判断</dd></div><div><dt>压力测试</dt><dd>当前报告接口未提供结果</dd></div></dl>
        <p>缺少的条件不会被默认当作零。只有回测引擎返回完整口径后，才会展示为可核对的真实回测报告。</p>
      </section>
      </>}

      {modelView && <section className="data-panel">
          <header className="panel-heading"><div><span>MODEL GOVERNANCE PREVIEW</span><h2>模型治理规则预览</h2></div><span className="status-chip research"><FlaskConical size={14} /> 历史研究，不代表未来</span></header>
        {demoMode ? <div className="responsive-table report-model-table"><table><thead><tr><th>模型</th><th>状态</th><th>样本</th><th>胜率</th><th>最大回撤</th><th>压力期望</th><th>稳定性</th></tr></thead><tbody>{modelReports.map((model) => <tr key={model.version}><td data-label="模型"><strong>{model.name}</strong><small>{model.version}</small></td><td data-label="状态"><span className={`model-state ${model.state}`}>{model.state === 'active' ? '正式运行' : model.state === 'shadow' ? '影子验证' : '已阻止'}</span></td><td data-label="样本">{model.sampleSize}</td><td data-label="胜率">{model.winRate}%</td><td data-label="最大回撤">{model.maxDrawdown}%</td><td data-label="压力期望">{model.stressExpectancy.toFixed(2)}</td><td data-label="稳定性"><span className="stability-value"><TrendingUp size={14} /> {model.stability}%</span></td></tr>)}</tbody></table></div> : <div className="inline-empty">真实模型注册表尚未开放到报告接口；登录状态不会显示演示模型成绩。</div>}
      </section>}

      {versionView && <section className="data-panel">
        <header className="panel-heading"><div><span>MODEL REGISTRY</span><h2>模型版本登记</h2></div><span className="status-chip research"><FlaskConical size={14} /> 只显示可核对版本</span></header>
        {demoMode ? <div className="compact-list">{modelReports.map((model) => <article key={model.version}><span className={`model-state ${model.state}`}>{model.state === 'active' ? '正式运行' : model.state === 'shadow' ? '影子验证' : '已阻止'}</span><div><strong>{model.name}</strong><small>{model.version} · 样本 {model.sampleSize}</small></div><div className="list-value"><strong>{model.stability}%</strong><small>稳定性</small></div></article>)}</div> : <div className="inline-empty">真实模型版本注册表尚未开放到报告接口。</div>}
      </section>}

      {researchView && <>
        <div className="toolbar-row strategy-research-scope-toolbar">
          <div className="segmented-control" role="group" aria-label={locale === 'zh-Hant' ? '策略研究範圍' : '策略研究范围'}>
            <button type="button" className={researchScope === 'stable' ? 'active' : ''} aria-pressed={researchScope === 'stable'} onClick={() => setResearchScope('stable')}>{locale === 'zh-Hant' ? '13 股穩定研究' : '13 股稳定研究'}</button>
            <button type="button" className={researchScope === 'expanded' ? 'active' : ''} aria-pressed={researchScope === 'expanded'} onClick={() => setResearchScope('expanded')}>{locale === 'zh-Hant' ? '97 隻股票擴容研究' : '97 只股票扩容研究'}</button>
          </div>
        </div>
        {researchScope === 'expanded' ? <StrategyResearch97Panel /> : <SystemCycleResearchPanel />}
      </>}
    </div>
  )
}
