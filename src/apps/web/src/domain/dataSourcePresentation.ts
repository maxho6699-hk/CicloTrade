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
  if (/实时|real.?time/i.test(value)) return '实时权限已验证'
  if (/延迟|delay/i.test(value)) return '延迟行情'
  if (/研究|research/i.test(value)) return '仅供研究'
  if (/历史|historic/i.test(value)) return '历史数据'
  if (/停用|disabled/i.test(value)) return '已停用'
  return '状态未记录'
}

export function safeDataError() { return '数据暂时不可用，请稍后重试。' }
