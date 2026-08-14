import { AlertTriangle, Clock3, Database, FlaskConical, LockKeyhole, ShieldAlert, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { BrowserApiError } from '../api/client'
import { fetchStrategyResearch97History, fetchStrategyResearch97Latest, fetchStrategyResearch97Status, type StrategyResearch97History, type StrategyResearch97Latest, type StrategyResearch97State, type StrategyResearch97Status } from '../api/strategyResearch97'
import { useLocale } from '../i18n/useLocale'
import '../styles/strategy-research-97.css'

type LoadState =
  | { phase: 'loading' }
  | { phase: 'error' }
  | { phase: 'forbidden' }
  | { phase: 'ready'; status: StrategyResearch97Status; latest: StrategyResearch97Latest; history: StrategyResearch97History }

const SIMPLIFIED = {
  title: '97 标的扩容研究',
  subtitle: '独立研究链 · 不覆盖 13 股稳定影子链',
  disclaimer: '本页只展示策略服务器回传的研究证据。研究结果不可执行、不可下单、不可推送 Telegram，也不会改变官方或实盘状态。',
  loading: '正在读取扩容研究记录。',
  error: '扩容研究暂时无法读取；为了安全，本页不会显示不完整或未经验证的结果。',
  forbidden: '当前账户没有读取扩容研究的权限。研究数据不会以演示数据替代。',
  empty: '尚未收到可展示的扩容研究周期。',
  waiting: '扩容研究链正在等待下一次周期；不会生成可执行操作。',
  stable: '13 股稳定 shadow',
  expanded: '97 标的 research',
  state: '运行状态',
  version: '研究版本',
  universe: 'Universe',
  coverage: '本周期覆盖',
  updated: '最后更新',
  expires: '证据过期时间',
  cycle: '最新周期',
  strategy: '策略摘要',
  long: 'Long 研究',
  flat: 'Flat 观察',
  wait: 'WAIT',
  missing: '无数据',
  fresh: '新鲜',
  stale: '已过期',
  dataMissing: '缺数据',
  symbol: '标的',
  dataState: '数据状态',
  signal: '研究标签',
  rationale: '摘要',
  history: '最近研究周期',
  received: '收到时间',
  noHistory: '暂无历史周期。',
  nonExecutable: '仅供研究 · 不可操作',
  status: { waiting: '等待中', healthy: '正常', stale: '已过期', degraded: '降级' },
} as const

const TRADITIONAL = {
  ...SIMPLIFIED,
  title: '97 標的擴容研究',
  subtitle: '獨立研究鏈 · 不覆蓋 13 股穩定影子鏈',
  disclaimer: '本頁只展示策略伺服器回傳的研究證據。研究結果不可執行、不可下單、不可推送 Telegram，也不會改變官方或實盤狀態。',
  loading: '正在讀取擴容研究記錄。',
  error: '擴容研究暫時無法讀取；為了安全，本頁不會顯示不完整或未驗證的結果。',
  forbidden: '當前帳戶沒有讀取擴容研究的權限。研究資料不會以演示資料替代。',
  empty: '尚未收到可展示的擴容研究週期。',
  waiting: '擴容研究鏈正在等待下一次週期；不會產生可執行操作。',
  stable: '13 股穩定 shadow',
  expanded: '97 標的 research',
  state: '運行狀態',
  version: '研究版本',
  universe: 'Universe',
  coverage: '本週期覆蓋',
  updated: '最後更新',
  expires: '證據過期時間',
  cycle: '最新週期',
  strategy: '策略摘要',
  long: 'Long 研究',
  flat: 'Flat 觀察',
  wait: 'WAIT',
  missing: '無資料',
  fresh: '新鮮',
  stale: '已過期',
  dataMissing: '缺資料',
  symbol: '標的',
  dataState: '資料狀態',
  signal: '研究標籤',
  rationale: '摘要',
  history: '最近研究週期',
  received: '收到時間',
  noHistory: '暫無歷史週期。',
  nonExecutable: '僅供研究 · 不可操作',
} as const

function displayTimestamp(value: string | null, formatLocale: string): string {
  if (!value) return '—'
  return new Date(value).toLocaleString(formatLocale, { hour12: false, dateStyle: 'medium', timeStyle: 'short' })
}

function stateClass(state: StrategyResearch97State): string {
  return `strategy-research-97-state ${state}`
}

function signalClass(signal: 'long' | 'flat' | 'wait'): string {
  return `strategy-research-97-signal ${signal}`
}

function dataStateLabel(state: 'fresh' | 'stale' | 'missing', text: { fresh: string; stale: string; dataMissing: string }): string {
  return state === 'fresh' ? text.fresh : state === 'stale' ? text.stale : text.dataMissing
}

export function StrategyResearch97Panel() {
  const { locale, formatLocale } = useLocale()
  const text = locale === 'zh-Hant' ? TRADITIONAL : SIMPLIFIED
  const [loadState, setLoadState] = useState<LoadState>({ phase: 'loading' })

  useEffect(() => {
    let current = true
    void Promise.all([fetchStrategyResearch97Status(), fetchStrategyResearch97Latest(), fetchStrategyResearch97History()]).then(([status, latest, history]) => {
      if (current) setLoadState({ phase: 'ready', status, latest, history })
    }).catch((error: unknown) => {
      if (!current) return
      setLoadState({ phase: error instanceof BrowserApiError && error.status === 403 ? 'forbidden' : 'error' })
    })
    return () => { current = false }
  }, [])

  if (loadState.phase === 'loading') return <section className="data-panel strategy-research-97-panel" aria-busy="true"><div className="strategy-research-97-message"><Clock3 size={20} /><span>{text.loading}</span></div></section>
  if (loadState.phase === 'forbidden') return <section className="data-panel strategy-research-97-panel"><div className="strategy-research-97-message forbidden"><LockKeyhole size={20} /><span>{text.forbidden}</span></div></section>
  if (loadState.phase === 'error') return <section className="data-panel strategy-research-97-panel"><div className="strategy-research-97-message error"><AlertTriangle size={20} /><span>{text.error}</span></div></section>

  const { status, latest, history } = loadState
  const cycle = latest.cycle
  const unavailable = !status.available || !latest.available || !history.available
  const waiting = status.state === 'waiting'
  const empty = unavailable || cycle === null
  const stateLabel = text.status[status.state]
  const summary = cycle?.summary

  return <section className="strategy-research-97-panel">
    <article className="data-panel strategy-research-97-summary">
      <header className="panel-heading"><div><span>EXPANDED RESEARCH / 97 SYMBOLS</span><h2>{text.title}</h2><p>{text.subtitle}</p></div><span className={stateClass(status.state)}>{stateLabel}</span></header>
      <p className="strategy-research-97-disclaimer"><ShieldAlert size={16} />{text.disclaimer}</p>
      <div className="strategy-research-97-lineage" aria-label="策略研究链分层"><span><ShieldCheck size={15} /><strong>{text.stable}</strong><small>独立链 · 稳定覆盖</small></span><i aria-hidden="true">→</i><span className="is-expanded"><Database size={15} /><strong>{text.expanded}</strong><small>本页 · 扩容观察</small></span></div>
      <dl className="strategy-research-97-metrics">
        <div><dt>{text.state}</dt><dd className={stateClass(status.state)}>{stateLabel}</dd></div>
        <div><dt>{text.version}</dt><dd>{status.universe.version}</dd></div>
        <div><dt>{text.universe}</dt><dd>{status.universe.key} · {status.universe.count}</dd></div>
        <div><dt>{text.coverage}</dt><dd>{status.coverage_count}/97 <small>· {status.no_data_count} {text.missing}</small></dd></div>
        <div><dt>{text.updated}</dt><dd>{displayTimestamp(status.last_result_at, formatLocale)}</dd></div>
        <div><dt>{text.expires}</dt><dd>{displayTimestamp(status.expires_at, formatLocale)}</dd></div>
      </dl>
      <div className="strategy-research-97-hash"><span>UNIVERSE SHA-256</span><code>{status.universe.sha256}</code></div>
    </article>

    {waiting ? <article className="data-panel"><div className="strategy-research-97-message"><Clock3 size={20} /><span>{text.waiting}</span></div></article>
      : empty ? <article className="data-panel"><div className="strategy-research-97-message"><FlaskConical size={20} /><span>{text.empty}</span></div></article>
        : <>
          <article className="data-panel strategy-research-97-cycle">
            <header className="panel-heading"><div><span>{cycle.strategy_key} / {cycle.strategy_version}</span><h2>{text.cycle} · {cycle.evaluation_date}</h2></div><small className="strategy-research-97-readonly">{text.nonExecutable}</small></header>
            <div className="strategy-research-97-summary-grid">
              <div><span>{text.strategy}</span><strong>{cycle.strategy_name}</strong></div>
              <div><span>{text.long}</span><strong className="positive-text">{summary?.long_count ?? 0}</strong></div>
              <div><span>{text.flat}</span><strong>{summary?.flat_count ?? 0}</strong></div>
              <div><span>{text.wait}</span><strong className="warning-text">{summary?.wait_count ?? 0}</strong></div>
              <div><span>{text.missing}</span><strong>{summary?.no_data_count ?? 0}</strong></div>
            </div>
            {status.state === 'stale' && <p className="strategy-research-97-stale"><AlertTriangle size={15} />{text.stale} · {displayTimestamp(status.last_result_at, formatLocale)}</p>}
            <div className="responsive-table strategy-research-97-table"><table><thead><tr><th>{text.symbol}</th><th>{text.dataState}</th><th>{text.signal}</th><th>{text.rationale}</th></tr></thead><tbody>{cycle.symbols.map((item) => <tr key={`${item.market}-${item.symbol}`}><td data-label={text.symbol}><strong>{item.symbol}</strong><small>{item.market}</small></td><td data-label={text.dataState}><span className={`strategy-research-97-data ${item.data_state}`}>{dataStateLabel(item.data_state, text)}</span></td><td data-label={text.signal}><span className={signalClass(item.signal)}>{item.signal === 'long' ? text.long : item.signal === 'flat' ? text.flat : text.wait}</span></td><td data-label={text.rationale}>{item.rationale ?? '—'}</td></tr>)}</tbody></table></div>
          </article>
          <article className="data-panel strategy-research-97-history">
            <header className="panel-heading"><div><span>RECENT CYCLES / 20</span><h2>{text.history}</h2></div></header>
            {history.items.length ? <div className="responsive-table strategy-research-97-history-table"><table><thead><tr><th>{text.cycle}</th><th>{text.coverage}</th><th>{text.long}</th><th>{text.flat}</th><th>{text.wait}</th><th>{text.received}</th></tr></thead><tbody>{history.items.map((item) => <tr key={item.cycle_id}><td data-label={text.cycle}><strong>{item.evaluation_date}</strong><small>{item.cycle_id}</small></td><td data-label={text.coverage}>{item.coverage_count}/97 · {item.no_data_count} {text.missing}</td><td data-label={text.long}>{item.long_count}</td><td data-label={text.flat}>{item.flat_count}</td><td data-label={text.wait}>{item.wait_count}</td><td data-label={text.received}>{displayTimestamp(item.received_at, formatLocale)}</td></tr>)}</tbody></table></div> : <div className="strategy-research-97-message"><Clock3 size={20} /><span>{text.noHistory}</span></div>}
          </article>
        </>}
  </section>
}

export type { StrategyResearch97State }
