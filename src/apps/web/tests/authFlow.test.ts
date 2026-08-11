import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const pageSource = readFileSync(new URL('../src/pages/LoginPage.tsx', import.meta.url), 'utf8')
const clientSource = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8')

test('public authentication client exposes every fail-closed account endpoint', () => {
  assert.match(clientSource, /session\/register/)
  assert.match(clientSource, /session\/verification/)
  assert.match(clientSource, /session\/verify-email/)
  assert.match(clientSource, /session\/password-reset'/)
  assert.match(clientSource, /session\/password-reset\/confirm/)
})

test('login page exposes login, registration, verification, and password reset modes', () => {
  assert.match(pageSource, /const authModes = \['login', 'register', 'verify', 'forgot'\]/)
  assert.match(pageSource, /registerAccount\(/)
  assert.match(pageSource, /requestEmailVerification\(/)
  assert.match(pageSource, /verifyEmailToken\(/)
  assert.match(pageSource, /requestPasswordReset\(/)
  assert.match(pageSource, /confirmPasswordReset\(/)
  assert.doesNotMatch(pageSource, /账户由 CicloTrade 管理员开通/)
  assert.doesNotMatch(pageSource, /帳戶由 CicloTrade 管理員開通/)
})

test('public authentication keeps separate Simplified and Traditional copy', () => {
  assert.match(pageSource, /建立 CicloTrade 账户/)
  assert.match(pageSource, /建立 CicloTrade 帳戶/)
  assert.match(pageSource, /系统不会透露某个电子邮件是否已存在/)
  assert.match(pageSource, /系統不會透露某個電子郵件是否已存在/)
})
