import { createMemoryHistory, createRouter } from 'vue-router'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import BuildReadinessCard from '../src/components/agent/BuildReadinessCard.vue'
import BuildPlanPreview from '../src/components/agent/BuildPlanPreview.vue'
import type { BuildPlan } from '../src/types'
import type { BuildReadinessEntity } from '../src/types'

function readiness(sufficient: boolean): BuildReadinessEntity {
  return {
    product: { id: 8, code: 'PROD-ATLAS-AMR', name: 'Atlas AMR 移动底盘' },
    revision: { id: 12, revision: 'EVT-R2', status: 'released', is_default: true },
    project: null,
    build_quantity: sufficient ? 3 : 4,
    sufficient,
    shortage_count: sufficient ? 0 : 1,
    max_buildable_units: 3,
    safety_risk_count: sufficient ? 4 : 3,
    material_blocker_count: 0,
    quantity_semantics: 'Product BOM quantity_per_unit × build_quantity',
    read_only: true,
    items: [
      {
        material_id: 19,
        code: 'DEMO-MOTOR-DRV-48V-DUAL',
        name: '48V 双路电机驱动板',
        mpn: 'DRV-48V-DUAL',
        unit: 'pcs',
        quantity_per_unit: '1.0000',
        available_quantity: '3.0000',
        safety_stock: '1.0000',
        required_total: sufficient ? '3.0000' : '4.0000',
        reserved_for_project: '0',
        additional_reservation_required: sufficient ? '3.0000' : '4.0000',
        projected_free_available_after_build: '0',
        coverage: '3.0000',
        shortage: sufficient ? '0' : '1.0000',
        remaining_after_build: '0',
        below_safety_after_build: true,
        material_available_for_build: true,
        material_blocker: null,
        approved_alternates: [
          {
            alternate_id: 71,
            material_id: 29,
            code: 'DEMO-MOTOR-DRV-48V-ALT',
            name: '48V 双路电机驱动板备选',
            mpn: 'DRV-48V-DUAL-ALT',
            available_quantity: '100.0000',
            unit: 'pcs',
            usage_condition: '仅适用于此产品版本 BOM 位',
            scope: 'product_revision_bom_position',
            informational_only: true,
          },
        ],
      },
    ],
  }
}

async function mounted(result: BuildReadinessEntity) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/products', component: { template: '<div />' } }],
  })
  await router.push('/products')
  await router.isReady()
  return mount(BuildReadinessCard, {
    props: { result },
    global: {
      plugins: [router],
      stubs: { ElButton: { template: '<button><slot /></button>' } },
    },
  })
}

describe('Product build readiness presentation', () => {
  it('uses per-unit business terminology and keeps safety risk amber', async () => {
    const wrapper = await mounted(readiness(true))
    expect(wrapper.text()).toContain('当前库存可满足')
    expect(wrapper.text()).toContain('最大可立即构建：3 台')
    expect(wrapper.text()).toContain('构建后将低于安全库存')
    expect(wrapper.find('.safety-risk').exists()).toBe(true)
    expect(wrapper.find('.shortage-list').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('quantity_per_unit')
    expect(wrapper.text()).not.toContain('BuildReadinessService')
    expect(wrapper.text()).toContain('已批准的 BOM 位备选（仅供参考）')
    expect(wrapper.text()).toContain('未自动使用备选库存')
  })

  it('shows actual shortage separately in red and formats decimals', async () => {
    const wrapper = await mounted(readiness(false))
    expect(wrapper.classes()).toContain('insufficient')
    expect(wrapper.text()).toContain('4 台暂时备不齐')
    expect(wrapper.text()).toContain('缺 1 类物料')
    expect(wrapper.text()).toContain('需要 4 · 可覆盖 3 · 缺 1')
    expect(wrapper.find('.shortage-list').exists()).toBe(true)
    expect(wrapper.find('[data-testid="generate-build-plan"]').exists()).toBe(false)
  })

  it('only offers a project reservation plan when a linked project is ready', async () => {
    const result = readiness(true)
    result.project = { id: 5, code: 'SAMPLE-PROJ-RBX1', name: 'Robot X1 项目' }
    const wrapper = await mounted(result)
    const button = wrapper.get('[data-testid="generate-build-plan"]')
    await button.trigger('click')
    expect(wrapper.emitted('generatePlan')?.[0]).toEqual([result])

    result.material_blocker_count = 1
    result.items[0].material_available_for_build = false
    result.items[0].material_blocker = 'inactive'
    await wrapper.setProps({ result: { ...result, items: [...result.items] } })
    expect(wrapper.text()).toContain('暂时无法生成构建计划')
    expect(wrapper.find('[data-testid="generate-build-plan"]').exists()).toBe(false)
  })

  it('previews immutable plan quantities and makes approval boundary explicit', async () => {
    const plan: BuildPlan = {
      id: 31,
      plan_no: 'BP-20260830-000031',
      status: 'ready',
      product_revision_id: 12,
      project_id: 5,
      product: { id: 8, code: 'PROD-ATLAS-AMR', name: 'Atlas AMR 移动底盘' },
      revision: { id: 12, revision: 'EVT-R2', status: 'released' },
      project: { id: 5, code: 'SAMPLE-PROJ-RBX1', name: 'Robot X1 项目' },
      build_quantity: 3,
      product_bom_hash: 'a'.repeat(64),
      snapshot_hash: 'b'.repeat(64),
      reservation_proposal_id: null,
      stale_reason: '',
      items: [
        {
          material_id: 19,
          code: 'DEMO-MOTOR-DRV-48V-DUAL',
          name: '48V 双路电机驱动板',
          unit: 'pcs',
          quantity_per_unit: '2.0000',
          required_total: '6.0000',
          reserved_for_project_at_plan: '2.0000',
          additional_reservation_required: '4.0000',
          available_quantity_at_plan: '10.0000',
          projected_free_available_after_build: '4.0000',
          safety_stock_at_plan: '5.0000',
          below_safety_after_build: true,
          material_status_at_plan: 'active',
        },
      ],
    }
    const wrapper = mount(BuildPlanPreview, {
      props: { plan },
      global: {
        stubs: {
          ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
          ElTag: { template: '<span><slot /></span>' },
        },
      },
    })
    expect(wrapper.text()).toContain('当前项目已预留')
    expect(wrapper.text()).toContain('本次还需新增预留')
    expect(wrapper.text()).toContain('总需求 6')
    expect(wrapper.text()).toContain('库存尚未修改')
    expect(wrapper.text()).toContain('人工批准前不会改变库存')
    await wrapper.get('[data-testid="create-build-reservation-proposal"]').trigger('click')
    expect(wrapper.emitted('propose')?.[0]).toEqual([plan])
  })
})
