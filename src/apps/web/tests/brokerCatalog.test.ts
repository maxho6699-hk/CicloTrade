import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const clientSource = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const tradePageSource = readFileSync(new URL('../src/pages/TradePage.tsx', import.meta.url), 'utf8')

test('broker catalog consumes the authoritative five-provider bootstrap contract', () => {
  assert.match(clientSource, /capability_catalog: BrokerCatalogEntry\[\]/)
  assert.match(clientSource, /key: 'futu_moomoo' \| 'tiger' \| 'ibkr' \| 'webull' \| 'longbridge'/)
  assert.match(clientSource, /connection_available: false/)
  assert.match(tradePageSource, /membership\.brokerage\.capability_catalog/)
  assert.match(tradePageSource, /brokerCatalog\.map/)
  assert.match(tradePageSource, /当前 5 家均不可由用户绑定/)
  assert.match(tradePageSource, /暂不可申请或绑定/)
})

test('launch page does not advertise deferred or fallback providers', () => {
  assert.match(tradePageSource, /A 股券商及其他候补平台全部后置/)
  assert.doesNotMatch(tradePageSource, /Alpaca|QMT|PTrade/)
  assert.doesNotMatch(tradePageSource, /label: '港股'|label: 'A股'/)
  assert.doesNotMatch(tradePageSource, /立即绑定|可申请连接/)
})
