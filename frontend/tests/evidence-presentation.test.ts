import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../src/api/client'
import EvidenceResultCard from '../src/components/agent/EvidenceResultCard.vue'
import MaterialEvidencePanel from '../src/components/evidence/MaterialEvidencePanel.vue'
import type {
  ComponentEvidenceComparisonEntity,
  EngineeringEvidenceEntity,
  EvidenceCitation,
} from '../src/types'

const citation: EvidenceCitation = {
  document_id: 1,
  document_key: 'SYN-CAN-B-RB',
  document_title: 'Synthetic CAN-FD Transceiver B Verification Sheet',
  document_revision: 'Rev B',
  document_status: 'current',
  page: 3,
  section: '3. Current Pinout',
  anchor_id: 13,
  excerpt: 'Rev B documents Pin 5 as VIO.',
  file_sha256: 'a'.repeat(64),
  page_text_sha256: 'b'.repeat(64),
  synthetic_fixture: true,
}

afterEach(() => vi.restoreAllMocks())

describe('Phase 2.4 engineering evidence presentation', () => {
  it('renders page-level citations without an ordinary-query governance banner', () => {
    const evidence: EngineeringEvidenceEntity = {
      material_scope: [],
      query: 'current Pin 5',
      include_superseded: false,
      facts: [{ field: 'pin', number: 5, name: 'VIO', anchor_id: 13 }],
      citations: [citation],
      allowed_anchor_ids: [13],
      evidence_coverage: 'supported',
      conclusion: '证据已定位',
      automatic_decision: false,
      read_only: true,
    }
    const wrapper = mount(EvidenceResultCard, {
      props: { evidence },
      global: {
        stubs: { ElTag: { template: '<span><slot /></span>' } },
      },
    })

    expect(wrapper.text()).toContain('VIO')
    expect(wrapper.text()).toContain('Rev B')
    expect(wrapper.text()).toContain('p.3')
    expect(wrapper.text()).toContain('3. Current Pinout')
    expect(wrapper.text()).toContain('合成 CI 测试证据，并非真实厂商数据手册')
    expect(wrapper.text()).not.toContain('不会自动验证、批准或撤销')
  })

  it('renders numeric pins and companion VIO facts with human-facing labels', () => {
    const evidence: EngineeringEvidenceEntity = {
      material_scope: [],
      query: 'MCP2562FD 的 5 脚到底是什么？',
      include_superseded: false,
      facts: [
        { field: 'pin', number: 5, name: 'VIO', anchor_id: 7 },
        { field: 'interface', values: ['CAN-FD'], anchor_id: 7 },
        { field: 'supply_voltage', min: 1.8, max: 5.5, unit: 'V', anchor_id: 7 },
        { field: 'purpose', value: 'VIO 为数字 I/O 供电并提供内部电平转换', anchor_id: 7 },
      ],
      citations: [{ ...citation, document_id: 42, page: 1, anchor_id: 7, synthetic_fixture: false }],
      allowed_anchor_ids: [7],
      evidence_coverage: 'supported',
      conclusion: '证据已定位',
      automatic_decision: false,
      read_only: true,
    }
    const wrapper = mount(EvidenceResultCard, {
      props: { evidence },
      global: { stubs: { ElTag: { template: '<span><slot /></span>' } } },
    })

    expect(wrapper.text()).toContain('Pin 5')
    expect(wrapper.text()).toContain('VIO')
    expect(wrapper.text()).toContain('接口')
    expect(wrapper.text()).toContain('CAN-FD')
    expect(wrapper.text()).toContain('供电范围')
    expect(wrapper.text()).toContain('1.8–5.5 V')
    expect(wrapper.text()).toContain('作用')
  })

  it('resets an expanded citation when a new evidence query reuses the same anchor', async () => {
    const evidence: EngineeringEvidenceEntity = {
      material_scope: [],
      query: 'MCP2562FD 的 5 脚到底是什么？',
      include_superseded: false,
      facts: [{ field: 'pin', number: 5, name: 'VIO', anchor_id: 7 }],
      citations: [{ ...citation, anchor_id: 7, synthetic_fixture: false }],
      allowed_anchor_ids: [7],
      evidence_coverage: 'supported',
      conclusion: '证据已定位',
      automatic_decision: false,
      read_only: true,
    }
    const wrapper = mount(EvidenceResultCard, {
      props: { evidence },
      global: { stubs: { ElTag: { template: '<span><slot /></span>' } } },
    })

    ;(wrapper.get('details').element as HTMLDetailsElement).open = true
    expect((wrapper.get('details').element as HTMLDetailsElement).open).toBe(true)

    await wrapper.setProps({ evidence: { ...evidence, query: '给我看依据。' } })
    expect((wrapper.get('details').element as HTMLDetailsElement).open).toBe(false)
  })

  it('links real citations to the authenticated original PDF at the cited page', () => {
    const evidence: EngineeringEvidenceEntity = {
      material_scope: [],
      query: 'MCP2562FD Pin 5',
      include_superseded: false,
      facts: [{ field: 'pin', name: 'VIO', anchor_id: 7 }],
      citations: [{ ...citation, document_id: 42, page: 3, anchor_id: 7, synthetic_fixture: false }],
      allowed_anchor_ids: [7],
      evidence_coverage: 'supported',
      conclusion: '证据已定位',
      automatic_decision: false,
      read_only: true,
    }
    const wrapper = mount(EvidenceResultCard, {
      props: { evidence },
      global: { stubs: { ElTag: { template: '<span><slot /></span>' } } },
    })

    const link = wrapper.get('a.document-link')
    expect(link.text()).toContain('打开原始数据手册')
    expect(link.attributes('href')).toBe('/api/v1/evidence-documents/42/file#page=3')
    expect(link.attributes('target')).toBe('_blank')
  })

  it('preserves units for scalar engineering evidence facts', () => {
    const evidence: EngineeringEvidenceEntity = {
      material_scope: [],
      query: 'INA240A1 增益是多少',
      include_superseded: false,
      facts: [{ field: 'gain', value: 20, unit: 'V/V', anchor_id: 21 }],
      citations: [{ ...citation, anchor_id: 21, synthetic_fixture: false }],
      allowed_anchor_ids: [21],
      evidence_coverage: 'supported',
      conclusion: '证据已定位',
      automatic_decision: false,
      read_only: true,
    }
    const wrapper = mount(EvidenceResultCard, {
      props: { evidence },
      global: { stubs: { ElTag: { template: '<span><slot /></span>' } } },
    })

    expect(wrapper.text()).toContain('20 V/V')
  })

  it('renders a grounded purpose fact with a user-facing label', () => {
    const evidence: EngineeringEvidenceEntity = {
      material_scope: [],
      query: '为什么 MCP2562FD 有 VIO？',
      include_superseded: false,
      facts: [{ field: 'purpose', value: 'VIO 为数字 I/O 供电并提供内部电平转换', anchor_id: 7 }],
      citations: [{ ...citation, document_id: 42, page: 1, anchor_id: 7, synthetic_fixture: false }],
      allowed_anchor_ids: [7],
      evidence_coverage: 'supported',
      conclusion: '证据已定位',
      automatic_decision: false,
      read_only: true,
    }
    const wrapper = mount(EvidenceResultCard, {
      props: { evidence },
      global: { stubs: { ElTag: { template: '<span><slot /></span>' } } },
    })

    expect(wrapper.text()).toContain('作用')
    expect(wrapper.text()).toContain('VIO 为数字 I/O 供电并提供内部电平转换')
  })

  it('renders derived power and thermal conditions instead of raw field names only', () => {
    const evidence: EngineeringEvidenceEntity = {
      material_scope: [],
      query: 'TLV761 12V 转 3.3V 800mA 功耗、封装和散热条件',
      include_superseded: false,
      facts: [
        {
          field: 'power_dissipation',
          value: 6.96,
          unit: 'W',
          fact_type: 'derived_calculation',
          calculation: '(12 - 3.3) × 0.8 = 6.96 W',
          conditions: { vin_v: 12, vout_v: 3.3, load_current_a: 0.8 },
          anchor_id: 61,
        },
        {
          field: 'thermal_resistance',
          value: { DCY_SOT223: 95.4, KVU_TO252: 67.2 },
          unit: 'degC/W',
          conditions: 'RθJA 为结至环境；热阻表封装标签',
          anchor_id: 60,
        },
      ],
      citations: [],
      allowed_anchor_ids: [60, 61],
      evidence_coverage: 'supported',
      conclusion: '证据已定位',
      automatic_decision: false,
      read_only: true,
    }
    const wrapper = mount(EvidenceResultCard, {
      props: { evidence },
      global: { stubs: { ElTag: { template: '<span><slot /></span>' } } },
    })

    expect(wrapper.text()).toContain('功耗')
    expect(wrapper.text()).toContain('6.96 W（派生计算：(12 - 3.3) × 0.8 = 6.96 W')
    expect(wrapper.text()).toContain('vin_v=12；vout_v=3.3；load_current_a=0.8')
    expect(wrapper.text()).toContain('热阻')
    expect(wrapper.text()).toContain('DCY_SOT223=95.4；KVU_TO252=67.2 degC/W')
    expect(wrapper.text()).toContain('条件：RθJA 为结至环境；热阻表封装标签')
  })

  it('shows differences and preserves unknown instead of claiming compatibility', () => {
    const comparison: ComponentEvidenceComparisonEntity = {
      materials: [],
      comparisons: [
        {
          field: 'pin_5',
          first: 'VREF',
          second: 'VIO',
          result: 'different',
          first_anchor_ids: [2],
          second_anchor_ids: [13],
        },
        {
          field: 'package',
          first: null,
          second: null,
          result: 'unknown',
          first_anchor_ids: [],
          second_anchor_ids: [],
        },
      ],
      differences: [],
      unknowns: ['package'],
      citations: [citation],
      conclusion: '当前证据不支持引脚兼容',
      pin_compatible_supported: false,
      automatic_decision: false,
      read_only: true,
    }
    const wrapper = mount(EvidenceResultCard, {
      props: {
        comparison,
        evidence: {
          material_scope: [],
          query: 'third device',
          include_superseded: false,
          facts: [],
          citations: [
            {
              ...citation,
              anchor_id: 14,
              document_title: 'Third vendor datasheet',
              synthetic_fixture: false,
            },
          ],
          allowed_anchor_ids: [14],
          evidence_coverage: 'supported',
          conclusion: '证据已定位',
          automatic_decision: false,
          read_only: true,
        },
      },
      global: {
        stubs: { ElTag: { template: '<span><slot /></span>' } },
      },
    })

    expect(wrapper.text()).toContain('VREF')
    expect(wrapper.text()).toContain('VIO')
    expect(wrapper.text()).toContain('不同')
    expect(wrapper.text()).toContain('证据不足')
    expect(wrapper.text()).toContain('当前证据不支持引脚兼容')
    expect(wrapper.text()).toContain('Third vendor datasheet')
  })

  it('loads only current material documents by default', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValueOnce({
      data: {
        items: [
          {
            id: 1,
            document_key: citation.document_key,
            scope_type: 'material',
            material_id: 9,
            product_revision_id: null,
            title: citation.document_title,
            manufacturer: 'MaterialBrain Synthetic Lab',
            document_revision: 'Rev B',
            document_date: '2026-08-30',
            original_filename: 'SYN_CAN_B_REV_B.pdf',
            file_sha256: citation.file_sha256,
            page_count: 4,
            status: 'current',
            supersedes_document_id: 2,
            ingest_status: 'ready',
            synthetic_fixture: true,
          },
        ],
      },
    })

    const wrapper = mount(MaterialEvidencePanel, {
      props: { materialId: 9 },
      global: {
        directives: { loading: () => undefined },
        stubs: {
          ElTag: { template: '<span><slot /></span>' },
          ElSwitch: true,
          ElEmpty: true,
        },
      },
    })
    await vi.waitFor(() => expect(get).toHaveBeenCalled())
    await flushPromises()

    expect(get).toHaveBeenCalledWith('/materials/9/evidence-documents', {
      params: { include_history: false },
    })
    expect(wrapper.text()).toContain('当前版本')
    expect(wrapper.text()).toContain('默认只显示当前版本')
    expect(wrapper.text()).toContain('合成 CI 测试证据')
  })
})
