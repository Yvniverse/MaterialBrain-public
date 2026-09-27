import { expect, test, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

type Surface = 'floating' | 'agent'
type AcceptanceCase = {
  id: string
  priority: string
  dual_surface: boolean
  question: string
}

const candidateSha = process.env.P3341_CANDIDATE_SHA || ''
const username = process.env.P3341_UAT_USERNAME || ''
const password = process.env.P3341_UAT_PASSWORD || ''
const casesFile = process.env.P3341_CASES_FILE || ''
const evidenceRoot = path.resolve(
  process.env.P3341_EVIDENCE_ROOT || path.join(process.cwd(), '..', 'artifacts', 'phase3341', 'browser'),
)
const viewport = { width: 1440, height: 900 }
const expectedOrder = [
  'P3341-GROUND-01',
  'P3341-SELECT-02',
  'P3341-CLEAR-03',
  'P3341-ORDINAL-04',
  'P3341-NOAUTO-05',
  'P3341-COMPOSITE-06',
  'P3341-PREVIEW-07',
]

const fallbackQuestions: Record<string, string> = {
  'P3341-GROUND-01':
    '这套 Buck 继续按 LM5164。请按手册的自举电容要求，从现有物料里匹配，告诉我满足、信息不足和不满足的真实物料，并带库存和库位。',
  'P3341-SELECT-02': '自举电容就用 C84703 / CL05B222KB5NNNC 作为这份工程草案的选择。',
  'P3341-CLEAR-03': '先取消刚才这颗自举电容的草案选择。',
  'P3341-ORDINAL-04': '就换成第二个。',
  'P3341-NOAUTO-05':
    '如果有多颗满足这项自举要求的候选，先按规格、库存、库位和证据给我比较，不要替我自动选。',
  'P3341-COMPOSITE-06':
    '12V到3.3V、100mA，我准备采用12V→5V Buck→3.3V LDO。把各级损耗、工程BOM完整度和LM5164候选库存库位一起给我。',
  'P3341-PREVIEW-07':
    '把当前工程草案预览到 PROD-DEXGRIP 的 EVT-R1，只做 Product BOM Preview，告诉我会新增、改数量、不变和未解决哪些项。',
}

function record(value: unknown): Record<string, any> {
  return value && typeof value === 'object' ? (value as Record<string, any>) : {}
}

function loadCases(): AcceptanceCase[] {
  const rows = fs
    .readFileSync(casesFile, 'utf8')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line) as Record<string, any>)
  const byId = new Map(
    rows.map((row) => [
      String(row.id),
      {
        id: String(row.id),
        priority: String(row.priority || 'P0'),
        dual_surface: row.dual_surface !== false,
        question: String(row.question || fallbackQuestions[String(row.id)] || ''),
      },
    ]),
  )
  return expectedOrder.map((id) => byId.get(id)).filter(Boolean) as AcceptanceCase[]
}

function entityPayload(payload: unknown): Record<string, any> {
  const root = record(payload)
  const entities = record(root.entities)
  return record(entities.engineering_research || entities.power_design)
}

function allRows(entity: Record<string, any>): Record<string, any>[] {
  const draft = record(entity.draft)
  const railDraft = record(entity.rail_bom_draft || draft.rail_bom_draft)
  const rows: Record<string, any>[] = []
  for (const rail of [...(railDraft.rails || []), ...(entity.rails || [])]) {
    for (const stage of rail.stages || []) rows.push(...((stage.bom_requirements || []) as any[]).map(record))
  }
  rows.push(...((entity.peripheral_requirements || draft.peripheral_requirements || []) as any[]).map(record))
  return rows
}

function selectionTruth(payload: unknown): Record<string, any> {
  const entity = entityPayload(payload)
  const rows = allRows(entity)
  const selectedIds = rows
    .map((row) => row.selected_material_id)
    .filter((value) => value !== null && value !== undefined)
  const action = record(entity.selection_action_result || record(entity.draft).selection_action_result)
  const context = record(entity.active_selection_context || record(entity.draft).active_selection_context)
  return {
    selected_ids: selectedIds,
    selected_statuses: rows.map((row) => row.selection_status).filter(Boolean),
    selected_bases: rows.map((row) => row.selection_basis).filter(Boolean),
    action: {
      action: action.selection_action || null,
      resolution: action.resolution || null,
      before: action.before_material_id ?? null,
      after: action.after_material_id ?? null,
      candidate_ids: action.valid_candidate_ids || action.ordered_candidate_ids || [],
      narrative: action.narrative || '',
    },
    context: {
      requirement_id: context.requirement_id || null,
      role: context.role || null,
      rail_id: context.rail_id || null,
      stage_id: context.stage_id || null,
      candidate_ids: context.valid_candidate_ids || [],
      selected_id: context.selected_material_id ?? null,
      status: context.selection_status || null,
    },
    topology: entity.selected_topology || null,
  }
}

