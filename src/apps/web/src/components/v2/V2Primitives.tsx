import { ArrowRight, Bot, Check, CircleAlert, Clock3, Database, LockKeyhole, Search, Sparkles, X } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Candle } from '../../types'
import { fetchMarketCandles } from '../../api/client'
import { getFormatLocale } from '../../i18n/runtime'

export type V2State = 'loading' | 'empty' | 'error' | 'forbidden' | 'locked' | 'stale' | 'missing' | 'disconnected' | 'offline' | 'partial' | 'success' | 'disabled' | 'selected'

export function V2Card({ className = '', children }: { className?: string; children: ReactNode }) {
  return <section className={`v2-card ${className}`.trim()}>{children}</section>
}

export function V2SectionHeader({ eyebrow, title, action }: { eyebrow?: string; title: string; action?: ReactNode }) {
  return <header className="v2-section-header"><div>{eyebrow && <span className="v2-eyebrow">{eyebrow}</span>}<h2>{title}</h2></div>{action}</header>
}

export function V2StatusPill({ state, children }: { state: V2State | 'info' | 'warning' | 'positive'; children: ReactNode }) {
  return <span className={`v2-status-pill v2-status-${state}`}><i aria-hidden="true" />{children}</span>
}

export function V2Freshness({ freshness, observedAt, detail }: { freshness?: string; observedAt?: string; detail?: string }) {
  const normalized = freshness?.toLowerCase() ?? ''
  const state: V2State | 'info' = normalized.includes('实时') || normalized.includes('fresh') ? 'success' : normalized.includes('延迟') || normalized.includes('stale') ? 'stale' : normalized.includes('不可') || normalized.includes('missing') ? 'missing' : 'info'
  return <div className="v2-freshness" title={detail}><V2StatusPill state={state}>{freshness || '数据状态未知'}</V2StatusPill>{observedAt && <time dateTime={observedAt}>{observedAt}</time>}</div>
}

export function V2StatePanel({ state, title, detail, action }: { state: V2State; title: string; detail: string; action?: ReactNode }) {
  const Icon = state === 'loading' ? Clock3 : state === 'locked' || state === 'forbidden' ? LockKeyhole : state === 'success' ? Check : state === 'missing' || state === 'empty' ? Database : CircleAlert
  return <div className={`v2-state-panel is-${state}`} role={state === 'error' || state === 'forbidden' ? 'alert' : 'status'}><Icon size={18} aria-hidden="true" /><div><strong>{title}</strong><p>{detail}</p>{action && <div className="v2-state-action">{action}</div>}</div></div>
}

export function V2PrimaryButton({ children, onClick, disabled = false, ariaLabel }: { children: ReactNode; onClick?: () => void; disabled?: boolean; ariaLabel?: string }) {
  return <button className="v2-button v2-button-primary" type="button" onClick={onClick} disabled={disabled} aria-label={ariaLabel}>{children}<ArrowRight size={15} aria-hidden="true" /></button>
}

export function V2SecondaryButton({ children, onClick, disabled = false }: { children: ReactNode; onClick?: () => void; disabled?: boolean }) {
  return <button className="v2-button v2-button-secondary" type="button" onClick={onClick} disabled={disabled}>{children}</button>
}

export function CicloStatusAvatar({ size = 'sm', label = 'Ciclo AI', appearanceEntitled = false }: { size?: 'sm' | 'md'; label?: string; appearanceEntitled?: boolean }) {
  const statusLabel = appearanceEntitled ? label : `${label} · 基础系统状态素材，外观演进锁定`
  return <span className={`v2-ciclo-avatar v2-ciclo-${size}`} role="img" aria-label={statusLabel} title={statusLabel} data-appearance={appearanceEntitled ? 'entitled' : 'base-system-status'}><span className="v2-ciclo-eye" /><span className="v2-ciclo-eye" /><span className="v2-ciclo-core" /></span>
}

export function V2PageContext({ task, account = '研究域', market = '美股', freshness, observedAt, detail }: { task: string; account?: string; market?: string; freshness?: string; observedAt?: string; detail?: string }) {
  return <div className="v2-page-context"><span className="v2-context-task"><Sparkles size={14} aria-hidden="true" />{task}</span><span>{account}</span><span>{market}</span><V2Freshness freshness={freshness} observedAt={observedAt} detail={detail} /></div>
}

