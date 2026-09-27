import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import OrganizerBox3D, {
  type OrganizerLocation,
} from '../src/components/locations/OrganizerBox3D.vue'
import type { Material } from '../src/types'

const organizerSource = readFileSync(
  resolve(process.cwd(), 'src/components/locations/OrganizerBox3D.vue'),
  'utf8',
)

function createBins(): OrganizerLocation[] {
  return Array.from({ length: 7 }, (_, row) =>
    Array.from({ length: 8 }, (_, column) => {
      const name = `${String.fromCharCode(65 + row)}${String(column + 1).padStart(2, '0')}`
      return {
        id: row * 8 + column + 1,
        parent_id: 100,
        code: `BOX-01-${name}`,
        name,
        type: 'bin',
        full_path: `研发仓库 / 电阻盒 / ${name}`,
        manager: '',
        notes: '',
        is_active: true,
      }
    }),
  ).flat()
}

function createMixedBins(
  left: 'small' | 'large',
  right: 'small' | 'large',
): OrganizerLocation[] {
  return ([
    ['L', left],
    ['R', right],
  ] as const).flatMap(([side, module], sideIndex) => {
    const moduleCode = module === 'small' ? 'S' : 'L'
    const count = module === 'small' ? 28 : 8
    return Array.from({ length: count }, (_, index) => {
      const name = `${side}-${moduleCode}${String(index + 1).padStart(2, '0')}`
      return {
        id: 200 + sideIndex * 100 + index,
        parent_id: 100,
        code: `BOX-MIX-${name}`,
        name,
        type: 'bin',
        full_path: `研发仓库 / 混合盒 / ${name}`,
        manager: '',
        notes: '',
        is_active: true,
      }
    })
  })
}

const material: Material = {
  id: 1,
  code: 'MAT-R-001',
  name: '贴片电阻 10kΩ',
  category_id: null,
  location_id: 1,
  supplier_id: null,
  mpn: 'RC0603FR-0710KL',
  specification: '10kΩ ±1%',
  package: '0603',
  footprint: 'R_0603',
  manufacturer: '',
  unit: 'pcs',
  unit_price: '0',
  safety_stock: '10',
  target_stock: '100',
  quantity: '120',
  reserved_quantity: '0',
  available_quantity: '120',
  barcode: '',
  lifecycle_status: 'active',
  rohs_status: 'compliant',
  datasheet_url: '',
  tags: [],
  attributes: {},
  notes: '',
  is_active: true,
  created_at: '2026-07-29T00:00:00Z',
  updated_at: '2026-07-29T00:00:00Z',
}

