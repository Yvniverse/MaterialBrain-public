import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../src/api/client'
import OrganizerBox3D from '../src/components/locations/OrganizerBox3D.vue'
import DrawerRack100 from '../src/components/locations/DrawerRack100.vue'
import ShelfRack6 from '../src/components/locations/ShelfRack6.vue'
import Locations from '../src/views/Locations.vue'

const locationsSource = readFileSync(resolve(process.cwd(), 'src/views/Locations.vue'), 'utf8')

const { routerPush, routeQuery } = vi.hoisted(() => ({
  routerPush: vi.fn(),
  routeQuery: {} as Record<string, string>,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
  useRoute: () => ({ query: routeQuery }),
}))

const locations = [
  {
    id: 1,
    parent_id: null,
    code: 'WH-RD',
    name: '研发仓库',
    type: 'warehouse',
    full_path: '研发仓库',
    manager: '',
    notes: '电子元件与研发物料',
    is_active: true,
    material_count: 0,
    quantity: '0',
    reserved_quantity: '0',
  },
  {
    id: 2,
    parent_id: null,
    code: 'WH-RACK',
    name: '存储货架',
    type: 'area',
    full_path: '存储货架',
    manager: '',
    notes: '大型物料仓储区',
    is_active: true,
    material_count: 0,
    quantity: '0',
    reserved_quantity: '0',
  },
  {
    id: 100,
    parent_id: 1,
    code: 'BOX-01',
    name: '0402 容阻',
    type: 'box',
    full_path: '研发仓库 / 0402 容阻',
    manager: '',
    notes: '贴片电阻与电容',
    is_active: true,
    material_count: 0,
    quantity: '0',
    reserved_quantity: '0',
  },
  {
    id: 200,
    parent_id: 2,
    code: 'BOX-02',
    name: '连接器',
    type: 'box',
    full_path: '存储货架 / 连接器',
    manager: '',
    notes: '排针与端子',
    is_active: true,
    material_count: 0,
    quantity: '0',
    reserved_quantity: '0',
  },
]

describe('Locations warehouse overview', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    routerPush.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('opens warehouses first, then equipment, and only loads materials after equipment selection', async () => {
    const getSpy = vi
      .spyOn(api, 'get')
      .mockResolvedValueOnce({ data: locations })
      .mockResolvedValue({ data: { items: [], total: 0, page: 1, page_size: 200 } })

    const wrapper = shallowMount(Locations, {
      global: {
        plugins: [createPinia()],
        directives: { loading: {} },
        stubs: {
          ElAlert: true,
          ElButton: true,
          ElDialog: true,
          ElDrawer: true,
          ElEmpty: true,
          ElForm: true,
          ElFormItem: true,
          ElInput: true,
          ElInputNumber: true,
          ElOption: true,
          ElRadioButton: true,
          ElRadioGroup: true,
          ElSelect: true,
          ElSwitch: true,
          ElTree: true,
          RouterLink: true,
        },
      },
    })
    await flushPromises()

    const library = wrapper.get('[data-testid="box-library"]')
    expect(library.classes()).toContain('is-overview')
    expect(library.classes()).toContain('is-warehouse-overview')
    expect(wrapper.find('[data-testid="box-workspace"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid="warehouse-card"]')).toHaveLength(2)
    expect(wrapper.findAll('button.box-card')).toHaveLength(0)
    expect(wrapper.text()).toContain('全部仓库')
    expect(wrapper.text()).toContain('2 个大仓库')
    expect(getSpy).toHaveBeenCalledTimes(1)
    expect(getSpy.mock.calls[0]?.[0]).toBe('/locations')

    await wrapper.findAll('[data-testid="warehouse-card"]')[1].trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('存储货架 · 库位总览')
    expect(wrapper.findAll('button.box-card')).toHaveLength(1)
    expect(wrapper.find('[data-testid="box-workspace"]').exists()).toBe(false)
    expect(getSpy).toHaveBeenCalledTimes(1)

    await wrapper.get('button.box-card').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="box-workspace"]').exists()).toBe(true)
    expect(getSpy).toHaveBeenCalledTimes(2)
    expect(getSpy.mock.calls[1]?.[0]).toBe('/materials')
    expect(wrapper.getComponent(OrganizerBox3D).props('box')).toMatchObject({
      id: 200,
      name: '连接器',
    })

    vi.useFakeTimers()
    window.dispatchEvent(new Event('locations:show-overview'))
    await flushPromises()
    expect(wrapper.getComponent(OrganizerBox3D).props('closing')).toBe(true)

    await vi.advanceTimersByTimeAsync(320)
    await flushPromises()
    expect(wrapper.get('[data-testid="box-library"]').classes()).toContain('is-overview')
    expect(wrapper.get('[data-testid="box-library"]').classes()).toContain('is-warehouse-overview')
    expect(wrapper.findAll('[data-testid="warehouse-card"]')).toHaveLength(2)
    expect(wrapper.findAll('button.box-card')).toHaveLength(0)
    expect(wrapper.find('[data-testid="box-workspace"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('edits lightweight bin content inline and never routes to a separate material form', () => {
    expect(locationsSource).toContain('物料名称（必填）')
    expect(locationsSource).toContain('数量（选填）')
    expect(locationsSource).toContain('备注（选填）')
    expect(locationsSource).toContain('停止输入约 0.7 秒后自动更新')
    expect(locationsSource).toContain('`/locations/${binId}/content`')
    expect(locationsSource).toContain('selectedBinHasAnyContent')
    expect(locationsSource).toContain('仅解除与该${unitLabel(selectedBox.value)}的关联')
    expect(locationsSource).toContain('await loadLocations()')
    expect(locationsSource).not.toContain('/materials/new')
    expect(locationsSource).not.toContain('createMaterialInSelectedBin')
  })

  it('offers a guarded large-box deletion action', () => {
    expect(locationsSource).toContain('删除大盒')
    expect(locationsSource).toContain('removeOrganizer(selectedBox)')
    expect(locationsSource).toContain('removeOrganizer(data)')
    expect(locationsSource).toContain('`/locations/organizers/${box.id}`')
    expect(locationsSource).toContain('仍有物料，请先逐${unitLabel(box)}清空内容后再删除')
  })

  it('includes the 20-row by 5-column rack as a persistent organizer style', () => {
    expect(locationsSource).toContain("'drawer_rack_100'")
    expect(locationsSource).toContain('20 行 × 5 列')
    expect(locationsSource).toContain('100 抽零件货架')
    expect(locationsSource).toContain('A01 → E01')
    expect(locationsSource).toContain('A20 → E20')
    expect(DrawerRack100).toBeTruthy()
  })

  it('includes a persistent six-level rack with dynamic boxes and multi-item contents', () => {
    expect(locationsSource).toContain("'shelf_rack_6'")
    expect(locationsSource).toContain('六层箱式货架')
    expect(locationsSource).toContain('每层可放多个箱子')
    expect(locationsSource).toContain('/locations/shelf-racks/${selectedBox.value.id}/shelves/')
    expect(locationsSource).toContain('/locations/shelf-boxes/${selectedShelfBox.value.id}/items')
    expect(locationsSource).toContain('一个箱子可以保存多种物料')
    expect(ShelfRack6).toBeTruthy()
  })
})
