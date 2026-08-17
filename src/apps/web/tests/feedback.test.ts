import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const clientSource = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const feedbackSource = readFileSync(new URL('../src/pages/FeedbackPage.tsx', import.meta.url), 'utf8')
const shellSource = readFileSync(new URL('../src/components/AppShell.tsx', import.meta.url), 'utf8')
const catalogSource = readFileSync(new URL('../src/domain/featureCatalog.ts', import.meta.url), 'utf8')
const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const deliberationSource = readFileSync(new URL('../src/pages/DeliberationPage.tsx', import.meta.url), 'utf8')
const deliberationStyles = readFileSync(new URL('../src/styles/deliberation.css', import.meta.url), 'utf8')

test('feedback is routed through More with the canonical idempotent API contract', () => {
  assert.match(clientSource, /'\/api\/rewrite\/v1\/feedback'/)
  assert.match(clientSource, /'Idempotency-Key'/)
  assert.match(clientSource, /context_path/)
  assert.match(clientSource, /contact_preference: 'none' \| 'telegram' \| 'email'/)
  assert.match(clientSource, /summary: string/)
  assert.match(feedbackSource, /item\.summary/)
  assert.match(feedbackSource, /historyError/)
  assert.match(feedbackSource, /暂时无法读取历史回执/)
  assert.match(feedbackSource, /onClick=\{\(\) => void loadReceipts\(\)\}/)
  assert.doesNotMatch(feedbackSource, /item\.message/)
  assert.match(feedbackSource, /maxLength=\{2000\}/)
  assert.doesNotMatch(feedbackSource, /dangerouslySetInnerHTML|attachment|<input[^>]+type="file"/)
  assert.match(catalogSource, /MORE_NAV_ROUTES = \[[^\]]*'\/feedback'/)
  assert.match(shellSource, /to="\/more"/)
  assert.doesNotMatch(shellSource, /to: '\/feedback'/)
  assert.match(appSource, /path="\/feedback"/)
})

test('deliberation keeps a readable in-page disclaimer and explicit legal entry', () => {
  assert.match(deliberationSource, /<h1>多空观点对照<\/h1>/)
  assert.match(deliberationSource, /完整免责声明与法律条款/)
  assert.match(deliberationSource, /仅供研究\/教育参考，不构成投资建议、交易邀约或收益承诺/)
  assert.match(deliberationStyles, /\.app-shell \.deliberation-compliance-banner \{[^}]*font-size: 14px/)
  assert.match(deliberationStyles, /\.app-shell \.deliberation-legal-link \{[^}]*min-height: 44px[^}]*font-size: 14px/)
})
