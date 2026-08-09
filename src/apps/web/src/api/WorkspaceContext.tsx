import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { fetchBootstrap, login as apiLogin, logout as apiLogout, restoreSession, type BootstrapPayload } from './client'
import { WorkspaceContext, type WorkspaceContextValue, type WorkspaceMode } from './workspace-context'

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<WorkspaceMode>('loading')
  const [data, setData] = useState<BootstrapPayload | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadBootstrap = useCallback(async () => {
    const bootstrap = await fetchBootstrap()
    setData(bootstrap)
    setMode('authenticated')
    setError(null)
  }, [])

  useEffect(() => {
    let active = true
    const restore = async () => {
      try {
        const restored = await restoreSession()
        if (!active) return
        if (!restored) {
          setMode('demo')
          return
        }
        if (!active) return
        await loadBootstrap()
      } catch (caught) {
        if (active) {
          setError(caught instanceof Error ? caught.message : '无法连接数据服务。')
          setMode('offline')
        }
      }
    }
    void restore()
    return () => { active = false }
  }, [loadBootstrap])

  const value = useMemo<WorkspaceContextValue>(() => ({
    mode,
    user: data?.me ?? null,
    data,
    error,
    refresh: loadBootstrap,
    login: async (email, password) => {
      setError(null)
      await apiLogin(email, password)
      await loadBootstrap()
    },
    logout: async () => {
      await apiLogout()
      setData(null)
      setMode('demo')
    },
  }), [data, error, loadBootstrap, mode])

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}
