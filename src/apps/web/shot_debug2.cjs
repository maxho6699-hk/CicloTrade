/* dev server + mock API 源码堆栈 */
const { chromium } = require('playwright');
const fs = require('fs');
const src = fs.readFileSync('shot_research.cjs', 'utf-8');
const m = src.match(/const bootstrap = (\{[\s\S]*?\n\};)/);
// 直接引用 bootstrap（简单方式：从文件里读）
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.on('pageerror', e => {
    console.log('PAGEERR:', e.message.slice(0, 120));
    console.log('STACK:', (e.stack || '').split('\n').slice(1, 8).join('\n'));
  });
  await page.route('**/api/**', route => {
    const url = route.request().url();
    if (url.includes('/bootstrap')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ me: { id: 1, email: 'q@c.t', display_name: 'Q', admin_role: 'user', verified: true }, membership: { auto_renewal: false, capabilities: ['research', 'paper', 'portfolio', 'ai'], plans: [], legacy_plans: [], policy: { key: null, version: null, sha256: null }, orders: [], payment_methods: { fps: { available: false, has_text: false, has_qr: false }, alipay: { available: false, has_text: false, has_qr: false }, wechat: { available: false, has_text: false, has_qr: false } }, brokerage: { auto_control_account_limit: 1, accounts_used: 0, accounts: [], capability_catalog: [], requires_user_authorization: true } }, execution_control: { auto_trading_enabled: false, pause_reason: null, block_reasons: [] }, telegram: { bound: false, verified: false, consented: false, chat_id_masked: '', events: {} }, portfolio: { account_mode: 'official', scope: 'system', positions: [], orders: [], accounts: { US: { equity: 10000, cash: 10000 }, CN: { equity: 0, cash: 0 }, HK: { equity: 0, cash: 0 } }, fresh_marks: false, mark_source: 'm', activity: { executions: [], intervals: [], updated_at: new Date().toISOString() } }, recommendations: { items: [], source: 'm', fresh_marks: false, delivery: { stock: 0, option: 0 } }, performance: { items: [], fresh_marks: false, mark_source: 'm' }, settings: { risk: {}, telegram_events: {}, watchlists: { us: ['AAPL'], a_share: [] }, watchlist_pins: { us: [], a_share: [] }, ui_locale: 'zh-Hant' }, alerts: { entries: [], items: [], version: 1 }, market_data: { status: 'delayed', freshness: '延遲15分鐘', delay_minutes: 15, updated_at: new Date().toISOString() } }) });
    if (url.includes('/session')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ authenticated: true, access_token: 'm' }) });
    if (url.includes('/features/')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ catalog_version: 1, items: [], preferences: { pinned: [], recent: [], version: 1 } }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });
  await page.goto('http://localhost:5176/research?symbol=AAPL&market=US', { waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav:', e.message.slice(0, 50)));
  await page.waitForTimeout(8000);
  console.log('BODY:', JSON.stringify((await page.evaluate(() => document.body.innerText.slice(0, 100))) || 'EMPTY'));
  await browser.close();
})().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
