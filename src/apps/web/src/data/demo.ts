import type { Candle, Decision, Instrument } from '../types'

export const instruments: Instrument[] = [
  { symbol: 'AAPL', name: 'Apple', market: 'US', price: 213.45, changePct: 2.31, currency: 'USD' },
  { symbol: 'NVDA', name: 'NVIDIA', market: 'US', price: 182.77, changePct: 1.84, currency: 'USD' },
  { symbol: 'MSFT', name: 'Microsoft', market: 'US', price: 451.32, changePct: 0.67, currency: 'USD' },
  { symbol: 'TSLA', name: 'Tesla', market: 'US', price: 318.41, changePct: -1.82, currency: 'USD' },
  { symbol: '600519', name: '贵州茅台', market: 'CN', price: 1428.6, changePct: 1.16, currency: 'CNY' },
]

export const primaryDecision: Decision = {
  state: 'official',
  action: 'buy',
  instrument: instruments[0],
  title: '分批建立观察仓位',
  summary: '量价条件已经通过正式策略与风险闸门。先用模拟盘验证，不建议在单一价格一次买满。',
  entry: '210.00–214.00',
  stop: '203.00',
  target: '228.00–235.00',
  maxLoss: 'USD 209 / 1.0%',
  horizon: '2–6 周',
  confidence: '中高 · 已校准',
  evidence: [
    '成交量升至 20 日均量的 1.6 倍',
    '日线重新站上趋势确认区间',
    '压力测试后的风险收益比仍高于 2.1',
  ],
  counterEvidence: ['财报事件临近，隔夜跳空风险升高', '短期波动率高于近一年中位数'],
  eventId: 'QE-20260809-0142',
  modelVersion: 'equity-stability-shadow-v3',
  updatedAt: '2026-08-09 14:32:18 TPE',
  actionable: false,
  currentInstruction: '界面演示，不可交易',
  currentPrice: '界面演示 213.45',
  quantityHint: '0 股（界面演示）',
  quoteUpdatedAt: '没有真实报价时间',
  quoteFreshness: 'missing',
  actionBlockReason: '界面演示不是真实交易建议',
  plainLanguage: {
    reason: '这只股票前面跌得比较多，卖压开始变小，价格已经出现回弹。',
    setup: '超卖从 8 月 5 日开始，连续 3 天低于平常水平。',
    rebound: '从低点 204.20 美元反弹到 213.45 美元，回弹 9.25 美元（约 4.5%）。',
    reboundSince: '8 月 7 日开始回弹',
    takeProfit: '如果继续站稳 214 美元，先看 228 美元附近；到达后先锁定部分利润。',
    quantityHint: '新手先用 1–5 股验证，不要一次买满。',
  },
}

export const candidateDecisions: Decision[] = [
  {
    ...primaryDecision,
    state: 'research',
    action: 'wait',
    instrument: instruments[1],
    title: '等待价格确认',
    summary: '趋势仍强，但当前距离风险线过远，不适合普通用户追价。',
    entry: '176.00–179.00',
    stop: '169.50',
    target: '194.00–201.00',
    maxLoss: '未计算',
    confidence: '研究候选',
    eventId: 'SHADOW-8841',
    modelVersion: 'equity-stability-challenger-v7',
    plainLanguage: {
      reason: '趋势仍然向上，但现在的价格离风险线太远，追进去容易买贵。',
      setup: '等价格回到 176–179 美元附近，并重新站稳后再评估。',
      rebound: '暂未确认新的反弹起点。',
      quantityHint: '新手先等确认，不要因为涨幅追单。',
    },
  },
  {
    ...primaryDecision,
    state: 'research',
    action: 'reduce',
    instrument: instruments[3],
    title: '风险偏高，避免追入',
    summary: '波动和回撤风险同时升高，候选模型建议降低观察优先级。',
    entry: '暂不参与',
    stop: '不适用',
    target: '等待重评',
    maxLoss: '不适用',
    confidence: '低 · 证据冲突',
    eventId: 'SHADOW-8840',
    modelVersion: 'equity-stability-challenger-v7',
    plainLanguage: {
      reason: '价格波动变大，回撤也在扩大，当前不适合新建仓位。',
      setup: '从最近一次风险升高开始，系统连续观察到反向证据。',
      rebound: '反弹尚未形成可以跟随的确认。',
      takeProfit: '不适用，等待重新评估。',
      quantityHint: '已有仓位才考虑减仓；空仓先不参与。',
    },
  },
]

export function makeCandles(base = 186): Candle[] {
  const result: Candle[] = []
  let close = base

  for (let index = 0; index < 120; index += 1) {
    const drift = Math.sin(index / 8) * 1.15 + Math.cos(index / 17) * 0.7 + 0.24
    const open = close + Math.sin(index * 1.7) * 0.52
    close = Math.max(10, open + drift * 0.42 + Math.cos(index / 3) * 0.24)
    const high = Math.max(open, close) + 0.7 + Math.abs(Math.sin(index)) * 0.8
    const low = Math.min(open, close) - 0.65 - Math.abs(Math.cos(index)) * 0.7
    const day = new Date(Date.UTC(2026, 2, 2 + index))

    result.push({
      time: day.toISOString().slice(0, 10),
      open: Number(open.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(low.toFixed(2)),
      close: Number(close.toFixed(2)),
      volume: Math.round(28_000_000 + Math.abs(Math.sin(index / 4)) * 32_000_000 + index * 90_000),
    })
  }

  return result
}

export const candles = makeCandles()
