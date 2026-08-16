/* 最终验收截图：修复后关键页面 */
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const BASE = 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收';
  const pages = [
    ['/today', '今日-修复后'],
    ['/discover', '发现-修复后'],
    ['/research?symbol=AAPL&market=US', '研究-修复后'],
    ['/paper', '模拟-修复后'],
    ['/membership', '会员-修复后'],
  ];
  for (const [path, name] of pages) {
    try {
      await page.goto('http://localhost:5180' + path, { waitUntil: 'networkidle', timeout: 45000 }).catch(e => console.log(name, 'nav:', e.message.slice(0, 40)));
      await page.waitForTimeout(3000);
      const bodyLen = await page.evaluate(() => document.body.innerHTML.length);
      await page.screenshot({ path: `${BASE}/${name}.png` });
      console.log(`✅ ${name} bodyLen=${bodyLen}`);
    } catch (e) { console.log(`❌ ${name}: ${e.message.slice(0, 60)}`); }
  }
  await browser.close();
})().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
