import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const routeStates = readFileSync(new URL('../src/components/RouteLoadStates.tsx', import.meta.url), 'utf8')
const budget = readFileSync(new URL('../scripts/verify-bundle-budget.mjs', import.meta.url), 'utf8')
const vite = readFileSync(new URL('../vite.config.ts', import.meta.url), 'utf8')
const packageJson = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))

test('protected feature pages use route-level lazy imports', () => {
  assert.match(app, /import \{ RouteLoadingState, lazyRoute \} from '\.\/components\/RouteLoadStates'/)
  assert.ok((app.match(/lazyRoute\(\(\) => import\(/g) ?? []).length >= 20)
  assert.match(app, /import\('\.\/pages\.tsx'\)/)
  assert.doesNotMatch(app, /import\('\.\/pages'\)/)
  assert.doesNotMatch(app, /import \{ AccountPage \} from '\.\/pages\/AccountPage'/)
})

test('the console shell stays mounted while a deferred page loads', () => {
  assert.match(app, /<AppShell><Suspense fallback=\{<RouteLoadingState \/>\}><Routes>/)
  assert.match(routeStates, /function RouteLoadingState\(\)/)
  assert.match(routeStates, /role="status"/)
})

test('only dynamic import rejection becomes a safe reload page', () => {
  assert.match(routeStates, /export function lazyRoute/)
  assert.match(routeStates, /catch\(\(\) => \(\{ default: RouteChunkFailurePage \}\)\)/)
  assert.match(routeStates, /window\.location\.reload\(\)/)
  assert.doesNotMatch(app, /class RouteChunkBoundary extends Component/)
  assert.doesNotMatch(routeStates, /error\.message|String\(error\)/)
})

test('bundle budget explicitly counts page chunks and runs during build', () => {
  assert.match(budget, /MAX_ENTRY_BYTES = 520 \* 1024/)
  assert.match(budget, /MIN_ROUTE_CHUNKS = 20/)
  assert.match(budget, /ROUTE_MODULES/)
  assert.match(budget, /\.vite.*manifest\.json/)
  assert.match(budget, /isDynamicEntry/)
  assert.match(budget, /resolvedRouteModules/)
  assert.match(budget, /resolvedRouteModules\.length === ROUTE_MODULES\.length/)
  assert.match(budget, /process\.exitCode = 1/)
  assert.match(packageJson.scripts.build, /verify-bundle-budget\.mjs/)
  assert.match(vite, /manifest:\s*true/)
})
