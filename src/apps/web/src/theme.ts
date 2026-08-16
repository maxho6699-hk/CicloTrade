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
    canvas: '#050914',
    surfaceBase: '#0B1020',
    surfaceRaised: '#10172B',
    surfaceSelected: '#151D35',
    textPrimary: '#F4F7FC',
    textSecondary: '#AEB9CC',
    textMuted: '#6F7C94',
    brandBlue: '#3B63FF',
    brandViolet: '#8B5CFF',
    brandPink: '#FF4FA3',
    navActiveBg: 'rgba(101, 79, 255, .24)',
    navActiveFg: '#F4F7FC',
    navForeground: '#B7C0D2',
    navMuted: '#68748B',
    controlSurface: '#10172B',
    controlBorder: 'rgba(120, 145, 255, .28)',
    controlDisabledBg: '#151A29',
    controlDisabledFg: '#58657D',
    surfaceGlass: 'rgba(12, 18, 34, .72)',
    borderGlass: 'rgba(120, 145, 255, .16)',
    aiPillBg: 'rgba(101, 79, 255, .16)',
    aiPillBorder: 'rgba(139, 92, 255, .32)',
    aiPillFg: '#E7E1FF',
    positive: '#2FE68A',
    negative: '#FF5277',
    warning: '#FFC857',
    info: '#6EA4FF',
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
    brandBlue: '#315FD1',
    brandViolet: '#7045D1',
    brandPink: '#C83284',
    navActiveBg: 'linear-gradient(115deg, #DCE8FF, #E9E2FF)',
    navActiveFg: '#1E3A8A',
    navForeground: '#17213F',
    navMuted: '#59657D',
    controlSurface: '#FFFFFF',
    controlBorder: '#52658E',
    controlDisabledBg: '#E6EBF4',
    controlDisabledFg: '#7A869D',
    surfaceGlass: 'rgba(255, 255, 255, .82)',
    borderGlass: 'rgba(66, 89, 136, .22)',
    aiPillBg: 'rgba(112, 69, 209, .09)',
    aiPillBorder: 'rgba(112, 69, 209, .2)',
    aiPillFg: '#57359E',
    positive: '#249966',
    negative: '#D94B68',
    warning: '#B77816',
    info: '#315FAE',
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
