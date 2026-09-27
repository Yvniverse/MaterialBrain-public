import { expect, test, type Page } from '@playwright/test'

const user = {
  id: 1,
  username: 'e2e-admin',
  full_name: 'E2E Admin',
  department: 'QA',
  is_active: true,
  must_change_password: false,
  role: {
    id: 1,
    name: 'admin',
    description: 'E2E role',
    permissions: ['*'],
    is_system: true,
  },
  created_at: '2026-01-01T00:00:00Z',
}

let queryPayloads: Array<{ message: string; conversation_id: string | null }> = []

function queryResponse(question: string, requestNumber: number) {
  return {
    answer: `找到了。${question} 对应物料位于研发仓库，当前库存 30。`,
    narrative: '',
    intent: 'find_location',
    entities: {
      locations: {
        material_id: 11,
        code: 'MAT-E2E-001',
        name: 'E2E 连接器',
        mpn: 'E2E-MPN-001',
        locations: [
          {
            location_id: 7,
            code: 'RD',
            name: '研发仓库',
            full_path: '研发仓库',
            organizer_id: null,
            organizer_style: null,
            parent_id: null,
            quantity_at_location: null,
            quantity_is_exact: false,
          },
        ],
        count: 1,
        material_quantity: '30.0000',
        lot_quantity_total: '0.0000',
        unallocated_quantity: '30.0000',
        distribution_status: 'partial',
      },
    },
    grounded_facts: [
      {
        kind: 'location',
        source_tool: 'find_material_locations',
        entity_id: 7,
        field: 'full_path',
        value: '研发仓库',
        unit: null,
        label: '库位路径',
      },
    ],
    tool_events: [
      {
        tool: 'search_materials',
        status: 'success',
        summary: '找到唯一物料',
        duration_ms: 8,
      },
      {
        tool: 'find_material_locations',
        status: 'success',
        summary: '找到 1 个库位',
        duration_ms: 10,
      },
    ],
    ui_actions: [{ type: 'focus_location', target_id: 7, payload: {} }],
    proposal_ids: [],
    telemetry: [],
    request_id: `282893fb-90c5-4510-a986-${String(requestNumber).padStart(12, '0')}`,
    conversation_id: 'e2e-conversation-001',
  }
}

function cableQueryResponse(question: string, requestNumber: number) {
  const clarification = question !== '反向的。'
  return {
    answer: clarification ? '触点方向需要同向(A型)还是反向(B型)？' : '没有完全匹配，以下是最接近的线缆。',
    narrative: '',
    intent: 'search_cables',
    entities: {
      cable_search: {
        query: question,
        constraints: {
          cable_kind: 'flat_flex',
          connector_pitch_mm: '0.5',
          pin_count: 30,
          target_length_cm: '15',
          ...(clarification ? {} : { direction: 'reverse' }),
        },
        count: clarification ? 0 : 1,
        evaluated_count: clarification ? 0 : 80,
        needs_direction_disambiguation: clarification,
        result_state: clarification ? 'awaiting_clarification' : 'near_match',
        clarification: clarification ? '触点方向需要同向(A型)还是反向(B型)？' : '',
        automatic_substitution: false,
        inventory_source: 'Material + InventoryLot',
        items: clarification
          ? []
          : [
              {
                material_id: 88,
                code: 'CBL-PF-S28',
                name: '0.5mm 30P 相机排线',
                mpn: 'CBL-S28-REVERSE',
                manufacturer: '',
                specification: 'flat_flex',
                unit: '条',
                quantity: '10.0000',
                reserved_quantity: '0.0000',
                available_quantity: '10.0000',
                cable_kind: 'flat_flex',
                end_style: 'double',
                connector_a: 'FPC-30P',
                connector_b: 'FPC-30P',
                connector_pitch_mm: '0.5',
                pin_count: 30,
                pin_count_b: 0,
                pin_layout: '',
                direction: 'reverse',
                length_cm: '15.0',
                locations: [
                  {
                    location_id: 208,
                    code: 'CABLE-R08',
                    name: 'R08',
                    full_path: '研发仓库 / 线缆柜 / R08',
                    quantity: '10.0000',
                  },
                ],
                location_count: 1,
                fallback_storage_location: '',
                location_truth_source: 'InventoryLot',
                match_reasons: ['0.5 mm', '30 Pin', '15 cm', '反向 B 型'],
                match_state: 'near_match',
                differences: ['长度 +3 cm'],
                technical_claims_allowed: true,
              },
            ],
      },
    },
    grounded_facts: [],
    tool_events: clarification
      ? []
      : [{ tool: 'search_cables', status: 'success', summary: '1', duration_ms: 2 }],
    ui_actions: [],
    proposal_ids: [],
    telemetry: [],
    request_id: `s28-${requestNumber}`,
    conversation_id: 'e2e-s28-conversation',
  }
}

