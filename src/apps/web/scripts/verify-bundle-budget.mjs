import { existsSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const DIST = join(ROOT, 'dist')
const MANIFEST_PATH = join(DIST, '.vite', 'manifest.json')
const MAX_ENTRY_BYTES = 520 * 1024
const MIN_ROUTE_CHUNKS = 20
const ROUTE_MODULES = [
  'src/pages.tsx', 'src/pages/AccountPage.tsx', 'src/pages/MembershipPage.tsx',
  'src/pages/PromotionCenterPage.tsx', 'src/pages/MysticPage.tsx', 'src/pages/NotificationsPage.tsx',
  'src/pages/PortfolioPage.tsx', 'src/pages/ReportsPage.tsx', 'src/pages/TradePage.tsx',
  'src/pages/HelpPage.tsx', 'src/pages/ProfessionalLabPage.tsx', 'src/pages/StockScreenerRoute.tsx',
  'src/pages/EarningsForecastPage.tsx', 'src/pages/FeedbackPage.tsx', 'src/pages/AdminPage.tsx',
  'src/pages/MoreRoute.tsx', 'src/pages/PersonalPaperPage.tsx', 'src/pages/TodayV2Page.tsx',
  'src/pages/DiscoverV2Page.tsx', 'src/pages/AIWorkspacePage.tsx', 'src/pages/WorkflowTaskPage.tsx',
  'src/pages/DeliberationPage.tsx', 'src/pages/RecommendationsPage.tsx', 'src/pages/LegalPage.tsx',
]
const manifest = JSON.parse(readFileSync(MANIFEST_PATH, 'utf8'))
const entry = manifest['index.html']?.isEntry
  ? manifest['index.html']
  : Object.values(manifest).find((item) => item && item.isEntry)
const resolvedRouteModules = ROUTE_MODULES.filter((source) => {
  const item = manifest[source]
  return Boolean(item?.isDynamicEntry && typeof item.file === 'string' && existsSync(join(DIST, item.file)))
})
const missingRouteModules = ROUTE_MODULES.filter((source) => !resolvedRouteModules.includes(source))
const entryBytes = entry?.file ? statSync(join(DIST, entry.file)).size : Number.POSITIVE_INFINITY
const passed = Boolean(entry?.file)
  && entryBytes <= MAX_ENTRY_BYTES
  && ROUTE_MODULES.length >= MIN_ROUTE_CHUNKS
  && resolvedRouteModules.length === ROUTE_MODULES.length
console.log(JSON.stringify({ passed, entry: entry?.file ?? null, entryBytes, maxEntryBytes: MAX_ENTRY_BYTES, routeModules: ROUTE_MODULES.length, resolvedRouteModules: resolvedRouteModules.length, missingRouteModules, minRouteChunks: MIN_ROUTE_CHUNKS }))
if (!passed) process.exitCode = 1
