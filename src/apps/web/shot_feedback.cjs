/* 反馈截图：发现页深色+浅色 + K线页 */
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await ctx.newPage();
  const BASE = 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收';

  // 发现页深色（完整页高）
  await page.goto('http://localhost:5180/discover', { waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('n1:', e.message.slice(0, 40)));
  await page.waitForTimeout(4000);
  await page.screenshot({ path: BASE + '/发现页-反馈-深色.png' });
  console.log('发现深色 OK');

  // 发现页浅色
  await page.evaluate(`localStorage.setItem('ciclotrade.theme', 'light')`);
  await page.reload({ waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('n2:', e.message.slice(0, 40)));
  await page.waitForTimeout(4000);
  await page.screenshot({ path: BASE + '/发现页-反馈-浅色.png' });
  console.log('发现浅色 OK');

  // K线页（研究页）
  await page.evaluate(`localStorage.setItem('ciclotrade.theme', 'dark')`);
  await page.goto('http://localhost:5180/research?symbol=AAPL&market=US', { waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('n3:', e.message.slice(0, 40)));
  await page.waitForTimeout(5000);
  await page.screenshot({ path: BASE + '/研究页-反馈-K线.png' });
  console.log('K线页 OK');

  await browser.close();
})().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
