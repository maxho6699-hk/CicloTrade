const RESERVED_TOKENS = new Set(['RSI', 'ATR', 'SMA', 'EMA', 'BUY', 'SELL', 'SHORT', 'COVER'])

export interface StrategyDraft {
  code: string
  symbol: string
  timeframe: string
  summary: string
}

function firstNumber(match: RegExpMatchArray | null, fallback: string) {
  return match?.slice(1).find(Boolean) ?? fallback
}

function normalizedSymbol(prompt: string, fallback: string) {
  const token = prompt.match(/\b(?:[A-Z][A-Z0-9.-]{0,11}|\d{6})\b/i)?.[0]?.toUpperCase()
  return token && !RESERVED_TOKENS.has(token) ? token : fallback.toUpperCase()
}

function normalizedTimeframe(prompt: string, fallback: string) {
  const explicit = [...prompt.matchAll(/(\d+)\s*(分钟|分|小时|日|天|周|月)(?:线)?/g)].find((match) => {
    const rest = prompt.slice((match.index ?? 0) + match[0].length)
    return !/^\s*均线/.test(rest)
  })
  if (!explicit) return /日线/.test(prompt) ? '日线' : fallback
  const [, amount, unit] = explicit
  if (unit === '分钟' || unit === '分') return `${amount}分`
  if (unit === '小时') return `${amount}小时`
  if (unit === '周') return amount === '1' ? '周线' : `${amount}周`
  if (unit === '月') return amount === '1' ? '月线' : `${amount}月`
  return amount === '1' ? '日线' : `${amount}日`
}

export function generateStrategyDraft(promptValue: string, fallbackSymbol: string, fallbackTimeframe: string): StrategyDraft {
  const prompt = promptValue.trim().replace(/\s+/g, ' ')
  const symbol = normalizedSymbol(prompt, fallbackSymbol)
  const timeframe = normalizedTimeframe(prompt, fallbackTimeframe)
  const action = /回补|平空/.test(prompt) ? 'COVER' : /做空|卖空|空头/.test(prompt) ? 'SHORT' : /买入|做多|多头/.test(prompt) ? 'BUY' : 'WAIT'
  const conditions: string[] = []

  const rsi = prompt.match(/RSI\s*(?:低于|小于|少于|<)\s*(\d+(?:\.\d+)?)/i)
  if (rsi) conditions.push(`rsi(14) < ${rsi[1]}`)
  else if (/超卖/.test(prompt)) conditions.push('rsi(14) < 30')

  const aboveAverage = prompt.match(/(?:重新)?站上\s*(\d+)\s*(?:日|天)?均线/)
  const belowAverage = prompt.match(/跌破\s*(\d+)\s*(?:日|天)?均线/)
  if (aboveAverage) conditions.push(`close > sma(${aboveAverage[1]})`)
  if (belowAverage) conditions.push(`close < sma(${belowAverage[1]})`)

  const priceBreak = belowAverage ? null : prompt.match(/跌破\s*(\d+(?:\.\d+)?)/)
  const priceRise = aboveAverage ? null : prompt.match(/(?:突破|站上)\s*(\d+(?:\.\d+)?)/)
  if (priceBreak) conditions.push(`close < ${priceBreak[1]}`)
  else if (priceRise) conditions.push(`close > ${priceRise[1]}`)

  const risk = firstNumber(prompt.match(/(?:最多亏(?:损)?(?:账户的)?|单笔风险)\s*(\d+(?:\.\d+)?)\s*%/), '1')
  const atr = firstNumber(prompt.match(/(?:ATR|平均真实波幅)(?:\s*的)?\s*(\d+(?:\.\d+)?)?\s*倍?/i), '1.5')
  const stopPercent = prompt.match(/止损[^。；,，]*?(\d+(?:\.\d+)?)\s*%/)
  const target = firstNumber(prompt.match(/(?:达到|目标|止盈)[^。；,，]*?(?:(\d+(?:\.\d+)?)\s*倍风险|(\d+(?:\.\d+)?)\s*R)/i), '2')
  const when = conditions.length ? conditions.join(' and ') : 'manual_confirmation = true'
  const stop = stopPercent ? `${stopPercent[1]}%` : `atr(14) * ${atr}`
  const escapedPrompt = prompt.replace(/\/\//g, '/ /').slice(0, 500)
  const recognized = conditions.length + (action === 'WAIT' ? 0 : 1)

  return {
    symbol,
    timeframe,
    code: `// 自然语言研究草稿 v1 · 不会自动执行\n// 原始描述：${escapedPrompt}\n\nstrategy {\n  symbol = ${symbol}\n  timeframe = ${timeframe}\n  when = ${when}\n  action = ${action}\n  risk = ${risk}%\n  stop = ${stop}\n  target = ${target}R\n  require = BACKTEST_AND_MANUAL_REVIEW\n}`,
    summary: recognized
      ? `已识别 ${recognized} 组行动或触发规则；请检查草稿，再接入真实回测引擎。`
      : '未识别到明确行动或触发条件，已生成需要人工补充的 WAIT 草稿。',
  }
}
