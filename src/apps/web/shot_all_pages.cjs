/* 批量截图：关键页面 */
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await ctx.newPage();
  const BASE = 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收';
  const pages = [
    ['/paper', '个人模拟'],
    ['/portfolio', '组合复盘'],
    ['/membership', '会员'],
    ['/promotion', '推广'],
    ['/earnings', '业绩预测'],
    ['/reports', '报告'],
    ['/lab', '实验室'],
    ['/more', '更多功能'],
    ['/notifications', '通知'],
    ['/trade', '券商'],
    ['/account', '账户'],
    ['/admin', '管理员'],
  ];
  for (const [path, name] of pages) {
    try {
      await page.goto('http://localhost:5180' + path, { waitUntil: 'networkidle', timeout: 45000 }).catch(e => console.log(name, 'nav:', e.message.slice(0, 40)));
      await page.waitForTimeout(2500);
      const bodyLen = await page.evaluate(() => document.body.innerHTML.length);
      await page.screenshot({ path: `${BASE}/${name}-新.png` });
      console.log(`✅ ${name} (${path}) bodyLen=${bodyLen}`);
    } catch (e) {
      console.log(`❌ ${name}: ${e.message.slice(0, 60)}`);
    }
  }
  await browser.close();
})().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
