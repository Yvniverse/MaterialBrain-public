import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../src/api/client'
import { useWarehouseAgentSuggestions } from '../src/composables/useWarehouseAgentSuggestions'
import { useWarehouseAgent } from '../src/composables/useWarehouseAgent'
import AgentConsole from '../src/components/agent/AgentConsole.vue'
import AgentResultCard from '../src/components/agent/AgentResultCard.vue'
import AgentTimeline from '../src/components/agent/AgentTimeline.vue'
import ApprovalCard from '../src/components/agent/ApprovalCard.vue'
import FloatingAgentLauncher from '../src/components/agent/FloatingAgentLauncher.vue'
import LocationResultCard from '../src/components/agent/LocationResultCard.vue'
import AgentWorkbench from '../src/views/AgentWorkbench.vue'
import { useAuthStore } from '../src/stores/auth'
import type { AgentActionProposal, AgentQueryResponse, User } from '../src/types'

const routerSource = readFileSync(resolve(process.cwd(), 'src/router/index.ts'), 'utf8')
const locationsSource = readFileSync(resolve(process.cwd(), 'src/views/Locations.vue'), 'utf8')
const workbenchSource = readFileSync(resolve(process.cwd(), 'src/views/AgentWorkbench.vue'), 'utf8')
const floatingConversationSource = readFileSync(
  resolve(process.cwd(), 'src/components/agent/FloatingAgentConversation.vue'),
  'utf8',
)

const viewer: User = {
  id: 9,
  username: 'engineer',
  full_name: '硬件工程师',
  department: '研发',
  is_active: true,
  must_change_password: false,
  created_at: '2026-08-26',
  role: {
    id: 3,
    name: '硬件工程师',
    description: '',
    permissions: ['material:view', 'project:manage'],
    is_system: true,
  },
}

const proposal: AgentActionProposal = {
  id: 12,
  proposal_no: 'AP-TEST-12',
  action_type: 'reserve_inventory',
  status: 'pending',
  payload: {
    action_type: 'reserve_inventory',
    project_id: 7,
    items: [{ material_id: 17, quantity: '5' }],
    reason: 'BOM 备料',
  },
  display: {
    project_code: 'PX-007',
    project_name: '机器人项目',
    items: [{ material_id: 17, code: 'MAT-MCU-0017', name: 'STM32F405', mpn: 'STM32F405RGT6' }],
  },
  reason: 'BOM 备料',
  created_by_id: 9,
  approved_by_id: null,
  request_id: 'request-1',
  client_operation_id: 'operation-test-1',
  payload_hash: 'a'.repeat(64),
  execution_result: null,
  error_message: '',
  expires_at: '2026-08-27T00:00:00Z',
  decided_at: null,
  executed_at: null,
  created_at: '2026-08-26T00:00:00Z',
  updated_at: '2026-08-26T00:00:00Z',
}

const result: AgentQueryResponse = {
  answer: 'STM32F405RGT6 可用 32，位于研发仓库 / A03 / A08。',
  narrative: '',
  intent: 'find_location',
  entities: {
    material_candidates: {
      count: 1,
      items: [
        {
          id: 17,
          code: 'MAT-MCU-0017',
          name: 'STM32F405',
          mpn: 'STM32F405RGT6',
          package: 'LQFP64',
          manufacturer: 'STMicroelectronics',
          quantity: '37',
          reserved_quantity: '5',
          available_quantity: '32',
        },
      ],
    },
    locations: {
      count: 1,
      locations: [
        {
          location_id: 72,
          code: 'A03-A08',
          name: 'A08',
          full_path: '研发仓库 / A03 / A08',
          organizer_id: 3,
          organizer_style: 'drawer_rack_100',
          parent_id: 3,
          quantity_at_location: '37',
          quantity_is_exact: true,
        },
      ],
      material_id: 17,
      code: 'MAT-MCU-0017',
      name: 'STM32F405',
      mpn: 'STM32F405RGT6',
      material_quantity: '37',
      lot_quantity_total: '37',
      unallocated_quantity: '0',
      distribution_status: 'complete',
    },
  },
  grounded_facts: [
    {
      kind: 'location',
      source_tool: 'find_material_locations',
      entity_id: 72,
      field: 'full_path',
      value: '研发仓库 / A03 / A08',
      unit: null,
      label: '库位路径',
    },
  ],
  tool_events: [
    { tool: 'search_materials', status: 'success', summary: '找到 1 个候选物料', duration_ms: 8 },
    {
      tool: 'find_material_locations',
      status: 'success',
      summary: '找到 1 个实际库位',
      duration_ms: 5,
    },
  ],
  ui_actions: [{ type: 'focus_location', target_id: 72, payload: {} }],
  proposal_ids: [],
  telemetry: [],
  execution_mode: 'deterministic',
  model_call_count: 0,
  request_id: 'request-1',
  conversation_id: 'conversation-1',
}

