import { createContext } from 'react'
import type { UiLocale } from './runtime'

export interface LocaleContextValue {
  locale: UiLocale
  formatLocale: 'zh-Hant-TW' | 'zh-Hans-CN'
  syncState: 'idle' | 'saving' | 'saved' | 'error'
  setLocale: (locale: UiLocale) => Promise<void>
}

export const LocaleContext = createContext<LocaleContextValue | null>(null)
