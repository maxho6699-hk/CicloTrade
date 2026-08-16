/* research 页截图（复用验证过的完整 bootstrap mock） */
const { chromium } = require('playwright');

const bootstrap = {
  me: { id: 1, email: 'qa@ciclotrade.test', display_name: 'QA 验收', admin_role: 'user', verified: true },
  membership: {
    auto_renewal: false,
    capabilities: ['research', 'paper', 'portfolio', 'ai'],
    plans: [], legacy_plans: [],
    policy: { key: null, version: null, sha256: null }, orders: [],
    payment_methods: { fps: { available: false, has_text: false, has_qr: false }, alipay: { available: false, has_text: false, has_qr: false }, wechat: { available: false, has_text: false, has_qr: false } },
    brokerage: { auto_control_account_limit: 1, accounts_used: 0, accounts: [], capability_catalog: [], requires_user_authorization: true, short_eligibility_source: 'broker', subscription_auto_connects_broker: false, us_short: { requires_ciclotrade_manual_approval: false, requires_broker_authorization: true, requires_margin: true, requires_borrowability: true } },
  },
  execution_control: { auto_trading_enabled: false, pause_reason: 'mock', block_reasons: [] },
  telegram: { bound: false, verified: false, consented: false, chat_id_masked: '', events: {} },
  portfolio: {
    account_mode: 'official', scope: 'system',
    positions: [], orders: [],
    accounts: { US: { equity: 10000, cash: 10000 }, CN: { equity: 0, cash: 0 }, HK: { equity: 0, cash: 0 } },
    fresh_marks: false, mark_source: 'mock',
    activity: { executions: [], intervals: [], updated_at: new Date().toISOString() },
  },
  recommendations: {
    items: [
      { event_id: 1, symbol: 'AAPL', market: 'US', instrument_type: 'stock', current_price: 232.5, reference_price: 228.1, state: 'active', action: '研究', contract_status: 'complete', currency: 'USD', available_at: new Date().toISOString(), occurred_at: new Date().toISOString(), quote_at: new Date().toISOString() },
      { event_id: 2, symbol: 'TSLA', market: 'US', instrument_type: 'stock', current_price: 285.3, reference_price: 290.0, state: 'active', action: '风险复核', contract_status: 'complete', currency: 'USD', available_at: new Date().toISOString(), occurred_at: new Date().toISOString(), quote_at: new Date().toISOString() },
    ],
    source: 'mock', fresh_marks: false, delivery: { stock: 60, option: 0 },
  },
  performance: { items: [], fresh_marks: false, mark_source: 'mock' },
  settings: {
    risk: {}, telegram_events: {},
    watchlists: { us: ['AAPL', 'TSLA'], a_share: ['BABA'] },
    watchlist_pins: { us: [], a_share: [] }, ui_locale: 'zh-Hant',
  },
  alerts: { entries: [], items: [], version: 1 },
  user: { plan: 'advanced', locale: 'zh-Hant', theme: 'dark' },
  market_data: { status: 'delayed', freshness: '延迟15分钟', delay_minutes: 15, updated_at: new Date().toISOString() },
  feature_catalog: {
    items: [
      { key: 'notifications', route: '/notifications', icon: 'bell', availability: 'available', primaryNav: false, pinAllowed: true, placements: ['secondary_nav'], title: { 'zh-Hant': '通知', 'zh-Hans': '通知' }, description: { 'zh-Hant': '通知中心', 'zh-Hans': '通知中心' } },
      { key: 'account', route: '/account', icon: 'user', availability: 'available', primaryNav: false, pinAllowed: true, placements: ['secondary_nav'], title: { 'zh-Hant': '帳戶', 'zh-Hans': '账户' }, description: { 'zh-Hant': '帳戶設定', 'zh-Hans': '账户设置' } },
    ],
    preferences: { pinned: [], version: 1 },
  },
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  const apiCalls = [];
  page.on('request', r => { const u = r.url(); if (u.includes('/api/')) apiCalls.push(u.replace('http://localhost:5175', '').split('?')[0]); });
  const allReqs = [];
  page.on('request', r => allReqs.push(r.url().replace('http://localhost:5175', '').split('?')[0]));
  page.on('response', r => { if (r.status() >= 400) console.log('HTTP', r.status(), r.url().replace('http://localhost:5175', '').slice(0, 60)); });
  page.on('pageerror', e => errors.push('PAGEERR: ' + e.message.slice(0, 180) + ' | ' + (e.stack || '').split('\n').slice(1, 3).join(' ').slice(0, 200)));

  await page.route('**/api/**', route => {
    const url = route.request().url();
    if (url.includes('/bootstrap')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bootstrap) });
    if (url.includes('/session')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ authenticated: true, access_token: 'mock' }) });
    if (url.includes('/features/catalog') || url.includes('/features/preferences') || url.includes('/features/recent')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ catalog_version: 1, items: [], preferences: { pinned: [], recent: [], version: 1 } }) });
    }
    if (url.includes('/market/candles')) {
      const items = Array.from({ length: 60 }, (_, i) => {
        const base = 200 + Math.sin(i / 5) * 15 + i * 0.3;
        const d = new Date(Date.now() - (59 - i) * 86400000);
        return { time: d.toISOString(), open: base, high: base + 4, low: base - 4, close: base + 1, volume: 1000000 + i * 8000 };
      });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        symbol: 'AAPL', timeframe: '日线', items,
        status: { display_source: 'Yahoo', delivery_delay_minutes: 15, freshness: '延遲15分鐘' }
      }) });
    }
    if (url.includes('/market/quote')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  await page.goto('http://localhost:5175/research?symbol=AAPL&market=US&timeframe=日线', { waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav:', e.message.slice(0, 50)));
  await page.waitForTimeout(4000);

  const info = await page.evaluate(() => {
    const shell = !!document.querySelector('.app-shell, nav, aside');
    const bodyLen = document.body.innerHTML.length;
    const rootHTML = document.getElementById('root')?.innerHTML.slice(0, 150) || 'NO_ROOT';
    const bodyHTML = document.body.innerHTML.slice(0, 200);
    const text = document.body.innerText.slice(0, 600);
    return { shell, bodyLen, rootHTML, bodyHTML, text };
  });
  console.log('INFO:', JSON.stringify(info));
  console.log('ERRORS:', JSON.stringify(errors.slice(0, 5)));
  console.log('ALLREQS:', JSON.stringify(allReqs.slice(0, 25)));

  await page.screenshot({ path: 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收/research-深色.png' });
  console.log('截图完成 research-深色');
  await browser.close();
})().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
