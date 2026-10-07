import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Dashboard from '../src/views/Dashboard.vue'
import { api } from '../src/api/client'
import { useAuthStore } from '../src/stores/auth'
import type { User } from '../src/types'
const chart = vi.hoisted(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }))
vi.mock('echarts', () => ({ init: () => chart }))
const summary = {
  material_count: 4,
  quantity: '100',
  reserved_quantity: '20',
  available_quantity: '80',
  inventory_value: '250',
  out_of_stock_count: 1,
  today_inbound: '10',
  today_outbound: '5',
  recent_movements: [
    {
      id: 1,
      movement_no: 'MV-001',
      material_id: 2,
      material_name: '控制器',
      material_mpn: 'STM32F405RGT6',
      operation_type: 'inbound',
      quantity_delta: '10',
      created_at: '2026-10-08T08:00:00Z',
    },
  ],
  trend: Array.from({ length: 30 }, (_, i) => ({
    date: '2026-10-' + String(i + 1).padStart(2, '0'),
    inbound: '10',
    outbound: '5',
  })),
}
async function setup() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.user = {
    id: 1,
    full_name: '工程师',
    role: { permissions: ['dashboard:view', 'material:view'] },
  } as User
  auth.initialized = true
  const router = createRouter({
    history: createMemoryHistory(),
    routes: ['/dashboard', '/agent', '/materials', '/locations', '/warehouse-twin', '/cables'].map(
      (path) => ({ path, component: { template: '<div />' } }),
    ),
  })
  await router.push('/dashboard')
  const wrapper = mount(Dashboard, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, router }
}
describe('Glacier live dashboard', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api, 'get').mockResolvedValue({ data: summary })
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        disconnect() {}
      },
    )
    vi.stubGlobal('matchMedia', () => ({ matches: false }))
  })
  it('renders inventory and movements returned by the backend', async () => {
    const { wrapper } = await setup()
    expect(api.get).toHaveBeenCalledWith('/dashboard/summary')
    expect(wrapper.findAll('.g-metric-number').map((node) => node.text())).toEqual([
      '4种',
      '80件',
      '20件',
      '250元',
    ])
    expect(wrapper.get('.g-activity').text()).toContain('MV-001')
    expect(wrapper.get('.g-activity').text()).toContain('STM32F405RGT6')
    expect(wrapper.get('.g-activity').text()).toContain('采购入库')
    expect(wrapper.text()).toContain('75%')
    wrapper.unmount()
  })
  it('changes the real trend range and keeps permission-aware navigation', async () => {
    const { wrapper, router } = await setup()
    await wrapper.get('.g-trend .g-segmented button').trigger('click')
    expect(chart.setOption.mock.lastCall?.[0].series[0].data).toHaveLength(7)
    expect(wrapper.findAll('.g-utility-actions button').map((node) => node.text())).not.toContain(
      '库存操作',
    )
    await wrapper.get('.g-start-panel button').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/agent')
    wrapper.unmount()
  })
  it('retains the last successful data when refresh fails', async () => {
    const { wrapper } = await setup()
    vi.mocked(api.get).mockRejectedValueOnce(new Error('unavailable'))
    await wrapper.get('.g-page-heading .g-btn').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role=status]').text()).toContain('保留上次数据')
    expect(wrapper.get('.g-metric-number').text()).toBe('4种')
    wrapper.unmount()
  })
})
