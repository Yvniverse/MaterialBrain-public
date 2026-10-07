import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'
import AgentResultCard from '../src/components/agent/AgentResultCard.vue'
import AgentTimeline from '../src/components/agent/AgentTimeline.vue'
import EvidenceResultCard from '../src/components/agent/EvidenceResultCard.vue'
import PowerArchitectureOptions from '../src/components/agent/PowerArchitectureOptions.vue'
import type { AgentQueryResponse } from '../src/types'
import { renderSafeMarkdown } from '../src/utils/safeMarkdown'
import { formatQuantity } from '../src/utils/format'
import {
  FLOATING_AGENT_SAFE_MARGIN,
  clampFloatingAgentSize,
  computeFloatingAgentPlacement,
} from '../src/utils/floatingAgentPlacement'

const partialResult: AgentQueryResponse = {
  answer: '找到了。该物料当前库存 30，主库位是研发仓库。',
  narrative: '',
  intent: 'find_location',
  entities: {
    locations: {
      material_id: 17,
      code: 'MAT-017',
      name: '连接器',
      mpn: 'KHA-TEST',
      locations: [
        {
          location_id: 1,
          code: 'RD',
          name: '研发仓库',
          full_path: '研发仓库',
          organizer_id: null,
          organizer_style: null,
          parent_id: null,
          quantity_at_location: null,
          quantity_is_exact: false,
        },
      ],
      count: 1,
      material_quantity: '30.0000',
      lot_quantity_total: '0.0000',
      unallocated_quantity: '30.0000',
      distribution_status: 'partial',
    },
  },
  grounded_facts: [
    {
      kind: 'inventory',
      source_tool: 'get_inventory_availability',
      entity_id: 17,
      field: 'quantity',
      value: '30.0000',
      unit: '件',
      label: '账面库存',
    },
  ],
  tool_events: [
    { tool: 'search_materials', status: 'success', summary: '找到物料', duration_ms: 8 },
    { tool: 'find_material_locations', status: 'success', summary: '找到库位', duration_ms: 10 },
  ],
  ui_actions: [],
  proposal_ids: [],
  telemetry: [],
  execution_mode: 'deterministic',
  model_call_count: 0,
  request_id: '282893fb-90c5-4510-a986-123456789abc',
  conversation_id: 'conversation-partial',
}

const componentResult: AgentQueryResponse = {
  ...partialResult,
  answer: '找到了 1 个工程候选器件。',
  entities: {
    component_search: {
      query: { raw_text: '找 5V CAN transceiver', replacement_intent: true },
      count: 1,
      candidate_only: true,
      read_only: true,
      engineering_caveat: '仅返回候选相似器件，需要工程验证。',
      candidates: [
        {
          material_id: 88,
          code: 'PORT-CAN-TCAN1044',
          name: 'CAN transceiver',
          mpn: 'TCAN1044AVDRQ1',
          specification: '5V CAN-FD',
          package: 'SOIC-8',
          manufacturer: 'Texas Instruments',
          match_reasons: ['匹配：CAN transceiver', '匹配：5V 供电'],
          hard_constraint_matches: ['匹配：CAN transceiver', '匹配：5V 供电'],
          soft_preference_matches: [],
          metadata_confidence: 'high',
          technical_claims_allowed: true,
          engineering_verification_required: true,
          validated_relations: [
            {
              relation_id: 7,
              relation_type: 'similar_to',
              status: 'validated',
              language: '已验证工程关系',
              related_material: {
                id: 89,
                code: 'PORT-CAN-SN65',
                name: 'CAN transceiver candidate',
                mpn: 'SN65HVD230DR',
                package: 'SOIC-8',
                manufacturer: 'Texas Instruments',
              },
              evidence_summary: '工程师核对了功能类别和电气边界。',
              global_replacement_approved: false,
            },
          ],
          inventory: {
            material_id: 88,
            code: 'PORT-CAN-TCAN1044',
            name: 'CAN transceiver',
            mpn: 'TCAN1044AVDRQ1',
            unit: 'pcs',
            available_quantity: '76.0000',
          },
          locations: {
            material_id: 88,
            code: 'PORT-CAN-TCAN1044',
            name: 'CAN transceiver',
            mpn: 'TCAN1044AVDRQ1',
            locations: [
              {
                location_id: 99,
                code: 'CAN-01',
                name: 'CAN 仓位',
                full_path: '研发仓库 / CAN 区 / 01',
                organizer_id: null,
                organizer_style: null,
                parent_id: null,
                quantity_at_location: '80',
                quantity_is_exact: true,
              },
            ],
            count: 1,
            material_quantity: '80',
            lot_quantity_total: '80',
            unallocated_quantity: '0',
            distribution_status: 'complete',
          },
        },
      ],
    },
  },
}

