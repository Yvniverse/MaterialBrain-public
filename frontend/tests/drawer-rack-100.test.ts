import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DrawerRack100 from '../src/components/locations/DrawerRack100.vue'
import type { OrganizerLocation } from '../src/components/locations/OrganizerBox3D.vue'

const rackSource = readFileSync(
  resolve(process.cwd(), 'src/components/locations/DrawerRack100.vue'),
  'utf8',
)

function createDrawers(): OrganizerLocation[] {
  return Array.from({ length: 20 }, (_, row) =>
    Array.from({ length: 5 }, (_, column) => {
      const name = `${String.fromCharCode(65 + column)}${String(row + 1).padStart(2, '0')}`
      return {
        id: row * 5 + column + 1,
        parent_id: 500,
        code: `RACK-01-${name}`,
        name,
        type: 'bin',
        full_path: `研发仓库 / 100抽货架 / ${name}`,
        manager: '',
        notes: `100抽货架 · 第 ${String(row + 1).padStart(2, '0')} 行`,
        is_active: true,
      }
    }),
  ).flat()
}

describe('DrawerRack100', () => {
  it('renders the physical 20-row by 5-column order and emits drawer selection', async () => {
    const bins = createDrawers()
    Object.assign(bins[99], {
      bin_material_name: 'M3 螺丝',
      bin_quantity: 50,
      bin_content_notes: '黑色',
    })
    const wrapper = mount(DrawerRack100, {
      props: {
        rack: {
          id: 500,
          parent_id: null,
          code: 'RACK-01',
          name: '结构件货架',
          type: 'box',
          full_path: '研发仓库 / 结构件货架',
          manager: '',
          notes: '螺丝与紧固件',
          is_active: true,
          organizer_style: 'drawer_rack_100',
        },
        bins,
        materials: [],
        selectedBinId: null,
        closing: false,
      },
    })

    const drawers = wrapper.findAll('button.rack-drawer')
    expect(drawers).toHaveLength(100)
    expect(drawers[0].attributes('data-drawer-label')).toBe('A01')
    expect(drawers[4].attributes('data-drawer-label')).toBe('E01')
    expect(drawers[5].attributes('data-drawer-label')).toBe('A02')
    expect(drawers[99].attributes('data-drawer-label')).toBe('E20')
    expect(drawers[99].attributes('aria-label')).toContain('M3 螺丝')
    expect(drawers[99].attributes('aria-label')).toContain('黑色')

    await drawers[99].trigger('click')
    expect(wrapper.emitted('select')?.[0]?.[0]).toEqual(bins[99])
  })

  it('finds drawers by material content without removing their physical positions', async () => {
    const bins = createDrawers()
    Object.assign(bins[42], {
      bin_material_name: '尼龙扎带',
      bin_quantity: 20,
      bin_content_notes: '白色 100mm',
    })
    const wrapper = mount(DrawerRack100, {
      props: {
        rack: {
          id: 500,
          parent_id: null,
          code: 'RACK-01',
          name: '耗材货架',
          type: 'box',
          full_path: '耗材货架',
          manager: '',
          notes: '',
          is_active: true,
          organizer_style: 'drawer_rack_100',
        },
        bins,
        materials: [],
        selectedBinId: null,
        closing: false,
      },
    })

    await wrapper.get('input[type="search"]').setValue('扎带')
    expect(wrapper.findAll('button.rack-drawer')).toHaveLength(100)
    expect(wrapper.findAll('button.rack-drawer.matched')).toHaveLength(1)
    expect(wrapper.findAll('button.rack-drawer.muted')).toHaveLength(99)
    expect(wrapper.text()).toContain('找到 1 个匹配抽屉')
  })

  it('uses five columns and four clearly separated five-row groups', () => {
    expect(rackSource).toMatch(
      /\.drawer-group\s*\{[\s\S]*?grid-template-columns:\s*repeat\(5,[\s\S]*?grid-template-rows:\s*repeat\(5,/,
    )
    expect(rackSource).toMatch(/\.drawer-grid\s*\{[\s\S]*?gap:\s*24px;/)
    expect(rackSource).toContain('.drawer-group + .drawer-group::before')
    expect(rackSource).toContain('const rowLabels = Array.from({ length: 20 }')
    expect(rackSource).toContain('const rowGroups = Array.from({ length: 4 }')
    expect(rackSource).toContain("const columnLabels = ['A', 'B', 'C', 'D', 'E']")
    expect(rackSource).toContain('--rack-width: 960px')
    expect(rackSource).toContain('--rack-grid-height: 1160px')
    expect(rackSource).toContain('--rack-width: 1160px')
    expect(rackSource).toContain('--rack-grid-height: 1380px')
    expect(rackSource).toContain('--rack-name-size: 16px')
    expect(rackSource).toContain("const viewSize = ref<'large' | 'xlarge'>('xlarge')")
  })
})
