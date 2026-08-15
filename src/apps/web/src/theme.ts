export const THEME_STORAGE_KEY = 'ciclotrade.theme'

export type Theme = 'dark' | 'light'

export const THEME_CANVAS: Record<Theme, string> = {
  dark: 'hsl(220 38% 4%)',
  light: 'hsl(220 38% 98%)',
}

/**
 * V2 theme contract. Geometry belongs to the component styles; this map is
 * intentionally limited to paired visual tokens so both themes stay isomorphic.
 */
export const THEME_V2_TOKENS = {
  dark: {
    brandBlue: 'hsl(232 88% 52%)',
    brandViolet: 'hsl(257 76% 53%)',
    navActiveBg: 'hsl(245 52% 20%)',
    navActiveFg: 'hsl(232 88% 75%)',
    aiPillBg: 'hsl(244 30% 16%)',
    aiPillBorder: 'hsl(244 28% 58% / .24)',
    aiPillFg: 'hsl(244 70% 88%)',
    positive: 'hsl(160 72% 58%)',
    negative: 'hsl(352 84% 70%)',
    warning: 'hsl(38 84% 76%)',
    info: 'hsl(216 78% 72%)',
  },
  light: {
    brandBlue: 'hsl(232 88% 54%)',
    brandViolet: 'hsl(257 76% 55%)',
    navActiveBg: 'hsl(245 52% 94%)',
    navActiveFg: 'hsl(232 88% 43%)',
    aiPillBg: 'hsl(244 30% 96%)',
    aiPillBorder: 'hsl(244 28% 45% / .18)',
    aiPillFg: 'hsl(244 70% 38%)',
    positive: 'hsl(160 72% 28%)',
    negative: 'hsl(352 84% 42%)',
    warning: 'hsl(38 84% 33%)',
    info: 'hsl(216 78% 43%)',
  },
} as const satisfies Record<Theme, Record<string, string>>

export function readStoredTheme(): Theme {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function applyTheme(theme: Theme, persist = false) {
  document.documentElement.dataset.theme = theme
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute('content', THEME_CANVAS[theme])
  if (!persist) return
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Storage can be disabled by the browser.
  }
}
