import { useEffect, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { LoaderCircle } from 'lucide-react'
import { useWorkspace } from './api/workspace-context'
import { AppShell } from './components/AppShell'
import { MarketsPage, TodayPage } from './pages'
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
import { OpportunitiesPage } from './pages/OpportunitiesPage'
import { EarningsForecastPage } from './pages/EarningsForecastPage'
import { FeedbackPage } from './pages/FeedbackPage'
import { AdminPage } from './pages/AdminPage'
import { useLocale } from './i18n/useLocale'
import { applyTheme, readStoredTheme } from './theme'

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

export default function App() {
  useLocale()
  const location = useLocation()
  useEffect(() => {
    applyTheme(readStoredTheme())
  }, [])
  const isPublic = location.pathname === '/' || location.pathname === '/login'
  if (isPublic) {
    return (
      <Routes>
        <Route path="/" element={<WelcomePage />} />
        <Route path="/login" element={<LoginPage />} />
      </Routes>
    )
  }
  return <ProtectedConsole><AppShell><Routes>
    <Route path="/today" element={<TodayPage />} />
    <Route path="/opportunities" element={<OpportunitiesPage />} />
    <Route path="/markets" element={<MarketsPage />} />
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
    <Route path="*" element={<Navigate to="/today" replace />} />
  </Routes></AppShell></ProtectedConsole>
}
