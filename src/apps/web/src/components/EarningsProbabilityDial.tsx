import { ArrowDownRight, ArrowUpRight, Minus, ShieldCheck } from 'lucide-react'
import { dominantEarningsDirection, type EarningsForecastSnapshot } from '../domain/earningsForecast.ts'

const RADIUS = 54
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

function percent(value: number) {
  return `${Math.round(value * 100)}%`
}

export function EarningsProbabilityDial({ forecast }: { forecast: EarningsForecastSnapshot }) {
  const direction = dominantEarningsDirection(forecast)
  const segments = [
    { key: 'up', label: '向上', value: forecast.p_up, className: 'is-up' },
    { key: 'flat', label: '横盘', value: forecast.p_flat, className: 'is-flat' },
    { key: 'down', label: '向下', value: forecast.p_down, className: 'is-down' },
  ]
  let consumed = 0

  return (
    <section className="earnings-probability" aria-labelledby="earnings-probability-title">
      <header className="earnings-section-heading">
        <div>
          <span>PROBABILITY / SEALED</span>
          <h2 id="earnings-probability-title">方向概率圆盘</h2>
        </div>
        <span className="earnings-boundary-chip"><ShieldCheck size={14} /> D-{forecast.countdown_day} 封存快照</span>
      </header>

      <div className="earnings-dial-layout">
        <div
          className="earnings-dial"
          role="img"
          aria-label={`向上 ${percent(forecast.p_up)}，横盘 ${percent(forecast.p_flat)}，向下 ${percent(forecast.p_down)}，置信度 ${percent(forecast.confidence)}`}
        >
          <svg viewBox="0 0 128 128" aria-hidden="true">
            <g className="earnings-dial-ticks">
              {Array.from({ length: 24 }, (_, index) => <line key={index} x1="64" y1="3" x2="64" y2={index % 3 === 0 ? '9' : '6'} transform={`rotate(${index * 15} 64 64)`} />)}
            </g>
            <circle className="earnings-dial-track" cx="64" cy="64" r={RADIUS} />
            {segments.map((segment) => {
              const offset = -consumed * CIRCUMFERENCE
              consumed += segment.value
              return (
                <circle
                  key={segment.key}
                  className={`earnings-dial-segment ${segment.className}`}
                  cx="64"
                  cy="64"
                  r={RADIUS}
                  strokeDasharray={`${segment.value * CIRCUMFERENCE} ${CIRCUMFERENCE}`}
                  strokeDashoffset={offset}
                />
              )
            })}
            <circle className="earnings-dial-confidence-track" cx="64" cy="64" r="43" />
            <circle className="earnings-dial-confidence" cx="64" cy="64" r="43" strokeDasharray={`${forecast.confidence * 270.18} 270.18`} />
          </svg>
          <span className={`earnings-dial-core is-${direction}`}>
            {direction === 'up' ? <ArrowUpRight /> : direction === 'down' ? <ArrowDownRight /> : <Minus />}
            <strong>{direction === 'up' ? '向上' : direction === 'down' ? '向下' : '横盘'}</strong>
            <small>主概率</small>
          </span>
        </div>

        <dl className="earnings-probability-legend">
          {segments.map((segment) => (
            <div key={segment.key} className={segment.className}>
              <dt><i />{segment.label}</dt>
              <dd>{percent(segment.value)}</dd>
            </div>
          ))}
          <div className="is-confidence">
            <dt>模型置信度</dt>
            <dd>{percent(forecast.confidence)}</dd>
          </div>
          <div>
            <dt>校准样本</dt>
            <dd>{forecast.calibration_sample_size.toLocaleString('zh-CN')}</dd>
          </div>
        </dl>
      </div>
    </section>
  )
}
