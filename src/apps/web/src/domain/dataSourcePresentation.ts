/** Presentation-only source masking. API payload values remain untouched. */
export function displayDataSource(source: string | null | undefined, fallback = '未记录') {
  const value = source?.trim()
  if (!value) return fallback
  const safeStates = new Map<string, string>([
    ['demo', '界面演示'],
    ['界面演示', '界面演示'],
    ['界面演示数据', '界面演示数据'],
    ['演示', '界面演示'],
    ['演示数据', '界面演示数据'],
    ['offline', '离线'],
    ['离线', '离线'],
    ['未接入', '未接入'],
    ['未记录', '未记录'],
    ['暂无', '暂无'],
    ['暂无来源', '暂无来源'],
    ['不可用', '不可用'],
    ['unavailable', '不可用'],
    ['disabled', '已停用'],
    ['停用', '已停用'],
    ['已停用', '已停用'],
  ])
  const safeState = safeStates.get(value.toLocaleLowerCase())
  if (safeState) return safeState
  return '真实数据来源'
}

export function displayFreshness(freshness: string | null | undefined) {
  const value = freshness?.trim() ?? ''
  if (/停用|disabled/i.test(value)) return '已停用'
  if (/未启用|未连接|不可用|失败|failed|unavailable|error/i.test(value)) return '未启用或暂不可用'
  if (/延迟|delay/i.test(value)) return '延迟行情'
  if (/研究|research/i.test(value)) return '仅供研究'
  if (/历史|historic/i.test(value)) return '历史数据'
  if (/实时|real.?time/i.test(value)) return '实时权限已验证'
  return '状态未记录'
}

/** The server determines this per account; do not infer it from a provider right. */
export function displayDeliveryDelay(delayMinutes: number | null | undefined) {
  if (!Number.isSafeInteger(delayMinutes) || delayMinutes === undefined || delayMinutes === null || delayMinutes < 0) return ''
  if (delayMinutes === 0) return ''
  if (delayMinutes === 60) return '延迟 1 小时'
  if (delayMinutes % 60 === 0) return `延迟 ${delayMinutes / 60} 小时`
  return `延迟 ${delayMinutes} 分钟`
}

export function deliveryAllowsImmediateAction(metadata: {
  delivery_delay_minutes?: number
  is_realtime: boolean
  actionable_quote: boolean
}) {
  if (metadata.delivery_delay_minutes !== undefined) {
    return metadata.delivery_delay_minutes === 0 && metadata.is_realtime && metadata.actionable_quote
  }
  // Older API deployments had no visibility field. Preserve their conservative
  // contract until the per-account field reaches every endpoint.
  return metadata.is_realtime && metadata.actionable_quote
}

export interface VisibilityPollingHost {
  isVisible: () => boolean
  addVisibilityListener: (listener: () => void) => void
  removeVisibilityListener: (listener: () => void) => void
  setTimeout: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>
  clearTimeout: (timer: ReturnType<typeof setTimeout>) => void
}

function browserVisibilityPollingHost(): VisibilityPollingHost {
  return {
    isVisible: () => document.visibilityState !== 'hidden',
    addVisibilityListener: (listener) => document.addEventListener('visibilitychange', listener),
    removeVisibilityListener: (listener) => document.removeEventListener('visibilitychange', listener),
    setTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
    clearTimeout: (timer) => window.clearTimeout(timer),
  }
}

/** Visible pages poll serially; hidden pages have no pending network work. */
export function createVisibilityPolling(
  task: () => Promise<void> | void,
  intervalMs: number,
  host: VisibilityPollingHost = browserVisibilityPollingHost(),
) {
  let stopped = false
  let inFlight = false
  let timer: ReturnType<typeof setTimeout> | null = null
  const clearScheduled = () => {
    if (timer === null) return
    host.clearTimeout(timer)
    timer = null
  }
  const schedule = () => {
    if (stopped || inFlight || !host.isVisible() || timer !== null) return
    timer = host.setTimeout(() => { timer = null; run() }, intervalMs)
  }
  const run = () => {
    if (stopped || inFlight || !host.isVisible()) return
    inFlight = true
    void Promise.resolve(task()).catch(() => undefined).finally(() => {
      inFlight = false
      schedule()
    })
  }
  const onVisibilityChange = () => {
    if (stopped) return
    clearScheduled()
    if (host.isVisible()) run()
  }
  host.addVisibilityListener(onVisibilityChange)
  run()
  return () => {
    stopped = true
    clearScheduled()
    host.removeVisibilityListener(onVisibilityChange)
  }
}

export function safeDataError() { return '数据暂时不可用，请稍后重试。' }
