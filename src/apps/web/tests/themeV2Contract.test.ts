import { readFileSync, readdirSync } from 'node:fs'
import test from 'node:test'
import assert from 'node:assert/strict'

const root = new URL('../src/', import.meta.url)
const base = readFileSync(new URL('styles/base.css', root), 'utf8')
const components = readFileSync(new URL('styles/components.css', root), 'utf8')
const navigation = readFileSync(new URL('styles/navigation.css', root), 'utf8')
const visualWaves = readFileSync(new URL('styles/visual-waves.css', root), 'utf8')
const market = readFileSync(new URL('styles/market.css', root), 'utf8')
const responsive = readFileSync(new URL('styles/responsive.css', root), 'utf8')
const deliberation = readFileSync(new URL('styles/deliberation.css', root), 'utf8')
const todayDiscover = readFileSync(new URL('styles/today-discover-v2.css', root), 'utf8')
const recommendations = readFileSync(new URL('styles/recommendations.css', root), 'utf8')
const intelligence = readFileSync(new URL('styles/intelligence.css', root), 'utf8')
const discoverPage = readFileSync(new URL('pages/DiscoverV2Page.tsx', root), 'utf8')
const theme = readFileSync(new URL('theme.ts', root), 'utf8')
const appShell = readFileSync(new URL('components/AppShell.tsx', root), 'utf8')
const allCss = readdirSync(new URL('styles/', root))
  .filter((name) => name.endsWith('.css'))
  .map((name) => [name, readFileSync(new URL(`styles/${name}`, root), 'utf8')] as const)

