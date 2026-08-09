import { createContext, useContext } from 'react'
import type { BootstrapPayload, SessionUser } from './client'

export type WorkspaceMode = 'loading' | 'demo' | 'authenticated' | 'offline'

export interface WorkspaceContextValue {
  mode: WorkspaceMode
  user: SessionUser | null
  data: BootstrapPayload | null
  error: string | null
  refresh: () => Promise<void>
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

export const WorkspaceContext = createContext<WorkspaceContextValue | null>(null)

export function useWorkspace() {
  const context = useContext(WorkspaceContext)
  if (!context) throw new Error('useWorkspace must be used inside WorkspaceProvider')
  return context
}
