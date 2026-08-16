/* 牛熊审议完整数据截图 v2：readiness 和 result 分开 mock */
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const now = new Date().toISOString();

  const bootstrap = {
    me: { id: 1, email: 'qa@test', display_name: 'QA', admin_role: 'user', verified: true, plan: '专业版' },
    membership: { auto_renewal: false, capabilities: ['research'], plans: [], legacy_plans: [], policy: { key: null, version: null, sha256: null }, orders: [], payment_methods: { fps: { available: false, has_text: false, has_qr: false }, alipay: { available: false, has_text: false, has_qr: false }, wechat: { available: false, has_text: false, has_qr: false } }, brokerage: { auto_control_account_limit: 1, accounts_used: 0, accounts: [], capability_catalog: [], requires_user_authorization: true } },
    execution_control: { auto_trading_enabled: false, pause_reason: 'mock', block_reasons: [] },
    telegram: { bound: false, verified: false, consented: false, chat_id_masked: '', events: {} },
    portfolio: { account_mode: 'official', scope: 'system', positions: [], orders: [], accounts: { US: { equity: 10000, cash: 10000 }, CN: { equity: 0, cash: 0 }, HK: { equity: 0, cash: 0 } }, fresh_marks: false, mark_source: 'mock', activity: { executions: [], updated_at: now } },
    recommendations: { items: [], source: 'mock', fresh_marks: false, delivery: { stock: 60, option: 0 } },
    performance: { items: [], fresh_marks: false, mark_source: 'mock' },
    settings: { risk: {}, telegram_events: {}, watchlists: { us: [], a_share: [] }, watchlist_pins: { us: [], a_share: [] }, ui_locale: 'zh-Hant' },
    alerts: { items: [] },
    market_data: { display_source: 'mock', is_realtime: false, freshness: 'delayed', detail: '延迟行情', delivery_visibility: 'public', observed_at: now },
    mode: 'compatibility',
  };

  const readiness = {
    market: 'US', symbol: 'TSLA', timeframe: '6M',
    question: '资料审阅',
    source_event_id: 'evt-mock-001', source_event_version: 1, source_event_sha256: 'c'.repeat(64),
    ready: true, status: 'succeeded', missing: [],
  };

  const seat = (seatKey, support, counter, summary, status = 'ready') => ({
    seat: seatKey,
    status,
    support_strength: support,
    counter_evidence_strength: counter,
    weight_bps: 2500,
    contribution: { support, counter },
    coverage: 0.8,
    source: null,
    citation: null,
    missing: [],
    invalidated_reason: null,
  });

  const result = {
    market: 'US', symbol: 'TSLA', timeframe: '6M',
    question: '资料审阅',
    source_event_id: 'evt-mock-001', source_event_version: 1, source_event_sha256: 'c'.repeat(64),
    deliberation_public_id: 'dlb-mock-001',
    task_public_id: 'task-mock-001',
    status: 'succeeded',
    method_version: 'v1',
    evidence_version: 'ev-v1',
    research_version: 'rs-v1',
    support_strength: 76,
    counter_evidence_strength: 64,
    coverage: 0.82,
    missing: [],
    seats: {
      market_structure: seat('market_structure', 8, 3, '趋势健康，资金面偏多，量能配合良好'),
      fundamentals: seat('fundamentals', 7, 4, '营收增长稳定，估值处于合理区间'),
      news_macro: seat('news_macro', 6, 5, '行业政策持续利好，但宏观利率仍有压力'),
      risk: seat('risk', 4, 7, '仓位集中度偏高，波动风险需要关注', 'missing'),
    },
    observed_at: now, available_at: now, as_of: now, calculated_at: now,
    invalidated_reason: null,
    evidence_snapshot_sha256: 'a'.repeat(64),
    result_sha256: 'b'.repeat(64),
  };

  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    if (url.includes('/bootstrap')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bootstrap) });
    if (url.includes('/session')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ authenticated: true, access_token: 'mock' }) });
    if (url.includes('/deliberations/readiness')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(readiness) });
    if (url.includes('/deliberations')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(result) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  const url = 'http://localhost:5175/deliberation?market=US&symbol=TSLA&timeframe=6M&question=%E8%B5%84%E6%96%99%E5%AE%A1%E9%98%85&source_event_id=evt-mock-001&source_event_version=1&source_event_sha256=' + 'c'.repeat(64) + '&deliberation_id=dlb-mock-001';
  await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav:', e.message.slice(0, 50)));
  await page.waitForTimeout(6000);
  const info = await page.evaluate(() => ({
    pkScore: document.querySelector('.deliberation-pk-score strong')?.textContent || '无',
    analysisRows: document.querySelectorAll('.deliberation-analysis tbody tr').length,
    hasRobot: !!document.querySelector('.ciclo-core'),
    hasVS: !!document.querySelector('.deliberation-vs, .deliberation-pk-vs'),
  }));
  console.log('INFO:', JSON.stringify(info));
  await page.screenshot({ path: 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收/牛熊审议-完整版.png' });
  console.log('截图完成');
  await browser.close();
})().catch(e => { console.error('ERR:', e.message); process.exit(1); });