export function MiniCandles({ candles, label = 'Mini K线' }: { candles?: Candle[]; label?: string }) {
  if (!candles?.length) return <div className="v2-mini-chart v2-mini-chart-empty" role="img" aria-label={`${label}暂无真实 OHLC 数据`}><span>暂无真实 K 线</span></div>
  const recent = candles.slice(-28)
  const low = Math.min(...recent.map((item) => item.low))
  const high = Math.max(...recent.map((item) => item.high))
  const range = Math.max(high - low, 0.0001)
  return <div className="v2-mini-chart" role="img" aria-label={`${label}，${recent.length} 根真实 OHLC 数据`}>
    <svg viewBox="0 0 280 76" preserveAspectRatio="none" aria-hidden="true">
      {recent.map((candle, index) => {
        const x = 5 + index * (270 / Math.max(recent.length, 1))
        const width = Math.max(2, 170 / Math.max(recent.length, 1))
        const y = (value: number) => 5 + ((high - value) / range) * 64
        const rising = candle.close >= candle.open
        return <g key={`${candle.time}-${index}`} className={rising ? 'v2-candle-up' : 'v2-candle-down'}><line x1={x + width / 2} x2={x + width / 2} y1={y(candle.high)} y2={y(candle.low)} /><rect x={x} width={width} y={Math.min(y(candle.open), y(candle.close))} height={Math.max(1.5, Math.abs(y(candle.open) - y(candle.close)))} rx="1" /></g>
      })}
    </svg>
    <span>{recent.length} 根 · 真实行情</span>
  </div>
}

type RemoteMiniState = { status: 'loading' | 'success' | 'empty' | 'error'; candles: Candle[] }
const REMOTE_MINI_SUCCESS_TTL_MS = 5 * 60 * 1000
const REMOTE_MINI_FAILURE_TTL_MS = 15 * 1000
type RemoteMiniCacheEntry = { promise: Promise<Candle[]>; expiresAt: number }
const remoteMiniRequests = new Map<string, RemoteMiniCacheEntry>()

function loadRemoteMiniCandles(symbol: string, timeframe: string) {
  const key = `${symbol.toUpperCase()}::${timeframe}`
  const cached = remoteMiniRequests.get(key)
  if (cached && cached.expiresAt > Date.now()) return cached.promise
  const request = fetchMarketCandles(symbol.toUpperCase(), timeframe).then((payload) => payload.items.map((item) => ({
    time: item.time,
    open: item.open,
    high: item.high,
    low: item.low,
    close: item.close,
    volume: item.volume,
  }))).then((candles) => {
    remoteMiniRequests.set(key, { promise: Promise.resolve(candles), expiresAt: Date.now() + REMOTE_MINI_SUCCESS_TTL_MS })
    return candles
  }, (error) => {
    const failed = Promise.reject(error) as Promise<Candle[]>
    failed.catch(() => undefined)
    remoteMiniRequests.set(key, { promise: failed, expiresAt: Date.now() + REMOTE_MINI_FAILURE_TTL_MS })
    throw error
  })
  remoteMiniRequests.set(key, { promise: request, expiresAt: Date.now() + REMOTE_MINI_FAILURE_TTL_MS })
  return request
}

export function RemoteMiniCandles({ symbol, authenticated, timeframe = '日线', label = '真实 Mini K线' }: { symbol?: string; authenticated: boolean; timeframe?: string; label?: string }) {
  const [state, setState] = useState<RemoteMiniState>({ status: 'empty', candles: [] })
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!authenticated || !symbol?.trim()) {
      setState({ status: 'empty', candles: [] })
      return
    }
    let active = true
    setState({ status: 'loading', candles: [] })
    void loadRemoteMiniCandles(symbol, timeframe).then((candles) => {
      if (!active) return
      setState({ status: candles.length ? 'success' : 'empty', candles })
    }).catch(() => {
      if (active) setState({ status: 'error', candles: [] })
    })
    return () => { active = false }
  }, [authenticated, symbol, timeframe, retry])
  if (state.status === 'success') return <MiniCandles candles={state.candles} label={label} />
  const message = state.status === 'loading' ? '正在读取真实 K 线' : state.status === 'error' ? 'K 线读取失败' : authenticated ? '暂无真实 K 线' : '登录后读取真实 K 线'
  return <div className={`v2-mini-chart v2-mini-chart-empty is-${state.status}`} role={state.status === 'error' ? 'alert' : 'status'} aria-label={`${label}${message}`}><span>{message}</span>{state.status === 'error' && <button className="v2-mini-retry" type="button" onClick={() => setRetry((value) => value + 1)}>重试</button>}</div>
}

