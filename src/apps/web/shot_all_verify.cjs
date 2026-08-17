/* 全页面 fail-closed 截图：显式身份、负向鉴权、查询参数与唯一标题 */
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
    ['/', '首页', 'public', '今天做什麼，風險在哪裡。'],
    ['/login', '登录', 'guest', '登入，繼續掌握下一步市場機會'],
    ['/today', '今日', 'auth', '今天先處理什麼'],
    ['/discover', '发现', 'auth', '發現值得研究的股票'],
    ['/research?symbol=AAPL&market=US', '研究', 'auth', '週期切換'],
    ['/deliberation?market=US&symbol=TSLA', '牛熊', 'auth', '多空觀點對照'],
    ['/paper', '个人模拟', 'auth', '個人模擬交易'],
    ['/portfolio', '组合复盘', 'auth', '官方驗證模擬組合與覆盤'],
    ['/membership', '会员', 'auth', '會員與帳單'],
    ['/promotion', '推广', 'auth', '推廣中心'],
    ['/more', '更多功能', 'auth', '更多功能'],
    ['/notifications', '通知', 'auth', '訊息通知'],
    ['/account', '账户', 'auth', '個人中心'],
    ['/earnings', '业绩', 'auth', '業績預測'],
    ['/reports', '报告', 'auth', '報告中心'],
    ['/lab', '实验室', 'auth', '專業研究工作臺'],
    ['/trade', '券商', 'auth', '券商實盤連線'],
    ['/ai', '全局AI', 'auth', 'Ciclo AI 工作臺'],
    ['/workflow', 'Workflow', 'auth', 'Workflow 任務詳情'],
    ['/recommendations', '推荐', 'auth', '正股與期權研判'],
    ['/help', '帮助', 'auth', '幫助與支援'],
    ['/feedback', '反馈', 'auth', '反饋建議'],
    ['/mystic', '娱乐', 'auth', '玄學預測'],
    ['/legal', '法律', 'public', '法律政策與帳戶邊界'],
    ['/admin', '管理员', 'admin', '超級管理'],
  ];

  async function installApiContract(page, mode) {
    await page.route('**/api/**', async (route) => {
      const requestUrl = new URL(route.request().url());
      if (requestUrl.pathname === '/api/rewrite/v1/session/refresh') {
        const authenticated = mode === 'auth' || mode === 'admin';
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(authenticated ? { authenticated: true, access_token: 'x' } : { authenticated: false }),
        });
      }
      if (requestUrl.pathname === '/api/rewrite/v1/bootstrap' && (mode === 'auth' || mode === 'admin')) {
        const payload = JSON.parse(JSON.stringify(bootstrap));
        payload.me = { ...(payload.me || {}), admin_role: mode === 'admin' ? 'super_admin' : 'user' };
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) });
      }
      const proxiedUrl = `${MOCK_URL}${requestUrl.pathname}${requestUrl.search}`;
      const response = await route.fetch({ url: proxiedUrl });
      return route.fulfill({ response });
    });
  }

  async function pageState(page) {
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
      const brokenImages = [...document.images].filter((image) => visible(image) && (!image.complete || image.naturalWidth === 0)).length;
      const unnamedInteractives = elements.filter((element) => {
        if (!element.matches('button,a,input,select,textarea')) return false;
        const labelledBy = element.getAttribute('aria-labelledby');
        const labelledText = labelledBy ? labelledBy.split(/\s+/).map((id) => document.getElementById(id)?.textContent || '').join(' ') : '';
        const associatedLabel = element.id ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`)?.textContent || '' : '';
        const wrappedLabel = element.closest('label')?.textContent || '';
        const imageAlt = element.querySelector('img[alt]:not([alt=""])')?.getAttribute('alt') || '';
        const name = [element.getAttribute('aria-label'), labelledText, associatedLabel, wrappedLabel, element.textContent, element.getAttribute('title'), element.getAttribute('placeholder'), imageAlt].filter(Boolean).join(' ').trim();
        return !name;
      }).length;
      let brandAsset = null;
      const brand = [...document.images].find((image) => image.currentSrc.includes('/brand/ciclotrade-icon.png'));
      if (brand?.complete && brand.naturalWidth) {
        const canvas = document.createElement('canvas');
        canvas.width = brand.naturalWidth;
        canvas.height = brand.naturalHeight;
        const context = canvas.getContext('2d', { willReadFrequently: true });
        context.drawImage(brand, 0, 0);
        const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
        let visiblePixels = 0;
        let purplePixels = 0;
        for (let index = 0; index < pixels.length; index += 4) {
          if (pixels[index + 3] < 20) continue;
          visiblePixels += 1;
          const r = pixels[index] / 255;
          const g = pixels[index + 1] / 255;
          const b = pixels[index + 2] / 255;
          const max = Math.max(r, g, b);
          const min = Math.min(r, g, b);
          const delta = max - min;
          const saturation = max === 0 ? 0 : delta / max;
          let hue = 0;
          if (delta) {
            if (max === r) hue = 60 * (((g - b) / delta) % 6);
            else if (max === g) hue = 60 * ((b - r) / delta + 2);
            else hue = 60 * ((r - g) / delta + 4);
            if (hue < 0) hue += 360;
          }
          if (hue >= 228 && hue <= 349 && saturation >= 0.18 && max >= 0.16) purplePixels += 1;
        }
        brandAsset = { width: brand.naturalWidth, height: brand.naturalHeight, purpleRatio: visiblePixels ? purplePixels / visiblePixels : 1 };
      }
      return {
        finalPath: location.pathname,
        finalSearch: location.search,
        rootLen: document.querySelector('#root')?.innerHTML.length || 0,
        heading: document.querySelector('h1,h2')?.textContent?.trim()?.slice(0, 120) || '',
        scrollWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        fontViolations,
        brokenImages,
        unnamedInteractives,
        brandAsset,
      };
    });
  }

  function requiredQueryMatches(requested, actualSearch) {
    const expected = new URL(requested, 'http://local.test').searchParams;
    const actual = new URLSearchParams(actualSearch);
    return [...expected.entries()].every(([key, value]) => actual.get(key) === value);
  }

  let failures = 0;

  // Negative auth contract: guest cannot enter a protected page.
  {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    await installApiContract(page, 'guest');
    await page.goto(`${BASE_URL}/today`, { waitUntil: 'networkidle', timeout: 40000 });
    await page.waitForTimeout(700);
    const state = await pageState(page);
    const returnTo = new URLSearchParams(state.finalSearch).get('returnTo');
    if (state.finalPath !== '/login' || returnTo !== '/today' || state.heading !== '登入，繼續掌握下一步市場機會') {
      failures += 1;
      console.log(`❌ AUTH guest /today final=${state.finalPath}${state.finalSearch} heading=${state.heading}`);
    } else console.log('✅ AUTH guest /today → /login?returnTo=/today');
    await context.close();
  }

  // Negative admin contract: a normal authenticated user cannot enter /admin.
  {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    await installApiContract(page, 'auth');
    await page.goto(`${BASE_URL}/admin`, { waitUntil: 'networkidle', timeout: 40000 });
    await page.waitForTimeout(700);
    const state = await pageState(page);
    if (state.finalPath !== '/today' || state.heading !== '今天先處理什麼') {
      failures += 1;
      console.log(`❌ AUTH user /admin final=${state.finalPath} heading=${state.heading}`);
    } else console.log('✅ AUTH user /admin → /today');
    await context.close();
  }

  for (const [requested, name, mode, expectedHeading] of pages) {
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
    await installApiContract(page, mode);

    try {
      await page.goto(BASE_URL + requested, { waitUntil: 'networkidle', timeout: 40000 });
      await page.waitForTimeout(1600);
      const state = await pageState(page);
      const expectedPath = new URL(requested, 'http://local.test').pathname;
      const routeOk = state.finalPath === expectedPath;
      const queryOk = requiredQueryMatches(requested, state.finalSearch);
      const headingOk = state.heading === expectedHeading;
      const domOk = state.rootLen >= 1000;
      const runtimeOk = errors.length === 0;
      const overflowOk = state.scrollWidth <= state.viewportWidth + 1;
      const typographyOk = state.fontViolations === 0;
      const imagesOk = state.brokenImages === 0;
      const accessibilityOk = state.unnamedInteractives === 0;
      const validBrand = Boolean(state.brandAsset && state.brandAsset.width === 128 && state.brandAsset.height === 128 && state.brandAsset.purpleRatio < 0.01);
      const brandOk = name === '法律' ? !state.brandAsset || validBrand : validBrand;
      await page.screenshot({ path: path.join(OUTPUT_DIR, `全站-${name}.png`) });
      if (!routeOk || !queryOk || !headingOk || !domOk || !runtimeOk || !overflowOk || !typographyOk || !imagesOk || !accessibilityOk || !brandOk) {
        failures += 1;
        console.log(`❌ ${name} requested=${requested} final=${state.finalPath}${state.finalSearch} heading=${state.heading} root=${state.rootLen} width=${state.scrollWidth}/${state.viewportWidth} fonts=${state.fontViolations} images=${state.brokenImages} unnamed=${state.unnamedInteractives} brand=${JSON.stringify(state.brandAsset)} errors=${errors.slice(0, 2).join(' | ')}`);
      } else {
        console.log(`✅ ${name} final=${state.finalPath}${state.finalSearch} heading=${state.heading} root=${state.rootLen} width=${state.scrollWidth}/${state.viewportWidth} fonts=0 images=0 unnamed=0`);
      }
    } catch (error) {
      failures += 1;
      console.log(`❌ ${name}: ${error.message.slice(0, 180)}`);
    } finally {
      await context.close();
    }
  }

  await browser.close();
  if (failures) {
    console.error(`FAILURES=${failures}`);
    process.exit(1);
  }
  console.log(`PASS=${pages.length}/${pages.length} NEGATIVE_AUTH=2/2`);
})().catch((error) => {
  console.error('FATAL:', error.message);
  process.exit(1);
});
