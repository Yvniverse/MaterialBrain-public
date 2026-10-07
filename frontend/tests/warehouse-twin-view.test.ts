import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'
import WarehouseTwin from '../src/views/WarehouseTwin.vue'
import GlacierWarehouse from '../src/glacier/components/GlacierWarehouse.vue'
import { api } from '../src/api/client'
import fixture from './fixtures/twin_snapshot.json'
vi.mock('../src/api/client', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
const snapshot = (id: number) => ({ ...fixture, map: { ...fixture.map, id } })
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.post).mockResolvedValue({ data: fixture.route })
})
async function setup(query = '?map=1') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/warehouse-twin', component: WarehouseTwin }],
  })
  await router.push('/warehouse-twin' + query)
  const wrapper = mount(WarehouseTwin, {
    global: {
      plugins: [router],
      stubs: {
        GlacierWarehouse: {
          props: ['snapshot', 'allowRoutePreview', 'hideHeading'],
          template: '<div data-testid="actual-map">{{ snapshot.map.id }}</div>',
        },
        EmbodiedTwin: { template: '<div data-testid="lab" />' },
        ElSelect: true,
        ElOption: true,
      },
    },
  })
  await flushPromises()
  return { wrapper, router }
}
it('clears previous scene and discards an older map response', async () => {
  let resolveOld!: (value: unknown) => void
  vi.mocked(api.get).mockImplementation(async (url) => {
    if (url.includes('/2/'))
      return new Promise((resolve) => {
        resolveOld = resolve
      })
    return { data: snapshot(url.includes('/3/') ? 3 : 1) }
  })
  const { wrapper, router } = await setup()
  expect(wrapper.get('[data-testid=actual-map]').text()).toBe('1')
  await router.push('/warehouse-twin?map=2')
  await flushPromises()
  expect(wrapper.find('[data-testid=actual-map]').exists()).toBe(false)
  await router.push('/warehouse-twin?map=3')
  await flushPromises()
  resolveOld({ data: snapshot(2) })
  await flushPromises()
  expect(wrapper.getComponent(GlacierWarehouse).props('snapshot')!.map.id).toBe(3)
  wrapper.unmount()
})
it('removes old scene on map failure without substituting offline data', async () => {
  vi.mocked(api.get)
    .mockResolvedValueOnce({ data: snapshot(1) })
    .mockRejectedValueOnce(new Error('读取失败'))
  const { wrapper, router } = await setup()
  await router.push('/warehouse-twin?map=2')
  await flushPromises()
  expect(wrapper.find('[data-testid=actual-map]').exists()).toBe(false)
  expect(wrapper.get('[role=alert]').text()).toContain('读取失败')
  wrapper.unmount()
})
it('uses the task frozen map and route and disables free route preview', async () => {
  const task = {
    id: 9,
    warehouse_map_id: 7,
    warehouse_graph_hash: fixture.map.graph_hash,
    route_plan: fixture.route,
  }
  vi.mocked(api.get).mockImplementation(async (url) => ({
    data: url.startsWith('/pick-tasks') ? task : snapshot(7),
  }))
  const { wrapper } = await setup('?task=9&map=1&workspace=robot-lab')
  expect(api.get).toHaveBeenCalledWith('/warehouse-maps/7/twin-snapshot')
  expect(api.post).not.toHaveBeenCalled()
  expect(wrapper.getComponent(GlacierWarehouse).props('snapshot')!.route).toEqual(task.route_plan)
  expect(wrapper.getComponent(GlacierWarehouse).props('allowRoutePreview')).toBe(false)
  expect(wrapper.find('[data-testid=lab]').exists()).toBe(false)
  wrapper.unmount()
})
it('switches both warehouse modes through the same page without loading lab as a live map', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: snapshot(1) })
  const { wrapper, router } = await setup('?workspace=robot-lab')
  expect(api.get).not.toHaveBeenCalled()
  expect(wrapper.find('[data-testid=lab]').exists()).toBe(true)
  await wrapper.get('[data-testid=warehouse-mode-live]').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.query.workspace).toBeUndefined()
  expect(wrapper.find('[data-testid=lab]').exists()).toBe(false)
  wrapper.unmount()
})