describe('Phase 1.9 Agent presentation', () => {
  it('formats decimal quantities without changing their stored precision', () => {
    expect(formatQuantity('30.0000')).toBe('30')
    expect(formatQuantity('30.5000')).toBe('30.5')
    expect(formatQuantity('30.2500')).toBe('30.25')
  })

  it('uses the structured low-stock result as the single primary list', () => {
    const lowStock: AgentQueryResponse = {
      ...partialResult,
      answer: '当前共有 2 项低库存物料：\n- DEMO-A：可用 2.0000，安全库存 10.0000。',
      entities: {
        low_stock: {
          count: 2,
          items: [
            {
              material_id: 1,
              code: 'DEMO-A',
              name: 'Demo A',
              mpn: 'DEMO-A',
              unit: 'pcs',
              quantity: '2.0000',
              reserved_quantity: '0.0000',
              available_quantity: '2.0000',
              safety_stock: '10.0000',
            },
          ],
        },
      },
    }
    const wrapper = mount(AgentResultCard, {
      props: { result: lowStock },
      global: { stubs: { ElTable: true, ElTableColumn: true } },
    })

    expect(wrapper.find('.answer').exists()).toBe(false)
    expect(wrapper.text()).toContain('低库存物料（2）')
    expect(wrapper.text()).not.toContain('当前共有 2 项低库存物料')
  })

  it('keeps developer vocabulary out of the normal workbench copy', () => {
    const sources = [
      'src/views/AgentWorkbench.vue',
      'src/components/agent/AgentConsole.vue',
      'src/components/agent/AgentTimeline.vue',
    ]
      .map((path) => readFileSync(resolve(process.cwd(), path), 'utf8'))
      .join('\n')
    for (const forbidden of [
      'NATURAL LANGUAGE WAREHOUSE CONTROL',
      'EXECUTION TRACE',
      'WAREHOUSE AGENT',
      'VERIFIED FACTS',
      'PHYSICAL LOCATIONS',
      'Safe execution',
      'LLM 无数据库写权限',
    ]) {
      expect(sources).not.toContain(forbidden)
    }
  })

  it('keeps governance banners out of ordinary structured cards', () => {
    const sources = [
      'src/components/agent/EvidenceResultCard.vue',
      'src/components/agent/CableResultCard.vue',
    ]
      .map((path) => readFileSync(resolve(process.cwd(), path), 'utf8'))
      .join('\n')
    expect(sources).not.toContain('只读证据不会自动验证')
    expect(sources).not.toContain('长度用于偏好排序，不代表自动兼容或替代批准')
  })

  it('renders safe Markdown blocks and escapes raw HTML and unsafe links', () => {
    const html = renderSafeMarkdown(
      '## 结论\n\n| 电源轨 | 电流 |\n| --- | --- |\n| 模拟 | 50mA |\n\n<script>alert(1)</script>\n\n[unsafe](javascript:alert(1)) [datasheet](https://www.ti.com/lit/ds/example.pdf)',
    )

    expect(html).toContain('<h2>结论</h2>')
    expect(html).toContain('<table>')
    expect(html).toContain('<td>50mA</td>')
    expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;')
    expect(html).not.toContain('<script>')
    expect(html).not.toContain('href="javascript:')
    const backslashLink = renderSafeMarkdown('[backslash](/\\\\evil.example)')
    expect(backslashLink).not.toContain('href="/\\\\evil.example"')
    expect(html).toContain('href="https://www.ti.com/lit/ds/example.pdf"')
    expect(html).toContain('rel="noopener noreferrer"')
  })

  it('does not repeat a structured power answer when its model narrative was rejected', () => {
    const result = {
      ...partialResult,
      answer: '12V 转 3.3V 的完整工程草案会在结构化卡片中展示。',
      narrative: '',
      execution_mode: 'llm_assisted',
      entities: {
        power_design: {
          workflow: 'power_design',
          topology_first: true,
          request: '12V 转 3.3V 的供电方案',
          status: 'needs_constraints',
          requirements: {
            input_voltage_v: '12',
            output_voltage_v: '3.3',
            load_current_a: null,
            load_current_min_a: null,
            load_current_max_a: null,
            load_current_range_a: null,
            load_current_cases_a: [],
            topology_constraints: [],
            location_requested: false,
          },
          missing_constraints: [],
          topologies: [],
          load_case_calculations: [],
          rail_bom_draft: {
            status: 'needs_selection',
            selected_topology: null,
            rails: [],
            manual_review: [],
            read_only: true,
            automatic_write: false,
          },
          branches: [],
          evidence_reconciliation: [],
          read_only: true,
        },
      },
    } as AgentQueryResponse
    const wrapper = mount(AgentResultCard, {
      props: { result },
      global: {
        stubs: {
          ElTag: true,
          PowerArchitectureOptions: true,
        },
      },
    })

    expect(wrapper.find('[data-testid="power-design-result"]').exists()).toBe(true)
    expect(wrapper.find('.answer').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('完整工程草案会在结构化卡片中展示')
  })

  it('shows a shared LDO calculation note once across topology cards', () => {
    const note = '理想效率仅按 Vout/Vin；静态电流单独列出，不把典型值伪装成全温保证值。'
    const stage = (stage_id: string) => ({
      stage_id,
      topology: 'ldo',
      input_voltage_v: '5',
      output_voltage_v: '3.3',
      load_current_a: '0.1',
      current_basis: 'user_total',
      loss_w: '0.17',
      ideal_efficiency: '0.66',
      quiescent_current_a: null,
      quiescent_input_power_w: null,
      thermal_screen: {},
      loss_status: 'calculated',
      headroom_v: '1.7',
      dropout_status: 'verify_at_load',
      candidate_devices: [],
      notes: [note],
    })
    const topologies = ['buck_ldo', 'split_rails'].map((topology) => ({
      topology,
      label: topology,
      availability: 'conceptual',
      selected_by_user: false,
      total_load_current_a: '0.1',
      rails: [],
      stages: [stage(`${topology}-ldo`)],
      summary: '只读架构比较。',
      constraints: [],
    }))
    const wrapper = mount(PowerArchitectureOptions, {
      props: { topologies: topologies as never },
      global: { stubs: { ElTag: { template: '<span><slot /></span>' } } },
    })

    expect(wrapper.text().match(/理想效率仅按 Vout\/Vin/g)).toHaveLength(1)
  })

  it('shows a deterministic power answer first and collapses duplicate candidate details', () => {
    const answer = '100mA时，12V直驱 LDO 损耗0.87W；5V后级 LDO 损耗0.17W。'
    const result = {
      ...partialResult,
      answer,
      narrative: answer,
      execution_mode: 'deterministic',
      entities: {
        power_design: {
          status: 'supported',
          requirements: { input_voltage_v: '12', output_voltage_v: '3.3' },
          missing_constraints: [],
          topologies: [],
          load_case_calculations: [],
          rail_bom_draft: {},
          branches: [],
        },
      },
    } as unknown as AgentQueryResponse
    const wrapper = mount(AgentResultCard, {
      props: { result },
      global: { stubs: { ElTag: true, ElAlert: true, PowerArchitectureOptions: true } },
    })

    expect(wrapper.findAll('.answer')).toHaveLength(1)
    expect(wrapper.get('.answer').text()).toBe(answer)
    expect(wrapper.get('.power-branch-details').attributes('open')).toBeUndefined()
  })

  it('renders the multirail BOM draft as read-only stage requirements with separate inventory and selection state', () => {
    const wrapper = mount(PowerArchitectureOptions, {
      props: {
        topologies: [
          {
            topology: 'buck_ldo',
            label: 'Buck + LDO',
            availability: 'candidate_found',
            selected_by_user: true,
            total_load_current_a: '0.1',
            rails: [],
            stages: [],
            summary: '只读架构比较。',
            constraints: [],
            read_only: true,
          },
        ] as never,
        railBomDraft: {
          status: 'needs_selection',
          selected_topology: 'buck_ldo',
          manual_review: [],
          read_only: true,
          automatic_write: false,
          rails: [
            {
              rail_id: 'main',
              label: '主数字轨',
              voltage_v: '3.3',
              load_current_a: '0.1',
              current_basis: 'user_total',
              stages: [
                {
                  stage_id: 'main-buck',
                  topology: 'buck',
                  input_voltage_v: '12',
                  output_voltage_v: '5',
                  load_current_a: '0.1',
                  selection_status: 'needs_selection',
                  engineering_facts: [
                    {
                      fact_id: 'cot-ripple',
                      mpn: 'LM5164DDAR',
                      role: 'COT feedback ripple',
                      value: '至少 20mV 同相反馈纹波',
                      source_page: 10,
                    },
                  ],
                  candidate_devices: [],
                  bom_requirements: [
                    {
                      requirement_id: 'main-buck-primary',
                      role: 'primary regulator IC',
                      status: 'candidate_found',
                      selection_status: 'needs_selection',
                      inventory_status: 'in_stock',
                      candidates: [
                        {
                          material_id: 701,
                          code: 'IC-LM5164',
                          mpn: 'LM5164DDAR',
                          package: 'SOIC-8',
                          inventory: { available_quantity: '14', unit: 'pcs' },
                          locations: ['D03'],
                          inventory_status: 'in_stock',
                          selection_status: 'needs_selection',
                        },
                      ],
                      notes: [],
                    },
                  ],
                },
                {
                  stage_id: 'main-ldo',
                  topology: 'ldo',
                  input_voltage_v: '5',
                  output_voltage_v: '3.3',
                  load_current_a: '0.1',
                  loss_w: '0.17',
                  selection_status: 'needs_selection',
                  engineering_facts: [],
                  candidate_devices: [],
                  bom_requirements: [
                    {
                      requirement_id: 'main-ldo-primary',
                      role: 'primary LDO IC',
                      status: 'needs_selection',
                      selection_status: 'needs_selection',
                      candidates: [],
                      notes: ['需要工程选型'],
                    },
                  ],
                },
              ],
            },
          ],
        } as never,
      },
      global: { stubs: { ElTag: { template: '<span><slot /></span>' } } },
    })

    const text = wrapper.text()
    expect(text).toContain('多轨工程 BOM 草案')
    expect(text).toContain('5V → 3.3V')
    expect(text).toContain('损耗 0.17W')
    expect(text).toContain('BOM Requirements')
    expect(text).toContain('至少 20mV 同相反馈纹波')
    expect(text).toContain('在库事实 · 可用 14 pcs')
    expect(text).toContain('库位 D03')
    expect(text).toContain('待选型')
    expect(text).toContain('只读，不写入正式 BOM')
  })

  it('keeps an unknown analog rail unallocated instead of displaying zero current', () => {
    const wrapper = mount(PowerArchitectureOptions, {
      props: {
        topologies: [
          {
            topology: 'split_rails',
            label: '分轨',
            availability: 'conceptual',
            selected_by_user: false,
            total_load_current_a: null,
            rails: [],
            stages: [],
            summary: '模拟负载待补输入。',
            constraints: [],
            read_only: true,
          },
        ] as never,
        railBomDraft: {
          status: 'needs_input',
          selected_topology: 'split_rails',
          manual_review: ['需要模拟轨电流'],
          read_only: true,
          automatic_write: false,
          rails: [
            {
              rail_id: 'analog',
              label: '模拟轨',
              voltage_v: '3.3',
              load_current_a: null,
              current_basis: 'not_allocated',
              stages: [
                {
                  stage_id: 'analog-ldo',
                  topology: 'ldo',
                  input_voltage_v: '5',
                  output_voltage_v: '3.3',
                  load_current_a: null,
                  selection_status: 'needs_input',
                  candidate_devices: [],
                  bom_requirements: [
                    {
                      requirement_id: 'analog-ldo-primary',
                      role: 'primary LDO IC',
                      status: 'needs_input',
                      selection_status: 'needs_input',
                      candidates: [],
                      notes: ['需要模拟轨电流'],
                    },
                  ],
                },
              ],
            },
          ],
        } as never,
      },
      global: { stubs: { ElTag: true } },
    })

    const text = wrapper.text()
    expect(text).toContain('待分配')
    expect(text).toContain('待补输入')
    expect(text).not.toContain('0A')
  })

  it('labels project BOM physical locations without pretending they are pick allocations', () => {
    const source = readFileSync(
      resolve(process.cwd(), 'src/components/agent/AgentResultCard.vue'),
      'utf8',
    )
    const floating = readFileSync(
      resolve(process.cwd(), 'src/components/agent/FloatingAgentConversation.vue'),
      'utf8',
    )
    expect(source).toContain('label="库存库位"')
    expect(source).toContain('库存尚未映射到具体库位')
    expect(source).toContain('物理库存')
    expect(source).toContain('物理库存库位')
    expect(source).not.toContain('label="实际库位"')
    expect(floating).toContain('data-testid="floating-bom-semantics"')
    expect(floating).toContain('物理库存库位')
    expect(floating).toContain('quantity_semantics')
  })

  it('shows the structured answer first while partial, UUID and latency stay technical', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/materials/:id', component: { template: '<div />' } },
        { path: '/locations', component: { template: '<div />' } },
      ],
    })
    await router.push('/')
    const wrapper = mount(AgentResultCard, {
      props: { result: partialResult },
      global: {
        plugins: [router],
        stubs: {
          ElButton: { template: '<button><slot /></button>' },
          ElEmpty: true,
          ElTable: true,
          ElTableColumn: true,
          ElTag: true,
        },
      },
    })

    expect(wrapper.find('.answer').exists()).toBe(false)
    const location = wrapper.get('[data-testid="agent-material-result"]')
    expect(location.text()).toContain('当前库存')
    expect(location.text()).toContain('尚未分配实际库位')
    expect(location.text()).toContain('30 件库存尚未分配到具体货架或抽屉')
    expect(location.text()).not.toContain('partial')
    expect(location.text()).not.toContain('库位 ID')

    const technical = wrapper.get('[data-testid="agent-technical-details"]')
    expect((technical.element as HTMLDetailsElement).open).toBe(false)
    expect(technical.text()).toContain(partialResult.request_id)
    expect(technical.text()).toContain('deterministic')
    expect(technical.text()).toContain('model_call_count')
    expect(technical.text()).toContain('0')
    expect(technical.text()).not.toContain('Provider:')
    expect(technical.text()).toContain('8 ms')
  })

  it('shows a grounded LLM explanation alongside structured result cards', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/materials/:id', component: { template: '<div />' } },
        { path: '/locations', component: { template: '<div />' } },
      ],
    })
    await router.push('/')
    const wrapper = mount(AgentResultCard, {
      props: {
        result: {
          ...partialResult,
          answer: '服务端确定性关系卡片。',
          narrative: '两颗器件功能相近，但不能据此认定为可直接替换。',
          execution_mode: 'llm_assisted',
          model_call_count: 1,
        },
      },
      global: {
        plugins: [router],
        stubs: {
          ElButton: { template: '<button><slot /></button>' },
          ElEmpty: true,
          ElTable: true,
          ElTableColumn: true,
          ElTag: true,
        },
      },
    })

    expect(wrapper.get('.answer').text()).toContain('不能据此认定为可直接替换')
    expect(wrapper.get('[data-testid="agent-material-result"]').text()).toContain('当前库存')
  })

  it('keeps the query process collapsed and uses user-facing step labels', () => {
    const wrapper = mount(AgentTimeline, {
      props: { events: partialResult.tool_events, running: false },
    })
    expect(wrapper.text()).toContain('已核对物料和库位')
    expect(wrapper.text()).toContain('找到物料')
    expect(wrapper.text()).toContain('核对存放位置')
    expect(wrapper.text()).not.toContain('8 ms')
    expect((wrapper.get('details').element as HTMLDetailsElement).open).toBe(false)
  })

  it('renders safe component candidate cards without internal scoring fields', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/materials/:id', component: { template: '<div />' } },
        { path: '/locations', component: { template: '<div />' } },
      ],
    })
    await router.push('/')
    const wrapper = mount(AgentResultCard, {
      props: { result: componentResult },
      global: {
        plugins: [router],
        stubs: { ElTable: true, ElTableColumn: true },
      },
    })

    const results = wrapper.get('[data-testid="component-candidate-results"]')
    expect(results.text()).toContain('候选相似器件')
    expect(results.text()).toContain('TCAN1044AVDRQ1')
    expect(results.text()).toContain('Texas Instruments')
    expect(results.text()).toContain('SOIC-8')
    expect(results.text()).toContain('可用库存')
    expect(results.text()).toContain('76 pcs')
    expect(results.text()).toContain('研发仓库 / CAN 区 / 01')
    expect(results.text()).toContain('需工程验证')
    expect(results.text()).toContain('已验证相似关系')
    expect(results.text()).toContain('不构成全局替代批准')
    expect(results.text()).not.toContain('已验证引脚兼容')
    expect(results.text()).not.toContain('technical_claims_allowed')
    expect(results.text()).not.toContain('material_id')
    expect(results.text()).not.toContain('score')
  })

  it('renders structured cable candidates and keeps direction ambiguity explicit', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/cables', component: { template: '<div />' } },
        { path: '/locations', component: { template: '<div />' } },
      ],
    })
    await router.push('/')
    const cableResult: AgentQueryResponse = {
      ...partialResult,
      answer: '触点方向需要同向(A型)还是反向(B型)？',
      intent: 'search_cables',
      entities: {
        cable_search: {
          query: '0.5mm 22Pin 极细同轴线',
          constraints: { cable_kind: 'micro_coax', length_is_soft: false },
          count: 1,
          evaluated_count: 80,
          needs_direction_disambiguation: true,
          clarification: '触点方向需要同向(A型)还是反向(B型)？',
          automatic_substitution: false,
          inventory_source: 'Material + InventoryLot',
          items: [
            {
              material_id: 88,
              code: 'CBL-PF-00008',
              name: 'FPC 极细同轴线',
              mpn: 'SAMPLE-CBL-PF-00008',
              manufacturer: '',
              specification: 'micro_coax',
              unit: '条',
              quantity: '10.0000',
              reserved_quantity: '0.0000',
              available_quantity: '10.0000',
              cable_kind: 'micro_coax',
              end_style: 'double',
              connector_a: 'FPC-COAX',
              connector_b: 'FPC-COAX',
              connector_pitch_mm: '0.5',
              pin_count: 22,
              pin_count_b: 0,
              pin_layout: '',
              direction: 'reverse',
              length_cm: '30.0',
              locations: [
                {
                  location_id: 9,
                  code: 'PF-CABLE-01',
                  name: '线缆箱 01',
                  full_path: '研发仓库 / 线缆与互连区 / 线缆箱 01',
                  quantity: '10.0000',
                },
              ],
              location_count: 1,
              fallback_storage_location: '',
              location_truth_source: 'InventoryLot',
              match_reasons: ['间距 0.5 mm 匹配', '22 Pin 匹配'],
              technical_claims_allowed: true,
            },
          ],
        },
      },
      tool_events: [{ tool: 'search_cables', status: 'success', summary: '1', duration_ms: 2 }],
    }
    const wrapper = mount(AgentResultCard, {
      props: { result: cableResult },
      global: { plugins: [router] },
    })

    expect(wrapper.find('.answer').exists()).toBe(false)
    const card = wrapper.get('[data-testid="agent-cable-results"]')
    expect(card.text()).toContain('触点方向需要同向(A型)还是反向(B型)？')
    expect(card.text()).toContain('等待确认方向')
    expect(card.text()).not.toContain('1 条')
    expect(card.text()).toContain('可用库存10 条')
    expect(card.text()).toContain('研发仓库 / 线缆与互连区 / 线缆箱 01')
    expect(card.text()).toContain('在线缆库查看')
    expect(card.text()).toContain('查看物料')
    expect(card.text()).toContain('带我去找')
    expect(card.text()).not.toContain('10.0000')
    expect(card.text()).not.toContain('长度用于偏好排序，不代表自动兼容或替代批准')
  })

  it('renders an explicit cable no-match state instead of an empty success region', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/materials/:id', component: { template: '<div />' } },
        { path: '/cables', component: { template: '<div />' } },
        { path: '/locations', component: { template: '<div />' } },
      ],
    })
    await router.push('/')
    const wrapper = mount(AgentResultCard, {
      props: {
        result: {
          ...partialResult,
          entities: {
            cable_search: {
              query: '0.5mm 30P 反向 15cm 排线',
              constraints: {},
              items: [],
              count: 0,
              evaluated_count: 80,
              needs_direction_disambiguation: false,
              result_state: 'no_match',
              clarification: '',
              automatic_substitution: false,
              inventory_source: 'Material + InventoryLot',
            },
          },
        },
      },
      global: { plugins: [router] },
    })

    expect(wrapper.get('[data-testid="agent-cable-results"]').text()).toContain(
      '没有满足当前规格的线缆',
    )
  })

  it('keeps an absent exact cable identity warning visible in the structured card', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/materials/:id', component: { template: '<div />' } },
        { path: '/cables', component: { template: '<div />' } },
        { path: '/locations', component: { template: '<div />' } },
      ],
    })
    await router.push('/')
    const wrapper = mount(AgentResultCard, {
      props: {
        result: {
          ...partialResult,
          entities: {
            cable_search: {
              query: '帮我找 HC-0.8-99PWT',
              constraints: { connector_a: 'HC-0.8-99PWT' },
              items: [],
              count: 0,
              evaluated_count: 80,
              needs_direction_disambiguation: false,
              result_state: 'no_match',
              clarification: '',
              automatic_substitution: false,
              inventory_source: 'Material + InventoryLot',
            },
          },
        },
      },
      global: { plugins: [router] },
    })

    expect(wrapper.get('[data-testid="cable-exact-identity-warning"]').text()).toContain(
      'HC-0.8-99PWT 完全相同的精确型号',
    )
  })

  it('derives a project-specific process summary from actual successful tools', () => {
    const wrapper = mount(AgentTimeline, {
      props: {
        events: [
          { tool: 'search_projects', status: 'success', summary: 'project', duration_ms: 2 },
          { tool: 'get_project_bom', status: 'success', summary: 'bom', duration_ms: 3 },
        ],
        running: false,
      },
    })
    expect(wrapper.text()).toContain('已核对项目和 BOM')
    expect(wrapper.text()).not.toContain('已核对物料和库位')
  })
})

