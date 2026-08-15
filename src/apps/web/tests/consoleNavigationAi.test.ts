import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const root = new URL('../src/', import.meta.url)
const read = (path: string) => readFileSync(new URL(path, root), 'utf8')

const app = read('App.tsx')
const shell = read('components/AppShell.tsx')
const login = read('pages/LoginPage.tsx')
const ai = read('pages/AIWorkspacePage.tsx')
const workflow = read('pages/WorkflowTaskPage.tsx')
const deliberation = read('pages/DeliberationPage.tsx')
const intelligencePrimitives = read('components/intelligence/IntelligencePrimitives.tsx')
const workflowStatus = read('components/intelligence/workflowStatus.ts')

test('canonical console and public routes are wired without changing the six primary modules', () => {
  assert.match(app, /path="\/ai" element=\{<AIWorkspacePage \/>\}/)
  assert.match(app, /path="\/workflow\/:taskId" element=\{<WorkflowTaskPage \/>\}/)
  assert.match(app, /path="\/deliberation" element=\{<DeliberationPage \/>\}/)
  assert.match(app, /path="\/legal" element=\{<LegalPage \/>\}/)
  assert.match(app, /location\.pathname === '\/legal'/)
  assert.match(app, /<LegacyRedirect to="\/research" \/>/)
})

test('Ciclo AI launcher navigates directly to the bounded-context page', () => {
  assert.match(shell, /className="ai-pill"/)
  assert.match(shell, /to="\/ai"/)
  assert.doesNotMatch(shell, /ai-unavailable-popover|aiPanelOpen/)
})

test('stock search and evidence links use the canonical research route without demo stocks', () => {
  assert.doesNotMatch(shell, /AAPL|NVDA|TSLA|MSFT|600519/)
  assert.doesNotMatch(shell, /`\/markets\?market=/)
  assert.doesNotMatch(shell, /界面演示标的|市场标的/)
  assert.match(shell, /`\/research\?market=/)
})

test('login safe return allowlist includes all canonical routes', () => {
  for (const route of ['/today', '/discover', '/research', '/paper', '/portfolio', '/more', '/ai', '/workflow', '/deliberation', '/legal']) {
    assert.match(login, new RegExp(`'${route.replace('/', '\\/')}'`))
  }
  assert.match(login, /value\.startsWith\(`\$\{route\}\/`\)/)
  assert.match(login, /value\.startsWith\(`\$\{route\}\?`\)/)
})

test('AI page fails closed when no provider is available and exposes no order-submit tool', () => {
  assert.match(ai, /AI 服务暂不可用/)
  assert.match(ai, /provider/i)
  assert.match(ai, /不会生成占位回答|不会伪造回答/)
  assert.doesNotMatch(ai, /broker_submit/)
  assert.doesNotMatch(ai, /mock|假回答|示例回答/)
})

test('Workflow reads the real backtest task projection and covers the public lifecycle', () => {
  assert.match(workflow, /backtestApi\.getJob/)
  for (const status of ['queued', 'running', 'partial', 'succeeded', 'failed', 'cancelled', 'blocked', 'timed_out']) {
    assert.match(workflowStatus, new RegExp(`${status}:`))
    assert.match(intelligencePrimitives, new RegExp(`'${status}'`))
  }
  assert.match(workflow, /任务服务没有返回/)
  assert.doesNotMatch(workflow, /伪造日志|demoLog|mockTask/)
})

test('Workflow terminal states use distinct failure, cancellation, block, and timeout semantics', () => {
  assert.match(intelligencePrimitives, /failed:\s*XCircle/)
  assert.match(intelligencePrimitives, /cancelled:\s*CircleSlash2/)
  assert.match(intelligencePrimitives, /blocked:\s*ShieldX/)
  assert.match(intelligencePrimitives, /timed_out:\s*TimerOff/)
  assert.match(intelligencePrimitives, /succeeded:\s*CheckCircle2/)
  assert.doesNotMatch(intelligencePrimitives, /terminalStatuses[\s\S]*CheckCircle2/)
})

test('Directional strength is ready only with authoritative score binding metadata', () => {
  for (const binding of [
    /status === 'ready'/,
    /coverage !== null/,
    /methodVersion\.trim\(\)/,
    /observedAt/,
    /availableAt/,
    /asOf/,
    /calculatedAt/,
    /Date\.parse/,
  ]) assert.match(intelligencePrimitives, binding)
  assert.match(deliberation, /status=\{null\}/)
  assert.match(deliberation, /coverage=\{null\}/)
  assert.match(deliberation, /methodVersion=\{null\}/)
})

test('Deliberation keeps the fixed four-seat, Ciclo, bull-bear and real-timeline structure', () => {
  assert.match(deliberation, /deliberation-researchers/)
  assert.equal((deliberation.match(/className="deliberation-seat/g) ?? []).length, 4)
  assert.match(deliberation, /deliberation-core/)
  assert.match(deliberation, /deliberation-evidence/)
  assert.match(deliberation, /deliberation-timeline/)
  assert.match(deliberation, /支持证据强度/)
  assert.match(deliberation, /反向证据强度/)
  assert.match(intelligencePrimitives, /不要求合计 100/)
  assert.match(deliberation, /—/)
  assert.match(deliberation, /missing/)
  assert.doesNotMatch(deliberation, /Math\.random|mock|假设分数/)
})

test('new console surfaces use stock terminology and never claim natural-language trading authority', () => {
  const source = `${shell}\n${ai}\n${workflow}\n${deliberation}`
  assert.doesNotMatch(source, /标的|標的/)
  assert.doesNotMatch(source, /AI.{0,24}(?:自动下单|一键下单|直接下单)/)
})