async function mockApi(page: Page) {
  let authenticated = false
  let requestNumber = 0
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/auth/me')) {
      await route.fulfill({
        status: authenticated ? 200 : 401,
        contentType: 'application/json',
        body: JSON.stringify(authenticated ? user : { code: 'UNAUTHORIZED' }),
      })
      return
    }
    if (url.pathname.endsWith('/auth/login')) {
      authenticated = true
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ user }),
      })
      return
    }
    if (url.pathname.endsWith('/agent/proposals')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return
    }
    if (url.pathname.endsWith('/agent/suggestions')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            {
              type: 'material_location',
              text: 'E2E-MPN-001 在哪里？',
              material_id: 11,
              project_id: null,
            },
            {
              type: 'material_inventory',
              text: 'E2E-MPN-001 现在还能用多少？',
              material_id: 11,
              project_id: null,
            },
            {
              type: 'low_stock',
              text: '哪些物料低于安全库存？',
              material_id: null,
              project_id: null,
            },
          ],
          generated_at: '2026-08-28T00:00:00Z',
          source: 'database',
        }),
      })
      return
    }
    if (url.pathname.endsWith('/agent/query')) {
      requestNumber += 1
      const payload = route.request().postDataJSON() as {
        message: string
        conversation_id: string | null
      }
      queryPayloads.push(payload)
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          payload.message.includes('0.5mm 30P') || payload.message === '反向的。'
            ? cableQueryResponse(payload.message, requestNumber)
            : queryResponse(payload.message, requestNumber),
        ),
      })
      return
    }
    if (url.pathname.endsWith('/locations')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
}

async function login(page: Page) {
  await page.goto('/login')
  await page.getByPlaceholder('请输入账号').fill('e2e-admin')
  await page.getByPlaceholder('请输入密码').fill('test-password')
  await page.getByRole('button', { name: '安全登录' }).click()
  await expect(page).toHaveURL(/\/dashboard$/)
}

async function dragLauncher(page: Page, x: number, y: number) {
  const launcher = page.getByTestId('floating-agent-launcher')
  const box = await launcher.boundingBox()
  expect(box).not.toBeNull()
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
  await page.mouse.down()
  await page.mouse.move(x, y, { steps: 8 })
  await page.mouse.up()
}

async function assertPanelInsideViewport(page: Page) {
  const panel = page.getByTestId('floating-agent-panel')
  await expect
    .poll(async () => {
      const rect = await panel.boundingBox()
      const viewport = page.viewportSize()
      if (!rect || !viewport) return false
      return (
        rect.x >= 11 &&
        rect.y >= 11 &&
        rect.x + rect.width <= viewport.width - 11 &&
        rect.y + rect.height <= viewport.height - 11
      )
    })
    .toBe(true)
  const rect = await panel.boundingBox()
  const viewport = page.viewportSize()
  expect(rect).not.toBeNull()
  expect(viewport).not.toBeNull()
  expect(rect!.x).toBeGreaterThanOrEqual(11)
  expect(rect!.y).toBeGreaterThanOrEqual(11)
  expect(rect!.x + rect!.width).toBeLessThanOrEqual(viewport!.width - 11)
  expect(rect!.y + rect!.height).toBeLessThanOrEqual(viewport!.height - 11)
}

