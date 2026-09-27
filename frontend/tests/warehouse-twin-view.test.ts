import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'
import WarehouseTwin from '../src/views/WarehouseTwin.vue'
import { api } from '../src/api/client'
import { mountWarehouseUI } from '../src/components/locations/digitalTwin/ui.js'
import fixture from './fixtures/twin_snapshot.json'

vi.mock('../src/api/client', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
vi.mock('../src/components/locations/digitalTwin/ui.js', () => ({ mountWarehouseUI: vi.fn() }))
const dispose = vi.fn()
const snapshot = (id: number) => ({ ...fixture, map: { ...fixture.map, id } })
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(mountWarehouseUI).mockReturnValue({ dispose } as unknown as ReturnType<typeof mountWarehouseUI>)
  vi.mocked(api.post).mockResolvedValue({ data: fixture.route })
})
async function setup(query = '?map=1') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/warehouse-twin', component: WarehouseTwin }] })
  await router.push('/warehouse-twin' + query)
  const wrapper = mount(WarehouseTwin, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}
it('clears the previous scene immediately and discards an older map response', async () => {
  let resolveOld!: (value: unknown) => void
  vi.mocked(api.get).mockImplementation(async url => {
    if (url.includes('/2/')) return new Promise(resolve => { resolveOld = resolve })
    return { data: snapshot(url.includes('/3/') ? 3 : 1) }
  })
  const { wrapper, router } = await setup()
  await router.push('/warehouse-twin?map=2')
  await flushPromises()
  expect(dispose).toHaveBeenCalledOnce()
  await router.push('/warehouse-twin?map=3')
  await flushPromises()
  resolveOld({ data: snapshot(2) })
  await flushPromises()
  expect(vi.mocked(mountWarehouseUI).mock.calls.map(call => call[1].map.id)).toEqual([1, 3])
  wrapper.unmount()
})
it('removes old content on failure and shows the error without offline substitution', async () => {
  vi.mocked(api.get).mockResolvedValueOnce({ data: snapshot(1) }).mockRejectedValueOnce(new Error('读取失败'))
  const { wrapper, router } = await setup()
  await router.push('/warehouse-twin?map=2')
  await flushPromises()
  expect(dispose).toHaveBeenCalledOnce()
  expect(wrapper.get('[role=alert]').text()).toContain('读取失败')
  expect(mountWarehouseUI).toHaveBeenCalledOnce()
  wrapper.unmount()
})
it('uses the task frozen map and route without requesting a new route', async () => {
  const task = { id: 9, warehouse_map_id: 7, warehouse_graph_hash: fixture.map.graph_hash, route_plan: fixture.route }
  vi.mocked(api.get).mockImplementation(async url => ({ data: url.startsWith('/pick-tasks') ? task : snapshot(7) }))
  const { wrapper } = await setup('?task=9&map=1')
  expect(api.get).toHaveBeenCalledWith('/warehouse-maps/7/twin-snapshot')
  expect(api.post).not.toHaveBeenCalled()
  expect(vi.mocked(mountWarehouseUI).mock.calls[0][1].route).toEqual(task.route_plan)
  wrapper.unmount()
})
