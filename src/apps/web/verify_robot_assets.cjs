const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')

const BASE = process.env.CICLO_PREVIEW_URL || 'http://localhost:5175'
const MOCK = process.env.CICLO_MOCK_URL || 'http://localhost:5180'
const OUT = process.env.CICLO_ROBOT_QA_DIR || path.resolve(process.cwd(), 'artifacts', 'robot-qa')
const tiers = [
  ['免费版', 'free', 'robot-lv1.png'],
  ['标准版', 'standard', 'robot-lv2.png'],
  ['高级版', 'advanced', 'robot-lv3.png'],
  ['专业版', 'professional', 'robot-lv4.png'],
]
function assert(value, message) { if (!value) throw new Error(message) }

async function installMockRoutes(page, bootstrap, plan) {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/rewrite/v1/session/refresh') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ authenticated: true, access_token: 'x' }) })
    if (url.pathname === '/api/rewrite/v1/bootstrap') {
      const payload = structuredClone(bootstrap)
      payload.me = { ...(payload.me || {}), plan }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) })
    }
    const response = await route.fetch({ url: `${MOCK}${url.pathname}${url.search}` })
    return route.fulfill({ response })
  })
}

function readStackingState(root) {
  const core = root.querySelector('.ciclo-core')
  const image = core?.querySelector('.ciclo-core-hero-image')
  const frame = core?.querySelector('.ciclo-core-image-frame')
  const orbits = core?.querySelector('.ciclo-core-image-orbits')
  const particles = core?.querySelector('.ciclo-core-image-particles')
  const energy = core?.querySelector('.ciclo-core-energy-field')
  const numberValue = (element) => element ? Number.parseInt(getComputedStyle(element).zIndex || '0', 10) : null
  return {
    tier: core?.getAttribute('data-tier') || '',
    src: image?.getAttribute('src') || '',
    frameZ: numberValue(frame),
    orbitsZ: numberValue(orbits),
    particlesZ: numberValue(particles),
    energyDisplay: energy ? getComputedStyle(energy).display : null,
  }
}

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  const bootstrap = await fetch(`${MOCK}/api/rewrite/v1/bootstrap`).then((response) => response.json())
  const browser = await chromium.launch({ headless: true })
  let passed = 0
  for (const [plan, tier, file] of tiers) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', (error) => errors.push(error.message))
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
    await installMockRoutes(page, bootstrap, plan)
    await page.goto(`${BASE}/account`, { waitUntil: 'networkidle', timeout: 40000 })
    await page.waitForTimeout(1000)
    const state = await page.evaluate(() => {
      const core = document.querySelector('.profile-agent-stage .ciclo-core')
      const image = core?.querySelector('.ciclo-core-hero-image')
      const frame = core?.querySelector('.ciclo-core-image-frame')
      const orbits = core?.querySelector('.ciclo-core-image-orbits')
      const particles = core?.querySelector('.ciclo-core-image-particles')
      const energy = core?.querySelector('.ciclo-core-energy-field')
      const stage = document.querySelector('.profile-agent-stage')
      const imageRect = image?.getBoundingClientRect()
      const stageRect = stage?.getBoundingClientRect()
      const numberValue = (element) => element ? Number.parseInt(getComputedStyle(element).zIndex || '0', 10) : null
      return {
        tier: core?.getAttribute('data-tier') || '',
        src: image?.getAttribute('src') || '',
        naturalWidth: image?.naturalWidth || 0,
        naturalHeight: image?.naturalHeight || 0,
        complete: Boolean(image?.complete),
        insideStage: Boolean(imageRect && stageRect && imageRect.left >= stageRect.left - 1 && imageRect.right <= stageRect.right + 1 && imageRect.top >= stageRect.top - 1 && imageRect.bottom <= stageRect.bottom + 1),
        frameZ: numberValue(frame),
        orbitsZ: numberValue(orbits),
        particlesZ: numberValue(particles),
        energyDisplay: energy ? getComputedStyle(energy).display : null,
      }
    })
    assert(state.tier === tier, `${plan}: expected ${tier}, got ${state.tier}`)
    assert(state.src.endsWith(`/assets/robot/${file}`), `${plan}: wrong asset ${state.src}`)
    assert(state.complete && state.naturalWidth === 1024 && state.naturalHeight === 1024, `${plan}: asset did not decode`)
    assert(state.insideStage, `${plan}: robot overflows stage`)
    assert(state.frameZ === 3, `${plan}: unexpected image frame z-index ${state.frameZ}`)
    if (state.orbitsZ !== null) assert(state.frameZ > state.orbitsZ, `${plan}: orbit overlays the robot`)
    if (state.particlesZ !== null) assert(state.frameZ > state.particlesZ, `${plan}: particles overlay the robot`)
    if (state.energyDisplay !== null) assert(state.energyDisplay === 'none', `${plan}: energy field crosses the robot`)
    assert(errors.length === 0, `${plan}: ${errors.join(' | ')}`)
    await page.locator('.profile-agent-stage').screenshot({ path: path.join(OUT, `${tier}.png`) })
    passed += 1
    await context.close()
  }
  const deliberationContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const deliberationPage = await deliberationContext.newPage()
  const deliberationErrors = []
  deliberationPage.on('pageerror', (error) => deliberationErrors.push(error.message))
  deliberationPage.on('console', (message) => { if (message.type() === 'error') deliberationErrors.push(message.text()) })
  await installMockRoutes(deliberationPage, bootstrap, '专业版')
  await deliberationPage.goto(`${BASE}/deliberation`, { waitUntil: 'networkidle', timeout: 40000 })
  await deliberationPage.locator('.deliberation-robot-stage .ciclo-core').waitFor({ state: 'visible', timeout: 10000 })
  const deliberationState = await deliberationPage.locator('.deliberation-robot-stage').evaluate(readStackingState)
  assert(deliberationState.tier === 'professional', `deliberation: wrong tier ${deliberationState.tier}`)
  assert(deliberationState.src.endsWith('/assets/robot/robot-lv4.png'), `deliberation: wrong asset ${deliberationState.src}`)
  assert(Number.isFinite(deliberationState.frameZ) && deliberationState.frameZ > 0, `deliberation: invalid image frame z-index ${deliberationState.frameZ}`)
  assert(deliberationState.orbitsZ !== null && deliberationState.frameZ > deliberationState.orbitsZ, 'deliberation: orbit overlays the robot')
  assert(deliberationState.particlesZ !== null && deliberationState.frameZ > deliberationState.particlesZ, 'deliberation: particles overlay the robot')
  assert(deliberationState.energyDisplay === 'none', 'deliberation: energy field crosses the robot')
  assert(deliberationErrors.length === 0, `deliberation: ${deliberationErrors.join(' | ')}`)
  await deliberationPage.locator('.deliberation-robot-stage').screenshot({ path: path.join(OUT, 'deliberation-professional.png') })
  passed += 1
  await deliberationContext.close()
  await browser.close()
  console.log(`PASS=${passed}/5 ROBOT_ASSETS`)
})().catch((error) => { console.error(error.message); process.exit(1) })
