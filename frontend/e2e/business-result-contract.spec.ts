import { expect, test, type Page } from '@playwright/test'

const user = {
  id: 1,
  username: 'e2e-admin',
  full_name: 'E2E Admin',
  department: 'QA',
  is_active: true,
  must_change_password: false,
  role: { id: 1, name: 'admin', description: 'QA', permissions: ['*'], is_system: true },
  created_at: '2026-01-01T00:00:00Z',
}

const actualLocation = {
  location_id: 73,
  code: 'RD-A03-02',
  name: '02 格',
  full_path: '研发仓库 / 元件柜 A03 / 02 格',
  quantity: '12.0000',
  quantity_at_location: '12.0000',
  quantity_is_exact: true,
}

const material = {
  id: 42,
  material_id: 42,
  code: 'MAT-XL2-001',
  name: '板端连接器',
  mpn: 'XL2EL89COI-111YLC-25M',
  manufacturer: 'PENGKA Components',
  specification: '25 Pin',
  package: 'Connector',
  unit: '件',
  quantity: '15.0000',
  reserved_quantity: '3.0000',
  available_quantity: '12.0000',
  low_stock: false,
  location: actualLocation,
  locations: [actualLocation],
  location_truth_source: 'InventoryLot',
  distribution_status: 'complete',
  unallocated_quantity: '0.0000',
}

const cable = {
  material_id: 88,
  code: 'PORTFOLIO-CBL-PF-00042',
  name: '0.5mm 30P 反向相机排线',
  mpn: 'FFC-0.5-30P-B-150',
  manufacturer: 'PENGKA Cable',
  specification: 'flat_flex',
  unit: '条',
  quantity: '10.0000',
  reserved_quantity: '1.0000',
  available_quantity: '9.0000',
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
  fallback_storage_location: '研发仓库',
  location_truth_source: 'InventoryLot',
  match_reasons: ['0.5 mm', '30 Pin', '15 cm', '反向 B 型'],
  technical_claims_allowed: true,
  match_state: 'exact_match',
  differences: [],
}

function response(answer: string, intent: string, entities: Record<string, unknown>) {
  return {
    answer,
    narrative: '',
    intent,
    entities,
    grounded_facts: [],
    tool_events: [{ tool: 'deterministic_fixture', status: 'success', summary: 'ok', duration_ms: 1 }],
    ui_actions: [],
    proposal_ids: [],
    telemetry: [],
    request_id: crypto.randomUUID(),
    conversation_id: 'business-contract-e2e',
  }
}

function resultFor(message: string) {
  if (message.includes('PORTFOLIO-CBL-PF-00042')) {
    return response('已找到线缆。', 'search_cables', {
      cable_search: {
        query: message,
        constraints: {},
        items: [cable],
        count: 1,
        evaluated_count: 80,
        needs_direction_disambiguation: false,
        result_state: 'exact_match',
        clarification: '',
        automatic_substitution: false,
        inventory_source: 'Material + InventoryLot',
      },
    })
  }
  if (message.includes('RB-GRIPPER-EVT')) {
    return response('库存分析已完成。', 'analyze_bom_stock', {
      project_candidates: {
        items: [{ id: 7, code: 'RB-GRIPPER-EVT', name: '夹爪 EVT', status: 'active' }],
        count: 1,
        selected_project_id: 7,
      },
      bom_analysis: {
        project: { id: 7, code: 'RB-GRIPPER-EVT', name: '夹爪 EVT' },
        items: [
          {
            code: 'MAT-GRIP-001',
            mpn: 'DRV-GRIP-01',
            required_quantity: '8.0000',
            available_quantity: '12.0000',
            reserved_for_project: '2.0000',
            shortage: '0.0000',
          },
        ],
        count: 1,
        quantity_semantics: '项目 BOM 数量为项目总需求',
      },
    })
  }
  if (message.includes('低于安全库存')) {
    return response('以下物料需要关注。', 'low_stock', {
      low_stock: {
        count: 1,
        items: [
          {
            id: 51,
            code: 'MAT-LOW-001',
            mpn: 'LOW-STOCK-01',
            available_quantity: '2.0000',
            safety_stock: '5.0000',
          },
        ],
      },
    })
  }
  if (message.includes('48V 转 5V')) {
    return response('找到了最符合描述的模块。', 'find_location', {
      material_candidates: {
        items: [
          {
            ...material,
            id: 57,
            material_id: 57,
            code: 'MAT-DCDC-485',
            name: '48V 转 5V DC/DC 模块',
            mpn: 'DCDC-48V-5V-3A',
          },
        ],
        count: 1,
        exact_match_ids: [57],
        selected_material_id: 57,
      },
    })
  }
  if (message.includes('Atlas')) {
    return response('Atlas EVT-R1 的该 BOM 位有一条已批准备选。', 'product_bom_alternate', {
      product_bom_alternates: {
        items: [
          {
            id: 5,
            product: { id: 2, code: 'PROD-ATLAS', name: 'Atlas 控制板' },
            revision: { id: 4, revision: 'EVT-R1', status: 'released' },
            primary_material: { id: 9, code: 'MAT-CAN-A', mpn: 'MCP2562FD' },
            alternate_material: { id: 10, code: 'MAT-CAN-B', mpn: 'TCAN1044A' },
            status: 'approved',
            engineering_note: '仅适用于 Atlas EVT-R1 的 CAN 收发器 BOM 位。',
            usage_condition: '',
            unavailable_reasons: [],
          },
        ],
        count: 1,
        approved_count: 1,
        candidate_count: 0,
        scope_required: false,
      },
    })
  }
  if (message.includes('CAN 芯片')) {
    return response(
      '请告诉我具体的 CAN 芯片型号；如果要查某块板上已批准的备选，也请说明产品或版本。',
      'clarify_alternate_scope',
      {},
    )
  }
  return response('找到了物料。', 'find_location', {
    material_candidates: {
      items: [material],
      count: 1,
      exact_match_ids: [42],
      selected_material_id: 42,
    },
  })
}

