import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import '@fontsource-variable/noto-sans-tc/wght.css'
import '@fontsource-variable/noto-sans-sc/wght.css'
import '@fontsource/ibm-plex-mono/latin-400.css'
import '@fontsource/ibm-plex-mono/latin-500.css'
import '@fontsource/ibm-plex-mono/latin-600.css'
import '@fontsource/ibm-plex-mono/latin-700.css'
import { WorkspaceProvider } from './api/WorkspaceContext.tsx'
import { LocaleProvider } from './i18n/LocaleContext.tsx'
import './styles/base.css'
import './styles/components.css'
import './styles/earnings.css'
import './styles/market.css'
import './styles/more.css'
import './styles/navigation.css'
import './styles/operations.css'
import './styles/options.css'
import './styles/paper.css'
import './styles/promotion.css'
import './styles/responsive.css'
import './styles/welcome.css'
import App from './App.tsx'

document.documentElement.dataset.app = 'ciclotrade'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <WorkspaceProvider>
      <BrowserRouter><LocaleProvider><App /></LocaleProvider></BrowserRouter>
    </WorkspaceProvider>
  </StrictMode>,
)
