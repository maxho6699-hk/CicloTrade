/* 免登录交互预览代理：转发 5175 preview + 注入 mock 登录态 */
const http = require('http');

const UPSTREAM = { host: 'localhost', port: 5175 };
const PORT = 5180;
const previewNow = new Date().toISOString();
const stockIdea = (event_id, symbol, market, currency, action, current_price, target_price, stop_price, rationale) => ({
  event_id, symbol, market, currency, instrument_type: 'stock', state: 'official', action,
  current_price, reference_price: current_price, target_price, stop_price,
  max_loss: Math.abs(current_price - stop_price) * 10, quantity_hint: 10,
  rationale, strategy_name: '多因子研究筛选', strategy_version: 'research-v5', actionable: true,
  contract_status: 'complete', missing_fields: [], available_at: previewNow, occurred_at: previewNow, quote_at: previewNow,
});
const optionIdea = (event_id, symbol, action, option_right, option_strike, option_expiry, bid, ask, target_price, rationale) => ({
  event_id, symbol, market: 'US', currency: 'USD', instrument_type: 'option', state: 'official', action,
  current_price: (bid + ask) / 2, reference_price: (bid + ask) / 2, target_price, stop_price: null,
  max_loss: ask * 100, quantity_hint: 1, option_right, option_strike, option_expiry, multiplier: 100,
  bid, ask, spread: ask - bid, implied_volatility: .34, volume: 1840, open_interest: 12600,
  rationale, strategy_name: '定义风险期权研究', strategy_version: 'options-v5', actionable: true,
  contract_status: 'complete', missing_fields: [], available_at: previewNow, occurred_at: previewNow, quote_at: previewNow,
});

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
      stockIdea(1, 'AAPL', 'US', 'USD', 'BUY', 232.5, 252, 221, '服务收入与现金流保持韧性，等待价格与证据同时满足后再进入个人模拟。'),
      stockIdea(2, 'NVDA', 'US', 'USD', 'BUY', 176.2, 194, 165, '数据中心需求仍强，但估值和波动较高，需要严格遵守止损与仓位边界。'),
      stockIdea(3, 'MSFT', 'US', 'USD', 'BUY', 521.4, 558, 498, '云业务与企业软件现金流提供支撑，需继续核验资本开支和增长兑现。'),
      stockIdea(4, 'AMZN', 'US', 'USD', 'BUY', 226.8, 246, 214, '零售利润率改善与云业务复苏构成支持，重点观察消费和费用变化。'),
      stockIdea(5, 'GOOGL', 'US', 'USD', 'BUY', 203.4, 220, 192, '广告与云业务提供双重支撑，反向证据是搜索竞争与监管风险。'),
      stockIdea(6, 'META', 'US', 'USD', 'BUY', 782.1, 842, 744, '广告效率与用户活跃度提供支持，仍需复核高资本开支带来的估值压力。'),
      stockIdea(7, 'AVGO', 'US', 'USD', 'BUY', 342.7, 371, 326, '定制芯片与软件收入组合改善可见度，短期波动仍可能扩大。'),
      stockIdea(8, 'JPM', 'US', 'USD', 'BUY', 301.6, 322, 287, '资产质量与资本回报稳定，需关注利率路径和信贷成本变化。'),
      stockIdea(9, 'TSLA', 'US', 'USD', 'SHORT', 335.3, 296, 354, '交付与价格竞争构成压力；做空风险无限，必须先核验借券和跳空风险。'),
      stockIdea(10, 'ADBE', 'US', 'USD', 'SHORT', 352.4, 318, 371, '增长预期与竞争压力需要复核，做空仅作为研究方向，不代表可执行订单。'),
      stockIdea(11, 'NFLX', 'US', 'USD', 'REDUCE', 1260, 1260, 1195, '价格已接近研究目标，等待新证据与更清晰的风险回报再决定。'),
      stockIdea(12, 'COST', 'US', 'USD', 'EXIT', 972.5, 972.5, 934, '估值与目标空间暂不匹配，当前归入等机会而非追价。'),
      stockIdea(13, '600519', 'CN', 'CNY', 'BUY', 1488, 1580, 1425, '品牌与现金流稳定，但需求和渠道库存仍需持续核验。'),
      stockIdea(14, '300750', 'CN', 'CNY', 'BUY', 298.6, 326, 282, '产业链地位提供支持，重点观察价格竞争与海外政策风险。'),
      stockIdea(15, '601318', 'CN', 'CNY', 'REDUCE', 62.8, 62.8, 59.4, 'A股不支持做空；当前仅等待更多证据，不生成任何做空执行入口。'),
      stockIdea(16, '000001', 'CN', 'CNY', 'REDUCE', 12.4, 12.4, 11.8, '息差与资产质量证据尚不充分，等待下一轮正式数据。'),
      optionIdea(17, 'AAPL', 'BUY', 'CALL', 240, '2026-10-16', 8.4, 8.8, 12.6, '以有限权利金研究上行弹性，需同时承担时间衰减与波动率回落风险。'),
      optionIdea(18, 'NVDA', 'BUY', 'CALL', 185, '2026-09-18', 9.1, 9.6, 14.2, 'Call 只用于定义风险研究，目标收益基于合约价格而非标的涨幅。'),
      optionIdea(19, 'TSLA', 'SHORT', 'PUT', 320, '2026-10-16', 18.2, 18.9, 27.5, 'Put 用于研究下行风险，权利金可能全部损失且受隐含波动率影响。'),
      optionIdea(20, 'META', 'REDUCE', 'CALL', 800, '2026-12-18', 32.1, 33.4, 33.4, '当前目标空间不足，保留完整合约字段并归入等机会。'),
    ],
    source: '验收预览数据', fresh_marks: false, delivery: { stock: 16, option: 4 },
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
      time: new Date(Date.now() - (79 - i) * 86400000).toISOString().slice(0, 10),
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
  if (url.includes('/api/') && !url.includes('/src/api/')) {
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
