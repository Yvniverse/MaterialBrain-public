import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const cablesSource = readFileSync(resolve(process.cwd(), 'src/views/Cables.vue'), 'utf8')
const routerSource = readFileSync(resolve(process.cwd(), 'src/router/index.ts'), 'utf8')
const layoutSource = readFileSync(resolve(process.cwd(), 'src/layouts/AppLayout.vue'), 'utf8')

describe('Cable management page', () => {
  it('provides required presets and accepts custom pitch, length and pin values', () => {
    expect(cablesSource).toContain(
      "const pitchPresets = ['0.8', '1.0', '1.25', '2.0', '2.54']",
    )
    expect(cablesSource).toContain("const lengthPresets = ['10', '15', '20', '30', '50']")
    expect(cablesSource).toContain('同向')
    expect(cablesSource).toContain('反向')
    expect(cablesSource.match(/allow-create/g)?.length).toBeGreaterThanOrEqual(3)
    expect(cablesSource).toContain('Pin 数必须是大于 0 的整数')
  })

  it('supports combined search and quick stock changes through dedicated APIs', () => {
    expect(cablesSource).toContain("api.get<CablePage>('/cables'")
    expect(cablesSource).toContain('params.connector_pitch_mm')
    expect(cablesSource).toContain('params.direction')
    expect(cablesSource).toContain('params.length_cm')
    expect(cablesSource).toContain('params.pin_count')
    expect(cablesSource).toContain('`/cables/${row.id}/quantity`')
    expect(cablesSource).toContain('idempotency_key: uuidKey()')
    expect(cablesSource).toContain('修改数量会自动产生库存流水')
  })

  it('analyzes and confirms XLS, XLSX and CSV cable order imports in place', () => {
    expect(cablesSource).toContain('accept=".xlsx,.xls,.csv"')
    expect(cablesSource).toContain("'/cables/import/preview'")
    expect(cablesSource).toContain("'/cables/import/commit'")
    expect(cablesSource).toContain('规格已经自动分析，可在下表直接修改后再入库')
    expect(cablesSource).toContain('重复上传同一订单时，已导入明细会自动跳过')
    expect(cablesSource).toContain('确认导入并入库')
  })

  it('is available from both the application route and sidebar', () => {
    expect(routerSource).toContain("path: 'cables'")
    expect(routerSource).toContain("import('../views/Cables.vue')")
    expect(layoutSource).toMatch(
      /\['\/cables',\s*'线缆管理',\s*Connection,\s*'material:view'\]/,
    )
  })

  it('supports Agent deep-link focus with row highlight and canonical quantity formatting', () => {
    expect(cablesSource).toContain('route.query.focus')
    expect(cablesSource).toContain('`/cables/${id}`')
    expect(cablesSource).toContain(':row-class-name="cableRowClass"')
    expect(cablesSource).toContain('scrollIntoView')
    expect(cablesSource).toContain('formatQuantity(row.quantity)')
  })

  it('separates actual InventoryLot locations from descriptive legacy notes', () => {
    expect(cablesSource).toContain('row.actual_locations')
    expect(cablesSource).toContain('实际库位：尚未分配')
    expect(cablesSource).toContain('实际库位：{{ location.full_path }}')
    expect(cablesSource).toContain('备注位置：{{ row.storage_location }}')
    expect(cablesSource).toContain("path: '/locations'")
  })
})
