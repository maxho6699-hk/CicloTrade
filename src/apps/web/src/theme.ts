export const THEME_STORAGE_KEY = 'ciclotrade.theme'

export type Theme = 'dark' | 'light'

export const THEME_CANVAS: Record<Theme, string> = {
  dark: '#0b0d0c',
  light: '#dbe2db',
}

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