test.beforeEach(async ({ page }) => {
  queryPayloads = []
  await page.addInitScript(() => localStorage.clear())
  await mockApi(page)
  await login(page)
})

test('places the panel left of a right robot and right of a left robot', async ({ page }) => {
  const launcher = page.getByTestId('floating-agent-launcher')
  await dragLauncher(page, page.viewportSize()!.width - 55, 250)
  await launcher.click()
  const panel = page.getByTestId('floating-agent-panel')
  await expect(panel).toBeVisible()
  await expect(panel).toHaveAttribute('data-side', 'left')
  let robotRect = await launcher.boundingBox()
  let panelRect = await panel.boundingBox()
  expect(panelRect!.x + panelRect!.width).toBeLessThan(robotRect!.x)
  await assertPanelInsideViewport(page)

  await page.keyboard.press('Escape')
  await expect(panel).toBeHidden()
  await dragLauncher(page, 55, 250)
  await launcher.click()
  await expect(panel).toBeVisible()
  await expect(panel).toHaveAttribute('data-side', 'right')
  robotRect = await launcher.boundingBox()
  panelRect = await panel.boundingBox()
  expect(panelRect!.x).toBeGreaterThan(robotRect!.x + robotRect!.width)
  await assertPanelInsideViewport(page)
})

test('resizes diagonally, persists size, and clamps it on a smaller viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const launcher = page.getByTestId('floating-agent-launcher')
  await dragLauncher(page, 80, 140)
  await launcher.click()
  const panel = page.getByTestId('floating-agent-panel')
  const before = await panel.boundingBox()
  const corner = page.getByTestId('floating-resize-corner')
  const handle = await corner.boundingBox()
  expect(handle).not.toBeNull()
  await page.mouse.move(handle!.x + handle!.width / 2, handle!.y + handle!.height / 2)
  await page.mouse.down()
  await page.mouse.move(handle!.x + handle!.width / 2 + 60, handle!.y + handle!.height / 2 + 50, {
    steps: 8,
  })
  await page.mouse.up()

  const resized = await panel.boundingBox()
  expect(resized!.width).toBeGreaterThan(before!.width + 40)
  expect(resized!.height).toBeGreaterThan(before!.height + 30)
  const stored = await page.evaluate(() =>
    localStorage.getItem('materialbrain:floating-agent:size:v1'),
  )
  expect(stored).not.toBeNull()

  await page.keyboard.press('Escape')
  await launcher.click()
  const reopened = await panel.boundingBox()
  expect(Math.abs(reopened!.width - resized!.width)).toBeLessThanOrEqual(2)
  expect(Math.abs(reopened!.height - resized!.height)).toBeLessThanOrEqual(2)

  await page.setViewportSize({ width: 640, height: 700 })
  await expect(page.getByTestId('floating-resize-corner')).toBeHidden()
  await assertPanelInsideViewport(page)
  const compact = await panel.boundingBox()
  expect(compact!.width).toBeLessThanOrEqual(616)
  expect(compact!.height).toBeLessThanOrEqual(676)
})

