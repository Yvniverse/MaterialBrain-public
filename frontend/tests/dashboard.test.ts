import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(process.cwd(), 'src/views/Dashboard.vue'), 'utf8')

describe('high-information dashboard', () => {
  it('prioritizes inventory status, today flow, trend and recent activity', () => {
    expect(source).toContain('data-testid="dashboard-hero"')
    expect(source).toContain('data-testid="today-flow"')
    expect(source).toContain('data-testid="inventory-trend"')
    expect(source).toContain('data-testid="recent-movements"')
    expect(source).toContain('库存可用率')
    expect(source).toContain('今日净变化')
    expect(source).not.toContain('低库存')
  })

  it('offers permission-aware links to the most common workspaces', () => {
    expect(source).toContain('data-testid="quick-actions"')
    expect(source).toContain("path: '/locations'")
    expect(source).toContain("path: '/cables'")
    expect(source).toContain("path: '/inventory'")
    expect(source).toContain("path: '/materials'")
    expect(source).toContain('.filter((item) => auth.can(item.permission))')
  })

  it('keeps quick actions large enough to read comfortably', () => {
    expect(source).toMatch(/\.quick-heading b\s*\{[^}]*font-size: 17px/)
    expect(source).toMatch(/\.quick-heading small\s*\{[^}]*font-size: 11px/)
    expect(source).toMatch(/\.quick-action\s*\{[^}]*min-height: 66px/)
    expect(source).toMatch(/\.quick-action b\s*\{[^}]*font-size: 13px/)
    expect(source).toMatch(/\.quick-action small\s*\{[^}]*font-size: 10px/)
  })

  it('supports live refresh, selectable trend ranges and responsive layouts', () => {
    expect(source).toContain('window.setInterval(() => void load(true), 30000)')
    expect(source).toContain("const chartRange = ref<7 | 14 | 30>(30)")
    expect(source).toContain("@media (max-width: 1020px)")
    expect(source).toContain("@media (max-width: 760px)")
  })
})
