import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  fetchBootstrap,
  login as apiLogin,
  logout as apiLogout,
  restoreSession,
  updateWatchlist as apiUpdateWatchlist,
  updateWatchlistPin as apiUpdateWatchlistPin,
  type BootstrapPayload,
  type WatchlistPayload,
} from './client'
import { WorkspaceContext, type WorkspaceContextValue, type WorkspaceMode } from './workspace-context'
import type { Market } from '../types'

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

  const updateMarketDataStatus = useCallback((status: Partial<BootstrapPayload['market_data']>) => {
    setData((current) => current ? {
      ...current,
      market_data: { ...current.market_data, ...status },
    } : current)
  }, [])

  const applyWatchlistPayload = useCallback((payload: WatchlistPayload) => {
    setData((current) => current ? {
      ...current,
      settings: {
        ...current.settings,
        watchlists: payload.watchlists,
        watchlist_pins: payload.pins,
      },
    } : current)
  }, [])

  const changeWatchlist = useCallback(async (market: Market, symbol: string, remove: boolean) => {
    const payload = await apiUpdateWatchlist(market, symbol, remove)
    applyWatchlistPayload(payload)
    return payload
  }, [applyWatchlistPayload])

  const changeWatchlistPin = useCallback(async (market: Market, symbol: string, pinned: boolean) => {
    const payload = await apiUpdateWatchlistPin(market, symbol, pinned)
    applyWatchlistPayload(payload)
    return payload
  }, [applyWatchlistPayload])

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
    updateMarketDataStatus,
    changeWatchlist,
    changeWatchlistPin,
    login: async (email, password) => {
      setError(null)
      await apiLogin(email, password)
      await loadBootstrap()
    },
    logout: async () => {
      try {
        await apiLogout()
        setError(null)
      } catch {
        setError('本机登录状态已清除；服务器会话可能需要稍后自动失效。')
      } finally {
        setData(null)
        setMode('demo')
      }
    },
  }), [changeWatchlist, changeWatchlistPin, data, error, loadBootstrap, mode, updateMarketDataStatus])

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}
