import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  fetchBootstrap,
  fetchMarketStatus,
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
import { createVisibilityPolling } from '../domain/dataSourcePresentation'
import { createSerialMutationQueue, createStateRevisionGuard } from './watchlistMutationCoordinator'

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<WorkspaceMode>('loading')
  const [data, setData] = useState<BootstrapPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const watchlistMutationQueueRef = useRef(createSerialMutationQueue())
  const watchlistStateRevisionRef = useRef(createStateRevisionGuard())

  const loadBootstrap = useCallback(async () => {
    const revision = watchlistStateRevisionRef.current.snapshot()
    const bootstrap = await fetchBootstrap()
    if (!watchlistStateRevisionRef.current.isCurrent(revision)) return
    setData(bootstrap)
    setMode('authenticated')
    setError(null)
  }, [])

  const updateMarketDataStatus = useCallback((status: Partial<BootstrapPayload['market_data']>) => {
    const definedStatus = Object.fromEntries(
      Object.entries(status).filter(([, value]) => value !== undefined),
    ) as Partial<BootstrapPayload['market_data']>
    setData((current) => {
      if (!current) return current
      const unchanged = Object.entries(definedStatus)
        .every(([key, value]) => Object.is(current.market_data[key as keyof BootstrapPayload['market_data']], value))
      if (unchanged) return current
      return { ...current, market_data: { ...current.market_data, ...definedStatus } }
    })
  }, [])

  const applyWatchlistPayload = useCallback((payload: WatchlistPayload) => {
    watchlistStateRevisionRef.current.advance()
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
    return watchlistMutationQueueRef.current(async () => {
      try {
        const payload = await apiUpdateWatchlist(market, symbol, remove)
        applyWatchlistPayload(payload)
        return payload
      } catch (caught) {
        await loadBootstrap().catch(() => undefined)
        throw caught
      }
    })
  }, [applyWatchlistPayload, loadBootstrap])

  const changeWatchlistPin = useCallback(async (market: Market, symbol: string, pinned: boolean) => {
    return watchlistMutationQueueRef.current(async () => {
      try {
        const payload = await apiUpdateWatchlistPin(market, symbol, pinned)
        applyWatchlistPayload(payload)
        return payload
      } catch (caught) {
        await loadBootstrap().catch(() => undefined)
        throw caught
      }
    })
  }, [applyWatchlistPayload, loadBootstrap])

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

  useEffect(() => {
    if (mode !== 'authenticated') return
    let active = true
    const stopPolling = createVisibilityPolling(async () => {
      const status = await fetchMarketStatus()
      if (!active) return
      const available = status.status === 'available' && status.upstream_connected
      updateMarketDataStatus({
        display_source: '真实数据来源',
        is_realtime: status.is_realtime,
        freshness: !available
          ? '不可用'
          : status.delivery_delay_minutes > 0
            ? '延迟行情'
            : status.is_realtime ? '实时' : '仅供研究',
        detail: available ? '账户行情可见性状态已核对' : '行情服务暂不可用',
        delivery_delay_minutes: status.delivery_delay_minutes,
        visible_as_of: status.visible_as_of,
        observed_at: status.observed_at,
      })
    }, 15_000)
    return () => { active = false; stopPolling() }
  }, [mode, updateMarketDataStatus])

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