async function mockApi(page: Page) {
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/auth/me')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(user) })
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
        body: JSON.stringify({ items: [], generated_at: '2026-09-01T00:00:00Z', source: 'database' }),
      })
      return
    }
    if (url.pathname.endsWith('/agent/query')) {
      const payload = route.request().postDataJSON() as { message: string }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(resultFor(payload.message)),
      })
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
}

async function query(page: Page, message: string) {
  await page.getByTestId('agent-query-input').fill(message)
  await page.getByTestId('agent-submit').click()
  await expect(page.getByTestId('agent-result')).toBeVisible()
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.clear())
  await mockApi(page)
  await page.goto('/agent')
})

test('renders rich Material and Cable cards with live inventory and actual location', async ({ page }) => {
  await query(page, 'XL2EL89COI-111YLC-25M 在哪里？')
  const materialCard = page.getByTestId('agent-material-result')
  await expect(materialCard).toContainText('可用库存')
  await expect(materialCard).toContainText('12')
  await expect(materialCard).toContainText(actualLocation.full_path)
  await expect(materialCard.getByRole('button', { name: '查看物料' })).toBeVisible()
  await expect(materialCard.getByRole('button', { name: '打开库位' })).toBeVisible()

  await query(page, '帮我找 PORTFOLIO-CBL-PF-00042')
  const cableCard = page.getByTestId('agent-cable-results')
  await expect(cableCard).toContainText('0.5 mm · 30 Pin · 15 cm')
  await expect(cableCard).toContainText('9 条')
  await expect(cableCard).toContainText('研发仓库 / 线缆柜 / R08')
  await expect(cableCard.getByRole('button', { name: '查看物料' })).toBeVisible()
  await expect(cableCard.getByRole('button', { name: '在线缆库查看' })).toBeVisible()
})

test('isolates explicit BOM and low-stock tasks from stale Material or Cable cards', async ({ page }) => {
  await query(page, 'XL2EL89COI-111YLC-25M 在哪里？')
  await query(page, 'RB-GRIPPER-EVT 的 BOM 库存够不够？')
  const result = page.getByTestId('agent-result')
  await expect(result).toContainText('夹爪 EVT · BOM 库存')
  await expect(result).not.toContainText('XL2EL89COI-111YLC-25M')
  await expect(result.getByTestId('agent-material-result')).toHaveCount(0)

  await query(page, '帮我找 PORTFOLIO-CBL-PF-00042')
  await query(page, '哪些物料低于安全库存？')
  await expect(result).toContainText('低库存物料（1）')
  await expect(result.getByTestId('agent-cable-results')).toHaveCount(0)
})

test('keeps fuzzy DC/DC location in business search instead of Evidence', async ({ page }) => {
  await query(page, '之前那个 48V 转 5V 的模块放哪儿了？名字我真记不住。')
  const result = page.getByTestId('agent-result')
  await expect(result.getByTestId('agent-material-result')).toContainText('DCDC-48V-5V-3A')
  await expect(result.getByTestId('agent-material-result')).toContainText(actualLocation.full_path)
  await expect(result.getByTestId('engineering-evidence-result')).toHaveCount(0)
})

test('CAN alternate is never an empty success and becomes Product/BOM scoped', async ({ page }) => {
  await query(page, '这个 CAN 芯片没货了，有没有能替它的？')
  const result = page.getByTestId('agent-result')
  await expect(result).toContainText('请告诉我具体的 CAN 芯片型号')
  await expect(result).not.toContainText('已核对项目')

  await query(page, '我说的是 Atlas 那块板上用的。')
  const alternates = result.getByTestId('agent-product-alternates')
  await expect(alternates).toContainText('PROD-ATLAS · EVT-R1')
  await expect(alternates).toContainText('MCP2562FD → TCAN1044A')
  await expect(alternates).toContainText('此 BOM 位已批准')
})