test('keeps one vertical conversation scroll and the composer ready for a second question', async ({
  page,
}) => {
  await page.getByTestId('floating-agent-launcher').click()
  const panel = page.getByTestId('floating-agent-panel')
  const composer = page.getByTestId('floating-agent-composer')
  await page.getByLabel('输入问题').fill('第一条：E2E-MPN-001 在哪里？')
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.locator('.conversation-turn')).toHaveCount(1)
  expect(queryPayloads[0].conversation_id).toBeNull()
  const materialCard = page.getByTestId('agent-material-result')
  await expect(materialCard).toContainText('当前库存')
  await expect(materialCard.locator('.stock-warning')).toContainText('30 件库存尚未分配')
  await expect(page.locator('.turn-process')).not.toHaveAttribute('open', '')
  await expect(page.locator('.turn-technical')).not.toHaveAttribute('open', '')

  let panelRect = await panel.boundingBox()
  let composerRect = await composer.boundingBox()
  expect(
    panelRect!.y + panelRect!.height - (composerRect!.y + composerRect!.height),
  ).toBeLessThanOrEqual(2)

  await page.getByLabel('输入问题').fill('第二条：那另一个呢？')
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.locator('.conversation-turn')).toHaveCount(2)
  expect(queryPayloads[1].conversation_id).toBe('e2e-conversation-001')
  await expect(page.locator('.conversation-turn').last()).toContainText('那另一个呢？')
  panelRect = await panel.boundingBox()
  composerRect = await composer.boundingBox()
  expect(
    panelRect!.y + panelRect!.height - (composerRect!.y + composerRect!.height),
  ).toBeLessThanOrEqual(2)

  const overflow = await page.evaluate(() => ({
    panel: getComputedStyle(document.querySelector('[data-testid="floating-agent-panel"]')!)
      .overflowY,
    conversation: getComputedStyle(document.querySelector('[data-testid="floating-agent-scroll"]')!)
      .overflowY,
    questionsY: getComputedStyle(document.querySelector('.quick-questions')!).overflowY,
    questionsX: getComputedStyle(document.querySelector('.quick-questions')!).overflowX,
  }))
  expect(overflow.panel).toBe('hidden')
  expect(overflow.conversation).toBe('auto')
  expect(overflow.questionsY).toBe('hidden')
  expect(overflow.questionsX).toBe('auto')

  await page.getByTestId('floating-new-conversation').click()
  await expect(page.locator('.conversation-turn')).toHaveCount(0)
  await page.getByLabel('输入问题').fill('新对话里的第一条')
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.locator('.conversation-turn')).toHaveCount(1)
  expect(queryPayloads[2].conversation_id).toBeNull()
})

test('keeps S28 slots across turns and renders one structured candidate without showcase copy', async ({
  page,
}) => {
  await page.getByTestId('floating-agent-launcher').click()
  await page.getByLabel('输入问题').fill('我想找一根给相机用的 0.5mm 30P 排线，15cm 左右。')
  await page.getByRole('button', { name: '发送问题' }).click()
  const firstTurn = page.locator('.conversation-turn').first()
  await expect(firstTurn.getByTestId('agent-cable-results')).toContainText('等待确认方向')
  await expect(firstTurn.getByTestId('agent-cable-results')).not.toContainText('线缆候选 0 条')

  await page.getByLabel('输入问题').fill('反向的。')
  await page.getByRole('button', { name: '发送问题' }).click()
  expect(queryPayloads[1]).toMatchObject({
    message: '反向的。',
    conversation_id: 'e2e-s28-conversation',
  })
  const secondTurn = page.locator('.conversation-turn').nth(1)
  await expect(secondTurn.getByTestId('agent-cable-results')).toContainText('0.5mm 30P 相机排线')
  await expect(secondTurn.getByTestId('agent-cable-results')).toContainText('没有找到完全符合')
  await expect(secondTurn.getByTestId('agent-cable-results')).toContainText('长度 +3 cm')
  await expect(secondTurn.getByText('CBL-PF-S28')).toHaveCount(1)
  await expect(secondTurn).not.toContainText('**')

  await page.goto('/locations')
  const forbiddenSubtitle = String.fromCodePoint(0x79cb, 0x62db, 0x4f5c, 0x54c1, 0x5c55, 0x793a)
  await expect(page.locator('body')).not.toContainText(forbiddenSubtitle)
})

test('remains closeable and contained at effective 100%, 125%, and 150% zoom', async ({ page }) => {
  const physicalViewport = { width: 1440, height: 900 }
  for (const zoom of [1, 1.25, 1.5]) {
    await page.setViewportSize({
      width: Math.round(physicalViewport.width / zoom),
      height: Math.round(physicalViewport.height / zoom),
    })
    const launcher = page.getByTestId('floating-agent-launcher')
    if (!(await page.getByTestId('floating-agent-panel').isVisible())) await launcher.click()
    await expect(page.getByRole('button', { name: '关闭物料助手' })).toBeVisible()
    await assertPanelInsideViewport(page)
    const overflow = await page.getByTestId('floating-agent-panel').evaluate((element) => ({
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
    }))
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1)
  }
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('floating-agent-panel')).toBeHidden()
})
