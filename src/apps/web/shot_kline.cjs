const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message.slice(0, 120)));
  await page.goto('http://localhost:5180/research?symbol=AAPL&market=US', { waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav:', e.message.slice(0, 50)));
  await page.waitForTimeout(4000);
  const info = await page.evaluate(() => ({ bodyLen: document.body.innerHTML.length, text: document.body.innerText.slice(0, 120) }));
  console.log('K线页:', JSON.stringify(info));
  console.log('错误:', JSON.stringify(errors));
  await page.screenshot({ path: 'C:/Users/maxho/Desktop/CicloTrade项目资料/10-视觉验收/样板验收/K线页-修复后.png' });
  await browser.close();
})().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
