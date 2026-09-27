import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ElMessageBox } from 'element-plus'
import PickingOperator from '../src/views/PickingOperator.vue'
import { api } from '../src/api/client'

vi.mock('../src/api/client', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { taskId: '7' } }),
  useRouter: () => ({ push: vi.fn() }),
}))

const twin = defineComponent({
  props: ['taskId', 'focusLocationId', 'embedded'],
  template: '<div data-testid="twin-stub">{{ taskId }}:{{ focusLocationId }}</div>',
})
const button = defineComponent({
  emits: ['click'],
  props: ['disabled'],
  template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
})
const input = defineComponent({
  inheritAttrs: false,
  emits: ['update:modelValue', 'keyup'],
  props: ['modelValue'],
  setup(_, { expose }) {
    expose({ focus: vi.fn() })
  },
  template:
    '<input data-testid="input-stub" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @keyup.enter="$emit(\'keyup\', $event)" />',
})
type TestAllocation = {
  id: number
  location_id: number
  location_code: string
  material_code: string
  [key: string]: unknown
}
type TestStation = {
  route_sequence: number
  route_node_code: string
  station_index: number
  status: string
  completed_allocations: number
  remaining_allocations: number
  allocations: TestAllocation[]
  [key: string]: unknown
}
type TestState = {
  task: Record<string, unknown>
  station_groups: TestStation[]
  current_station: TestStation | null
  current_allocation: TestAllocation | null
  progress: Record<string, number>
  events: unknown[]
}
const state: TestState = {
  task: {
    id: 7,
    pick_task_no: 'PK-7',
    status: 'ready',
    warehouse_map_id: 2,
    warehouse_graph_hash: 'a'.repeat(64),
    route_distance_m: '12.4',
    route_constraints: { closed_edge_codes: [] },
    allocations: [],
  },
  station_groups: [
    {
      route_sequence: 1,
      route_node_code: 'PF-PORT-IC-100',
      station_index: 1,
      status: 'current',
      completed_allocations: 0,
      remaining_allocations: 2,
      allocations: [
        {
          id: 11,
          location_id: 101,
          location_code: 'PORT-IC-100-A03',
          full_path: '研发仓 / IC 与通信芯片 100 抽 / A03',
          material_code: 'PORT-MDRV-DRV8323',
          material_name: '三相无刷栅极驱动器',
          mpn: 'DRV8323RH',
          planned_quantity: '2',
          picked_quantity: '0',
          remaining_quantity: '2',
          route_sequence: 1,
          route_node_code: 'PF-PORT-IC-100',
          status: 'pending',
        },
        {
          id: 12,
          location_id: 102,
          location_code: 'PORT-IC-100-B03',
          full_path: '研发仓 / IC 与通信芯片 100 抽 / B03',
          material_code: 'PORT-MDRV-TMC5160',
          material_name: '步进电机驱动器',
          mpn: 'TMC5160A-TA',
          planned_quantity: '1',
          picked_quantity: '0',
          remaining_quantity: '1',
          route_sequence: 1,
          route_node_code: 'PF-PORT-IC-100',
          status: 'pending',
        },
      ],
    },
  ],
  current_station: null,
  current_allocation: null,
  progress: { stations_total: 1, stations_completed: 0, allocations_total: 2, allocations_completed: 0 },
  events: [],
}
state.current_station = state.station_groups[0]
state.current_allocation = state.station_groups[0].allocations[0]

async function setup() {
  const wrapper = mount(PickingOperator, {
    global: {
      stubs: {
        WarehouseTwin: twin,
        ElButton: button,
        ElInput: input,
        ElInputNumber: true,
        ElProgress: true,
        ElSelect: true,
        ElOption: true,
        ElEmpty: true,
        ElResult: true,
      },
    },
  })
  await flushPromises()
  return wrapper
}

describe('Guided Picking operator flow', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(ElMessageBox.prompt).mockResolvedValue({ value: '扫码枪故障，已现场核对标签' } as never)
    vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm' as never)
    vi.mocked(api.get).mockResolvedValue({ data: structuredClone(state) })
    vi.mocked(api.post).mockImplementation(async (url, payload) => {
      const scanPayload = (payload || {}) as {
        location_token?: string
        material_token?: string
      }
      if (String(url).endsWith('/validate-scan')) {
        const locationMatch = scanPayload.location_token === 'PORT-IC-100-A03'
        const materialMatch = scanPayload.material_token
          ? scanPayload.material_token === 'PORT-MDRV-DRV8323'
          : null
        return {
          data: {
            allocation_id: 11,
            location_match: locationMatch,
            material_match: materialMatch,
            ready: locationMatch && materialMatch === true,
            expected_location_code: 'PORT-IC-100-A03',
            expected_material_code: 'PORT-MDRV-DRV8323',
            material_name: '三相无刷栅极驱动器',
            remaining_quantity: '2',
          },
        }
      }
      return { data: {} }
    })
  })

  it('renders one physical station with multiple exact drawer allocations', async () => {
    const wrapper = await setup()
    expect(wrapper.text()).toContain('同一设备内还有 2 个格口待取')
    expect(wrapper.text()).toContain('PORT-IC-100-A03')
    expect(wrapper.text()).toContain('PORT-IC-100-B03')
    expect(wrapper.get('[data-testid="twin-stub"]').text()).toBe('7:101')
    wrapper.unmount()
  })

  it('requires location then material validation before enabling settlement', async () => {
    const wrapper = await setup()
    const scan = wrapper.find('input[data-testid="input-stub"]')
    await scan.setValue('PORT-IC-100-A03')
    await scan.trigger('keyup.enter')
    await flushPromises()
    expect(wrapper.text()).toContain('扫描物料：PORT-MDRV-DRV8323')

    await scan.setValue('PORT-MDRV-DRV8323')
    await scan.trigger('keyup.enter')
    await flushPromises()
    expect(wrapper.text()).toContain('扫描已核对，可以确认取料')
    expect(api.post).not.toHaveBeenCalledWith(
      '/pick-allocations/11/confirm',
      expect.anything(),
    )
    wrapper.unmount()
  })

  it('wrong location is rejected by the read-only validator and never confirms stock', async () => {
    const wrapper = await setup()
    const scan = wrapper.find('input[data-testid="input-stub"]')
    await scan.setValue('WRONG-A01')
    await scan.trigger('keyup.enter')
    await flushPromises()
    expect(wrapper.text()).toContain('库位不匹配')
    expect(vi.mocked(api.post).mock.calls.some(([url]) => String(url).endsWith('/confirm'))).toBe(
      false,
    )
    wrapper.unmount()
  })

  it('manual override requires an explicit reason and sends it with manual confirmation', async () => {
    const wrapper = await setup()
    await wrapper.get('[data-testid="operator-manual-override"]').trigger('click')
    await flushPromises()
    expect(ElMessageBox.prompt).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('人工核对模式')

    await wrapper.get('[data-testid="operator-confirm-pick"]').trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith(
      '/pick-allocations/11/confirm',
      expect.objectContaining({
        confirmation_method: 'manual',
        manual_override_reason: '扫码枪故障，已现场核对标签',
        location_token: 'PORT-IC-100-A03',
        material_token: 'PORT-MDRV-DRV8323',
      }),
    )
    wrapper.unmount()
  })

})
