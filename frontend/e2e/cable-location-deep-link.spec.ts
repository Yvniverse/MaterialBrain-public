import { expect, test, type Page } from '@playwright/test'

const materialId = 131
const drawerId = 1001
const actualPath = '研发仓库 / 线缆专区 / 100抽线缆柜 / A01'

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

const cable = {
  id: materialId,
  code: 'CBL-PF-00001',
  name: 'SH1.0-3P 双头端子线',
  custom_name: 'SH1.0-3P 双头端子线',
  model: 'SH1.0-3P',
  cable_kind: 'terminal',
  end_style: 'double',
  connector_a: 'SH1.0-3P',
  connector_b: 'SH1.0-3P',
  connector_pitch_mm: '1.0',
  direction: 'same',
  length_cm: '30.0',
  pin_count: 3,
  pin_count_b: 0,
  pin_layout: '',
  quantity: 20,
  reserved_quantity: 0,
  available_quantity: 20,
  unit_price: '0.0',
  storage_location: '研发仓库',
  actual_locations: [
    {
      location_id: drawerId,
      code: 'CABLE-RACK-01-A01',
      name: 'A01',
      full_path: actualPath,
      warehouse: '研发仓库',
      quantity: 20,
    },
  ],
  actual_location_status: 'complete',
  actual_location_quantity: 20,
  notes: 'Synthetic sample cable catalog item.',
  updated_at: '2026-08-31T00:00:00Z',
}

function location(id: number, parentId: number | null, code: string, name: string, type: string, fullPath: string, style: string | null = null) {
  return {
    id,
    parent_id: parentId,
    code,
    name,
    type,
    full_path: fullPath,
    manager: 'Demo Warehouse',
    notes: '',
    is_active: true,
    organizer_style: style,
    organizer_left_module: null,
    organizer_right_module: null,
    bin_material_name: '',
    bin_quantity: null,
    bin_content_notes: '',
    material_count: id === drawerId ? 1 : 0,
    quantity: id === drawerId ? '20' : '0',
    reserved_quantity: '0',
  }
}

const locations = [
  location(1, null, 'WH-RD', '研发仓库', 'warehouse', '研发仓库'),
  location(2, 1, 'PF-CABLE', '线缆专区', 'zone', '研发仓库 / 线缆专区'),
  location(3, 2, 'CABLE-RACK-01', '100抽线缆柜', 'box', '研发仓库 / 线缆专区 / 100抽线缆柜', 'drawer_rack_100'),
  ...Array.from({ length: 100 }, (_, index) => {
    const row = Math.floor(index / 5) + 1
    const column = String.fromCharCode(65 + (index % 5))
    const name = `${column}${String(row).padStart(2, '0')}`
    return location(
      drawerId + index,
      3,
      `CABLE-RACK-01-${name}`,
      name,
      'bin',
      `研发仓库 / 线缆专区 / 100抽线缆柜 / ${name}`,
    )
  }),
]

async function mockApi(page: Page) {
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/auth/me')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(user) })
      return
    }
    if (url.pathname === `/api/v1/cables/${materialId}`) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(cable) })
      return
    }
    if (url.pathname.endsWith('/cables')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [cable], total: 1, page: 1, page_size: 50,
          summary: { quantity: 20, available_quantity: 20, in_stock_types: 1, pitch_count: 1 },
          facets: { connector_pitches: ['1.0'], lengths: ['30.0'], pin_counts: [3], cable_kinds: ['terminal'], end_styles: ['double'] },
        }),
      })
      return
    }
    if (url.pathname.endsWith('/locations')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(locations) })
      return
    }
    if (url.pathname.endsWith('/materials')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [{
            id: materialId, code: cable.code, name: cable.name, mpn: cable.model,
            location_id: drawerId, quantity: '20', reserved_quantity: '0', available_quantity: '20',
            safety_stock: '5', notes: cable.notes,
          }],
          total: 1, page: 1, page_size: 200,
        }),
      })
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
}

test('focuses one seeded Cable and navigates to its actual visible drawer', async ({ page }) => {
  await mockApi(page)
  await page.goto(`/cables?focus=${materialId}`)
  const row = page.locator('.cable-table .focus-row')
  await expect(row).toContainText('CBL-PF-00001')
  await expect(row).toContainText('仓库：研发仓库')
  await expect(row).toContainText(`实际库位：${actualPath}`)
  await expect(row).toContainText('备注位置：研发仓库')

  await row.getByTestId('cable-actual-location-link').click()
  await expect(page).toHaveURL(new RegExp(`/locations\\?focus=${drawerId}$`))
  const drawer = page.locator(`[data-location-id="${drawerId}"]`)
  await expect(drawer).toBeVisible()
  await expect(drawer).toHaveClass(/highlighted/)
  await expect(drawer).toContainText('A01')
})
