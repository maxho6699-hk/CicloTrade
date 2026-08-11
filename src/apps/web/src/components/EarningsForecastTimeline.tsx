import { Check, Clock3, LockKeyhole } from 'lucide-react'
import type { EarningsForecastSnapshot } from '../domain/earningsForecast.ts'

const DAYS = [7, 6, 5, 4, 3, 2, 1] as const

function actionLabel(action: EarningsForecastSnapshot['simulated_action']) {
  return ({
    OBSERVE: '观察',
    PAPER_OPEN: '纸上开仓',
    PAPER_ADD: '纸上加仓',
    PAPER_REDUCE: '纸上减仓',
    PAPER_CLOSE: '纸上平仓',
    RESEARCH_LONG_CALL: '研究看涨期权',
    RESEARCH_LONG_PUT: '研究看跌期权',
    RESEARCH_LONG_STRADDLE: '研究跨式',
    RESEARCH_LONG_STRANGLE: '研究宽跨式',
  })[action]
}

function compactDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

export function EarningsForecastTimeline({
  forecasts,
  selectedDay,
  onSelect,
}: {
  forecasts: EarningsForecastSnapshot[]
  selectedDay: number | null
  onSelect: (forecast: EarningsForecastSnapshot) => void
}) {
  const byDay = new Map(forecasts.map((forecast) => [forecast.countdown_day, forecast]))

  return (
    <section className="earnings-timeline" aria-labelledby="earnings-timeline-title">
      <header className="earnings-section-heading">
        <div>
          <span>D7—D1 / IMMUTABLE</span>
          <h2 id="earnings-timeline-title">财报前预测时间线</h2>
        </div>
        <span className="earnings-boundary-chip"><LockKeyhole size={14} /> 快照不可编辑</span>
      </header>
      <div className="earnings-timeline-rail" aria-label="财报前七日预测快照">
        {DAYS.map((day) => {
          const forecast = byDay.get(day)
          const selected = forecast?.countdown_day === selectedDay
          return (
            <button
              key={day}
              className={selected ? 'is-selected' : ''}
              type="button"
              disabled={!forecast}
              aria-pressed={selected}
              onClick={() => forecast && onSelect(forecast)}
            >
              <span className="earnings-timeline-day">D-{day}</span>
              <i>{forecast ? <Check /> : <Clock3 />}</i>
              <strong>{forecast ? actionLabel(forecast.simulated_action) : '等待封存'}</strong>
              <small>{forecast ? compactDate(forecast.decision_at) : '尚无合规快照'}</small>
            </button>
          )
        })}
      </div>
      <p className="earnings-inline-note">每个节点只读取当时已可用证据；后续信息不会回写到历史快照。</p>
    </section>
  )
}
