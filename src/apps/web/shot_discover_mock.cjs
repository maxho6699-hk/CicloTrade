const { chromium } = require('playwright');

// Mock bootstrap 响应让前端认为已认证
const bootstrapMock = {
  user: { id: 1, email: 'qa@ciclotrade.test', admin_role: 'user' },
  membership: { tier: 'premium', status: 'active' },
  market_data: { freshness: 'delayed', observed_at: new Date().toISOString(), detail: '延迟行情' },
  recommendations: { items: [] },
  mode: 'authenticated',
};

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });

  // 拦截所有 /api/ 请求
  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    if (url.includes('/session')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ authenticated: true, access_token: 'mock' }) });
    }
    if (url.includes('/bootstrap')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bootstrapMock) });
    }
    if (url.includes('/recommendations')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
    }
    // 其他 API 返回空数据
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  // 深色（默认）
  await page.goto('http://localhost:5175/discover', { waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav1:', e.message));
  await page.waitForTimeout(5000);
  await page.screenshot({ path: 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收/发现页-新实现-深色.png' });
  console.log('深色截图完成, URL:', page.url());

  // 浅色
  await page.evaluate(`localStorage.setItem('ciclotrade.theme', 'light')`);
  await page.reload({ waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav2:', e.message));
  await page.waitForTimeout(5000);
  await page.screenshot({ path: 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收/发现页-新实现-浅色.png' });
  console.log('浅色截图完成, URL:', page.url());

  await browser.close();
})().catch(e => { console.error('ERR:', e.message); process.exit(1); });
