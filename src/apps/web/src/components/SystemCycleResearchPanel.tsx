import { AlertTriangle, Clock3, FlaskConical, ShieldAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  fetchSystemCycleResearchHistory,
  fetchSystemCycleResearchLatest,
  fetchSystemCycleResearchStatus,
  type SystemCycleResearchHistory,
  type SystemCycleResearchLatest,
  type SystemCycleResearchState,
  type SystemCycleResearchStatus,
} from '../api/client'
import { useLocale } from '../i18n/useLocale'

type LoadState =
  | { phase: 'loading' }
  | { phase: 'error' }
  | { phase: 'ready'; status: SystemCycleResearchStatus; latest: SystemCycleResearchLatest; history: SystemCycleResearchHistory }

const SIMPLIFIED = {
  title: '影子策略研究', disclaimer: '历史规则回放与状态扫描，不是严格样本外验证；研究结果不可执行，不会发送Telegram或订单。',
  running: '运行状态', lastCycle: '最后周期', coverage: '13只股票覆盖', noData: '无数据',
  loading: '正在读取影子策略研究记录。', error: '研究记录暂时无法读取；为了安全，本页不会显示不完整或未经验证的结果。',
  empty: '尚未有可展示的研究周期。', waiting: '等待下一次研究周期；不会生成任何可执行操作。',
  tableTitle: '13只股票研究覆盖', historyTitle: '最近周期历史', market: '市场', stock: '股票', dataEnd: '数据截止', researchState: '研究状态', paperTarget: '纸面目标数量', nonExecutable: '不可执行',
  long: '多头研究', flat: '空仓观察', noDataState: '无数据', status: { waiting: '等待中', healthy: '正常', stale: '已过期', degraded: '降级' },
  historyCycle: '周期', historyCoverage: '覆盖', historyNoData: '无数据', historySelected: '纸面长仓', noHistory: '暂无历史周期。',
} as const

const TRADITIONAL = {
  title: '影子策略研究', disclaimer: '歷史規則回放與狀態掃描，不是嚴格樣本外驗證；研究結果不可執行，不會發送Telegram或訂單。',
  running: '運行狀態', lastCycle: '最後週期', coverage: '13隻股票覆蓋', noData: '無資料',
  loading: '正在讀取影子策略研究記錄。', error: '研究記錄暫時無法讀取；為了安全，本頁不會顯示不完整或未經驗證的結果。',
  empty: '尚未有可展示的研究週期。', waiting: '等待下一次研究週期；不會產生任何可執行操作。',
  tableTitle: '13隻股票研究覆蓋', historyTitle: '最近週期歷史', market: '市場', stock: '股票', dataEnd: '資料截止', researchState: '研究狀態', paperTarget: '紙面目標數量', nonExecutable: '不可執行',
  long: '多頭研究', flat: '空倉觀察', noDataState: '無資料', status: { waiting: '等待中', healthy: '正常', stale: '已過期', degraded: '降級' },
  historyCycle: '週期', historyCoverage: '覆蓋', historyNoData: '無資料', historySelected: '紙面長倉', noHistory: '暫無歷史週期。',
} as const

function displayTimestamp(value: string | null, formatLocale: string): string {
  if (!value) return '—'
  return new Date(value).toLocaleString(formatLocale, { hour12: false, dateStyle: 'medium', timeStyle: 'short' })
}

function stateClass(state: SystemCycleResearchState) {
  return `system-cycle-research-state ${state}`
}

