import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ProductBomPreviewCard from '../src/components/agent/ProductBomPreviewCard.vue'
import type { ProductBomPreviewEntity } from '../src/types'

const preview: ProductBomPreviewEntity = {
  workflow: 'product_bom_preview',
  target_product: { id: 8, code: 'PROD-ATLAS-AMR', name: 'Atlas AMR' },
  target_revision: { id: 12, revision: 'EVT-R2', status: 'released', is_default: true },
  source_engineering_draft: {
    status: 'reviewable',
    focus_scope: 'primary',
    row_count: 3,
    completeness: { required_roles: 3 },
  },
  lines: [
    {
      action: 'add',
      role: 'bootstrap capacitor',
      requirement_id: 'bootstrap-capacitor',
      material_code: 'C84703',
      mpn: 'CL05B222KB5NNNC',
      draft_quantity: '1.0000',
      existing_quantity_per_unit: null,
      reason: '预览 ADD。',
      evidence_refs: [],
    },
    {
      action: 'update_quantity',
      role: 'controller',
      requirement_id: 'controller',
      material_code: 'CTRL-01',
      mpn: 'CTRL-MPN',
      draft_quantity: '2.0000',
      existing_quantity_per_unit: '1.0000',
      reason: '预览数量更新。',
      evidence_refs: [],
    },
    {
      action: 'unresolved',
      role: 'inductor',
      requirement_id: 'inductor',
      reason: '仍需工程选择。',
      evidence_refs: [],
    },
  ],
  summary: {
    add_count: 1,
    update_quantity_count: 1,
    no_change_count: 0,
    unresolved_count: 1,
    complete_for_apply_preview: false,
    blocking_reasons: ['仍需工程选择。'],
  },
  unresolved: [{ requirement_id: 'inductor', role: 'inductor', reason: '仍需工程选择。' }],
  read_only: true,
  automatic_write: false,
  formal_product_bom_modified: false,
  quantity_semantics: 'ProductBomItem.quantity_per_unit；不是库存或构建数量。',
}

describe('Product BOM Preview presentation', () => {
  it('shows read-only actions, per-unit quantities, and unresolved reasons', () => {
    const wrapper = mount(ProductBomPreviewCard, {
      props: { preview },
      global: {
        stubs: {
          ElTag: { template: '<span><slot /></span>' },
        },
      },
    })

    expect(wrapper.find('[data-testid="product-bom-preview"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Product BOM Preview')
    expect(wrapper.text()).toContain('仅预览 · 未写入正式 BOM')
    expect(wrapper.text()).toContain('ADD 1')
    expect(wrapper.text()).toContain('UPDATE 1')
    expect(wrapper.text()).toContain('UNRESOLVED 1')
    expect(wrapper.text()).toContain('CL05B222KB5NNNC')
    expect(wrapper.text()).toContain('仍需工程选择。')
    expect(wrapper.text()).toContain('不是库存或构建数量')
    expect(wrapper.find('button').exists()).toBe(false)
  })
})
