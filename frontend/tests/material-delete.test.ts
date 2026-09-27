import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const materialsSource = readFileSync(resolve(process.cwd(), 'src/views/Materials.vue'), 'utf8')
const detailSource = readFileSync(
  resolve(process.cwd(), 'src/views/MaterialDetail.vue'),
  'utf8',
)

describe('material deletion controls', () => {
  it('exposes a guarded delete action in the material list', () => {
    expect(materialsSource).toContain('type="danger"')
    expect(materialsSource).toContain('@click="removeMaterial(row)"')
    expect(materialsSource).toContain('await api.delete(`/materials/${row.id}`)')
    expect(materialsSource).toContain('仍有库存或预留')
    expect(materialsSource).toContain('历史库存流水仍会保留')
  })

  it('exposes the same delete action on the detail page', () => {
    expect(detailSource).toContain('删除物料')
    expect(detailSource).toContain('@click="removeMaterial"')
    expect(detailSource).toContain('await api.delete(`/materials/${row.id}`)')
    expect(detailSource).toContain("await router.push('/materials')")
  })
})
