/* 用 Playwright 复用线上会话测试数据接口 */
const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  // 使用持久化 profile（用户平时登录的 Chrome 数据目录可能无法直接复用，这里用 Playwright 独立登录）
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext();
  const page = await context.newPage();

  // 登录
  await page.goto('https://ciclotrade.com/login', { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(2000);
  console.log('登录页 URL:', page.url());

  // 填表
  try {
    await page.fill('input[type="email"], input[name="email"]', 'MaxHo6699@gmail.com');
    await page.fill('input[type="password"], input[name="password"]', 'Qwer0000..');
    await page.click('button[type="submit"], button:has-text("登录"), button:has-text("進入決策工作臺")');
    await page.waitForTimeout(6000);
    console.log('登录后 URL:', page.url());
  } catch (e) {
    console.log('填表失败:', e.message.slice(0, 150));
  }

  // 从 localStorage/sessionStorage 取 token
  const token = await page.evaluate(() => {
    try {
      return localStorage.getItem('access_token') || sessionStorage.getItem('access_token') || '';
    } catch (e) { return ''; }
  });
  console.log('token:', token ? token.slice(0, 30) + '...' : '无(尝试从响应获取)');

  // 测试数据接口（带 cookie 会话）
  const results = [];
  const paths = [
    ['market/status', '/api/rewrite/v1/market/status'],
    ['market/quote', '/api/rewrite/v1/market/quote?symbol=AAPL'],
    ['market/candles', '/api/rewrite/v1/market/candles?symbol=AAPL&timeframe=1d'],
    ['market/search', '/api/rewrite/v1/market/search?q=apple&market=US'],
    ['options/candles', '/api/rewrite/v1/options/candles?symbol=AAPL'],
    ['bootstrap', '/api/rewrite/v1/bootstrap'],
  ];
  for (const [name, path] of paths) {
    try {
      const resp = await page.evaluate(async (p) => {
        const r = await fetch(p, { credentials: 'include' });
        const text = await r.text();
        return { status: r.status, body: text.slice(0, 300) };
      }, path);
      results.push({ name, status: resp.status, ok: resp.status === 200, body: resp.body });
      console.log(`${name}: HTTP ${resp.status} ${resp.status === 200 ? 'OK' : 'FAIL'} ${resp.body.slice(0, 120)}`);
    } catch (e) {
      results.push({ name, error: e.message.slice(0, 100) });
      console.log(`${name}: ERR ${e.message.slice(0, 100)}`);
    }
  }

  fs.writeFileSync('C:/Users/maxho/AppData/Local/hermes/logs/api_test_results.json', JSON.stringify(results, null, 2));
  await browser.close();
})();