export function SystemCycleResearchPanel() {
  const { locale, formatLocale } = useLocale()
  const text = locale === 'zh-Hant' ? TRADITIONAL : SIMPLIFIED
  const [loadState, setLoadState] = useState<LoadState>({ phase: 'loading' })

  useEffect(() => {
    let current = true
    void Promise.all([
      fetchSystemCycleResearchStatus(),
      fetchSystemCycleResearchLatest(),
      fetchSystemCycleResearchHistory(),
    ]).then(([status, latest, history]) => {
      if (current) setLoadState({ phase: 'ready', status, latest, history })
    }).catch(() => {
      if (current) setLoadState({ phase: 'error' })
    })
    return () => { current = false }
  }, [])

  if (loadState.phase === 'loading') return <section className="data-panel system-cycle-research-panel" aria-busy="true"><div className="system-cycle-research-message"><Clock3 size={20} /><span>{text.loading}</span></div></section>
  if (loadState.phase === 'error') return <section className="data-panel system-cycle-research-panel"><div className="system-cycle-research-message error"><AlertTriangle size={20} /><span>{text.error}</span></div></section>

  const { status, latest, history } = loadState
  const cycle = latest.cycle
  const unavailable = !status.available || !latest.available || !history.available
  const waiting = status.state === 'waiting'
  const empty = unavailable || cycle === null
  const stateLabel = text.status[status.state]

  return <section className="system-cycle-research-panel">
    <article className="data-panel system-cycle-research-summary">
      <header className="panel-heading"><div><span>SHADOW RESEARCH / READ ONLY</span><h2>{text.title}</h2></div><span className={stateClass(status.state)}>{stateLabel}</span></header>
      <p className="system-cycle-research-disclaimer"><ShieldAlert size={16} />{text.disclaimer}</p>
      <div className="system-cycle-research-metrics">
        <article><span>{text.running}</span><strong className={stateClass(status.state)}>{stateLabel}</strong></article>
        <article><span>{text.lastCycle}</span><strong>{cycle?.evaluation_date ?? displayTimestamp(status.last_result_at, formatLocale)}</strong></article>
        <article><span>{text.coverage}</span><strong>{status.coverage_count}/13</strong></article>
        <article><span>{text.noData}</span><strong>{status.no_data_count}</strong></article>
      </div>
    </article>

    {waiting ? <article className="data-panel"><div className="system-cycle-research-message"><Clock3 size={20} /><span>{text.waiting}</span></div></article>
      : empty ? <article className="data-panel"><div className="system-cycle-research-message"><FlaskConical size={20} /><span>{text.empty}</span></div></article>
        : <>
          <article className="data-panel system-cycle-research-table-panel">
            <header className="panel-heading"><div><span>RESEARCH COVERAGE / 13</span><h2>{text.tableTitle}</h2></div><small>{text.nonExecutable}</small></header>
            <div className="responsive-table system-cycle-research-table"><table><thead><tr><th>{text.market}</th><th>{text.stock}</th><th>{text.dataEnd}</th><th>{text.researchState}</th><th>{text.paperTarget}</th><th>{text.nonExecutable}</th></tr></thead><tbody>{cycle.stocks.map((stock) => {
              const researchState = stock.signal_state === 'no_data' ? text.noDataState : stock.signal_state === 'long' ? text.long : text.flat
              return <tr key={`${stock.market}-${stock.symbol}`}><td data-label={text.market}>{stock.market}</td><td data-label={text.stock}><strong>{stock.symbol}</strong></td><td data-label={text.dataEnd}>{stock.dataset_end ?? '—'}</td><td data-label={text.researchState}><span className={`system-cycle-research-signal ${stock.signal_state}`}>{researchState}</span></td><td data-label={text.paperTarget}>{stock.target_quantity}</td><td data-label={text.nonExecutable}><span className="system-cycle-research-not-executable">{text.nonExecutable}</span></td></tr>
            })}</tbody></table></div>
          </article>
          <article className="data-panel system-cycle-research-history">
            <header className="panel-heading"><div><span>RECENT CYCLES / 20</span><h2>{text.historyTitle}</h2></div></header>
            {history.items.length ? <div className="responsive-table system-cycle-research-history-table"><table><thead><tr><th>{text.historyCycle}</th><th>{text.historyCoverage}</th><th>{text.historyNoData}</th><th>{text.historySelected}</th></tr></thead><tbody>{history.items.map((item) => <tr key={item.cycle_id}><td data-label={text.historyCycle}><strong>{item.evaluation_date}</strong><small>{item.cycle_slot}</small></td><td data-label={text.historyCoverage}>{item.coverage_count}/13</td><td data-label={text.historyNoData}>{item.no_data_count}</td><td data-label={text.historySelected}>{item.selected_count}</td></tr>)}</tbody></table></div> : <div className="system-cycle-research-message"><Clock3 size={20} /><span>{text.noHistory}</span></div>}
          </article>
        </>}
  </section>
}
