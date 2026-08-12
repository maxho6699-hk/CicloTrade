import { CalendarDays, Check, ChevronDown, ChevronLeft, ChevronRight, BellOff } from 'lucide-react'
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import type { Market } from '../types'

type CalendarRange = '本周' | '本月' | '下周' | '下个月' | '自订'
type PreviewEvent = {
  market: Market
  date: string
  dateLabel: string
  time: string
  flag: string
  country: string
  event: string
  impact: 1 | 2 | 3
  current: string
  forecast: string
  previous: string
  symbol?: string
}

const RANGES: CalendarRange[] = ['本周', '本月', '下周', '下个月', '自订']
const WEEKDAYS = ['週一', '週二', '週三', '週四', '週五', '週六', '週日']
const PREVIEW_EVENTS: PreviewEvent[] = [
  { market: 'CN', date: '2026-08-10', dateLabel: '8月10日 星期一', time: '09:30', flag: '🇨🇳', country: '中國', event: 'CPI 消費者物價指數年率（7月）', impact: 3, current: '—', forecast: '0.1%', previous: '0.1%' },
  { market: 'CN', date: '2026-08-11', dateLabel: '8月11日 星期二', time: '盤後', flag: '●', country: 'A股', event: '600519 財報日期預覽（正式日期待來源接入）', impact: 2, current: '—', forecast: '—', previous: '—', symbol: '600519' },
  { market: 'US', date: '2026-08-11', dateLabel: '8月11日 星期二', time: '20:30', flag: '🇺🇸', country: '美國', event: '生產者物價指數 PPI（7月）', impact: 3, current: '—', forecast: '0.2%', previous: '0.1%' },
  { market: 'US', date: '2026-08-12', dateLabel: '8月12日 星期三', time: '20:30', flag: '🇺🇸', country: '美國', event: '核心消費者物價指數 CPI（月率）', impact: 3, current: '—', forecast: '0.3%', previous: '0.2%' },
  { market: 'US', date: '2026-08-12', dateLabel: '8月12日 星期三', time: '盤後', flag: '●', country: '美股', event: 'AAPL 財報日期預覽（正式日期待來源接入）', impact: 3, current: '—', forecast: '—', previous: '—', symbol: 'AAPL' },
  { market: 'US', date: '2026-08-13', dateLabel: '8月13日 星期四', time: '盤前', flag: '●', country: '美股', event: 'NVDA 財報日期預覽（正式日期待來源接入）', impact: 3, current: '—', forecast: '—', previous: '—', symbol: 'NVDA' },
]

function toDateKey(date: Date) {
  return date.toISOString().slice(0, 10)
}

function monthLabel(date: Date) {
  return `${date.getUTCFullYear()}年${date.getUTCMonth() + 1}月`
}

function monthDays(date: Date) {
  const year = date.getUTCFullYear()
  const month = date.getUTCMonth()
  const first = new Date(Date.UTC(year, month, 1))
  const offset = (first.getUTCDay() + 6) % 7
  const count = new Date(Date.UTC(year, month + 1, 0)).getUTCDate()
  return [...Array(offset).fill(null), ...Array.from({ length: count }, (_, index) => new Date(Date.UTC(year, month, index + 1)))]
}

function ImpactBars({ level }: { level: PreviewEvent['impact'] }) {
  return <span className="calendar-impact" aria-label={`影响等级 ${level} 级`}>{[1, 2, 3].map((bar) => <i className={bar <= level ? `on level-${level}` : ''} key={bar} />)}</span>
}

interface MarketEventCalendarProps {
  market: Market
  onRangeChange?: (range: CalendarRange) => void
}

