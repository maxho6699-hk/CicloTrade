/* 全页面自检截图 */
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const BASE = 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收';
  const pages = [
    ['/', '首页'],
    ['/login', '登录'],
    ['/today', '今日'],
    ['/discover', '发现'],
    ['/research?symbol=AAPL&market=US', '研究'],
    ['/deliberation?market=US&symbol=TSLA', '牛熊'],
    ['/paper', '个人模拟'],
    ['/portfolio', '组合复盘'],
    ['/membership', '会员'],
    ['/promotion', '推广'],
    ['/more', '更多功能'],
    ['/notifications', '通知'],
    ['/account', '账户'],
    ['/earnings', '业绩'],
    ['/reports', '报告'],
    ['/lab', '实验室'],
    ['/trade', '券商'],
    ['/ai', '全局AI'],
    ['/workflow', 'Workflow'],
    ['/recommendations', '推荐'],
    ['/help', '帮助'],
    ['/feedback', '反馈'],
    ['/mystic', '娱乐'],
    ['/legal', '法律'],
    ['/admin', '管理员'],
  ];
  for (const [path, name] of pages) {
    try {
      await page.goto('http://localhost:5180' + path, { waitUntil: 'networkidle', timeout: 40000 }).catch(e => console.log(name, 'nav:', e.message.slice(0, 30)));
      await page.waitForTimeout(2200);
      const bodyLen = await page.evaluate(() => document.body.innerHTML.length);
      await page.screenshot({ path: `${BASE}/全站-${name}.png` });
      console.log(`✅ ${name} len=${bodyLen}`);
    } catch (e) { console.log(`❌ ${name}: ${e.message.slice(0, 40)}`); }
  }
  await browser.close();
})().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
