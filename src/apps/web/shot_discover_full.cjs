const { chromium } = require('playwright');

// 最小但结构完整的 bootstrap（照 BootstrapPayload 接口）
const bootstrap = {
  me: { id: 1, email: 'qa@ciclotrade.test', display_name: 'QA 验收', admin_role: 'user', verified: true },
  membership: {
    auto_renewal: false,
    capabilities: ['research', 'paper', 'portfolio', 'ai'],
    plans: [],
    legacy_plans: [],
    policy: { key: null, version: null, sha256: null },
    orders: [],
    payment_methods: { fps: { available: false, has_text: false, has_qr: false }, alipay: { available: false, has_text: false, has_qr: false }, wechat: { available: false, has_text: false, has_qr: false } },
    brokerage: { auto_control_account_limit: 1, accounts_used: 0, accounts: [], capability_catalog: [], requires_user_authorization: true },
  },
  execution_control: { auto_trading_enabled: false, pause_reason: 'mock' },
  telegram: { bound: false, verified: false, consented: false, chat_id_masked: '', events: {} },
  portfolio: {
    account_mode: 'official',
    scope: 'ciclotrade_system_validation',
    positions: [],
    orders: [],
    accounts: { US: { equity: 10000, cash: 10000 }, CN: { equity: 0, cash: 0 }, HK: { equity: 0, cash: 0 } },
    fresh_marks: false,
    mark_source: 'mock',
  },
  recommendations: {
    items: [
      { event_id: 1, symbol: 'AAPL', market: 'US', instrument_type: 'stock', current_price: 232.5, reference_price: 228.1, state: 'active', action: '研究', contract_status: 'complete', currency: 'USD', available_at: new Date().toISOString(), occurred_at: new Date().toISOString(), quote_at: new Date().toISOString() },
      { event_id: 2, symbol: 'TSLA', market: 'US', instrument_type: 'stock', current_price: 285.3, reference_price: 290.0, state: 'active', action: '风险复核', contract_status: 'complete', currency: 'USD', available_at: new Date().toISOString(), occurred_at: new Date().toISOString(), quote_at: new Date().toISOString() },
      { event_id: 3, symbol: 'BABA', market: 'CN', instrument_type: 'stock', current_price: 88.2, reference_price: 86.5, state: 'waiting', action: '等待', contract_status: 'incomplete', currency: 'USD', available_at: new Date().toISOString(), occurred_at: new Date().toISOString(), quote_at: new Date().toISOString() },
    ],
    source: 'mock',
    fresh_marks: false,
    delivery: { stock: 60, option: 0 },
  },
  performance: { items: [], fresh_marks: false, mark_source: 'mock' },
  settings: {
    risk: {},
    telegram_events: {},
    watchlists: { us: ['AAPL', 'TSLA'], a_share: ['BABA'] },
    watchlist_pins: { us: [], a_share: [] },
    ui_locale: 'zh-Hant',
  },
  alerts: { items: [] },
  market_data: { display_source: 'mock', is_realtime: false, freshness: 'delayed', detail: '延迟行情', delivery_visibility: 'public', observed_at: new Date().toISOString() },
  mode: 'compatibility',
};

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', e => errors.push('PAGEERROR: ' + String(e).slice(0, 300)));
  page.on('console', m => { if (m.type() === 'error') errors.push('CONSOLE: ' + m.text().slice(0, 200)); });

  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    if (url.includes('/bootstrap')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bootstrap) });
    if (url.includes('/session')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ authenticated: true, access_token: 'mock' }) });
    if (url.includes('/mini-candles') || url.includes('/candles')) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  await page.goto('http://localhost:5175/discover', { waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav1:', e.message));
  await page.waitForTimeout(6000);
  console.log('深色 URL:', page.url());
  const root1 = await page.evaluate(() => { const r = document.getElementById('root'); return r ? { children: r.children.length, len: r.innerHTML.length, hasShell: !!document.querySelector('.app-shell'), hasDiscover: !!document.querySelector('.discover') } : null; });
  console.log('深色 ROOT:', JSON.stringify(root1));
  await page.screenshot({ path: 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收/发现页-新实现-深色.png' });
  console.log('深色截图完成');

  // 浅色
  await page.evaluate(`localStorage.setItem('ciclotrade.theme', 'light')`);
  await page.reload({ waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav2:', e.message));
  await page.waitForTimeout(6000);
  console.log('浅色 URL:', page.url());
  const root2 = await page.evaluate(() => { const r = document.getElementById('root'); return r ? { children: r.children.length, len: r.innerHTML.length, hasShell: !!document.querySelector('.app-shell') } : null; });
  console.log('浅色 ROOT:', JSON.stringify(root2));
  await page.screenshot({ path: 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收/发现页-新实现-浅色.png' });
  console.log('浅色截图完成');

  console.log('ERRORS:', errors.slice(0, 5));
  await browser.close();
})().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
