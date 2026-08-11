import assert from 'node:assert/strict'
import test from 'node:test'
import { generateStrategyDraft } from '../src/domain/strategyDraft.ts'

test('converts common long rules and risk language into an editable draft', () => {
  const draft = generateStrategyDraft(
    'AAPL 日线 RSI 低于 30，重新站上 50 日均线时买入；每笔最多亏账户的 1%，ATR 1.5 倍止损，达到 2 倍风险止盈。',
    'MSFT',
    '1小时',
  )

  assert.equal(draft.symbol, 'AAPL')
  assert.equal(draft.timeframe, '日线')
  assert.match(draft.code, /rsi\(14\) < 30 and close > sma\(50\)/)
  assert.match(draft.code, /action = BUY/)
  assert.match(draft.code, /risk = 1%/)
  assert.match(draft.code, /target = 2R/)
})

test('keeps short entry semantics distinct from selling an existing long position', () => {
  const draft = generateStrategyDraft('TSLA 1小时跌破 312 后做空，止损 2%，目标 3R。', 'AAPL', '日线')

  assert.equal(draft.symbol, 'TSLA')
  assert.equal(draft.timeframe, '1小时')
  assert.match(draft.code, /close < 312/)
  assert.match(draft.code, /action = SHORT/)
  assert.match(draft.code, /stop = 2%/)
  assert.match(draft.code, /target = 3R/)
})

test('does not invent a tradable rule when the prompt has no recognized trigger', () => {
  const draft = generateStrategyDraft('关注这家公司，稍后再研究。', 'NVDA', '日线')

  assert.match(draft.code, /manual_confirmation = true/)
  assert.match(draft.code, /action = WAIT/)
  assert.match(draft.summary, /人工补充/)
})
