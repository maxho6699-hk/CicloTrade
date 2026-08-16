import { CalendarDays, Check, ChevronDown, ChevronLeft, ChevronRight, BellOff, DatabaseZap } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Market } from '../types'

type CalendarRange = '本周' | '本月' | '下周' | '下个月' | '自订'
const RANGES: CalendarRange[] = ['本周', '本月', '下周', '下个月', '自订']
const WEEKDAYS = ['週一', '週二', '週三', '週四', '週五', '週六', '週日']

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

interface MarketEventCalendarProps {
  market: Market
  onRangeChange?: (range: CalendarRange) => void
}

export function MarketEventCalendar({ market, onRangeChange }: MarketEventCalendarProps) {
  const today = useMemo(() => toDateKey(new Date()), [])
  const [range, setRange] = useState<CalendarRange>('本周')
  const [draftDate, setDraftDate] = useState(today)
  const [selectedDate, setSelectedDate] = useState(today)
  const [pickerOpen, setPickerOpen] = useState(false)
  const pickerRef = useRef<HTMLDivElement>(null)
  const selected = useMemo(() => new Date(`${draftDate}T00:00:00Z`), [draftDate])
  const months = useMemo(() => [selected, new Date(Date.UTC(selected.getUTCFullYear(), selected.getUTCMonth() + 1, 1))], [selected])

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
      <small className="event-calendar-source">{market === 'US' ? '美股' : 'A股'} · 真实事件源待接入</small>
    </header>
    <div className="event-calendar-controls" ref={pickerRef}>
      <div className="event-range-tabs" role="tablist" aria-label="事件日期范围">{RANGES.map((item) => <button className={range === item ? 'active' : ''} type="button" role="tab" aria-selected={range === item} onClick={() => chooseRange(item)} key={item}>{item}{item === '自订' && <ChevronDown size={14} className={pickerOpen ? 'rotated' : ''} />}</button>)}</div>
      {pickerOpen && <div className="calendar-date-popover" role="dialog" aria-label="自订日期">
        <header><button type="button" aria-label="上一个月" title="上一个月" onClick={() => setDraftDate(toDateKey(new Date(Date.UTC(selected.getUTCFullYear(), selected.getUTCMonth() - 1, 10))))}><ChevronLeft size={18} /></button><strong>選擇的日期：{selected.getUTCDate()} {selected.getUTCMonth() + 1}月</strong><button type="button" aria-label="下一个月" title="下一个月" onClick={() => setDraftDate(toDateKey(new Date(Date.UTC(selected.getUTCFullYear(), selected.getUTCMonth() + 1, 10))))}><ChevronRight size={18} /></button></header>
        <div className="calendar-months">{months.map((month) => <div className="calendar-month" key={month.toISOString()}><strong>{monthLabel(month)}</strong><div className="calendar-weekdays">{WEEKDAYS.map((day) => <span key={day}>{day}</span>)}</div><div className="calendar-days">{monthDays(month).map((day, index) => day ? <button className={toDateKey(day) === draftDate ? 'selected' : ''} type="button" key={toDateKey(day)} onClick={() => setDraftDate(toDateKey(day))}>{day.getUTCDate()}</button> : <span aria-hidden="true" key={`empty-${index}`} />)}</div></div>)}</div>
        <footer><button className="button primary" type="button" onClick={applyDate}><Check size={15} /> 應用</button></footer>
      </div>}
    </div>
    <div className="event-calendar-status"><span>显示范围：<strong>{range === '自订' ? selectedDate.replaceAll('-', '/') : range}</strong></span><span><i className="status-dot" /> 未接入前不展示预测或占位事件</span></div>
    <div className="event-calendar-empty" role="status"><span><DatabaseZap aria-hidden="true" /></span><div><small>LIVE EVENT SOURCE PENDING</small><h3>真实事件数据源尚未接入</h3><p>财报、分红、宏观和新股事件将在统一数据接口接入后显示。当前保留日期范围与市场筛选，但不会用预览日期或虚构数字填充。</p></div><ul><li>财报与分红</li><li>宏观数据</li><li>新股与公司行动</li></ul></div>
    <footer className="event-calendar-footer"><span><BellOff size={14} /> 事件提醒将在真实来源接入后开放</span><span>账户与市场筛选已保留</span></footer>
  </section>
}