const slotStub = { template: '<div><slot /></div>' }

describe('Warehouse Agent frontend', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    setActivePinia(createPinia())
    useWarehouseAgent().newConversation()
  })

  it('registers the material-view route and the shared location focus protocol', () => {
    expect(routerSource).toContain("path: 'agent'")
    expect(routerSource).toContain("title: '物料大脑'")
    expect(routerSource).toContain("permission: 'material:view'")
    expect(locationsSource).toContain('route.query.focus')
    expect(locationsSource).toContain('focusLocationFromRoute')
    expect(locationsSource).toContain(':highlighted-bin-id="focusedLocationId"')
    expect(locationsSource).toContain(':highlighted-location-id="focusedLocationId"')
  })

  it('submits a real query and exposes returned tool events and result cards', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = viewer
    auth.initialized = true
    vi.spyOn(api, 'get').mockResolvedValue({ data: [] })
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: result })
    const ConsoleStub = {
      emits: ['update:modelValue', 'submit'],
      template:
        "<button data-testid=\"fake-submit\" @click=\"$emit('update:modelValue', 'STM32F405 在哪里？'); $emit('submit')\">提交</button>",
    }
    const wrapper = mount(AgentWorkbench, {
      global: {
        plugins: [pinia],
        stubs: {
          AgentConsole: ConsoleStub,
          AgentTimeline: true,
          AgentResultCard: true,
          ApprovalCard: true,
          ElAlert: true,
          ElTag: true,
          ElTooltip: { template: '<div><slot /></div>' },
        },
      },
    })
    await flushPromises()
    await wrapper.get('[data-testid="fake-submit"]').trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith(
      '/agent/query',
      {
        message: 'STM32F405 在哪里？',
        conversation_id: null,
        client_operation_id: expect.any(String),
      },
      { timeout: 100_000 },
    )
    expect(wrapper.getComponent(AgentTimeline).props('events')).toEqual(result.tool_events)
    expect(wrapper.getComponent(AgentResultCard).props('result')).toEqual(result)
  })

  it('renders console, material data, tool trace and a focus deep-link', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div />' } },
        { path: '/locations', component: { template: '<div />' } },
        { path: '/materials/:id', component: { template: '<div />' } },
      ],
    })
    await router.push('/')
    const consoleWrapper = mount(AgentConsole, {
      props: { modelValue: 'STM32F405 在哪里？', loading: false },
      global: {
        stubs: {
          ElButton: { template: '<button data-testid="submit"><slot /></button>' },
          ElInput: true,
          ElTag: true,
        },
      },
    })
    await consoleWrapper.get('[data-testid="agent-submit"]').trigger('click')
    expect(consoleWrapper.emitted('submit')).toHaveLength(1)

    const resultWrapper = mount(AgentResultCard, {
      props: { result },
      global: {
        plugins: [router],
        stubs: {
          ElAlert: true,
          ElButton: true,
          ElEmpty: true,
          ElTable: true,
          ElTableColumn: true,
          ElTag: true,
        },
      },
    })
    expect(resultWrapper.text()).toContain('STM32F405RGT6')
    expect(resultWrapper.text()).toContain('32')
    expect(resultWrapper.get('[data-testid="agent-material-result"]').text()).toContain(
      '研发仓库 / A03 / A08',
    )

    const timeline = mount(AgentTimeline, { props: { events: result.tool_events, running: false } })
    expect(timeline.text()).toContain('找到物料')
    expect(timeline.text()).toContain('核对存放位置')

    const locations = mount(LocationResultCard, {
      props: { locationResult: result.entities.locations! },
      global: {
        plugins: [router],
        stubs: {
          ElButton: { template: '<button data-testid="focus"><slot /></button>' },
          ElAlert: true,
          ElEmpty: true,
        },
      },
    })
    await locations.get('[data-testid="open-location"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/locations?focus=72')
  })

  it('renders database suggestions and submits exactly the returned question', async () => {
    const wrapper = mount(AgentConsole, {
      props: {
        modelValue: '',
        loading: false,
        suggestions: [
          {
            type: 'material_inventory',
            text: 'DB-MPN-42 现在还能用多少？',
            material_id: 42,
            project_id: null,
          },
        ],
      },
      global: {
        stubs: {
          ElButton: { template: '<button><slot /></button>' },
          ElInput: true,
          ElTag: true,
        },
      },
    })
    await wrapper.get('.suggestions button').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['DB-MPN-42 现在还能用多少？'])
    expect(wrapper.emitted('submit')).toHaveLength(1)
  })

  it('reuses the server conversation id and clears it only for a new conversation', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({
      data: { ...result, conversation_id: 'server-conversation-42' },
    })
    const agent = useWarehouseAgent()
    agent.message.value = '帮我找 STM32F405'
    await agent.submit()
    agent.message.value = 'RGT6 那个'
    await agent.submit()

    expect(post.mock.calls[0][1]).toMatchObject({ conversation_id: null })
    expect(post.mock.calls[1][1]).toMatchObject({
      message: 'RGT6 那个',
      conversation_id: 'server-conversation-42',
    })
    expect(agent.conversation.value).toHaveLength(2)

    agent.newConversation()
    expect(agent.conversationId.value).toBeNull()
    expect(agent.conversation.value).toEqual([])
    expect(agent.result.value).toBeNull()
  })

  it('keeps the previous engineering candidate list locally so a selection can be undone', async () => {
    const componentResult: AgentQueryResponse = {
      ...result,
      entities: {
        component_search: {
          query: { raw_text: '给我找 CAN-FD 收发器', replacement_intent: false },
          candidates: [],
          count: 2,
          candidate_only: true,
          engineering_caveat: '需要工程验证',
          read_only: true,
        },
      },
    }
    const selectedResult: AgentQueryResponse = {
      ...result,
      entities: {
        material_detail: { id: 17, code: 'CAN-A', name: 'CAN A', mpn: 'MCP2562FD-H/SN' },
      },
    }
    const post = vi
      .spyOn(api, 'post')
      .mockResolvedValueOnce({ data: componentResult })
      .mockResolvedValueOnce({ data: selectedResult })
    const agent = useWarehouseAgent()

    agent.message.value = '给我找 CAN-FD 收发器'
    await agent.submit()
    await agent.selectCandidate('material', 'MCP2562FD-H/SN')

    expect(agent.candidateHistory.value).toEqual(componentResult)
    expect(agent.result.value).toEqual(selectedResult)
    agent.restoreCandidateResults()
    expect(agent.result.value).toEqual(componentResult)
    expect(post).toHaveBeenCalledTimes(2)
  })

  it('does not retry an expired pronoun against a fresh conversation', async () => {
    const post = vi
      .spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { ...result, conversation_id: 'expired-conversation' } })
      .mockRejectedValueOnce({
        response: {
          data: {
            code: 'AGENT_CONVERSATION_EXPIRED',
            message: 'expired',
          },
        },
      })
    const agent = useWarehouseAgent()
    agent.message.value = '帮我找 AS5047P'
    await agent.submit()
    agent.message.value = '它在哪？'
    await agent.submit()

    expect(post).toHaveBeenCalledTimes(2)
    expect(agent.conversationId.value).toBeNull()
    expect(agent.message.value).toBe('')
    expect(agent.localError.value).toContain('上下文已过期')
  })

  it('renders unresolved candidates as explicit choices without promoting candidate zero', async () => {
    const ambiguous: AgentQueryResponse = {
      ...result,
      answer: '找到多个候选，请选择。',
      entities: {
        material_candidates: {
          count: 2,
          items: [
            { id: 17, code: 'MCU-R', name: 'STM32F405', mpn: 'STM32F405RGT6', package: 'LQFP64' },
            { id: 18, code: 'MCU-V', name: 'STM32F405', mpn: 'STM32F405VGT6', package: 'LQFP100' },
          ],
          exact_match_ids: [],
          selected_material_id: null,
        },
      },
      tool_events: [{ tool: 'search_materials', status: 'success', summary: '2', duration_ms: 2 }],
    }
    const wrapper = mount(AgentResultCard, {
      props: { result: ambiguous },
      global: {
        stubs: {
          MaterialResultCard: { template: '<div data-testid="promoted-material" />' },
          ElTable: true,
          ElTableColumn: true,
        },
      },
    })

    expect(wrapper.find('[data-testid="promoted-material"]').exists()).toBe(false)
    const choices = wrapper.findAll('[data-testid="material-candidate-picker"] button')
    expect(choices).toHaveLength(2)
    await choices[1].trigger('click')
    expect(wrapper.emitted('selectCandidate')?.[0]).toEqual(['material', 'STM32F405VGT6'])
  })

  it('uses one shared suggestion store and a safe fallback when the API fails', async () => {
    const first = useWarehouseAgentSuggestions()
    const second = useWarehouseAgentSuggestions()
    expect(first.suggestions).toBe(second.suggestions)
    expect(workbenchSource).toContain('useWarehouseAgentSuggestions')
    expect(floatingConversationSource).toContain('useWarehouseAgentSuggestions')

    vi.spyOn(api, 'get').mockRejectedValueOnce(new Error('suggestions unavailable'))
    await first.loadSuggestions(true)
    expect(first.suggestions.value).toEqual([
      {
        type: 'low_stock',
        text: '哪些物料低于安全库存？',
        material_id: null,
        project_id: null,
      },
    ])
  })

  it('renders the structured power and engineering BOM cards on the floating surface', () => {
    expect(floatingConversationSource).toContain('import EngineeringResearchCard')
    expect(floatingConversationSource).toContain('import PowerArchitectureOptions')
    expect(floatingConversationSource).toContain('floating-engineering-structured')
    expect(floatingConversationSource).toContain('power_design.rail_bom_draft')
    expect(floatingConversationSource).toContain('engineering_research')
  })

  it('suppresses location navigation when lot totals are inconsistent', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: { template: '<div />' } }],
    })
    await router.push('/')
    const inconsistent = {
      ...result.entities.locations!,
      lot_quantity_total: '42',
      distribution_status: 'inconsistent' as const,
    }
    const wrapper = mount(LocationResultCard, {
      props: { locationResult: inconsistent },
      global: {
        plugins: [router],
        stubs: {
          ElAlert: { props: ['title'], template: '<div>{{ title }}</div>' },
          ElButton: { template: '<button data-testid="focus"><slot /></button>' },
          ElEmpty: true,
        },
      },
    })
    expect(wrapper.text()).toContain('数量不一致')
    expect(wrapper.find('[data-testid="open-location"]').exists()).toBe(false)
  })

  it('renders the transparent robot without a caption and emits its anchor and open', async () => {
    localStorage.clear()
    const wrapper = mount(FloatingAgentLauncher)
    await flushPromises()
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
    expect(wrapper.get('img').attributes('src')).toContain('pengka-agent-robot')
    expect(wrapper.text()).not.toContain('问物料大脑')
    expect(wrapper.get('[data-testid="floating-agent-launcher"]').attributes('style')).toContain(
      'top: 8px',
    )
    expect(wrapper.emitted('positionChange')).toBeTruthy()
    await wrapper.get('[data-testid="floating-agent-launcher"]').trigger('click')
    expect(wrapper.emitted('open')).toHaveLength(1)
  })

  it('drags freely, persists position, and does not open on drag release', async () => {
    localStorage.clear()
    const wrapper = mount(FloatingAgentLauncher)
    await flushPromises()
    const launcher = wrapper.get('[data-testid="floating-agent-launcher"]')
    const initialStyle = launcher.attributes('style')

    await launcher.trigger('pointerdown', {
      button: 0,
      clientX: 500,
      clientY: 20,
      pointerId: 1,
    })
    window.dispatchEvent(
      new MouseEvent('pointermove', { clientX: 700, clientY: 220, bubbles: true }),
    )
    window.dispatchEvent(new Event('pointerup'))
    await launcher.trigger('click')

    expect(launcher.attributes('style')).not.toBe(initialStyle)
    expect(localStorage.getItem('materialbrain:floating-agent-position:v1')).toBeTruthy()
    expect(wrapper.emitted('open')).toBeUndefined()

    await launcher.trigger('click')
    expect(wrapper.emitted('open')).toHaveLength(1)
  })

  it('does not allow a viewer without inventory:operate to approve', () => {
    const wrapper = mount(ApprovalCard, {
      props: { proposal, canApprove: false, busy: false },
      global: {
        stubs: {
          ElAlert: true,
          ElButton: {
            props: ['disabled'],
            template: '<button :disabled="disabled"><slot /></button>',
          },
          ElTag: slotStub,
        },
      },
    })
    expect(wrapper.get('[data-testid="approve-proposal"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('你没有批准库存变更的权限')
  })
})
