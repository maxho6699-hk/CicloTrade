import { useEffect, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useSearchParams } from 'react-router-dom'
import { LoaderCircle } from 'lucide-react'
import { useWorkspace } from './api/workspace-context'
import { AppShell } from './components/AppShell'
import { MarketsPage } from './pages'
import { AccountPage } from './pages/AccountPage'
import { MembershipPage } from './pages/MembershipPage'
import { PromotionCenterPage } from './pages/PromotionCenterPage'
import { MysticPage } from './pages/MysticPage'
import { NotificationsPage } from './pages/NotificationsPage'
import { PortfolioPage } from './pages/PortfolioPage'
import { ReportsPage } from './pages/ReportsPage'
import { TradePage } from './pages/TradePage'
import { LoginPage } from './pages/LoginPage'
import { HelpPage } from './pages/HelpPage'
import { ProfessionalLabPage } from './pages/ProfessionalLabPage'
import { WelcomePage } from './pages/WelcomePage'
import { StockScreenerRoute } from './pages/StockScreenerRoute'
import { EarningsForecastPage } from './pages/EarningsForecastPage'
import { FeedbackPage } from './pages/FeedbackPage'
import { AdminPage } from './pages/AdminPage'
import { MoreRoute } from './pages/MoreRoute'
import { PersonalPaperPage } from './pages/PersonalPaperPage'
import { TodayV2Page } from './pages/TodayV2Page'
import { DiscoverV2Page } from './pages/DiscoverV2Page'
import { AIWorkspacePage } from './pages/AIWorkspacePage'
import { WorkflowTaskPage } from './pages/WorkflowTaskPage'
import { DeliberationPage } from './pages/DeliberationPage'
import { LegalPage } from './pages/LegalPage'
import { useLocale } from './i18n/useLocale'
import { applyTheme, readStoredTheme } from './theme'
import './styles/visual-waves.css'

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
      <Routes>
        <Route path="/" element={<WelcomePage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/legal" element={<LegalPage />} />
      </Routes>
    )
  }
  return <ProtectedConsole><AppShell><Routes>
    <Route path="/today" element={<TodayV2Page />} />
    <Route path="/discover" element={<DiscoverRoute />} />
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
  </Routes></AppShell></ProtectedConsole>
}
