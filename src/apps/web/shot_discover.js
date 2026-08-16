const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  
  // 深色（默认）
  await page.goto('http://localhost:5175/discover', { waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav1:', e.message));
  await page.waitForTimeout(4000);
  await page.screenshot({ path: 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收/发现页-新实现-深色.png' });
  console.log('深色截图完成');
  
  // 浅色
  await page.evaluate(`localStorage.setItem('ciclotrade.theme', 'light')`);
  await page.reload({ waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav2:', e.message));
  await page.waitForTimeout(4000);
  await page.screenshot({ path: 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收/发现页-新实现-浅色.png' });
  console.log('浅色截图完成');
  
  await browser.close();
})().catch(e => { console.error('ERR:', e.message); process.exit(1); });
