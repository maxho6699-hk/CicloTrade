import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { MarketsPage, TodayPage } from './pages'
import { AccountPage } from './pages/AccountPage'
import { MembershipPage } from './pages/MembershipPage'
import { MysticPage } from './pages/MysticPage'
import { NotificationsPage } from './pages/NotificationsPage'
import { PortfolioPage } from './pages/PortfolioPage'
import { ReportsPage } from './pages/ReportsPage'
import { TradePage } from './pages/TradePage'
import { LoginPage } from './pages/LoginPage'
import { HelpPage } from './pages/HelpPage'
import { useLocale } from './i18n/useLocale'

export default function App() {
  useLocale()
  return (
    <AppShell>
      <Routes>
        <Route path="/today" element={<TodayPage />} />
        <Route path="/markets" element={<MarketsPage />} />
        <Route path="/portfolio" element={<PortfolioPage />} />
        <Route path="/trade" element={<TradePage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/notifications" element={<NotificationsPage />} />
        <Route path="/account" element={<AccountPage />} />
        <Route path="/help" element={<HelpPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/membership" element={<MembershipPage />} />
        <Route path="/mystic" element={<MysticPage />} />
        <Route path="*" element={<Navigate to="/today" replace />} />
      </Routes>
    </AppShell>
  )
}