describe('OrganizerBox3D', () => {
  it('renders 56 selectable compartments and exposes stored material information', async () => {
    const bins = createBins()
    Object.assign(bins[55], {
      bin_material_name: '功率继电器',
      bin_quantity: 8,
      bin_content_notes: '待测试',
    })
    const wrapper = mount(OrganizerBox3D, {
      props: {
        box: {
          id: 100,
          parent_id: null,
          code: 'BOX-01',
          name: '贴片电阻盒',
          type: 'box',
          full_path: '贴片电阻盒',
          manager: '',
          notes: '0603 电阻',
          is_active: true,
        },
        bins,
        materials: [material],
        selectedBinId: null,
        open: true,
        closing: false,
      },
    })

    const slots = wrapper.findAll('button.slot')
    expect(slots).toHaveLength(56)
    expect(slots[55].attributes('data-slot-label')).toBe('G08')
    expect(slots[55].attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('RC0603FR-0710KL')
    expect(wrapper.text()).toContain('120')
    expect(wrapper.text()).toContain('功率继电器')
    expect(wrapper.text()).toContain('8')
    expect(slots[55].attributes('aria-label')).toContain('待测试')
    expect(slots[55].find('.slot-notes').text()).toBe('待测试')

    await slots[0].trigger('click')
    expect(wrapper.emitted('select')?.[0]?.[0]).toEqual(bins[0])

    await slots[55].trigger('click')
    expect(wrapper.emitted('select')?.[1]?.[0]).toEqual(bins[55])
  })

  it('returns to the box overview from the lid action', async () => {
    const wrapper = mount(OrganizerBox3D, {
      props: {
        box: {
          id: 100,
          parent_id: null,
          code: 'BOX-01',
          name: '贴片电阻盒',
          type: 'box',
          full_path: '贴片电阻盒',
          manager: '',
          notes: '0603 电阻',
          is_active: true,
        },
        bins: createBins(),
        materials: [],
        selectedBinId: null,
        open: true,
        closing: false,
      },
    })

    const lidButton = wrapper.get('button.lid-toggle')
    expect(lidButton.text()).toContain('返回当前仓库')
    await lidButton.trigger('click')
    expect(wrapper.emitted('toggle')).toHaveLength(1)

    await wrapper.setProps({ closing: true })
    expect(lidButton.attributes('disabled')).toBeDefined()
    expect(lidButton.text()).toContain('正在合上')
  })

  it('renders a configurable 36-slot split box and emits half-module changes', async () => {
    const bins = createMixedBins('large', 'small')
    const wrapper = mount(OrganizerBox3D, {
      props: {
        box: {
          id: 100,
          parent_id: null,
          code: 'BOX-MIX',
          name: '36 格混合元件盒',
          type: 'box',
          full_path: '36 格混合元件盒',
          manager: '',
          notes: '',
          is_active: true,
          organizer_style: 'split_configurable',
          organizer_left_module: 'large',
          organizer_right_module: 'small',
        },
        bins,
        materials: [],
        selectedBinId: null,
        open: true,
        closing: false,
      },
    })

    const slots = wrapper.findAll('button.slot')
    expect(slots).toHaveLength(36)
    expect(slots[0].attributes('data-slot-label')).toBe('L-L01')
    expect(slots[7].attributes('data-slot-label')).toBe('L-L08')
    expect(slots[35].attributes('data-slot-label')).toBe('R-S28')
    expect(wrapper.text()).toContain('36 格平面库位图')
    expect(wrapper.findAll('.slot-section.module-large')).toHaveLength(1)
    expect(wrapper.findAll('.slot-section.module-small')).toHaveLength(1)

    await slots[35].trigger('click')
    expect(wrapper.emitted('select')?.[0]?.[0]).toEqual(bins[35])

    const leftSmallButton = wrapper.findAll('.module-control')[0].findAll('button')[0]
    await leftSmallButton.trigger('click')
    expect(wrapper.emitted('changeModule')?.[0]).toEqual(['left', 'small'])
  })

  it('uses a flat grid without perspective at every responsive breakpoint', () => {
    expect(organizerSource).toMatch(/\.scene\s*\{[^}]*height:\s*930px;[^}]*\}/)
    expect(organizerSource).toMatch(
      /@media\s*\(max-width:\s*900px\)[\s\S]*?\.scene\s*\{\s*height:\s*810px;/,
    )
    expect(organizerSource).toMatch(
      /@media\s*\(max-width:\s*640px\)[\s\S]*?\.scene\s*\{\s*height:\s*650px;/,
    )
    expect(organizerSource).not.toContain('rotateX(')
    expect(organizerSource).not.toContain('perspective(')
  })

  it('keeps the requested standard slot proportions and aligns mixed modules', () => {
    expect(organizerSource).toMatch(
      /\.standard-slot-grid \.section-grid\s*\{[^}]*grid-template-columns:\s*repeat\(8,/,
    )
    expect(organizerSource).toMatch(/\.slot\s*\{[^}]*height:\s*76px;/)
    expect(organizerSource).toMatch(
      /\.mixed-slot-grid \.module-small \.section-grid\s*\{[^}]*repeat\(4,[^}]*repeat\(7,/,
    )
    expect(organizerSource).toMatch(
      /\.mixed-slot-grid \.module-large \.section-grid\s*\{[^}]*repeat\(2,[^}]*repeat\(4,/,
    )
    expect(organizerSource).toMatch(
      /@media\s*\(max-width:\s*900px\)[\s\S]*?\.slot\s*\{\s*height:\s*68px;/,
    )
    expect(organizerSource).toMatch(
      /@media\s*\(max-width:\s*640px\)[\s\S]*?\.slot\s*\{\s*height:\s*60px;/,
    )
    expect(organizerSource).toMatch(/\.slot-code\s*\{[^}]*font-size:\s*11px;/)
    expect(organizerSource).toMatch(
      /\.slot-material\s*\{[^}]*top:\s*50%;[^}]*transform:\s*translateY\(-50%\);[^}]*font-size:\s*22px;[^}]*text-align:\s*center;/,
    )
    expect(organizerSource).toMatch(
      /\.mixed-slot-grid \.module-large \.slot-code\s*\{[^}]*font-size:\s*13px;/,
    )
    expect(organizerSource).toMatch(
      /\.mixed-slot-grid \.module-large \.slot-material\s*\{[^}]*font-size:\s*24px;/,
    )
    expect(organizerSource).toMatch(
      /\.slot-notes\s*\{[^}]*top:\s*calc\(50% \+ 15px\);[^}]*font-size:\s*10px;[^}]*text-align:\s*center;/,
    )
    expect(organizerSource).toMatch(
      /\.mixed-slot-grid \.module-large \.slot-notes\s*\{[^}]*font-size:\s*12px;/,
    )
  })
})
