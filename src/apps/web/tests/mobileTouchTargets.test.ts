import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const responsive = readFileSync(new URL('../src/styles/responsive.css', import.meta.url), 'utf8')
const more = readFileSync(new URL('../src/styles/more.css', import.meta.url), 'utf8')
const base = readFileSync(new URL('../src/styles/base.css', import.meta.url), 'utf8')

test('phone shell keeps search, account utilities, and fixed navigation at a 44px touch target', () => {
  assert.match(base, /\.skip-link \{[^}]*display: inline-flex;[^}]*min-height: 44px;[^}]*align-items: center/)
  const phoneRules = responsive.slice(responsive.indexOf('@media (max-width: 760px)'), responsive.indexOf('@media (max-width: 620px)'))
  assert.match(phoneRules, /\.command-search \{[^}]*min-height: 44px/)
  assert.match(phoneRules, /\.locale-button,[\s\S]*?\.theme-button,[\s\S]*?\.user-menu,[\s\S]*?\.mobile-menu-button \{[^}]*width: 44px;[^}]*min-height: 44px;[^}]*height: 44px/)
  assert.match(phoneRules, /\.account-popover a,[\s\S]*?\.account-popover button \{[^}]*min-height: 44px/)
  assert.match(phoneRules, /\.mobile-nav a,[\s\S]*?\.mobile-nav button \{[^}]*min-height: 44px/)
})

test('844px landscape shell keeps every topbar interaction at least 44px', () => {
  const start = responsive.indexOf('@media (max-width: 980px) and (max-height: 560px) and (orientation: landscape)')
  const end = responsive.indexOf('@media (max-width: 420px)', start)
  const landscapeRules = responsive.slice(start, end)
  assert.ok(start >= 0 && end > start)
  assert.match(landscapeRules, /\.command-search \{[^}]*min-height: 44px/)
  assert.match(landscapeRules, /\.locale-button,[\s\S]*?\.theme-button,[\s\S]*?\.user-menu,[\s\S]*?\.mobile-menu-button \{[^}]*min-width: 44px;[^}]*min-height: 44px;[^}]*height: 44px/)
  assert.match(landscapeRules, /\.account-popover a,[\s\S]*?\.account-popover button \{[^}]*min-height: 44px/)
  assert.match(landscapeRules, /\.shell-content \{[^}]*padding-bottom: calc\(58px \+ env\(safe-area-inset-bottom\)\)/)
  assert.match(landscapeRules, /\.mobile-nav \{[^}]*display: grid;[^}]*grid-template-columns: repeat\(5, 1fr\)/)
  assert.match(landscapeRules, /\.mobile-nav a,[\s\S]*?\.mobile-nav button \{[^}]*min-height: 44px/)
  assert.match(landscapeRules, /\.app-shell:has\(\.chart-workspace-shell\.is-workbench-open\) \.mobile-nav \{ display: none; \}/)
  assert.match(landscapeRules, /\.market-workspace \.chart-frame \{[^}]*58px[^}]*safe-area-inset-bottom/)
})

test('More feature tools retain a compact 44px mobile hit area', () => {
  const phoneRules = more.slice(more.indexOf('@media (max-width: 760px)'))
  assert.match(phoneRules, /\.more-search input \{ min-height: 44px; \}/)
  assert.match(phoneRules, /\.feature-pin \{ min-width: 44px; min-height: 44px;/)
  assert.match(phoneRules, /\.feature-view-toggle button \{ min-width: 44px; min-height: 44px;/)
  assert.match(phoneRules, /\.feature-grid--icon \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); \}/)
  assert.match(more, /\.feature-card--icon \.feature-pin \{[^}]*min-width: 44px;[^}]*min-height: 44px/)
  assert.match(phoneRules, /\.more-tools \{ display: grid; width: 100%; grid-template-columns: minmax\(0, 1fr\) auto;/)
  assert.match(more, /@media \(max-width: 420px\) \{[\s\S]*?\.feature-view-toggle button span \{ display: none; \}/)
})