describe('adaptive floating placement', () => {
  const viewport = { viewportWidth: 1440, viewportHeight: 900 }

  it('places the panel left of a robot on the right and right of a robot on the left', () => {
    const rightRobot = { left: 1240, top: 90, right: 1340, bottom: 190, width: 100, height: 100 }
    const leftPlacement = computeFloatingAgentPlacement({
      robotRect: rightRobot,
      panelWidth: 460,
      panelHeight: 650,
      ...viewport,
    })
    expect(leftPlacement.side).toBe('left')
    expect(leftPlacement.left + 460).toBeLessThan(rightRobot.left)

    const leftRobot = { left: 20, top: 90, right: 120, bottom: 190, width: 100, height: 100 }
    const rightPlacement = computeFloatingAgentPlacement({
      robotRect: leftRobot,
      panelWidth: 460,
      panelHeight: 650,
      ...viewport,
    })
    expect(rightPlacement.side).toBe('right')
    expect(rightPlacement.left).toBeGreaterThan(leftRobot.right)
  })

  it('clamps both axes when neither side can fully contain the panel', () => {
    const placement = computeFloatingAgentPlacement({
      robotRect: { left: 320, top: 760, right: 420, bottom: 860, width: 100, height: 100 },
      panelWidth: 760,
      panelHeight: 820,
      viewportWidth: 900,
      viewportHeight: 860,
    })
    expect(placement.side).toBe('clamped')
    expect(placement.left).toBeGreaterThanOrEqual(FLOATING_AGENT_SAFE_MARGIN)
    expect(placement.left + 760).toBeLessThanOrEqual(900 - FLOATING_AGENT_SAFE_MARGIN)
    expect(placement.top).toBeGreaterThanOrEqual(FLOATING_AGENT_SAFE_MARGIN)
    expect(placement.top + 820).toBeLessThanOrEqual(860 - FLOATING_AGENT_SAFE_MARGIN)
  })

  it('flips to the available side when the preferred candidate is blocked', () => {
    const placement = computeFloatingAgentPlacement({
      // The independently supplied bounds exercise the defensive fallback branch used
      // while a browser is between robot resize/layout observations.
      robotRect: { left: 100, top: 100, right: 220, bottom: 200, width: 1100, height: 100 },
      panelWidth: 460,
      panelHeight: 600,
      viewportWidth: 1200,
      viewportHeight: 800,
    })
    expect(placement.side).toBe('right')
    expect(placement.left).toBe(232)
  })

  it('clamps a persisted desktop size to a smaller viewport', () => {
    expect(clampFloatingAgentSize({ width: 700, height: 800 }, 640, 700)).toEqual({
      width: 616,
      height: 676,
    })
  })

  it('shows a BOM version picker when an exact project has multiple BOM versions', async () => {
    const result: AgentQueryResponse = {
      ...partialResult,
      answer: 'BOM_VERSION_REQUIRED：项目存在多个 BOM 版本（V1、V2），请先明确选择版本。',
      intent: 'search_project',
      entities: {
        project_candidates: {
          items: [
            {
              id: 41,
              code: 'RB-AMR-EVT',
              name: 'Atlas AMR 移动底盘 EVT',
              status: 'active',
              available_versions: ['V1', 'V2'],
            },
          ],
          count: 1,
          exact_match_ids: [41],
          selected_project_id: 41,
          selected_bom_version: null,
        },
      },
    }
    const wrapper = mount(AgentResultCard, {
      props: { result },
      global: { stubs: { ElTable: true, ElTableColumn: true } },
    })

    expect(wrapper.find('[data-testid="project-bom-version-picker"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('有多个 BOM 版本，请选择')
    expect(wrapper.text()).toContain('V1')
    expect(wrapper.text()).toContain('V2')
    expect(wrapper.find('.answer').exists()).toBe(false)

    const buttons = wrapper.findAll('[data-testid="project-bom-version-picker"] button')
    await buttons[1].trigger('click')
    expect(wrapper.emitted('selectCandidate')?.[0]).toEqual(['project', 'V2'])
  })

  it('shows exact evidence material scope and keeps variants distinct', () => {
    const wrapper = mount(EvidenceResultCard, {
      props: {
        evidence: {
          material_scope: [
            {
              id: 61,
              code: 'PORT-CAN-MCP2561FD',
              name: 'MCP2561FD CAN transceiver',
              mpn: 'MCP2561FD',
              package: 'SOIC-14',
              manufacturer: 'Microchip Technology',
            },
          ],
          query: 'MCP2561FD Pin 5 封装',
          include_superseded: false,
          facts: [
            { field: 'pin', number: 5, name: 'SPLIT', variant: 'MCP2561FD', anchor_id: 701 },
            {
              field: 'input_voltage_absolute_max',
              min: -0.3,
              max: 100,
              unit: 'V',
              variant: 'LM5164',
              anchor_id: 702,
            },
          ],
          citations: [],
          allowed_anchor_ids: [701, 702],
          evidence_coverage: 'supported',
          conclusion: '证据已定位',
          automatic_decision: false,
          read_only: true,
        },
      },
    })

    expect(wrapper.text()).toContain('MCP2561FD · SOIC-14 · Microchip Technology')
    expect(wrapper.text()).toContain('Pin 5 · MCP2561FD')
    expect(wrapper.text()).toContain('绝对最大输入电压 · LM5164')
  })

  it('renders the read-only multi-step engineering research draft and trace', () => {
    const result = {
      ...partialResult,
      answer: '工程研究草案：12V → 3.3V / 100mA。',
      intent: 'engineering_research',
      entities: {
        engineering_research: {
          workflow: 'engineering_research',
          read_only: true,
          automatic_write: false,
          status: 'supported',
          candidate_status: 'found',
          evidence_status: 'partial',
          draft_status: 'reviewable',
          requirements: { input_voltage_v: '12', output_voltage_v: '3.3', load_current_a: '0.1' },
          plan: {
            workflow: 'engineering_research',
            task_contract: { entity_kind: 'engineering_research' },
            requirements: {},
            steps: [
              {
                sequence: 0,
                tool: 'plan_power_design',
                purpose: '生成拓扑候选',
                status: 'success',
                read_only: true,
                write_scope: 'none',
              },
              {
                sequence: 1,
                tool: 'engineering_fact_verifier',
                purpose: '核对工程事实',
                status: 'success',
                read_only: true,
                write_scope: 'none',
              },
            ],
            round: 1,
            continuation: false,
            read_only: true,
            write_scope: 'none',
          },
          draft: {
            status: 'supported',
            candidate_status: 'found',
            evidence_status: 'partial',
            draft_status: 'reviewable',
            conclusion: '按本轮100mA计算，直接 LDO 损耗0.87W，5V轨后级 LDO 损耗0.17W。',
            buck: { summary: '效率通常更高。', candidates: [] },
            ldo: { summary: '需审核热损耗。', candidates: [] },
            manual_review: ['核对具体器件数据手册和 PCB 热设计。'],
            unknowns: ['精确外围值保持未知。'],
            citations: [],
            read_only: true,
            automatic_write: false,
          },
          citations: [],
        },
      },
    } as AgentQueryResponse
    const wrapper = mount(AgentResultCard, {
      props: { result },
      global: { stubs: { ElTable: true, ElTableColumn: true } },
    })

    expect(wrapper.find('[data-testid="engineering-research-result"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Research Plan')
    expect(wrapper.text()).toContain('人工审核清单')
    expect(wrapper.text()).toContain('不会自动修改 BOM、库存、预留或 Picking 结算')
    expect(wrapper.findAll('.answer')).toHaveLength(1)
    expect(wrapper.find('.answer').text()).toContain('按本轮100mA计算')
    expect(wrapper.find('.research-conclusion').exists()).toBe(false)
    expect(wrapper.find('.research-model-explanation').exists()).toBe(false)
    expect((wrapper.find('.research-plan').element as HTMLDetailsElement).open).toBe(false)
  })

  it('shows architecture options once, keeps missing stock unknown and interpolates model text safely', () => {
    const result = {
      ...partialResult,
      answer: '结构化研究详情',
      narrative:
        "## 本轮结论\n\n这轮按100mA回答。<script>alert('x')</script>\n\n| 电源轨 | 电流 |\n| --- | --- |\n| MCU | 100mA |",
      intent: 'engineering_research',
      execution_mode: 'llm_assisted',
      entities: {
        engineering_research: {
          workflow: 'engineering_research',
          read_only: true,
          automatic_write: false,
          status: 'supported',
          candidate_status: 'found',
          evidence_status: 'partial',
          draft_status: 'reviewable',
          requirements: { input_voltage_v: '12', output_voltage_v: '3.3', load_current_a: '0.1' },
          plan: {
            workflow: 'engineering_research',
            task_contract: {},
            requirements: {},
            steps: [],
            round: 2,
            continuation: true,
            read_only: true,
            write_scope: 'none',
          },
          draft: {
            status: 'supported',
            candidate_status: 'found',
            evidence_status: 'partial',
            draft_status: 'reviewable',
            conclusion: '本轮确定性首答。',
            buck: {
              summary: '可比较的 Buck 候选。',
              candidates: [
                {
                  material_id: 9,
                  code: 'PORT-BUCK-LM5164',
                  mpn: 'LM5164DDAR',
                  inventory: { code: 'PWR-LDO', name: 'LDO', mpn: 'TLV76133DCYR' },
                  locations: {
                    material_id: 9,
                    code: 'PWR-LDO',
                    name: 'LDO',
                    mpn: 'TLV76133DCYR',
                    locations: [],
                    count: 0,
                    material_quantity: '0',
                    lot_quantity_total: '0',
                    unallocated_quantity: '0',
                    distribution_status: 'partial',
                  },
                  evidence_coverage: 'supported',
                  evidence_status: 'partial',
                  evidence_gaps: [],
                  citations: [],
                  peripheral_roles: [],
                },
              ],
            },
            ldo: { summary: '需审查损耗。', candidates: [] },
            manual_review: [],
            unknowns: [],
            citations: [],
            topologies: [
              {
                topology: 'direct_buck',
                label: '12V → Buck → 3.3V',
                availability: 'candidate_found',
                selected_by_user: false,
                total_load_current_a: '0.1',
                rails: [],
                stages: [],
                summary: '满足纹波与瞬态条件时可用于 MCU。',
                constraints: [],
                read_only: true,
              },
              {
                topology: 'buck_ldo',
                label: '12V → Buck → 5V → LDO → 3.3V',
                availability: 'candidate_found',
                selected_by_user: true,
                total_load_current_a: '0.1',
                rails: [
                  {
                    rail_id: 'main-low-noise',
                    label: '3.3V 后级稳压轨',
                    voltage_v: '3.3',
                    load_current_a: '0.1',
                    current_basis: 'user_total',
                    sensitive_analog: false,
                    stage_ids: ['ldo'],
                    notes: [],
                  },
                ],
                stages: [
                  {
                    stage_id: 'ldo',
                    topology: 'ldo',
                    input_voltage_v: '5',
                    output_voltage_v: '3.3',
                    load_current_a: '0.1',
                    current_basis: 'user_total',
                    loss_w: '0.17',
                    ideal_efficiency: '0.66',
                    quiescent_current_a: '0.00006',
                    thermal_screen: { estimated_delta_t_c: '16.218' },
                    loss_status: 'calculated',
                    headroom_v: '1.7',
                    dropout_status: 'verify_at_load',
                    candidate_devices: [
                      {
                        material_id: 10,
                        code: 'C7527500',
                        mpn: 'TLV76133DCYR',
                        package: 'SOT-223',
                        inventory: { available_quantity: '14', unit: '件' },
                        locations: ['研发仓 / E03'],
                        engineering_parameters: [
                          {
                            key: 'psrr',
                            label: 'PSRR（典型摘要）',
                            value: '60',
                            unit: 'dB @ 1kHz',
                            conditions: '按实际频率核对',
                            source_page: 1,
                            source_url: 'https://www.ti.com/lit/ds/symlink/tlv761.pdf',
                          },
                        ],
                        peripheral_roles: [],
                        citations: [],
                      },
                    ],
                    notes: [],
                  },
                ],
                summary: '中间 Buck 先形成 5V。',
                constraints: ['LDO 输入为5V，不是12V。'],
                read_only: true,
              },
              {
                topology: 'split_rails',
                label: '数字与敏感模拟电源分轨',
                availability: 'conceptual',
                selected_by_user: false,
                total_load_current_a: '0.1',
                rails: [],
                stages: [],
                summary: '分别确认两轨负载。',
                constraints: [],
                read_only: true,
              },
            ],
            rail_bom_draft: {
              status: 'needs_confirmation',
              selected_topology: 'buck_ldo',
              rails: [
                {
                  rail_id: 'main-low-noise',
                  label: '3.3V 后级稳压轨',
                  voltage_v: '3.3',
                  load_current_a: '0.1',
                  current_basis: 'user_total',
                  sensitive_analog: false,
                  stages: [
                    {
                      stage_id: 'ldo',
                      topology: 'ldo',
                      input_voltage_v: '5',
                      output_voltage_v: '3.3',
                      load_current_a: '0.1',
                      loss_w: '0.17',
                      candidate_devices: [],
                    },
                  ],
                },
              ],
              manual_review: ['核对负载下 dropout 和 PSRR。'],
              read_only: true,
              automatic_write: false,
            },
            read_only: true,
            automatic_write: false,
          },
          citations: [],
        },
      },
    } as AgentQueryResponse
    const wrapper = mount(AgentResultCard, {
      props: { result },
      global: { stubs: { ElTable: true, ElTableColumn: true } },
    })

    expect(wrapper.findAll('.answer')).toHaveLength(1)
    expect(wrapper.text()).toContain("这轮按100mA回答。<script>alert('x')</script>")
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.find('.answer h2').text()).toBe('本轮结论')
    expect(wrapper.find('.answer table').exists()).toBe(true)
    expect(wrapper.find('.answer td').text()).toContain('MCU')
    expect(wrapper.text()).toContain('12V → Buck → 5V → LDO → 3.3V')
    expect(wrapper.text()).toContain('损耗 0.17W')
    expect(wrapper.text()).toContain('理想效率 66%')
    expect(wrapper.text()).toContain('静态电流典型 60µA')
    expect(wrapper.text()).toContain('一阶温升筛查 16.218°C')
    expect(wrapper.text()).toContain('PSRR（典型摘要）：60 dB @ 1kHz')
    expect(wrapper.text()).toContain('本轮库存未读取')
    expect(wrapper.text()).not.toContain('可用 0')
    expect(wrapper.find('[data-testid="power-rail-bom-draft"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-testid="power-architecture-options"]')).toHaveLength(1)
  })
})
