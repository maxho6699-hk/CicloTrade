/* 免登录交互预览代理：转发 5175 preview + 注入 mock 登录态 */
const http = require('http');

const UPSTREAM = { host: 'localhost', port: 5175 };
const PORT = 5180;

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
  execution_control: { auto_trading_enabled: false, pause_reason: null, block_reasons: [] },
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
  market_data: { status: 'delayed', freshness: '延遲15分鐘', delay_minutes: 15, updated_at: new Date().toISOString() },
};

function candlesFor(symbol) {
  const items = Array.from({ length: 80 }, (_, i) => {
    const seed = symbol.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
    const base = 100 + ((seed * 7 + i * 13) % 200) + Math.sin(i / 6 + seed) * 12;
    return {
      time: new Date(Date.now() - (79 - i) * 86400000).toISOString(),
      open: base, high: base + 5, low: base - 5, close: base + (i % 3 === 0 ? -2 : 2), volume: 500000 + ((seed * i) % 3000000),
    };
  });
  return items;
}

function quoteFor(symbol) {
  const seed = symbol.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  const last = 100 + ((seed * 11) % 300);
  return {
    symbol, last, bid: last - 0.15, ask: last + 0.15, spread: 0.3,
    open: last - 3, high: last + 5, low: last - 5, prev_close: last - 2, volume: 12000000,
    status: 'available', freshness: '延遲15分鐘', delivery_delay_minutes: 15,
    data_time: new Date().toISOString(), source: '真实数据来源', verification: 'verified',
    is_realtime: false, actionable_quote: false, configuration_allows_realtime: false,
    contract_code: 'mock', contract: 'mock', request_succeeded: true,
    quote_at: new Date().toISOString(), display_source: 'Yahoo',
  };
}

const server = http.createServer((req, res) => {
  const url = req.url || '/';
  // mock API
  if (url.includes('/api/')) {
    let body;
    let status = 200;
    if (url.includes('/bootstrap')) {
      body = JSON.stringify(bootstrap);
    } else if (url.includes('/session')) {
      body = JSON.stringify({ authenticated: true, access_token: 'mock' });
    } else if (url.includes('/features/')) {
      // 合法目录：6 个主导航 + 次级工具（满足 decodeFeatureCatalog 检查）
      const t = (k, hant, hans) => ({ 'zh-Hant': hant, 'zh-Hans': hans });
      const items = [
        { key: 'today', route: '/today', routes: ['/today'], icon: 'dashboard', category: 'core', availability: 'available', access: 'full', data_state: 'live', health: 'healthy', pin_allowed: false, primary_nav: true, placements: ['primary_nav'], sort_order: 0, title_key: 'today', description_key: 'today' },
        { key: 'discover', route: '/discover', routes: ['/discover'], icon: 'search', category: 'core', availability: 'available', access: 'full', data_state: 'live', health: 'healthy', pin_allowed: false, primary_nav: true, placements: ['primary_nav'], sort_order: 1, title_key: 'discover', description_key: 'discover' },
        { key: 'research', route: '/research', routes: ['/research'], icon: 'chart', category: 'core', availability: 'available', access: 'full', data_state: 'live', health: 'healthy', pin_allowed: false, primary_nav: true, placements: ['primary_nav'], sort_order: 2, title_key: 'research', description_key: 'research' },
        { key: 'paper', route: '/paper', routes: ['/paper'], icon: 'wallet', category: 'core', availability: 'available', access: 'full', data_state: 'live', health: 'healthy', pin_allowed: false, primary_nav: true, placements: ['primary_nav'], sort_order: 3, title_key: 'paper', description_key: 'paper' },
        { key: 'portfolio', route: '/portfolio', routes: ['/portfolio'], icon: 'pie', category: 'core', availability: 'available', access: 'full', data_state: 'live', health: 'healthy', pin_allowed: false, primary_nav: true, placements: ['primary_nav'], sort_order: 4, title_key: 'portfolio', description_key: 'portfolio' },
        { key: 'more', route: '/more', routes: ['/more'], icon: 'grid', category: 'core', availability: 'available', access: 'full', data_state: 'live', health: 'healthy', pin_allowed: false, primary_nav: true, placements: ['primary_nav'], sort_order: 5, title_key: 'more', description_key: 'more' },
        { key: 'notifications', route: '/notifications', routes: ['/notifications'], icon: 'bell', category: 'service', availability: 'available', access: 'full', data_state: 'live', health: 'healthy', pin_allowed: true, primary_nav: false, placements: ['secondary_nav'], sort_order: 10, title_key: 'notifications', description_key: 'notifications' },
        { key: 'account', route: '/account', routes: ['/account'], icon: 'user', category: 'service', availability: 'available', access: 'full', data_state: 'live', health: 'healthy', pin_allowed: true, primary_nav: false, placements: ['secondary_nav'], sort_order: 11, title_key: 'account', description_key: 'account' },
      ];
      body = JSON.stringify({ catalog_version: 1, items, preferences: { pinned: ['notifications', 'account'], recent: [], version: 1 } });
    } else if (url.includes('/market/candles')) {
      const m = url.match(/symbol=([^&]+)/);
      const symbol = m ? decodeURIComponent(m[1]) : 'AAPL';
      body = JSON.stringify({ symbol, timeframe: '日线', items: candlesFor(symbol), status: { display_source: 'Yahoo', delivery_delay_minutes: 15, freshness: '延遲15分鐘' } });
    } else if (url.includes('/market/quote')) {
      const m = url.match(/symbol=([^&]+)/);
      const symbol = m ? decodeURIComponent(m[1]) : 'AAPL';
      body = JSON.stringify(quoteFor(symbol));
    } else if (url.includes('/alerts')) {
      body = JSON.stringify({ entries: [], items: [], version: 1 });
    } else {
      body = '{}';
    }
    res.writeHead(status, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
    return res.end(body);
  }
  // 静态资源 → 转发到 5175（重写 Host）
  const headers = { ...req.headers, host: `127.0.0.1:${UPSTREAM.port}` };
  const proxyReq = http.request({ host: UPSTREAM.host, port: UPSTREAM.port, path: url, method: req.method, headers }, (proxyRes) => {
    res.writeHead(proxyRes.statusCode || 200, proxyRes.headers);
    proxyRes.pipe(res);
  });
  proxyReq.on('error', (e) => {
    res.writeHead(502, { 'Content-Type': 'text/plain' });
    res.end('上游 5175 未启动: ' + e.message);
  });
  req.pipe(proxyReq);
});

server.listen(PORT, () => {
  console.log(`✅ 免登录预览代理: http://localhost:${PORT}`);
  console.log(`   研究页: http://localhost:${PORT}/research?symbol=AAPL&market=US`);
  console.log(`   今日页: http://localhost:${PORT}/today`);
  console.log(`   发现页: http://localhost:${PORT}/discover`);
  console.log(`   牛熊页: http://localhost:${PORT}/deliberation?market=US&symbol=TSLA`);
});
