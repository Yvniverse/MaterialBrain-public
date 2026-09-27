import { defineComponent } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ElMessageBox } from 'element-plus'
import ProductionWorkspace from '../src/components/ProductionWorkspace.vue'
import { api } from '../src/api/client'

vi.mock('../src/api/client', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
vi.mock('../src/stores/auth', () => ({ useAuthStore: () => ({ can: () => true }) }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {}, path: '/projects' }),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}))
const plan = {
  id: 1,
  plan_no: 'BP-PENDING',
  status: 'reservation_pending',
  pick_task_id: null,
  project: { id: 1, name: '项目' },
  product: { code: 'PROD', name: '产品' },
  revision: { revision: 'R1' },
  build_quantity: 1,
  snapshot_hash: 'a'.repeat(64),
}
const table = defineComponent({
  props: ['data'],
  emits: ['row-click'],
  template:
    '<div><button class="select-plan" @click="$emit(\'row-click\', data[0])">选择计划</button><slot /></div>',
})
const button = defineComponent({
  emits: ['click'],
  template: '<button @click="$emit(\'click\')"><slot /></button>',
})
async function openWorkspace() {
  const wrapper = mount(ProductionWorkspace, {
    global: {
      stubs: {
        ElTable: table,
        ElButton: button,
        ElTableColumn: true,
        ElInput: true,
        ElInputNumber: true,
        ElSelect: true,
        ElOption: true,
        ElDialog: true,
        ElForm: true,
        ElFormItem: true,
        BuildPlanPreview: true,
        BuildReadinessCard: true,
        WarehouseRouteMap: true,
      },
    },
  })
  await flushPromises()
  await wrapper.find('.select-plan').trigger('click')
  await flushPromises()
  return wrapper
}
describe('production reservation recovery', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.mocked(api.get).mockImplementation(async (url) => ({
      data: String(url).startsWith('/picking/readiness')
        ? { executable: false, items: [] }
        : url === '/build-plans'
          ? [plan]
          : [],
    }))
    vi.mocked(api.post).mockResolvedValue({ data: { fully_reserved: false, proposal: { id: 9 } } })
  })
  it('resumes a pending plan after refresh and explicitly approves its existing proposal', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue(
      'confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>,
    )
    const wrapper = await openWorkspace()
    const action = wrapper.findAll('button').find((b) => b.text() === '申请 / 审批预留')!
    expect(action).toBeDefined()
    await action.trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/agent/proposals/9/approve')
    wrapper.unmount()
  })
  it('cancelling the confirmation keeps the proposal pending without approving inventory', async () => {
    vi.mocked(api.post).mockClear()
    vi.spyOn(ElMessageBox, 'confirm').mockRejectedValue('cancel')
    const wrapper = await openWorkspace()
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '申请 / 审批预留')!
      .trigger('click')
    await flushPromises()
    expect(vi.mocked(api.post).mock.calls.some(([url]) => String(url).endsWith('/approve'))).toBe(
      false,
    )
    expect(wrapper.text()).toContain('申请 / 审批预留')
    wrapper.unmount()
  })
})
