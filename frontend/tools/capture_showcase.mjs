/* global process */
/** Real public-runtime capture. Supply an external plan and auth storage state.
 * No DOM substitutes, API mocks, screenshot composites or application-state writes.
 * Run `node tools/capture_showcase.mjs responsive` for breakpoint inspection, then
 * `node tools/capture_showcase.mjs gallery` after the application commit is built.
 */
import { chromium } from '@playwright/test'
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  assertCaptureEvidence,
  assertLayout,
  assertRenderedScene,
  assertRuntimeIdentity,
  episodeRecoveryEvidence,
  requiredShots,
  responsiveViewports,
} from './showcase_contract.mjs'

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repo = resolve(frontend, '..')
const mode = process.argv[2] || 'responsive'
if (!['gallery', 'responsive'].includes(mode)) throw new Error('Use gallery or responsive')
const planFile = process.env.MB_SHOWCASE_PLAN
if (!planFile || !existsSync(planFile)) throw new Error('Supply an external MB_SHOWCASE_PLAN JSON')
const plan = JSON.parse(readFileSync(planFile, 'utf8'))
const base = (process.env.MB_SHOWCASE_BASE_URL || plan.base_url || '').replace(/\/$/, '')
if (!/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(base)) {
  throw new Error('Capture requires the explicitly configured isolated public loopback runtime')
}
const auth = process.env.MB_SHOWCASE_AUTH_STATE || plan.auth_state
if (!auth || !existsSync(auth)) throw new Error('Supply an external authenticated storageState JSON')
const sourceCommit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repo, encoding: 'utf8' }).trim()
const output = resolve(process.env.MB_SHOWCASE_OUTPUT_DIR || plan.output_dir || '../docs/screenshots')
const proof = resolve(process.env.MB_SHOWCASE_REPORT_DIR || plan.report_dir || output)
mkdirSync(output, { recursive: true })
mkdirSync(proof, { recursive: true })
if (mode === 'gallery') {
  const changed = execFileSync('git', ['diff', '--name-only', 'HEAD', '--', 'frontend/src', 'frontend/Dockerfile', 'frontend/pnpm-lock.yaml', 'backend/app'], { cwd: repo, encoding: 'utf8' }).trim()
  if (changed) throw new Error('Commit the application source before formal capture: ' + changed)
}