export function MarketEventCalendar({ market, onRangeChange }: MarketEventCalendarProps) {
  const [range, setRange] = useState<CalendarRange>('本周')
  const [draftDate, setDraftDate] = useState('2026-08-10')
  const [selectedDate, setSelectedDate] = useState('2026-08-10')
  const [pickerOpen, setPickerOpen] = useState(false)
  const pickerRef = useRef<HTMLDivElement>(null)
  const selected = useMemo(() => new Date(`${draftDate}T00:00:00Z`), [draftDate])
  const months = useMemo(() => [selected, new Date(Date.UTC(selected.getUTCFullYear(), selected.getUTCMonth() + 1, 1))], [selected])
  const groups = useMemo(() => PREVIEW_EVENTS.filter((item) => item.market === market).reduce<Array<{ date: string; label: string; items: PreviewEvent[] }>>((result, item) => {
    const group = result.find((candidate) => candidate.date === item.date)
    if (group) group.items.push(item)
    else result.push({ date: item.date, label: item.dateLabel, items: [item] })
    return result
  }, []), [market])

  useEffect(() => {
    const close = (event: MouseEvent) => { if (pickerRef.current && !pickerRef.current.contains(event.target as Node)) setPickerOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const chooseRange = (next: CalendarRange) => {
    setRange(next)
    onRangeChange?.(next)
    if (next === '自订') setPickerOpen(true)
    else setPickerOpen(false)
  }

  const applyDate = () => {
    setSelectedDate(draftDate)
    setRange('自订')
    onRangeChange?.('自订')
    setPickerOpen(false)
  }

  return <section className="market-event-calendar" aria-label="市场事件日历">
    <header className="event-calendar-heading">
      <div><span><CalendarDays size={15} /> ECONOMIC CALENDAR</span><strong>重要事项与宏观数据</strong></div>
      <small className="event-calendar-source">{market === 'US' ? '美股' : 'A股'}预览 · 仅显示 US / CN 事件与财报</small>
    </header>
    <div className="event-calendar-controls" ref={pickerRef}>
      <div className="event-range-tabs" role="tablist" aria-label="事件日期范围">{RANGES.map((item) => <button className={range === item ? 'active' : ''} type="button" role="tab" aria-selected={range === item} onClick={() => chooseRange(item)} key={item}>{item}{item === '自订' && <ChevronDown size={14} className={pickerOpen ? 'rotated' : ''} />}</button>)}</div>
      {pickerOpen && <div className="calendar-date-popover" role="dialog" aria-label="自订日期">
        <header><button type="button" aria-label="上一个月" title="上一个月" onClick={() => setDraftDate(toDateKey(new Date(Date.UTC(selected.getUTCFullYear(), selected.getUTCMonth() - 1, 10))))}><ChevronLeft size={18} /></button><strong>選擇的日期：{selected.getUTCDate()} {selected.getUTCMonth() + 1}月</strong><button type="button" aria-label="下一个月" title="下一个月" onClick={() => setDraftDate(toDateKey(new Date(Date.UTC(selected.getUTCFullYear(), selected.getUTCMonth() + 1, 10))))}><ChevronRight size={18} /></button></header>
        <div className="calendar-months">{months.map((month) => <div className="calendar-month" key={month.toISOString()}><strong>{monthLabel(month)}</strong><div className="calendar-weekdays">{WEEKDAYS.map((day) => <span key={day}>{day}</span>)}</div><div className="calendar-days">{monthDays(month).map((day, index) => day ? <button className={toDateKey(day) === draftDate ? 'selected' : ''} type="button" key={toDateKey(day)} onClick={() => setDraftDate(toDateKey(day))}>{day.getUTCDate()}</button> : <span aria-hidden="true" key={`empty-${index}`} />)}</div></div>)}</div>
        <footer><button className="button primary" type="button" onClick={applyDate}><Check size={15} /> 應用</button></footer>
      </div>}
    </div>
    <div className="event-calendar-status"><span>显示范围：<strong>{range === '自订' ? selectedDate.replaceAll('-', '/') : range}</strong></span><span><i className="status-dot" /> 数据为组件预览，不构成交易依据</span></div>
    <div className="event-calendar-table-wrap"><table className="event-calendar-table"><thead><tr><th>时间</th><th>事件</th><th>影响</th><th>当前</th><th>预测</th><th>前次</th></tr></thead>{groups.map((group) => <Fragment key={group.date}><tbody><tr className="event-date-row"><th colSpan={6}>{group.label}</th></tr>{group.items.map((item) => <tr key={`${item.date}-${item.time}-${item.event}`}><td data-label="时间"><time>{item.time}</time></td><td data-label="事件"><span className="event-name"><b className="event-flag" aria-hidden="true">{item.flag}</b><span><strong>{item.event}</strong><small>{item.symbol ? `${item.symbol} · ` : ''}{item.country} · 界面预览</small></span></span></td><td data-label="影响"><ImpactBars level={item.impact} /></td><td data-label="当前" className="calendar-number">{item.current}</td><td data-label="预测" className="calendar-number">{item.forecast}</td><td data-label="前次" className="calendar-number">{item.previous}</td></tr>)}</tbody></Fragment>)}</table></div>
    <footer className="event-calendar-footer"><span><BellOff size={14} /> 真实提醒需等待事件服务接入</span><span>时间显示为香港时区预览</span></footer>
  </section>
}
