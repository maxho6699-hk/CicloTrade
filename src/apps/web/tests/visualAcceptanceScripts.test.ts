import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const root = path.resolve(import.meta.dirname, '..')
const allPages = fs.readFileSync(path.join(root, 'shot_all_verify.cjs'), 'utf8')
const responsive = fs.readFileSync(path.join(root, 'shot_nav_verify.cjs'), 'utf8')
const userFeedback = fs.readFileSync(path.join(root, 'verify_user_feedback.cjs'), 'utf8')

test('full-page visual acceptance uses explicit guest, user and admin contracts', () => {
  assert.match(allPages, /CICLO_PREVIEW_URL[^\n]*localhost:5175/)
  assert.match(allPages, /CICLO_MOCK_URL[^\n]*localhost:5180/)
  assert.match(allPages, /authenticated: false/)
  assert.match(allPages, /authenticated: true, access_token: 'x'/)
  assert.match(allPages, /admin_role: mode === 'admin' \? 'super_admin' : 'user'/)
  assert.match(allPages, /AUTH guest \/today/)
  assert.match(allPages, /AUTH user \/admin/)
  assert.match(allPages, /returnTo !== '\/today'/)
})

test('full-page visual acceptance fails closed on path, query, heading, DOM and runtime errors', () => {
  assert.match(allPages, /requiredQueryMatches/)
  assert.match(allPages, /state\.heading === expectedHeading/)
  assert.match(allPages, /state\.rootLen >= 1000/)
  assert.match(allPages, /errors\.length === 0/)
  assert.match(allPages, /state\.fontViolations === 0/)
  assert.match(allPages, /state\.brokenImages === 0/)
  assert.match(allPages, /state\.unnamedInteractives === 0/)
  assert.match(allPages, /state\.brandAsset\.purpleRatio < 0\.01/)
  assert.match(allPages, /const validBrand = Boolean\(state\.brandAsset && state\.brandAsset\.width === 128/)
  assert.match(allPages, /name === '法律' \? !state\.brandAsset \|\| validBrand : validBrand/)
  assert.match(allPages, /PASS=\$\{pages\.length\}\/\$\{pages\.length\} NEGATIVE_AUTH=2\/2/)
  assert.doesNotMatch(allPages, /C:\/Users\/maxho/)
})

test('responsive acceptance uses the same explicit auth and query contract', () => {
  assert.match(responsive, /CICLO_SCREENSHOT_DIR/)
  assert.match(responsive, /authenticated: true, access_token: 'x'/)
  assert.match(responsive, /requiredQueryMatches/)
  assert.match(responsive, /state\.heading === expectedHeading/)
  assert.match(responsive, /state\.scrollWidth <= state\.viewportWidth \+ 1/)
  assert.match(responsive, /state\.fontViolations === 0/)
  assert.match(responsive, /state\.brokenImages === 0/)
  assert.match(responsive, /const imageInViewport = \(image\) =>[\s\S]*?rect\.bottom > 0 && rect\.top < innerHeight && rect\.right > 0 && rect\.left < innerWidth/)
  assert.match(responsive, /visible\(image\) && imageInViewport\(image\) && \(!image\.complete \|\| image\.naturalWidth === 0\)/)
  assert.match(responsive, /state\.unnamedInteractives === 0/)
  assert.match(responsive, /viewports = \[\['平板'.*\['手机'/)
  assert.match(responsive, /PASS=\$\{completed\}\/\$\{pages\.length \* viewports\.length\}/)
  assert.doesNotMatch(responsive, /C:\/Users\/maxho/)
})

test('user feedback geometry covers exact breakpoints and interaction states', () => {
  for (const width of ['1917', '1114', '1062', '898', '841', '813', '390']) assert.match(userFeedback, new RegExp(`width: ${width}`))
  assert.match(userFeedback, /requestFullscreen = \(\) => Promise\.reject/)
  assert.match(userFeedback, /document\.fullscreenElement/)
  assert.match(userFeedback, /Dark saved star is not yellow/)
  assert.match(userFeedback, /Light saved star is not red/)
  assert.match(userFeedback, /Recommendation card stretches/)
  assert.match(userFeedback, /Mobile recommendation detail is not a bottom sheet/)
  assert.match(userFeedback, /Research\/drawing\/chart order is wrong/)
  assert.match(userFeedback, /assertVisibleTypography/)
  assert.match(userFeedback, /PASS=\$\{checks\}\/25 USER_FEEDBACK_GEOMETRY/)
  assert.doesNotMatch(userFeedback, /C:\/Users\/maxho/)
})