test('V2 theme exposes paired surfaces, brand, AI and fixed semantic hues', () => {
  for (const token of [
    '--canvas', '--workspace', '--surface', '--surface-raised', '--surface-pressed',
    '--brand-blue', '--brand-violet', '--brand-sky', '--premium', '--ai-blue', '--nav-active-bg', '--nav-active-fg',
    '--ai-pill-bg', '--ai-pill-border', '--ai-pill-fg', '--positive', '--negative',
    '--warning', '--info', '--text-primary', '--text-secondary', '--text-muted',
  ]) {
    assert.match(base, new RegExp(`${token}:`), `missing ${token}`)
  }
  assert.match(base, /--canvas: #040711/)
  assert.match(base, /--workspace: #0b1120/i)
  assert.match(base, /--surface-raised: #131d33/i)
  assert.match(base, /--brand-blue: #1d4ed8/i)
  assert.match(base, /--brand-violet: #2563eb/i)
  assert.match(base, /--brand-sky: #38bdf8/i)
  assert.match(base, /--premium: #e8b85c/i)
  assert.match(base, /--ai-blue: #60a5fa/i)
  assert.match(base, /--text-secondary: #cbd5e1/i)
  assert.match(base, /--text-muted: #8fa0b8/i)
  assert.match(base, /--border: rgb\(148 163 184 \/ \.18\)/i)
  assert.match(base, /--border-strong: rgb\(148 163 184 \/ \.28\)/i)
  assert.match(base, /--market-up: #ff3b30/i)
  assert.match(base, /--market-down: #05c46b/i)
  assert.match(base, /--positive: #05c46b/i)
  assert.match(base, /--negative: #ff3b30/i)
  assert.match(base, /--pnl-profit: var\(--market-up\)/i)
  assert.match(base, /--pnl-loss: var\(--market-down\)/i)
  assert.match(base, /--warning: #ffa800/i)
  assert.match(base, /--info: #60a5fa/i)
  assert.match(base, /--shadow-cta:/)
  assert.match(visualWaves, /--text-secondary: #cbd5e1/i)
  assert.match(visualWaves, /--text-muted: #8fa0b8/i)
  assert.match(visualWaves, /--border: rgb\(148 163 184 \/ 0\.18\)/)
  assert.match(visualWaves, /--border-strong: rgb\(148 163 184 \/ 0\.28\)/)
  assert.match(visualWaves, /--shadow-float: 0 18px 48px rgb\(0 0 0 \/ 0\.36\);/)
  assert.match(visualWaves, /--shadow-cta: 0 8px 22px rgb\(37 99 235 \/ 0\.18\);/)
  assert.match(visualWaves, /:is\(\.app-shell, \.login-page, \.welcome-page\) p \{[\s\S]*?font-size: max\(13px, 1em\) !important;/)
  assert.match(visualWaves, /:is\(\.app-shell, \.login-page, \.welcome-page\) :is\(button, \.button, input, select, textarea\) \{[\s\S]*?font-size: max\(13px, 1em\) !important;/)
  assert.match(theme, /textSecondary: '#CBD5E1'/)
  assert.match(theme, /textMuted: '#8FA0B8'/)
  assert.match(todayDiscover, /--today-subtle:var\(--theme-v2-text-muted,#8fa0b8\)/i)
  assert.match(todayDiscover, /--discover-subtle:#8fa0b8/i)
  assert.match(deliberation, /--dl-copy: #cbd5e1/i)
  assert.match(deliberation, /--dl-muted: #8fa0b8/i)
  assert.match(deliberation, /--dl-yellow: #f59e0b/i)
  assert.doesNotMatch(todayDiscover, /\.today-v2-page \.v2-card\{[^}]*color-mix\(in srgb,var\(--today-(?:blue|violet)/)
  assert.match(todayDiscover, /--discover-shadow:0 16px 36px rgba\(0,0,0,\.28\),inset 0 1px 0 rgba\(255,255,255,\.045\)/)
  assert.doesNotMatch(deliberation, /rgb\(255 200 87/)
  assert.match(base, /:root\[data-theme='light'\][\s\S]*?--warning: #b26a00/i)
  assert.match(base, /:root\[data-theme='light'\][\s\S]*?--positive: #047857[\s\S]*?--negative: #d92d20[\s\S]*?--market-up: #d92d20[\s\S]*?--market-down: #047857/i)
  assert.match(theme, /THEME_V2_TOKENS/)
  assert.match(theme, /brandSky:/)
  assert.match(theme, /premiumGold:/)
  assert.match(theme, /aiBlue:/)
  assert.doesNotMatch(theme, /#(?:ff4fa3|8b5cff|3b63ff|c83284|7045d1)/i)
  assert.doesNotMatch(todayDiscover, /brand-pink|today-pink/i)
})

test('public brand uses the cobalt-safe icon rather than legacy purple artwork', () => {
  const header = readFileSync(new URL('components/PublicPageHeader.tsx', root), 'utf8')
  assert.match(header, /src="\/brand\/ciclotrade-icon\.png"/)
  assert.doesNotMatch(header, /ciclotrade-logo\.webp/)
  assert.match(appShell, /src="\/brand\/ciclotrade-icon\.png"/)
  assert.doesNotMatch(appShell, /ciclotrade-logo\.(?:jpg|webp)/)
})

test('Discover finish gate raises readable copy and removes undersized text and persistent glow', () => {
  assert.doesNotMatch(todayDiscover, /font-size\s*:\s*(?:7|8|9|10)px/i)
  assert.doesNotMatch(todayDiscover, /font\s*:\s*[^;{}]*\b(?:7|8|9|10)px/i)
  assert.match(todayDiscover, /\.discover-heading-copy p\{[^}]*font-size:13px/)
  assert.match(todayDiscover, /\.discover-watchlist-list \.v2-stock-identity strong\{font-size:12px/)
  assert.match(todayDiscover, /\.discover-filter-grid select\{[^}]*font-size:13px/)
  assert.match(todayDiscover, /\.discover-filter-search \.v2-search-field input\{font-size:13px/)
  assert.match(todayDiscover, /\.discover-v2-page \.v2-button-primary\{[^}]*box-shadow:0 0 0 1px rgba\(96, 165, 250,\.18\),0 8px 22px rgba\(37, 99, 235,\.18\)/)
  assert.doesNotMatch(todayDiscover, /\.discover-view-tabs button\.is-active\{[^}]*0 0 28px/)
  assert.match(todayDiscover, /\.discover-ai-banner\{[^}]*min-height:212px/)
  assert.doesNotMatch(todayDiscover, /rgba\(24,17,53/)
  assert.doesNotMatch(discoverPage, /#(?:3B63FF|8B5CFF|FF4FA3)/i)
  assert.match(discoverPage, /stopColor="#1D4ED8"[\s\S]*stopColor="#2563EB"[\s\S]*stopColor="#38BDF8"/)
  assert.match(discoverPage, /feGaussianBlur stdDeviation="2\.2"/)
  assert.match(recommendations, /@media\(min-width:1480px\)\{\.recommendation-preview-grid\{grid-template-columns:repeat\(4,minmax\(0,1fr\)\)/)
  assert.match(intelligence, /\.workflow-index-layout\{[^}]*grid-template-columns:minmax\(0,1\.18fr\) minmax\(380px,\.82fr\)[^}]*align-items:start/)
})

test('V2 component contract keeps cards borderless and CTA cobalt-to-sky', () => {
  assert.match(components, /\.button\.primary[\s\S]*?border-color: transparent;/)
  assert.match(components, /\.button\.primary[\s\S]*?linear-gradient\(90deg, var\(--brand-blue\) 0%, var\(--brand-violet\) 58%, var\(--brand-sky\) 100%\)/)
  assert.match(components, /\.decision-card \{[\s\S]*?border: 0;[\s\S]*?box-shadow: var\(--shadow-panel\)/)
  assert.match(components, /\.status-chip \{[\s\S]*?border: 0;[\s\S]*?border-radius: 3px;/)
  assert.match(components, /\.ai-pill \{[\s\S]*?height: 36px;[\s\S]*?border-radius: 8px;/)
  assert.match(components, /\.plan-card\.recommended \{[^}]*var\(--premium\)/)
  assert.match(components, /\.recommended-label \{[^}]*var\(--premium\)/)
})

test('V2 palette removes the legacy pink violet blue gradient from major surfaces', () => {
  const majorStyles = [base, visualWaves, market, responsive, deliberation, todayDiscover].join('\n')
  assert.doesNotMatch(majorStyles, /#(?:ff4fa3|8b5cff|3b63ff|7000ff|a78bfa|8058ff|345fff)/i)
  assert.doesNotMatch(majorStyles, /(?:255\s*[, ]\s*79\s*[, ]\s*163|139\s*[, ]\s*92\s*[, ]\s*255|59\s*[, ]\s*99\s*[, ]\s*255)/i)
})

test('V2 palette contains no high-saturation indigo, purple or pink colors in any stylesheet', () => {
  const offenders: string[] = []
  const inspectRgb = (name: string, label: string, r: number, g: number, b: number) => {
    const normalized = [r, g, b].map((value) => value / 255)
    const max = Math.max(...normalized)
    const min = Math.min(...normalized)
    const delta = max - min
    const saturation = max === 0 ? 0 : delta / max
    let hue = 0
    if (delta > 0) {
      if (max === normalized[0]) hue = 60 * (((normalized[1] - normalized[2]) / delta) % 6)
      else if (max === normalized[1]) hue = 60 * ((normalized[2] - normalized[0]) / delta + 2)
      else hue = 60 * ((normalized[0] - normalized[1]) / delta + 4)
    }
    if (hue < 0) hue += 360
    if (hue >= 228 && hue <= 349 && saturation >= .14 && max >= .28) offenders.push(`${name}:${label}`)
  }
  for (const [name, css] of allCss) {
    for (const match of css.matchAll(/#[0-9a-f]{6}\b/gi)) {
      const hex = match[0]
      const [r, g, b] = [1, 3, 5].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16))
      inspectRgb(name, hex, r, g, b)
    }
    for (const match of css.matchAll(/rgba?\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)/gi)) {
      inspectRgb(name, `rgb(${match[1]} ${match[2]} ${match[3]})`, Number(match[1]), Number(match[2]), Number(match[3]))
    }
  }
  assert.deepEqual([...new Set(offenders)], [])
})

test('V2 typography contains no 7px to 10px text in any stylesheet', () => {
  const offenders: string[] = []
  for (const [name, css] of allCss) {
    for (const match of css.matchAll(/font-size\s*:\s*(7|8|9|10)px/gi)) {
      offenders.push(`${name}:${match[0]}`)
    }
    for (const match of css.matchAll(/font\s*:\s*[^;{}]*?\b(7|8|9|10)px/gi)) {
      offenders.push(`${name}:${match[0]}`)
    }
  }
  assert.deepEqual([...new Set(offenders)], [])
})

test('V2 navigation emphasizes four gradient groups while keeping children collapsed and quiet', () => {
  assert.match(navigation, /\.app-shell \.nav-item\.active::before \{ display: none; \}/)
  assert.match(navigation, /\.app-shell \.nav-group\.is-active \.nav-group-header \{[^}]*border-color: transparent;[^}]*background: transparent;[^}]*box-shadow: none/)
  assert.match(navigation, /\.app-shell \.nav-group-children \{ display: none;/)
  assert.match(navigation, /\.app-shell \.nav-group\.is-expanded \.nav-group-children \{ display: grid; \}/)
  assert.match(navigation, /\.app-shell \.nav-item\.nav-child \{[^}]*font-size: 14px;[^}]*font-weight: 600/)
  assert.match(navigation, /\.app-shell \.nav-item\.nav-child\.active \{[^}]*background: color-mix[^}]*box-shadow: inset 3px/)
  assert.match(navigation, /\.app-shell \.nav-group-toggle svg \{[^}]*width: 18px;[^}]*height: 18px/)
  assert.match(navigation, /@media \(min-width: 701px\) and \(max-width: 1180px\)[\s\S]*?\.nav-group-link \{[^}]*width: 48px;[^}]*height: 48px/)
})

test('Ciclo AI launcher uses the shared theme primitive and opens the bounded-context page', () => {
  assert.match(appShell, /className=\{`ai-pill \$\{aiAvailable \? '' : 'is-locked'\}`\}/)
  assert.match(appShell, /to=\{aiAvailable \? '\/ai' : '\/membership'\}/)
  assert.doesNotMatch(appShell, /ai-unavailable-popover|aiPanelOpen/)
  assert.doesNotMatch(appShell, /AI.{0,20}(?:自动下单|一键下单)/)
})