export function MiniTrend({ values, label = '趋势图' }: { values?: number[]; label?: string }) {
  if (!values?.length) return <div className="v2-mini-trend v2-mini-chart-empty" role="img" aria-label={`${label}暂无真实数据`}><span>暂无趋势数据</span></div>
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = Math.max(max - min, 0.0001)
  const points = values.map((value, index) => `${(index / Math.max(values.length - 1, 1)) * 100},${100 - ((value - min) / range) * 82 - 9}`).join(' ')
  return <div className="v2-mini-trend" role="img" aria-label={`${label}，${values.length} 个真实数据点`}><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><polyline points={points} /></svg><span>{values.length} 个数据点</span></div>
}

export type EvidenceSummary = { support?: number | null; counter?: number | null; riskCount?: number; unknownCount?: number; coverage?: number | null; methodVersion?: string | null; status?: string | null; asOf?: string | null }

export function EvidenceSummary({ evidence }: { evidence?: EvidenceSummary }) {
  const hasSnapshot = Boolean(evidence && (evidence.support !== undefined || evidence.counter !== undefined))
  return <div className="v2-evidence-summary">
    <div className="v2-evidence-row"><span>支持证据</span><strong>{evidence?.support ?? '—'}</strong>{hasSnapshot && <small>/100</small>}</div>
    <div className="v2-evidence-row"><span>反向证据</span><strong>{evidence?.counter ?? '—'}</strong>{hasSnapshot && <small>/100</small>}</div>
    <div className="v2-evidence-meta"><span>风险 {evidence?.riskCount ?? '—'}</span><span>未知 {evidence?.unknownCount ?? '—'}</span><span>覆盖 {evidence?.coverage == null ? '—' : `${evidence.coverage}%`}</span></div>
    <p>{evidence?.status ? `状态：${evidence.status}` : '双向强度尚未由服务端返回，前端不计算。'}{evidence?.methodVersion && ` · 方法 ${evidence.methodVersion}`}{evidence?.asOf && ` · 截至 ${evidence.asOf}`}</p>
  </div>
}

export function SearchField({ value, onChange, placeholder = '搜索股票代码或名称' }: { value: string; onChange: (value: string) => void; placeholder?: string }) {
  return <label className="v2-search-field"><Search size={16} aria-hidden="true" /><span className="sr-only">搜索股票</span><input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} /></label>
}

export function DataSourceNote({ source, observedAt, availableAt, recordedAt }: { source?: string; observedAt?: string; availableAt?: string; recordedAt?: string }) {
  return <div className="v2-source-note"><Database size={13} aria-hidden="true" /><span>{source || '来源未提供'}</span>{observedAt && <time>observed {observedAt}</time>}{availableAt && <time>available {availableAt}</time>}{recordedAt && <time>recorded {recordedAt}</time>}</div>
}

export function EmptyInspector({ title, detail }: { title: string; detail: string }) {
  return <V2StatePanel state="empty" title={title} detail={detail} />
}

export function V2Logo({ symbol }: { symbol?: string }) {
  const text = symbol?.slice(0, 2).toUpperCase() || '—'
  return <span className="v2-stock-logo" aria-hidden="true">{text}</span>
}

export function LoadingSkeleton() {
  return <div className="v2-skeleton" aria-hidden="true"><i /><i /><i /></div>
}

export function safeNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function formatMoney(value: number | null | undefined, currency = 'USD', formatLocale = getFormatLocale()) {
  if (value == null || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat(formatLocale, { style: 'currency', currency, maximumFractionDigits: 2 }).format(value)
}

export function formatTime(value?: string | null, formatLocale = getFormatLocale()) {
  if (!value) return '时间未提供'
  const parsed = new Date(value)
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString(formatLocale, { hour12: false })
}

export function StockTaskBadge({ symbol, name, market }: { symbol?: string; name?: string; market?: string }) {
  return <div className="v2-stock-identity"><V2Logo symbol={symbol} /><span><strong>{name || symbol || '股票名称未提供'}</strong><small>{symbol || '股票代码未提供'}{market ? ` · ${market}` : ''}</small></span></div>
}

export function BotMark() {
  return <span className="v2-bot-mark"><Bot size={14} aria-hidden="true" /></span>
}

export function InspectorToggle({ open, onClick, label = '打开信息面板' }: { open: boolean; onClick: () => void; label?: string }) {
  const buttonRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      onClick()
      window.requestAnimationFrame(() => buttonRef.current?.focus())
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClick, open])
  return <button ref={buttonRef} className="v2-inspector-toggle v2-button v2-button-secondary" type="button" onClick={onClick} aria-expanded={open} aria-label={open ? '关闭信息面板' : label}>{open ? <X size={15} aria-hidden="true" /> : <Search size={15} aria-hidden="true" />}{open ? '关闭面板' : label}</button>
}
