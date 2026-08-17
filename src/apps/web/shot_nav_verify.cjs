/* 全站平板/手机 fail-closed 矩阵：显式身份、参数、标题、字体、图片、可访问名称与溢出 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const BASE_URL = process.env.CICLO_PREVIEW_URL || 'http://localhost:5175';
  const MOCK_URL = process.env.CICLO_MOCK_URL || 'http://localhost:5180';
  const OUTPUT_DIR = process.env.CICLO_SCREENSHOT_DIR || path.resolve(process.cwd(), 'artifacts', 'visual');
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  const bootstrap = await fetch(`${MOCK_URL}/api/rewrite/v1/bootstrap`).then((response) => {
    if (!response.ok) throw new Error(`bootstrap ${response.status}`);
    return response.json();
  });

  const pages = [
    ['/', '首页', 'public', '今天做什麼，風險在哪裡。'], ['/login', '登录', 'guest', '登入，繼續掌握下一步市場機會'],
    ['/today', '今日', 'auth', '今天先處理什麼'], ['/discover', '发现', 'auth', '發現值得研究的股票'],
    ['/research?symbol=AAPL&market=US', '研究', 'auth', '週期切換'], ['/deliberation?market=US&symbol=TSLA', '牛熊', 'auth', '多空觀點對照'],
    ['/paper', '个人模拟', 'auth', '個人模擬交易'], ['/portfolio', '组合复盘', 'auth', '官方驗證模擬組合與覆盤'],
    ['/membership', '会员', 'auth', '會員與帳單'], ['/promotion', '推广', 'auth', '推廣中心'], ['/more', '更多功能', 'auth', '更多功能'],
    ['/notifications', '通知', 'auth', '訊息通知'], ['/account', '账户', 'auth', '個人中心'], ['/earnings', '业绩', 'auth', '業績預測'],
    ['/reports', '报告', 'auth', '報告中心'], ['/lab', '实验室', 'auth', '專業研究工作臺'], ['/trade', '券商', 'auth', '券商實盤連線'],
    ['/ai', '全局AI', 'auth', 'Ciclo AI 工作臺'], ['/workflow', 'Workflow', 'auth', 'Workflow 任務詳情'],
    ['/recommendations', '推荐', 'auth', '正股與期權研判'], ['/help', '帮助', 'auth', '幫助與支援'], ['/feedback', '反馈', 'auth', '反饋建議'],
    ['/mystic', '娱乐', 'auth', '玄學預測'], ['/legal', '法律', 'public', '法律政策與帳戶邊界'], ['/admin', '管理员', 'admin', '超級管理'],
  ];
  const viewports = [['平板', { width: 1024, height: 768 }], ['手机', { width: 390, height: 844 }]];

  async function installApiContract(page, mode) {
    await page.route('**/api/**', async (route) => {
      const requestUrl = new URL(route.request().url());
      if (requestUrl.pathname === '/api/rewrite/v1/session/refresh') {
        const authenticated = mode === 'auth' || mode === 'admin';
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(authenticated ? { authenticated: true, access_token: 'x' } : { authenticated: false }) });
      }
      if (requestUrl.pathname === '/api/rewrite/v1/bootstrap' && (mode === 'auth' || mode === 'admin')) {
        const payload = JSON.parse(JSON.stringify(bootstrap));
        payload.me = { ...(payload.me || {}), admin_role: mode === 'admin' ? 'super_admin' : 'user' };
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) });
      }
      const response = await route.fetch({ url: `${MOCK_URL}${requestUrl.pathname}${requestUrl.search}` });
      return route.fulfill({ response });
    });
  }

  function requiredQueryMatches(requested, actualSearch) {
    const expected = new URL(requested, 'http://local.test').searchParams;
    const actual = new URLSearchParams(actualSearch);
    return [...expected.entries()].every(([key, value]) => actual.get(key) === value);
  }

  async function audit(page) {
    return page.evaluate(() => {
      const visible = (element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > 0 && rect.width > 0 && rect.height > 0;
      };
      const elements = [...document.querySelectorAll('body *')].filter(visible);
      const textElements = elements.filter((element) => [...element.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim()) || ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(element.tagName));
      const fontViolations = textElements.filter((element) => {
        const size = Number.parseFloat(getComputedStyle(element).fontSize);
        const required = ['P', 'BUTTON', 'INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName) ? 13 : 11;
        return Number.isFinite(size) && size + 0.01 < required;
      }).length;
      const imageInViewport = (image) => {
        const rect = image.getBoundingClientRect();
        return rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth;
      };
      const brokenImages = [...document.images].filter((image) => visible(image) && imageInViewport(image) && (!image.complete || image.naturalWidth === 0)).length;
      const unnamedInteractives = elements.filter((element) => {
        if (!element.matches('button,a,input,select,textarea')) return false;
        const labelledBy = element.getAttribute('aria-labelledby');
        const labelledText = labelledBy ? labelledBy.split(/\s+/).map((id) => document.getElementById(id)?.textContent || '').join(' ') : '';
        const associatedLabel = element.id ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`)?.textContent || '' : '';
        const wrappedLabel = element.closest('label')?.textContent || '';
        const imageAlt = element.querySelector('img[alt]:not([alt=""])')?.getAttribute('alt') || '';
        return ![element.getAttribute('aria-label'), labelledText, associatedLabel, wrappedLabel, element.textContent, element.getAttribute('title'), element.getAttribute('placeholder'), imageAlt].filter(Boolean).join(' ').trim();
      }).length;
      return {
        finalPath: location.pathname,
        finalSearch: location.search,
        heading: document.querySelector('h1,h2')?.textContent?.trim()?.slice(0, 120) || '',
        rootLen: document.querySelector('#root')?.innerHTML.length || 0,
        scrollWidth: document.documentElement.scrollWidth,
        viewportWidth: innerWidth,
        fontViolations,
        brokenImages,
        unnamedInteractives,
      };
    });
  }

  let failures = 0;
  let completed = 0;
  for (const [viewportName, viewport] of viewports) {
    for (const [requested, name, mode, expectedHeading] of pages) {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', (error) => errors.push(error.message));
      page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
      await installApiContract(page, mode);
      try {
        await page.goto(BASE_URL + requested, { waitUntil: 'networkidle', timeout: 40000 });
        await page.waitForTimeout(1200);
        const state = await audit(page);
        const expectedPath = new URL(requested, 'http://local.test').pathname;
        const valid = state.finalPath === expectedPath
          && requiredQueryMatches(requested, state.finalSearch)
          && state.heading === expectedHeading
          && state.rootLen >= 1000
          && state.scrollWidth <= state.viewportWidth + 1
          && state.fontViolations === 0
          && state.brokenImages === 0
          && state.unnamedInteractives === 0
          && errors.length === 0;
        await page.screenshot({ path: path.join(OUTPUT_DIR, `三端-${name}-${viewportName}.png`) });
        completed += 1;
        if (!valid) {
          failures += 1;
          console.log(`❌ ${name}-${viewportName} final=${state.finalPath}${state.finalSearch} heading=${state.heading} root=${state.rootLen} width=${state.scrollWidth}/${state.viewportWidth} fonts=${state.fontViolations} images=${state.brokenImages} unnamed=${state.unnamedInteractives} errors=${errors.slice(0, 2).join(' | ')}`);
        } else console.log(`✅ ${name}-${viewportName} width=${state.scrollWidth}/${state.viewportWidth} fonts=0 images=0 unnamed=0`);
      } catch (error) {
        failures += 1;
        console.log(`❌ ${name}-${viewportName}: ${error.message.slice(0, 180)}`);
      } finally {
        await context.close();
      }
    }
  }
  await browser.close();
  if (failures) {
    console.error(`FAILURES=${failures} COMPLETED=${completed}/${pages.length * viewports.length}`);
    process.exit(1);
  }
  console.log(`PASS=${completed}/${pages.length * viewports.length}`);
})().catch((error) => {
  console.error('FATAL:', error.message);
  process.exit(1);
});
