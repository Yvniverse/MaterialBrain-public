import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const materialsSource = readFileSync(resolve(process.cwd(), 'src/views/Materials.vue'), 'utf8')
const routerSource = readFileSync(resolve(process.cwd(), 'src/router/index.ts'), 'utf8')
const layoutSource = readFileSync(resolve(process.cwd(), 'src/layouts/AppLayout.vue'), 'utf8')

describe('material import migration', () => {
  it('moves the complete material import flow into material management', () => {
    expect(materialsSource).toContain('data-testid="toggle-material-import"')
    expect(materialsSource).toContain('data-testid="material-import-panel"')
    expect(materialsSource).toContain('accept=".csv,.xlsx,.xls"')
    expect(materialsSource).toContain("'/imports/materials/preview'")
    expect(materialsSource).toContain("'/imports/materials/commit'")
    expect(materialsSource).toContain('data-testid="commit-material-import"')
    expect(materialsSource).toContain('await load()')
  })

  it('guards import controls with import permission and keeps a downloadable template', () => {
    expect(materialsSource).toContain("auth.can('import:manage')")
    expect(materialsSource).toContain('downloadMaterialTemplate')
    expect(materialsSource).toContain('物料导入模板.csv')
    expect(materialsSource).toContain('按物料编码自动跳过')
  })

  it('removes the separate import and export page, route and sidebar entry', () => {
    expect(existsSync(resolve(process.cwd(), 'src/views/ImportExport.vue'))).toBe(false)
    expect(routerSource).not.toContain("path: 'data'")
    expect(routerSource).not.toContain("import('../views/ImportExport.vue')")
    expect(layoutSource).not.toContain("['/data','导入导出'")
  })
})
