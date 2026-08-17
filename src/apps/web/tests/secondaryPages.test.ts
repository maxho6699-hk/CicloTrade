import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = (name: string) => readFileSync(new URL(`../src/pages/${name}`, import.meta.url), 'utf8')

test('Account reads the server account limit and routes brokerage actions to Trade', () => {
  const source = read('AccountPage.tsx')
  assert.match(source, /auto_control_account_limit/)
  assert.doesNotMatch(source, /高级会员\s*1|专业会员最多\s*5|高级會員\s*1|專業會員最多\s*5/)
  assert.match(source, /单只股票上限/)
  assert.match(source, /to="\/trade"/)
  assert.match(source, /机器人外观|设备与会话|mandate|外观.*锁定|session.*锁定/i)
})

test('Account fails closed when an authorization policy or complete risk DTO is absent', () => {
  const source = read('AccountPage.tsx')
  assert.match(source, /disabled=\{!configured \|\| authorizationBusy !== null\}/)
  assert.match(source, /aria-label=\{`\$\{label\}：\$\{configured \? item\?\.authorized \? '已授权，点击关闭' : '未授权，点击开启' : '政策未配置'\}`\}/)
  assert.match(source, /ai_memory: \['account', 'research', 'ai'\]/)
  assert.doesNotMatch(source, /resumeOpeningPause|验证并恢复/)
  assert.match(source, /to="\/trade">前往交易控制台核验/)
  assert.match(source, /const hasVerifiedRisk =/)
  assert.match(source, /disabled=\{!hasVerifiedRisk\}/)
  assert.match(source, /服务端未返回完整风控设置/)
  assert.doesNotMatch(source, /\{ \.\.\.riskSettings, \.\.\.workspace\.data\.settings\.risk \}/)
})

test('Notifications gates preferences by server capabilities and does not fabricate deliveries', () => {
  const source = read('NotificationsPage.tsx')
  assert.match(source, /capabilities/)
  assert.match(source, /EVENT_CAPABILITIES|服务端能力|能力未返回/)
  assert.match(source, /stock_signal: "tg_stock_signal"[\s\S]*option_signal: "tg_option_signal"/)
  assert.match(source, /!telegramReady/)
  assert.doesNotMatch(source, /risk_rejected:\s*"tg_|order_filled:\s*"tg_|membership_update:\s*"tg_/)
  assert.doesNotMatch(source, /高级会员|专业会员|標準會員|高級會員/)
  assert.match(source, /没有返回任何真实投递结果|不会展示演示送达记录/)
})

test('Help uses router links and documents the four isolated account domains', () => {
  const source = read('HelpPage.tsx')
  assert.match(source, /from 'react-router-dom'/)
  assert.match(source, /<Link[^>]+to="\/(today|discover|research|paper|portfolio|trade)/)
  assert.match(source, /研究|官方验证|个人模拟|券商实盘/)
  assert.doesNotMatch(source, /工单状态|ticket status|已建立工单/)
})

test('Feedback defaults to research and localizes only known statuses', () => {
  const source = read('FeedbackPage.tsx')
  assert.match(source, /\/research/)
  assert.match(source, /status.*zh-Hant|status.*zh-Hans|STATUS_LABELS/i)
  assert.match(source, /statusLabel/)
  assert.match(source, /Idempotency|newIdempotencyKey|submitFeedback/)
})

test('Mystic is truthful locked or empty and has no local social records', () => {
  const source = read('MysticPage.tsx')
  assert.match(source, /locked|锁定|尚未接入|没有真实/)
  assert.match(source, /mystic-boundary-grid/)
  assert.match(source, /不虚构贴文、互动数字、发布日期或命中率/)
  assert.match(source, /股票判断、风险证据和模拟交易继续在研究区处理/)
  assert.doesNotMatch(source, /initialPosts|likedBy|calendarDays|m-0811|人点赞|2026-08-/)
})

test('Legal exposes static policy entry points without claiming a consent receipt', () => {
  const source = read('LegalPage.tsx')
  assert.match(source, /政策|隐私|账户域|版本化同意收据/)
  assert.match(source, /未接入|未提供|locked|锁定/)
  assert.doesNotMatch(source, /consent.*true|versioned.*receipt.*verified/i)
})

test('secondary page reduced motion rule targets the actual operations page host', () => {
  const styles = readFileSync(new URL('../src/styles/secondary-pages.css', import.meta.url), 'utf8')
  assert.match(styles, /prefers-reduced-motion:[^}]*\{[\s\S]*\.operations-page \*/)
  assert.doesNotMatch(styles, /\.secondary-pages \*/)
})
