/* 截图今日总览 + 牛熊审议（深浅双主题） */
const { chromium } = require('playwright');

const bootstrap = {
  me: { id: 1, email: 'qa@ciclotrade.test', display_name: 'QA 验收', admin_role: 'user', verified: true },
  membership: {
    auto_renewal: false,
    capabilities: ['research', 'paper', 'portfolio', 'ai'],
    plans: [], legacy_plans: [],
    policy: { key: null, version: null, sha256: null }, orders: [],
    payment_methods: { fps: { available: false, has_text: false, has_qr: false }, alipay: { available: false, has_text: false, has_qr: false }, wechat: { available: false, has_text: false, has_qr: false } },
    brokerage: { auto_control_account_limit: 1, accounts_used: 0, accounts: [], capability_catalog: [], requires_user_authorization: true },
  },
  execution_control: { auto_trading_enabled: false, pause_reason: 'mock', block_reasons: [] },
  telegram: { bound: false, verified: false, consented: false, chat_id_masked: '', events: {} },
  portfolio: {
    account_mode: 'official', scope: 'system',
    positions: [], orders: [],
    accounts: { US: { equity: 10000, cash: 10000 }, CN: { equity: 0, cash: 0 }, HK: { equity: 0, cash: 0 } },
    fresh_marks: false, mark_source: 'mock',
    activity: { executions: [], updated_at: new Date().toISOString() },
  },
  recommendations: {
    items: [
      { event_id: 1, symbol: 'AAPL', market: 'US', instrument_type: 'stock', current_price: 232.5, reference_price: 228.1, state: 'active', action: '研究', contract_status: 'complete', currency: 'USD', available_at: new Date().toISOString(), occurred_at: new Date().toISOString(), quote_at: new Date().toISOString() },
      { event_id: 2, symbol: 'TSLA', market: 'US', instrument_type: 'stock', current_price: 285.3, reference_price: 290.0, state: 'active', action: '风险复核', contract_status: 'complete', currency: 'USD', available_at: new Date().toISOString(), occurred_at: new Date().toISOString(), quote_at: new Date().toISOString() },
      { event_id: 3, symbol: 'BABA', market: 'CN', instrument_type: 'stock', current_price: 88.2, reference_price: 86.5, state: 'waiting', action: '等待', contract_status: 'incomplete', currency: 'USD', available_at: new Date().toISOString(), occurred_at: new Date().toISOString(), quote_at: new Date().toISOString() },
    ],
    source: 'mock', fresh_marks: false, delivery: { stock: 60, option: 0 },
  },
  performance: { items: [], fresh_marks: false, mark_source: 'mock' },
  settings: {
    risk: {}, telegram_events: {},
    watchlists: { us: ['AAPL', 'TSLA'], a_share: ['BABA'] },
    watchlist_pins: { us: [], a_share: [] }, ui_locale: 'zh-Hant',
  },
  alerts: { items: [] },
  market_data: { display_source: 'mock', is_realtime: false, freshness: 'delayed', detail: '延迟行情', delivery_visibility: 'public', observed_at: new Date().toISOString() },
  mode: 'compatibility',
};

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });

  await context.route('**/api/**', async (route) => {
    const url = route.request().url();
    if (url.includes('/bootstrap')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bootstrap) });
    if (url.includes('/session')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ authenticated: true, access_token: 'mock' }) });
    if (url.includes('/deliberation')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ readiness: 'ready', seats: [] }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  const page = await context.newPage();
  const BASE = 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收';

  // 今日总览 - 深色
  await page.goto('http://localhost:5175/today', { waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav1:', e.message));
  await page.waitForTimeout(4000);
  await page.screenshot({ path: BASE + '/今日总览-新实现-深色.png' });
  console.log('今日-深色完成', page.url());

  // 今日总览 - 浅色
  await page.evaluate(`localStorage.setItem('ciclotrade.theme', 'light')`);
  await page.reload({ waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav2:', e.message));
  await page.waitForTimeout(4000);
  await page.screenshot({ path: BASE + '/今日总览-新实现-浅色.png' });
  console.log('今日-浅色完成');

  // 牛熊审议 - 深色
  await page.evaluate(`localStorage.setItem('ciclotrade.theme', 'dark')`);
  await page.goto('http://localhost:5175/deliberation', { waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav3:', e.message));
  await page.waitForTimeout(4000);
  await page.screenshot({ path: BASE + '/牛熊审议-新实现-深色.png' });
  console.log('牛熊-深色完成', page.url());

  // 牛熊审议 - 浅色
  await page.evaluate(`localStorage.setItem('ciclotrade.theme', 'light')`);
  await page.reload({ waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav4:', e.message));
  await page.waitForTimeout(4000);
  await page.screenshot({ path: BASE + '/牛熊审议-新实现-浅色.png' });
  console.log('牛熊-浅色完成');

  await browser.close();
  console.log('全部完成');
})().catch(e => { console.error('ERR:', e.message); process.exit(1); });