const browser = await chromium.launch({
  headless: true,
  channel: process.env.MB_SHOWCASE_BROWSER_CHANNEL || undefined,
  args: ['--enable-webgl', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
})
const records = []
let runtime

async function sceneDiagnostics(page, kind) {
  if (kind === 'warehouse') {
    return page.evaluate(() => {
      const target = document.querySelector('.g-warehouse')
      const detail = { stats: null }
      target?.dispatchEvent(new CustomEvent('warehouse-twin:diagnostics', { detail }))
      return detail.stats
    })
  }
  return page.evaluate(() => {
    let observed = null
    const listener = event => { observed = event.detail }
    window.addEventListener('embodied-lab:diagnostics', listener, { once: true })
    window.dispatchEvent(new CustomEvent('embodied-lab:diagnostics-request'))
    window.removeEventListener('embodied-lab:diagnostics', listener)
    return observed
  })
}

async function waitScene(page, kind) {
  const deadline = Date.now() + 60_000
  let scene
  while (Date.now() < deadline) {
    scene = await sceneDiagnostics(page, kind)
    try { assertRenderedScene(scene, kind); return scene } catch { /* Wait for actual geometry. */ }
    await page.waitForTimeout(200)
  }
  throw new Error('Three.js readiness timed out: ' + JSON.stringify(scene))
}

function valueAt(object, path) {
  return path.split('.').reduce((value, key) => value?.[key], object)
}

async function executeSteps(page, shot, steps = []) {
  const executed = []
  for (const step of steps) {
    const target = step.selector ? page.locator(step.selector) : null
    switch (step.action) {
      case 'click': await target.click({ timeout: 30_000 }); break
      case 'press':
        if (target) await target.press(String(step.value))
        else await page.keyboard.press(String(step.value))
        break
      case 'fill': await target.fill(String(step.value)); break
      case 'select': await target.selectOption(String(step.value)); break
      case 'check': await target.setChecked(step.value !== false); break
      case 'scroll-visible': await target.scrollIntoViewIfNeeded(); break
      case 'wait-visible': await target.waitFor({ state: 'visible', timeout: step.timeout_ms || 60_000 }); break
      case 'wait-hidden': await target.waitFor({ state: 'hidden', timeout: step.timeout_ms || 60_000 }); break
      case 'wait-text':
        await page.waitForFunction(({ selector, text }) => document.querySelector(selector)?.textContent.includes(text), { selector: step.selector, text: step.value }, { timeout: step.timeout_ms || 60_000 })
        break
      case 'wait-diagnostic': {
        const deadline = Date.now() + (step.timeout_ms || 60_000)
        let passed = false
        while (Date.now() < deadline) {
          const observed = valueAt(await sceneDiagnostics(page, shot.kind), step.path)
          passed = step.equals !== undefined ? observed === step.equals
            : step.minimum !== undefined ? observed >= step.minimum
              : step.includes !== undefined ? observed?.includes(step.includes) : !!observed
          if (passed) break
          await page.waitForTimeout(150)
        }
        if (!passed) throw new Error('Actual scene state did not reach ' + step.path)
        break
      }
      case 'handoff-current': {
        const scene = await sceneDiagnostics(page, shot.kind)
        if (scene?.state !== 'WAITING_HANDOFF' || !scene.current_goal) throw new Error('No actual handoff is pending')
        const response = await page.request.get(base + '/api/v1/navigation-lab/world')
        if (!response.ok()) throw new Error('Could not read registered handoff slot')
        const world = await response.json()
        const goal = world.goals.find(item => item.id === scene.current_goal)
        if (!goal?.slot) throw new Error('The current station has no registered slot')
        await page.locator('[data-action="handoff"]').click()
        await page.locator('#mb-scan').fill(goal.slot)
        await page.locator('[data-action="confirm-scan"]').click()
        break
      }
      case 'handoff-server-current': {
        const scene = await sceneDiagnostics(page, shot.kind)
        const execution = scene?.spatial_execution
        if (execution?.status !== 'AWAITING_HANDOFF' || !execution.current_goal_id) throw new Error('No actual server Nav2 handoff is pending')
        await page.locator('.spatial-handoff input').fill(execution.current_goal_id)
        await page.getByRole('button', { name: '扫码并确认交接', exact: true }).click()
        break
      }
      case 'finish-server-mission': {
        const deadline = Date.now() + (step.timeout_ms || 300_000)
        const original = (await sceneDiagnostics(page, shot.kind))?.spatial_execution?.mission_id
        let lastStatus = ''
        let complete = false
        let replanned = false
        const submittedHandoffs = new Set()
        while (Date.now() < deadline) {
          const execution = (await sceneDiagnostics(page, shot.kind))?.spatial_execution
          if (!original || execution?.mission_id !== original) throw new Error('Cleanup mission identity changed')
          if (execution.status !== lastStatus) console.log(`CLEANUP ${original} ${execution.status}`)
          lastStatus = execution.status
          if (execution.status === 'COMPLETED') { complete = true; break }
          if (['FAILED', 'CANCELLED'].includes(execution.status)) throw new Error('The recovery mission did not complete')
          if (execution.status === 'AWAITING_HANDOFF') {
            const goal = execution.current_goal_id
            const input = page.locator('.spatial-handoff input')
            if (goal && !execution.completed_goal_ids?.includes(goal) && !submittedHandoffs.has(goal)
              && await input.isVisible() && await input.isEnabled()) {
              await input.fill(goal)
              await page.getByRole('button', { name: '扫码并确认交接', exact: true }).click()
              submittedHandoffs.add(goal)
              await page.waitForTimeout(400)
            }
          } else if (!replanned && ['REPLANNING', 'PAUSED', 'RECOVERING'].includes(execution.status)) {
            const replan = page.getByRole('button', { name: '重规划剩余任务', exact: true })
            if (await replan.isEnabled()) { await replan.click(); replanned = true }
            await page.waitForTimeout(400)
          }
          await page.waitForTimeout(250)
        }
        if (!complete) throw new Error('The recovery mission did not return HOME before cleanup timeout')
        break
      }
      default: throw new Error('Unsupported UI interaction: ' + step.action)
    }
    executed.push({ action: step.action, selector: step.selector, key: step.action === 'press' ? String(step.value) : undefined, completed_at_utc: new Date().toISOString() })
  }
  return executed
}

async function layoutMetrics(page, kind) {
  return page.evaluate(({ kind }) => {
    const rect = element => {
      if (!element) return null
      const r = element.getBoundingClientRect()
      const visibleWidth = Math.max(0, Math.min(innerWidth, r.right) - Math.max(0, r.left))
      const visibleHeight = Math.max(0, Math.min(innerHeight, r.bottom) - Math.max(0, r.top))
      return { left: r.left, top: r.top, width: r.width, height: r.height, visibleArea: visibleWidth * visibleHeight }
    }
    const canvas = document.querySelector(kind === 'warehouse' ? '.g-three-host canvas' : '#mb-scene canvas')
    const shell = document.querySelector('.g-sidebar') || document.querySelector('.g-shell')
    const color = shell ? getComputedStyle(shell).backgroundColor : ''
    const colors = color.match(/[\d.]+/g)?.map(Number) || []
    const modal = document.querySelector('[aria-modal="true"]')
    const inspector = document.querySelector('.spatial-observability-body')
    const primaryScope = modal || inspector || document
    const primary = [...primaryScope.querySelectorAll('.g-btn.dark, .g-btn.primary, #mb-start, [data-testid="agent-submit"], [data-action="pause"], [data-action="detail"], [data-action="obstacle"], .spatial-actions button, .spatial-handoff button, .spatial-drawer button')]
    const bot = document.querySelector('[data-testid="floating-agent-launcher"]')
    const botRect = bot?.getBoundingClientRect()
    const overlappingBotControls = primary.filter(element => {
      const r = element.getBoundingClientRect()
      if (!botRect || element.disabled || !r.width || !r.height || r.top >= innerHeight || r.bottom <= 0) return false
      return Math.min(r.right, botRect.right) > Math.max(r.left, botRect.left)
        && Math.min(r.bottom, botRect.bottom) > Math.max(r.top, botRect.top)
    }).map(element => element.textContent.trim())
    const obscuredPrimaryControls = primary.filter(element => {
      const r = element.getBoundingClientRect()
      if (element.disabled || !r.width || !r.height || r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth) return false
      const point = document.elementFromPoint(Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2)), Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2)))
      return point && !element.contains(point)
    }).map(element => element.textContent.trim())
    return {
      viewport: { width: innerWidth, height: innerHeight },
      documentWidth: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
      shellLight: colors.length >= 3 && colors.slice(0, 3).reduce((a, b) => a + b, 0) / 3 > 190,
      shellBackground: color,
      stage: rect(canvas),
      obscuredPrimaryControls,
      overlappingBotControls,
      bodyFontPx: Number.parseFloat(getComputedStyle(document.body).fontSize),
      visibleTextFonts: [...document.querySelectorAll('p,label,button')].filter(element => {
        const r = element.getBoundingClientRect()
        return r.width && r.height && r.top < innerHeight && r.bottom > 0
      }).map(element => Number.parseFloat(getComputedStyle(element).fontSize)),
    }
  }, { kind })
}