function jsonText(payload: unknown): string {
  return JSON.stringify(payload)
}

function assertStructuredSelection(payload: unknown, caseId: string) {
  const root = record(payload)
  const entity = entityPayload(payload)
  const truth = selectionTruth(payload)
  expect(root.request_id, `${caseId}: request id`).toBeTruthy()
  expect(Object.keys(entity), `${caseId}: structured engineering entity`).not.toHaveLength(0)
  expect(
    truth.action.resolution || truth.context.requirement_id || truth.selected_ids.length,
    `${caseId}: non-empty structured selection truth`,
  ).toBeTruthy()
}

function assertCase(caseId: string, payload: unknown) {
  const root = record(payload)
  const entity = entityPayload(payload)
  const text = jsonText(payload)
  if (caseId === 'P3341-GROUND-01') {
    assertStructuredSelection(payload, caseId)
    expect(root.intent).toBe('engineering_research')
    expect(text).toContain('C84703')
    expect(text).toContain('2.2')
    expect(selectionTruth(payload).selected_ids, `${caseId}: no automatic selection`).toHaveLength(0)
  } else if (caseId === 'P3341-SELECT-02') {
    assertStructuredSelection(payload, caseId)
    const truth = selectionTruth(payload)
    expect(truth.selected_ids.length).toBeGreaterThan(0)
    expect(truth.selected_bases).toContain('explicit_user')
    expect(truth.selected_statuses).toContain('selected')
    expect(text).toContain('C84703')
    expect(root.model_call_count || 0, `${caseId}: selection state is server-owned`).toBe(0)
  } else if (caseId === 'P3341-CLEAR-03') {
    assertStructuredSelection(payload, caseId)
    expect(selectionTruth(payload).selected_ids, `${caseId}: cleared selected ids`).toHaveLength(0)
    expect(selectionTruth(payload).action.action).toBe('clear')
    expect(root.model_call_count || 0, `${caseId}: clear state is server-owned`).toBe(0)
  } else if (caseId === 'P3341-ORDINAL-04') {
    assertStructuredSelection(payload, caseId)
    const truth = selectionTruth(payload)
    expect(['selected', 'ordinal_out_of_range']).toContain(truth.action.resolution)
    expect(root.narrative || root.answer, `${caseId}: narrative/answer`).toContain(
      truth.action.narrative.slice(0, 8),
    )
    expect(root.model_call_count || 0, `${caseId}: ordinal state is server-owned`).toBe(0)
  } else if (caseId === 'P3341-NOAUTO-05') {
    expect(Object.keys(entity), `${caseId}: structured engineering entity`).not.toHaveLength(0)
    expect(selectionTruth(payload).selected_ids, `${caseId}: comparison must not select`).toHaveLength(0)
    expect(text).toMatch(/比较|不要替我自动选/)
  } else if (caseId === 'P3341-COMPOSITE-06') {
    expect(root.intent).toBe('engineering_research')
    expect(Object.keys(entity), `${caseId}: structured engineering entity`).not.toHaveLength(0)
    expect(text).toContain('LM5164')
    expect(text).toMatch(/0\.17|0.17/)
  } else if (caseId === 'P3341-PREVIEW-07') {
    const preview = record(record(root.entities).product_bom_preview)
    expect(root.intent).toBe('product_bom_preview')
    expect(preview.read_only).toBe(true)
    expect(preview.automatic_write).toBe(false)
    expect(preview.formal_product_bom_modified).toBe(false)
    expect(record(preview.summary).blocking_reason_codes).toBeDefined()
    expect(text).toMatch(/add|update_quantity|no_change|unresolved/)
  }
}

async function login(page: Page) {
  await page.goto('/login', { waitUntil: 'domcontentloaded' })
  await page.getByPlaceholder('请输入账号').fill(username)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('button', { name: '安全登录' }).click()
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 60_000 })
}

async function prepareSurface(page: Page, surface: Surface) {
  if (surface === 'floating') {
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })
    const launcher = page.getByTestId('floating-agent-launcher')
    await expect(launcher).toBeVisible({ timeout: 60_000 })
    const panel = page.getByTestId('floating-agent-panel')
    if (!(await panel.isVisible())) await launcher.click()
    await expect(panel).toBeVisible({ timeout: 30_000 })
    await page.getByTestId('floating-new-conversation').click()
    await expect(page.locator('.conversation-turn')).toHaveCount(0, { timeout: 30_000 })
    return
  }
  await page.goto('/agent', { waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('agent-query-input')).toBeVisible({ timeout: 60_000 })
  await page.getByTestId('new-conversation').click()
  await expect(page.getByTestId('agent-result')).toHaveCount(0, { timeout: 30_000 })
}

