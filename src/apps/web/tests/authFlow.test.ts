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

test('login page keeps registration verification inside one card and retains password reset', () => {
  assert.match(pageSource, /const authModes = \['login', 'register', 'forgot'\]/)
  assert.match(pageSource, /registrationStage/)
  assert.match(pageSource, /registrationStage === 'verify'/)
  assert.match(pageSource, /maskedEmail/)
  assert.match(pageSource, /改用其他电子邮件|改用其他電子郵件/)
  assert.doesNotMatch(pageSource, /changeMode\('verify'/)
  assert.match(pageSource, /registerAccount\(/)
  assert.match(pageSource, /requestEmailVerification\(/)
  assert.match(pageSource, /verifyEmailToken\(/)
  assert.match(pageSource, /requestPasswordReset\(/)
  assert.match(pageSource, /confirmPasswordReset\(/)
  assert.match(pageSource, /const submittedData = new FormData\(submittedForm\)/)
  assert.match(pageSource, /workspace\.login\(submittedEmail, submittedPassword\)/)
  assert.match(pageSource, /minLength=\{mode === 'login' \? undefined : 8\}/)
  assert.match(pageSource, /minLength=\{8\} name="confirm_password"/)
  assert.match(pageSource, /mode === 'login' \? \(traditional \? '輸入你的密碼…' : '输入你的密码…'\)/)
  assert.match(pageSource, /至少 8 个字符，包含字母和数字/)
  assert.doesNotMatch(pageSource, /账户由 CicloTrade 管理员开通/)
  assert.doesNotMatch(pageSource, /帳戶由 CicloTrade 管理員開通/)
})

test('public authentication keeps separate Simplified and Traditional copy', () => {
  assert.match(pageSource, /建立 CicloTrade 账户/)
  assert.match(pageSource, /建立 CicloTrade 帳戶/)
  assert.match(pageSource, /系统不会透露某个电子邮件是否已存在/)
  assert.match(pageSource, /系統不會透露某個電子郵件是否已存在/)
})
