/* dev server 源码级错误定位 */
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.on('pageerror', e => {
    console.log('PAGEERR:', e.message.slice(0, 150));
    console.log('STACK:', (e.stack || '').split('\n').slice(1, 6).join('\n'));
  });
  await page.goto('http://localhost:5180/research?symbol=AAPL&market=US', { waitUntil: 'networkidle', timeout: 60000 }).catch(e => console.log('nav:', e.message.slice(0, 50)));
  await page.waitForTimeout(8000);
  console.log('BODY:', (await page.evaluate(() => document.body.innerText.slice(0, 80))) || 'EMPTY');
  await browser.close();
})().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