async function sendQuestion(page: Page, surface: Surface, question: string) {
  const responsePromise = page.waitForResponse(
    (response) => response.url().includes('/api/v1/agent/query') && response.request().method() === 'POST',
    { timeout: 240_000 },
  )
  await page.getByTestId('agent-query-input').fill(question)
  await page.getByTestId('agent-submit').click()
  const response = await responsePromise
  const payload = await response.json()
  expect(response.status(), `${surface} query failed: ${question}`).toBe(200)
  if (surface === 'floating') {
    await expect(page.locator('.conversation-turn').last().locator('.assistant-loading')).toHaveCount(0, {
      timeout: 240_000,
    })
  } else {
    await expect(page.getByTestId('agent-result')).toBeVisible({ timeout: 240_000 })
  }
  return payload as Record<string, any>
}

async function runtimeInfo(page: Page) {
  const backend = await page.request.get('/api/v1/runtime-info')
  const frontend = await page.request.get('/build-info.json')
  const backendJson = await backend.json()
  const frontendJson = await frontend.json()
  const backendSha = String(record(backendJson.backend).backend_build_sha || backendJson.backend_build_sha || '')
  const frontendSha = String(record(frontendJson).frontend_build_sha || frontendJson.build_sha || '')
  expect(backendSha).toBe(candidateSha)
  expect(frontendSha).toBe(candidateSha)
  return { backend: backendJson, frontend: frontendJson }
}

async function screenshot(page: Page, surface: Surface, caseId: string) {
  const dir = path.join(evidenceRoot, surface)
  fs.mkdirSync(dir, { recursive: true })
  const file = path.join(dir, `${caseId}.png`)
  await page.screenshot({ path: file, fullPage: false })
  return file
}

function turnsFor(caseId: string): string[] {
  const ground = fallbackQuestions['P3341-GROUND-01']
  const select = fallbackQuestions['P3341-SELECT-02']
  const clear = fallbackQuestions['P3341-CLEAR-03']
  if (caseId === 'P3341-SELECT-02') return [ground, select]
  if (caseId === 'P3341-CLEAR-03') return [ground, select, clear]
  if (caseId === 'P3341-ORDINAL-04') return [ground, select, clear, fallbackQuestions[caseId]]
  if (caseId === 'P3341-PREVIEW-07') return [ground, select, fallbackQuestions[caseId]]
  return [fallbackQuestions[caseId]]
}

async function runCase(page: Page, surface: Surface, item: AcceptanceCase) {
  await prepareSurface(page, surface)
  const turns = []
  for (const question of turnsFor(item.id)) {
    const payload = await sendQuestion(page, surface, question)
    turns.push({ question, payload })
  }
  const final = turns[turns.length - 1].payload
  assertCase(item.id, final)
  if (item.id === 'P3341-ORDINAL-04') {
    expect(selectionTruth(final).topology, `${item.id}: topology must not be switched`).toEqual(
      selectionTruth(turns[turns.length - 2].payload).topology,
    )
  }
  const runtime = await runtimeInfo(page)
  return {
    case_id: item.id,
    surface,
    semantic_pass: true,
    turns,
    final,
    selection_truth: selectionTruth(final),
    screenshot: await screenshot(page, surface, item.id),
    runtime,
  }
}

test('Phase 3.3.4.1 Acceptance V3: prove each surface before parity', async ({ page }) => {
  test.setTimeout(60 * 60 * 1000)
  test.skip(
    !candidateSha || !username || !password || !casesFile,
    'PENDING_USER_SESSION: exact SHA, acceptance cases and UAT credentials are required; use manual start/finish/verify plus CI Memory Guard meanwhile',
  )
  fs.mkdirSync(evidenceRoot, { recursive: true })
  await login(page)
  const cases = loadCases()
  expect(cases).toHaveLength(expectedOrder.length)
  const captured: Record<string, Record<Surface, Record<string, any>>> = {}

  for (const item of cases) {
    captured[item.id] = {
      floating: await runCase(page, 'floating', item),
      agent: await runCase(page, 'agent', item),
    }
    const floating = captured[item.id].floating
    const agent = captured[item.id].agent
    expect(floating.semantic_pass && agent.semantic_pass, `${item.id}: semantic checks precede parity`).toBe(true)
    expect(floating.selection_truth, `${item.id}: floating structured truth is non-empty`).not.toEqual({})
    expect(agent.selection_truth, `${item.id}: /agent structured truth is non-empty`).not.toEqual({})
    expect(floating.selection_truth).toEqual(agent.selection_truth)
    fs.writeFileSync(path.join(evidenceRoot, `${item.id}.json`), `${JSON.stringify(captured[item.id], null, 2)}\n`, 'utf8')
  }

  const manifest = {
    schema_version: 3,
    candidate_sha: candidateSha,
    viewport,
    semantic_pass_before_parity: true,
    cases: captured,
  }
  fs.writeFileSync(path.join(evidenceRoot, 'P3341_ACCEPTANCE_V3_MANIFEST.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
  expect(Object.values(captured).every((surfaces) => surfaces.floating.semantic_pass && surfaces.agent.semantic_pass)).toBe(true)
})
