import { Suspense, useEffect, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useSearchParams } from 'react-router-dom'
import { LoaderCircle } from 'lucide-react'
import { useWorkspace } from './api/workspace-context'
import { AppShell } from './components/AppShell'
import { RouteLoadingState, lazyRoute } from './components/RouteLoadStates'
import { LoginPage } from './pages/LoginPage'
import { WelcomePage } from './pages/WelcomePage'
import { useLocale } from './i18n/useLocale'
import { applyTheme, readStoredTheme } from './theme'
import './styles/visual-waves.css'

const MarketsPage = lazyRoute(() => import('./pages.tsx').then((module) => ({ default: module.MarketsPage })))
const AccountPage = lazyRoute(() => import('./pages/AccountPage').then((module) => ({ default: module.AccountPage })))
const MembershipPage = lazyRoute(() => import('./pages/MembershipPage').then((module) => ({ default: module.MembershipPage })))
const PromotionCenterPage = lazyRoute(() => import('./pages/PromotionCenterPage').then((module) => ({ default: module.PromotionCenterPage })))
const MysticPage = lazyRoute(() => import('./pages/MysticPage').then((module) => ({ default: module.MysticPage })))
const NotificationsPage = lazyRoute(() => import('./pages/NotificationsPage').then((module) => ({ default: module.NotificationsPage })))
const PortfolioPage = lazyRoute(() => import('./pages/PortfolioPage').then((module) => ({ default: module.PortfolioPage })))
const ReportsPage = lazyRoute(() => import('./pages/ReportsPage').then((module) => ({ default: module.ReportsPage })))
const TradePage = lazyRoute(() => import('./pages/TradePage').then((module) => ({ default: module.TradePage })))
const HelpPage = lazyRoute(() => import('./pages/HelpPage').then((module) => ({ default: module.HelpPage })))
const ProfessionalLabPage = lazyRoute(() => import('./pages/ProfessionalLabPage').then((module) => ({ default: module.ProfessionalLabPage })))
const StockScreenerRoute = lazyRoute(() => import('./pages/StockScreenerRoute').then((module) => ({ default: module.StockScreenerRoute })))
const EarningsForecastPage = lazyRoute(() => import('./pages/EarningsForecastPage').then((module) => ({ default: module.EarningsForecastPage })))
const FeedbackPage = lazyRoute(() => import('./pages/FeedbackPage').then((module) => ({ default: module.FeedbackPage })))
const AdminPage = lazyRoute(() => import('./pages/AdminPage').then((module) => ({ default: module.AdminPage })))
const MoreRoute = lazyRoute(() => import('./pages/MoreRoute').then((module) => ({ default: module.MoreRoute })))
const PersonalPaperPage = lazyRoute(() => import('./pages/PersonalPaperPage').then((module) => ({ default: module.PersonalPaperPage })))
const TodayV2Page = lazyRoute(() => import('./pages/TodayV2Page').then((module) => ({ default: module.TodayV2Page })))
const DiscoverV2Page = lazyRoute(() => import('./pages/DiscoverV2Page').then((module) => ({ default: module.DiscoverV2Page })))
const AIWorkspacePage = lazyRoute(() => import('./pages/AIWorkspacePage').then((module) => ({ default: module.AIWorkspacePage })))
const WorkflowTaskPage = lazyRoute(() => import('./pages/WorkflowTaskPage').then((module) => ({ default: module.WorkflowTaskPage })))
const DeliberationPage = lazyRoute(() => import('./pages/DeliberationPage').then((module) => ({ default: module.DeliberationPage })))
const RecommendationsPage = lazyRoute(() => import('./pages/RecommendationsPage').then((module) => ({ default: module.RecommendationsPage })))
const LegalPage = lazyRoute(() => import('./pages/LegalPage').then((module) => ({ default: module.LegalPage })))

function ProtectedConsole({ children }: { children: ReactNode }) {
  const workspace = useWorkspace()
  const location = useLocation()

  if (workspace.mode === 'loading') {
    return <div className="auth-loading" role="status"><LoaderCircle /><strong>正在检查登录状态</strong><span>CicloTrade 正在恢复安全会话</span></div>
  }
  if (workspace.mode !== 'authenticated') {
    const returnTo = `${location.pathname}${location.search}`
    return <Navigate to={`/login?returnTo=${encodeURIComponent(returnTo)}`} replace state={{ reason: 'login-required' }} />
  }
  return children
}

function SuperAdminRoute({ children }: { children: ReactNode }) {
  const workspace = useWorkspace()
  if (workspace.user?.admin_role !== 'super_admin') return <Navigate to="/today" replace />
  return children
}

function LegacyRedirect({ to }: { to: string }) {
  const location = useLocation()
  return <Navigate to={`${to}${location.search}${location.hash}`} replace />
}

function DiscoverRoute() {
  const [searchParams] = useSearchParams()
  return searchParams.get('tool') === 'screener' ? <StockScreenerRoute /> : <DiscoverV2Page />
}

export default function App() {
  useLocale()
  const location = useLocation()
  useEffect(() => {
    applyTheme(readStoredTheme())
  }, [])
  const isPublic = location.pathname === '/' || location.pathname === '/login' || location.pathname === '/legal'
  if (isPublic) {
    return (
      <Suspense fallback={<RouteLoadingState />}><Routes>
        <Route path="/" element={<WelcomePage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/legal" element={<LegalPage />} />
      </Routes></Suspense>
    )
  }
  return <ProtectedConsole><AppShell><Suspense fallback={<RouteLoadingState />}><Routes>
    <Route path="/today" element={<TodayV2Page />} />
    <Route path="/discover" element={<DiscoverRoute />} />
    <Route path="/recommendations" element={<RecommendationsPage />} />
    <Route path="/research" element={<MarketsPage />} />
    <Route path="/paper" element={<PersonalPaperPage />} />
    <Route path="/more" element={<MoreRoute />} />
    <Route path="/opportunities" element={<LegacyRedirect to="/discover" />} />
    <Route path="/markets" element={<LegacyRedirect to="/research" />} />
    <Route path="/portfolio" element={<PortfolioPage />} />
    <Route path="/earnings" element={<EarningsForecastPage />} />
    <Route path="/trade" element={<TradePage />} />
    <Route path="/reports" element={<ReportsPage />} />
    <Route path="/lab" element={<ProfessionalLabPage />} />
    <Route path="/notifications" element={<NotificationsPage />} />
    <Route path="/account" element={<AccountPage />} />
    <Route path="/help" element={<HelpPage />} />
    <Route path="/feedback" element={<FeedbackPage />} />
    <Route path="/admin" element={<SuperAdminRoute><AdminPage /></SuperAdminRoute>} />
    <Route path="/membership" element={<MembershipPage />} />
    <Route path="/promotion" element={<PromotionCenterPage />} />
    <Route path="/mystic" element={<MysticPage />} />
    <Route path="/ai" element={<AIWorkspacePage />} />
    <Route path="/workflow" element={<WorkflowTaskPage />} />
    <Route path="/workflow/:taskId" element={<WorkflowTaskPage />} />
    <Route path="/deliberation" element={<DeliberationPage />} />
    <Route path="/opportunities/*" element={<LegacyRedirect to="/discover" />} />
    <Route path="/markets/*" element={<LegacyRedirect to="/research" />} />
    <Route path="*" element={<Navigate to="/today" replace />} />
  </Routes></Suspense></AppShell></ProtectedConsole>
}
