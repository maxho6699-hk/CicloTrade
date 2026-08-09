import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { saveLocale } from '../api/client'
import { useWorkspace } from '../api/workspace-context'
import { localizeText, setRuntimeLocale, type UiLocale } from './runtime'
import { LocaleContext, type LocaleContextValue } from './locale-context'

const STORAGE_KEY = 'ciclotrade.uiLocale'

function storedLocale(): UiLocale {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    return value === 'zh-Hans' || value === 'zh-Hant' ? value : 'zh-Hant'
  } catch {
    return 'zh-Hant'
  }
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  const workspace = useWorkspace()
  const [locale, setLocaleState] = useState<UiLocale>(storedLocale)
  const [syncState, setSyncState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const serverPreferenceAppliedFor = useRef<number | null>(null)
  const localPreferenceExplicit = useRef<boolean | null>(null)
  const saveSequence = useRef(0)
  if (localPreferenceExplicit.current === null) {
    try { localPreferenceExplicit.current = window.localStorage.getItem(STORAGE_KEY) !== null } catch { localPreferenceExplicit.current = false }
  }
  setRuntimeLocale(locale)

  useEffect(() => {
    const userId = workspace.user?.id ?? null
    const serverLocale = workspace.data?.settings.ui_locale
    if (userId === null) {
      serverPreferenceAppliedFor.current = null
      return
    }
    if (serverPreferenceAppliedFor.current === userId) return
    serverPreferenceAppliedFor.current = userId
    if (serverLocale) {
      if (serverLocale !== locale) setLocaleState(serverLocale)
      try { window.localStorage.setItem(STORAGE_KEY, serverLocale) } catch { /* storage can be disabled */ }
      setSyncState('idle')
      return
    }
    if (localPreferenceExplicit.current) {
      const sequence = ++saveSequence.current
      setSyncState('saving')
      void saveLocale(locale).then(() => {
        if (saveSequence.current === sequence) setSyncState('saved')
      }).catch(() => {
        if (saveSequence.current === sequence) setSyncState('error')
      })
    }
  }, [locale, workspace.data?.settings.ui_locale, workspace.user?.id])

  useEffect(() => {
    document.documentElement.lang = locale
    document.title = localizeText('CicloTrade · 决策终端')
  }, [locale])

  const value = useMemo<LocaleContextValue>(() => ({
    locale,
    formatLocale: locale === 'zh-Hant' ? 'zh-Hant-TW' : 'zh-Hans-CN',
    syncState,
    setLocale: async (next) => {
      setRuntimeLocale(next)
      setLocaleState(next)
      localPreferenceExplicit.current = true
      try { window.localStorage.setItem(STORAGE_KEY, next) } catch { /* storage can be disabled */ }
      if (workspace.mode === 'authenticated') {
        const sequence = ++saveSequence.current
        setSyncState('saving')
        try {
          await saveLocale(next)
          if (saveSequence.current === sequence) setSyncState('saved')
        } catch {
          if (saveSequence.current === sequence) setSyncState('error')
        }
      } else {
        setSyncState('saved')
      }
    },
  }), [locale, syncState, workspace.mode])

  return <LocaleContext.Provider value={value}><div className="locale-root">{children}</div></LocaleContext.Provider>
}
