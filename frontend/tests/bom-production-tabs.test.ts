import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('BOM / 生产 workspace shell', () => {
  it('keeps exactly the two requested truth-domain tabs', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components/BomProductionTabs.vue'), 'utf8')
    expect(source).toContain("label: '产品定义'")
    expect(source).toContain("label: '项目 / 生产任务'")
    expect(source).not.toContain("label: '拣货'")
    expect(source).toContain("产品定义 = 单台工程 BOM")
    expect(source).toContain("BuildPlan、预留与备料执行")
  })

  it('embeds the shared workspace tabs in both existing deep-link pages', () => {
    const products = readFileSync(resolve(process.cwd(), 'src/views/Products.vue'), 'utf8')
    const projects = readFileSync(resolve(process.cwd(), 'src/views/Projects.vue'), 'utf8')
    expect(products).toContain('<BomProductionTabs />')
    expect(projects).toContain('<BomProductionTabs />')
  })
})
