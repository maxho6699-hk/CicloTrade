import { useCallback, useEffect, useState } from 'react'
import {
  fetchFeatureCatalog,
  recordRecentFeature,
  saveFeatureCatalogPreferences,
} from '../api/client'
import type { FeatureCatalogPayload } from '../domain/featureCatalog'
import { MorePage } from './MorePage'

function message(error: unknown): string {
  return error instanceof Error ? error.message : '功能目录暂时不可用。'
}

export function MoreRoute() {
  const [catalog, setCatalog] = useState<FeatureCatalogPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setCatalog(await fetchFeatureCatalog())
    } catch (caught) {
      setCatalog(null)
      setError(message(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  return (
    <MorePage
      catalog={catalog}
      loading={loading}
      error={error}
      onRetry={() => void load()}
      onSavePins={async (pinned, recent, expectedVersion) => {
        const next = await saveFeatureCatalogPreferences({
          expected_version: expectedVersion,
          pinned,
          recent,
        })
        setCatalog(next)
        return next
      }}
      onRecordRecent={async (key, expectedVersion) => {
        const next = await recordRecentFeature(key, expectedVersion)
        setCatalog(next)
        return next
      }}
    />
  )
}
