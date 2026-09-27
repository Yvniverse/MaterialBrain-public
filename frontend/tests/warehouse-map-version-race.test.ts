import { defineComponent } from 'vue'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import WarehouseMapAdmin from '../src/components/locations/WarehouseMapAdmin.vue'
import { api } from '../src/api/client'

vi.mock('../src/api/client', () => ({ api: { get: vi.fn(), put: vi.fn() } }))
const button = defineComponent({
  emits: ['click'],
  template: '<button @click="$emit(\'click\')"><slot /></button>',
})
const maps = [1, 2, 3].map((id) => ({
  id,
  code: `MAP-${id}`,
  name: `地图 ${id}`,
  version: String(id),
  status: id === 1 ? 'active' : 'draft',
  calibration_status: 'demo_synthetic',
  graph_hash: String(id).repeat(64),
  width_m: 12,
  height_m: 8,
  default_start_node_code: 'PACK',
  nodes: [],
  edges: [],
  organizer_bindings: [],
}))
function deferred() {
  let resolve!: (value: { data: (typeof maps)[number] }) => void
  const promise = new Promise<{ data: (typeof maps)[number] }>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
async function setup() {
  const pending = { 2: deferred(), 3: deferred() }
  vi.mocked(api.get).mockImplementation(async (url) => {
    if (url === '/warehouse-maps') return { data: maps }
    if (url === '/locations') return { data: [] }
    if (url === '/warehouse-maps/2') return pending[2].promise
    if (url === '/warehouse-maps/3') return pending[3].promise
    return { data: maps[0] }
  })
  const wrapper = shallowMount(WarehouseMapAdmin, {
    props: { warehouseId: 1, canManage: true },
    global: {
      stubs: {
        ElButton: button,
        ElEmpty: true,
        ElAlert: true,
        ElInput: true,
        ElInputNumber: true,
        ElOption: true,
        ElSelect: true,
        ElTableColumn: true,
        ElTable: true,
        ElSwitch: true,
      },
      directives: { loading: () => {} },
    },
  })
  await flushPromises()
  return { wrapper, pending }
}
describe('map version detail races', () => {
  it('hides editing until the selected draft detail has arrived', async () => {
    const { wrapper, pending } = await setup()
    await wrapper.findAll('.version-card')[1].trigger('click')
    expect(wrapper.text()).not.toContain('编辑草稿')
    pending[2].resolve({ data: maps[1] })
    await flushPromises()
    expect(wrapper.find('.map-toolbar').text()).toContain('MAP-2')
    expect(wrapper.text()).toContain('编辑草稿')
    wrapper.unmount()
  })
  it('discards a delayed earlier version response after another version is selected', async () => {
    const { wrapper, pending } = await setup()
    await wrapper.findAll('.version-card')[1].trigger('click')
    await wrapper.findAll('.version-card')[2].trigger('click')
    pending[3].resolve({ data: maps[2] })
    await flushPromises()
    pending[2].resolve({ data: maps[1] })
    await flushPromises()
    expect(wrapper.find('.map-toolbar').text()).toContain('MAP-3')
    expect(wrapper.find('.map-toolbar').text()).not.toContain('MAP-2')
    wrapper.unmount()
  })
})
