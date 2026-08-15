import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const today = readFileSync(resolve(root, 'src/pages/TodayV2Page.tsx'), 'utf8')
const discover = readFileSync(resolve(root, 'src/pages/DiscoverV2Page.tsx'), 'utf8')
const primitives = readFileSync(resolve(root, 'src/components/v2/V2Primitives.tsx'), 'utf8')
const css = readFileSync(resolve(root, 'src/styles/today-discover-v2.css'), 'utf8')

test('V2 pages keep stock terminology and safe missing-data paths', () => {
  assert.equal(today.includes('标的'), false)
  assert.equal(discover.includes('标的'), false)
  assert.match(today, /workspace\.mode !== 'authenticated'/)
  assert.match(discover, /workspace\.mode === 'authenticated'/)
  assert.match(primitives, /暂无真实 K 线/)
  assert.match(primitives, /前端不计算/)
})

test('Today owns priority work and delegates full workflows', () => {
  assert.match(today, /继续股票研究/)
  assert.match(today, /navigate\('\/discover'\)/)
  assert.match(today, /最多显示 5 条/)
  assert.doesNotMatch(today, /完整 K 线|完整 AI 对话|订单表/)
})

test('Discover keeps one primary research handoff and only safe paper seed', () => {
  assert.match(discover, /进入股票研究/)
  assert.match(discover, /source=screener/)
  assert.match(discover, /服务端评分未提供/)
  assert.match(discover, /候选股票|事件发现|研究覆盖/)
  assert.doesNotMatch(discover, /<tr[^>]*tabIndex/)
  assert.doesNotMatch(discover, /<tr[^>]*onClick/)
})

test('V2 theme tokens preserve borderless cards and blue-purple CTA', () => {
  assert.match(css, /\.v2-card\{[^}]*border:0/)
  assert.match(css, /\.v2-button-primary\{[^}]*linear-gradient\(115deg,var\(--v2-primary-a\),var\(--v2-primary-b\)\)/)
  assert.match(css, /--v2-canvas:var\(--canvas\)/)
  assert.match(css, /--v2-primary-a:var\(--brand-blue/)
  assert.match(css, /--v2-primary-b:var\(--brand-violet/)
  assert.doesNotMatch(css, /\[data-theme="light"\] \.v2-page\{--v2-/)
  assert.match(css, /v2-candidate-row\.is-selected/)
  assert.match(css, /prefers-reduced-motion:reduce/)
})

test('remote Mini K lines are authenticated, cached, and fail closed', () => {
  assert.match(primitives, /fetchMarketCandles\(symbol\.toUpperCase\(\), timeframe\)/)
  assert.match(primitives, /const remoteMiniRequests = new Map<string, RemoteMiniCacheEntry>\(\)/)
  assert.match(primitives, /if \(!authenticated \|\| !symbol\?\.trim\(\)\)/)
  assert.match(primitives, /K 线读取失败/)
})

test('Today and Discover keep authoritative handoffs', () => {
  assert.match(today, /stocks\.find\(\(item\) => item\.actionable\)/)
  assert.match(today, /建议投递延迟（股票\/期权）/)
  assert.match(discover, /panel=预警&draft=1/)
  assert.doesNotMatch(discover, /onClick=\{\(\) => undefined\}/)
})

test('Today uses real task priorities and keeps account domains separate', () => {
  assert.match(today, /auto-live-block/)
  assert.match(today, /route: '\/trade'/)
  assert.match(today, /route: '\/research'/)
  assert.match(today, /route: '\/notifications'/)
  assert.match(today, /route: '\/portfolio'/)
  assert.match(today, /route: '\/account'/)
  assert.match(today, /ciclotrade\.personalPaper\.activeSeason\.v1/)
  assert.match(today, /route: '\/paper'/)
  assert.match(today, /account_mode/)
  assert.match(today, /ciclotrade_system_validation/)
  assert.match(today, /个人模拟在独立账户页读取/)
  assert.match(today, /建议投递延迟（股票\/期权）/)
  assert.match(today, /formatDeliveryDelay/)
  assert.match(today, /localizeText/)
  assert.match(today, /useLocale/)
})

test('Ciclo avatar does not claim an unprovided membership skin', () => {
  assert.match(primitives, /appearanceEntitled = false/)
  assert.match(primitives, /基础系统状态素材，外观演进锁定/)
  assert.match(primitives, /data-appearance/)
})

test('Finish gate removes fabricated counts, risk, self-watch controls, and no-op views', () => {
  assert.doesNotMatch(today, /13\/97 研究/)
  assert.match(today, /建议投递延迟（股票\/期权）/)
  assert.doesNotMatch(discover, /riskCount: item\.max_loss == null \? undefined : 1/)
  assert.doesNotMatch(discover, /v2-icon-button|<Bookmark/)
  assert.match(discover, /view === '候选股票'/)
  assert.match(discover, /研究覆盖暂不可用|事件发现暂不可用|locked/)
})

test('Discover URL state and pagination are controlled without first-row auto-selection', () => {
  assert.match(discover, /useSearchParams/)
  assert.match(discover, /pageSize/)
  assert.match(discover, /setSearchParams/)
  assert.match(discover, /selectedId.*null/)
  assert.doesNotMatch(discover, /filtered\[0\] \?\? null/)
})

test('Remote Mini K lines expire and retry failed requests', () => {
  assert.match(primitives, /REMOTE_MINI_SUCCESS_TTL_MS/)
  assert.match(primitives, /REMOTE_MINI_FAILURE_TTL_MS/)
  assert.match(primitives, /expiresAt/)
  assert.match(primitives, /REMOTE_MINI_FAILURE_TTL_MS[\s\S]*error/)
})

test('Finish gate includes mobile drawer/sheet and touch-safe controls', () => {
  assert.match(css, /v2-inspector\.is-open/)
  assert.match(css, /@media \(min-width:701px\) and \(max-width:1070px\)/)
  assert.match(css, /@media \(max-width:700px\)/)
  assert.match(css, /min-height:44px/)
})
