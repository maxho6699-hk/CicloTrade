import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const clientSource = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const feedbackSource = readFileSync(new URL('../src/pages/FeedbackPage.tsx', import.meta.url), 'utf8')
const shellSource = readFileSync(new URL('../src/components/AppShell.tsx', import.meta.url), 'utf8')
const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')

test('feedback is routed from desktop and mobile navigation with the canonical idempotent API contract', () => {
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
  assert.match(shellSource, /to: '\/feedback'/)
  assert.match(shellSource, /state=\{\{ sourcePage: pathname \}\}/)
  assert.match(appSource, /path="\/feedback"/)
})
