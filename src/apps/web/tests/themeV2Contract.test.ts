import { readFileSync } from 'node:fs'
import test from 'node:test'
import assert from 'node:assert/strict'

const root = new URL('../src/', import.meta.url)
const base = readFileSync(new URL('styles/base.css', root), 'utf8')
const components = readFileSync(new URL('styles/components.css', root), 'utf8')
const navigation = readFileSync(new URL('styles/navigation.css', root), 'utf8')
const theme = readFileSync(new URL('theme.ts', root), 'utf8')
const appShell = readFileSync(new URL('components/AppShell.tsx', root), 'utf8')

test('V2 theme exposes paired surfaces, brand, AI and fixed semantic hues', () => {
  for (const token of [
    '--canvas', '--workspace', '--surface', '--surface-raised', '--surface-pressed',
    '--brand-blue', '--brand-violet', '--nav-active-bg', '--nav-active-fg',
    '--ai-pill-bg', '--ai-pill-border', '--ai-pill-fg', '--positive', '--negative',
    '--warning', '--info', '--text-primary', '--text-secondary', '--text-muted',
  ]) {
    assert.match(base, new RegExp(`${token}:`), `missing ${token}`)
  }
  assert.match(base, /--positive: hsl\(160 72% \d+%\)/)
  assert.match(base, /--negative: hsl\(352 84% \d+%\)/)
  assert.match(base, /--warning: hsl\(38 84% \d+%\)/)
  assert.match(base, /--info: hsl\(216 78% \d+%\)/)
  assert.match(base, /--brand-blue: hsl\(232 88% 52%\)/)
  assert.match(base, /--brand-violet: hsl\(257 76% 53%\)/)
  assert.match(base, /--shadow-cta:/)
  assert.match(base, /:root\[data-theme='light'\][\s\S]*?--warning: hsl\(38 84% 33%\)/)
  assert.match(theme, /THEME_V2_TOKENS/)
})

test('V2 component contract keeps cards borderless and CTA blue-to-violet', () => {
  assert.match(components, /\.button\.primary[\s\S]*?border-color: transparent;/)
  assert.match(components, /\.button\.primary[\s\S]*?linear-gradient\(90deg, var\(--brand-blue\) 0%, var\(--brand-violet\) 100%\)/)
  assert.match(components, /\.decision-card \{[\s\S]*?border: 0;[\s\S]*?box-shadow: var\(--shadow-panel\)/)
  assert.match(components, /\.status-chip \{[\s\S]*?border: 0;[\s\S]*?border-radius: 3px;/)
  assert.match(components, /\.ai-pill \{[\s\S]*?height: 36px;[\s\S]*?border-radius: 8px;/)
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
