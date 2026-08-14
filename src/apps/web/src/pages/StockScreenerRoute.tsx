import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchStockScreener,
  fetchStockScreenerPreset,
  saveStockScreenerPreset,
} from '../api/client'
import { StockScreenerPanel } from '../components/StockScreenerPanel'
import type {
  StockScreenerPayload,
  StockScreenerPreset,
  StockScreenerRequest,
} from '../domain/stockScreener'

const INITIAL_REQUEST: StockScreenerRequest = {
  schema_version: 1,
  preset: 'all',
  filters: {},
  sort: { field: 'updated_at', direction: 'desc' },
  page: 1,
  page_size: 20,
}

export function StockScreenerRoute() {
  const [query, setQuery] = useState(INITIAL_REQUEST)
  const [payload, setPayload] = useState<StockScreenerPayload | null>(null)
  const [preset, setPreset] = useState<StockScreenerPreset | null>(null)
  const [loading, setLoading] = useState(true)
  const sequence = useRef(0)

  const load = useCallback(async (next: StockScreenerRequest) => {
    const current = ++sequence.current
    setLoading(true)
    try {
      const result = await fetchStockScreener(next)
      if (sequence.current === current) setPayload(result)
    } catch {
      if (sequence.current === current) setPayload(null)
    } finally {
      if (sequence.current === current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    const initialize = async () => {
      let savedPreset: StockScreenerPreset | null = null
      try { savedPreset = await fetchStockScreenerPreset() } catch { /* A broken preset must not block screening. */ }
      if (!active) return
      setPreset(savedPreset)
      const initial = savedPreset ? { ...INITIAL_REQUEST, filters: savedPreset.filters, sort: savedPreset.sort } : INITIAL_REQUEST
      setQuery(initial)
      void load(initial)
    }
    void initialize()
    return () => { active = false }
  }, [load])

  const changeQuery = (next: StockScreenerRequest) => {
    setQuery(next)
    void load(next)
  }

  return <StockScreenerPanel
    payload={payload}
    preset={preset}
    loading={loading}
    onQueryChange={changeQuery}
    onPageChange={(page) => changeQuery({ ...query, page })}
    onSavePreset={async (next) => {
      const saved = await saveStockScreenerPreset(next)
      setPreset(saved)
      return saved
    }}
  />
}