async function capture(shot, viewport, formal) {
  const settings = plan.shots?.[shot.name] || {}
  const route = settings.route || shot.route
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1, storageState: auth, colorScheme: 'light' })
  const page = await context.newPage()
  const errors = []
  const responseErrors = []
  const episodes = new Map()
  const pendingResponses = new Set()
  page.on('pageerror', error => errors.push(error.message))
  page.on('response', response => {
    if (response.url().startsWith(base + '/api/') && response.status() >= 400) responseErrors.push({ path: new URL(response.url()).pathname, status: response.status() })
    const path = new URL(response.url()).pathname
    if (response.ok() && response.url().startsWith(base + '/api/') && /^\/api\/v1\/spatial\/missions\/[^/]+\/episode$/.test(path)) {
      const pending = (async () => {
        const body = await response.body()
        const episode = JSON.parse(body.toString('utf8'))
        episodes.set(episode.mission_id, { episode, response: { mission_id: episode.mission_id, path, response_sha256: createHash('sha256').update(body).digest('hex') } })
      })().catch(error => errors.push('Server episode capture: ' + error.message))
      pendingResponses.add(pending)
      pending.finally(() => pendingResponses.delete(pending))
    }
  })
  try {
    await page.goto(base + route, { waitUntil: 'networkidle', timeout: 60_000 })
    if (new URL(page.url()).pathname === '/login') throw new Error('The external authentication state is invalid')
    await page.evaluate(() => document.fonts.ready)
    if (!runtime) {
      const frontendInfo = await page.request.get(base + '/build-info.json')
      const backendInfo = await page.request.get(base + '/api/v1/runtime-info')
      if (!frontendInfo.ok() || !backendInfo.ok()) throw new Error('Application build metadata is unavailable')
      const front = await frontendInfo.json()
      const back = await backendInfo.json()
      runtime = { frontend_build_sha: front.frontend_build_sha, backend_build_sha: back.backend_build_sha, base_url: base, frontend_build_time_utc: front.build_time_utc, backend_build_time_utc: back.build_time_utc }
      assertRuntimeIdentity(runtime, sourceCommit)
    }
    const hasScene = ['warehouse', 'laboratory', 'recovery'].includes(shot.kind)
    const readInventory = async () => {
      const response = await page.request.get(base + '/api/v1/dashboard/summary')
      if (!response.ok()) throw new Error('Cannot read the real inventory summary')
      return response.json()
    }
    const inventoryBefore = formal && shot.kind === 'recovery' ? await readInventory() : null
    if (hasScene) await waitScene(page, shot.kind)
    const uiSteps = await executeSteps(page, shot, formal ? settings.steps : settings.responsive_steps)
    await page.waitForTimeout(700)
    const scene = hasScene ? await waitScene(page, shot.kind) : null
    const metrics = await layoutMetrics(page, shot.kind)
    assertLayout(metrics, hasScene)
    if (errors.length || responseErrors.length) throw new Error(JSON.stringify({ errors, responseErrors }))
    const evidence = { kind: shot.kind, verified: true, scene, layout: metrics, ui_steps: uiSteps }
    if (formal) {
      const probes = settings.evidence || {}
      const observedText = async selector => selector ? page.locator(selector).allTextContents() : []
      evidence.selected_equipment = (await observedText(probes.equipment_selector || '.g-location-rail h2')).join(' ')
      evidence.result_selector = probes.result_selector && await page.locator(probes.result_selector).isVisible() ? probes.result_selector : null
      evidence.summary_values = await observedText(probes.summary_selector || '.g-metric-number')
      evidence.equipment_selector = probes.equipment_selector && await page.locator(probes.equipment_selector).isVisible() ? probes.equipment_selector : null
      evidence.compartment_selector = probes.compartment_selector && await page.locator(probes.compartment_selector).isVisible() ? probes.compartment_selector : null
      if (shot.kind === 'recovery') {
        await Promise.all([...pendingResponses])
        const captured = episodes.get(scene?.spatial_execution?.mission_id)
        if (!captured) throw new Error('Open the actual server mission replay before capture')
        Object.assign(evidence, episodeRecoveryEvidence(captured.episode, captured.response))
      }
      if (shot.kind === 'recovery' && probes.recovery_selector) {
        evidence.recovery_visible_text = (await observedText(probes.recovery_selector)).join('\n')
        if (!/重规划|重新规划|恢复/.test(evidence.recovery_visible_text)) throw new Error('Show the actual recovery trace in the visible UI')
      }
      assertCaptureEvidence(shot.kind, evidence)
    }
    const name = formal ? shot.name : `${viewport.width}-${shot.kind}.png`
    const path = resolve(output, name)
    await page.screenshot({ path, fullPage: false, animations: 'disabled' })
    const bytes = readFileSync(path)
    if (bytes.length > 1_500_000) throw new Error('The original PNG exceeds the 1.5 MB gallery limit')
    const record = {
      path: formal ? `docs/screenshots/${name}` : name,
      source_commit: sourceCommit,
      frontend_build_sha: runtime.frontend_build_sha,
      backend_build_sha: runtime.backend_build_sha,
      route: new URL(page.url()).pathname + new URL(page.url()).search,
      state: settings.state || `Actual ${shot.kind} page with rendered product data`,
      viewport,
      captured_at_utc: new Date().toISOString(),
      sha256: createHash('sha256').update(bytes).digest('hex'),
      bytes: bytes.length,
      capture_evidence: evidence,
      visual_review: { status: 'pending' },
    }
    records.push(record)
    console.log(`CAPTURE ${name} ${record.sha256}`)
    if (formal && shot.kind === 'recovery') {
      if (!settings.cleanup_steps?.length) throw new Error('Configure normal UI recovery cleanup steps')
      const cleanupSteps = await executeSteps(page, shot, settings.cleanup_steps)
      const final = (await sceneDiagnostics(page, shot.kind))?.spatial_execution
      const after = await readInventory()
      const unchanged = ['material_count', 'quantity', 'reserved_quantity', 'available_quantity', 'inventory_value']
        .every(key => inventoryBefore[key] !== undefined && after[key] !== undefined && String(inventoryBefore[key]) === String(after[key]))
      if (final?.status !== 'COMPLETED' || !final.completed_goal_ids?.includes('HOME') || final.metrics?.collision_count !== 0 || !unchanged) {
        throw new Error('Recovery cleanup must complete HOME with zero observed collisions and preserve actual inventory')
      }
      if (errors.length || responseErrors.length) throw new Error(JSON.stringify({ errors, responseErrors }))
      record.cleanup = { status: final.status, mission_id: final.mission_id, completed_goal_ids: final.completed_goal_ids, current_pose: final.current_pose, collision_count: final.metrics.collision_count, inventory_unchanged: unchanged, ui_steps: cleanupSteps, checked_at_utc: new Date().toISOString() }
      console.log(`CLEANUP ${final.mission_id} HOME collision_count=0 inventory unchanged`)
    }
  } catch (error) {
    await page.screenshot({ path: resolve(proof, `failure-${viewport.width}-${shot.kind}.png`), fullPage: true }).catch(() => {})
    throw new Error(`${shot.kind} at ${viewport.width}: ${error.message}`)
  } finally {
    await context.close()
  }
}

try {
  if (mode === 'gallery') {
    for (const shot of requiredShots) await capture(shot, { width: shot.width, height: shot.height }, true)
    const manifest = { schema_version: 1, capture_method: 'playwright-public-runtime', source_commit: sourceCommit, runtime, files: records }
    writeFileSync(resolve(output, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n')
    console.log('All six real UI captures exist. Visually inspect them and record review before the release gate.')
  } else {
    const kinds = plan.responsive_kinds || ['warehouse', 'laboratory', 'agent', 'dashboard', 'storage']
    for (const viewport of responsiveViewports) {
      for (const kind of kinds) {
        const shot = requiredShots.find(item => item.kind === kind)
        if (!shot) throw new Error('Unknown responsive page kind: ' + kind)
        await capture(shot, viewport, false)
      }
    }
  }
} finally {
  writeFileSync(resolve(proof, `${mode}-capture-report.json`), JSON.stringify({ source_commit: sourceCommit, runtime, records }, null, 2) + '\n')
  await browser.close()
}
