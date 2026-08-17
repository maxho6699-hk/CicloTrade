export const THEME_STORAGE_KEY = 'ciclotrade.theme'

export type Theme = 'dark' | 'light'

export const THEME_CANVAS: Record<Theme, string> = {
  dark: '#040711',
  light: '#F8FAFC',
}

/**
 * V2 theme contract. Geometry belongs to the component styles; this map is
 * intentionally limited to paired visual tokens so both themes stay isomorphic.
 */
export const THEME_V2_TOKENS = {
  dark: {
    canvas: '#040711',
    surfaceBase: '#0B1120',
    surfaceRaised: '#131D33',
    surfaceSelected: '#17233B',
    textPrimary: '#F8FAFC',
    textSecondary: '#CBD5E1',
    textMuted: '#8FA0B8',
    brandBlue: '#1D4ED8',
    brandViolet: '#2563EB',
    brandSky: '#38BDF8',
    premiumGold: '#E8B85C',
    aiBlue: '#60A5FA',
    navActiveBg: 'rgba(29, 78, 216, .18)',
    navActiveFg: '#8DD8FF',
    navForeground: '#CBD5E1',
    navMuted: '#68748B',
    controlSurface: '#131D33',
    controlBorder: 'rgba(96, 165, 250, .28)',
    controlDisabledBg: '#101827',
    controlDisabledFg: '#58657D',
    surfaceGlass: 'rgba(11, 17, 32, .78)',
    borderGlass: 'rgba(96, 165, 250, .16)',
    aiPillBg: 'rgba(37, 99, 235, .14)',
    aiPillBorder: 'rgba(96, 165, 250, .28)',
    aiPillFg: '#DBEAFE',
    positive: '#05C46B',
    negative: '#FF3B30',
    warning: '#FFA800',
    info: '#60A5FA',
  },
  light: {
    canvas: '#F8FBFC',
    shellCanvas: '#F8FBFC',
    sidebarSurface: '#FFFFFF',
    topbarSurface: 'rgba(255, 255, 255, .96)',
    pageSurface: '#F8FBFC',
    shellDivider: '#D9E2F0',
    surfaceBase: '#FFFFFF',
    surfaceRaised: '#F2F6FC',
    surfaceSelected: '#E9EFFA',
    textPrimary: '#101526',
    textSecondary: '#59657D',
    textMuted: '#647188',
    brandBlue: '#1D4ED8',
    brandViolet: '#2563EB',
    brandSky: '#0284C7',
    premiumGold: '#9A6700',
    aiBlue: '#2563EB',
    navActiveBg: 'rgba(29, 78, 216, .1)',
    navActiveFg: '#1E3A8A',
    navForeground: '#17213F',
    navMuted: '#59657D',
    controlSurface: '#FFFFFF',
    controlBorder: '#52658E',
    controlDisabledBg: '#E6EBF4',
    controlDisabledFg: '#7A869D',
    surfaceGlass: 'rgba(255, 255, 255, .82)',
    borderGlass: 'rgba(66, 89, 136, .22)',
    aiPillBg: 'rgba(37, 99, 235, .08)',
    aiPillBorder: 'rgba(37, 99, 235, .18)',
    aiPillFg: '#1E3A8A',
    positive: '#047857',
    negative: '#D92D20',
    warning: '#B26A00',
    info: '#1D4ED8',
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
  const root = document.documentElement
  root.dataset.theme = theme
  Object.entries(THEME_V2_TOKENS[theme]).forEach(([name, value]) => {
    const cssName = name.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)
    root.style.setProperty(`--theme-v2-${cssName}`, value)
  })
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute('content', THEME_CANVAS[theme])
  if (!persist) return
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Storage can be disabled by the browser.
  }
}
