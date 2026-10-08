import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { api } from '../src/api/client'
import { resetWarehouseAgentSession, useWarehouseAgent } from '../src/composables/useWarehouseAgent'
import SpatialMissionCard from '../src/spatial/SpatialMissionCard.vue'
import FloatingAgentConversation from '../src/components/agent/FloatingAgentConversation.vue'
import SpatialObservability from '../src/spatial/SpatialObservability.vue'
import SpatialNavInspector from '../src/spatial/SpatialNavInspector.vue'
import SpatialBenchmarkDrawer from '../src/spatial/SpatialBenchmarkDrawer.vue'
import SpatialReplayDrawer from '../src/spatial/SpatialReplayDrawer.vue'
import { currentZones, formatMetric, formatPercent } from '../src/spatial/presentation'
import {
  acceptSpatialMission,
  defaultMissionRequest,
  resetSpatialMissionSession,
  setSpatialConversationId,
  subscribeSpatialMission,
  subscribeSpatialScene,
  useSpatialMission,
} from '../src/spatial/useSpatialMission'
import type {
  SpatialMapSnapshot,
  MissionPlan,
  SpatialMissionProjection,
  SpatialNavigationHealth,
  WarehouseBenchmark,
} from '../src/spatial/contracts'
import type { AgentQueryResponse } from '../src/types'

const revision = 'registered-map-revision'
function map(): SpatialMapSnapshot {
  return {
    schema_version: 1,
    map_id: 'MB-EMB-LAB-03',
    revision,
    frame: { name: 'warehouse_map', unit: 'm', srid: 0 },
    geometry: {},
    zones: [
      {
        id: 'ESD',
        name: '精密防静电区',
        kind: 'esd',
        polygon: {
          type: 'Polygon',
          coordinates: [
            [
              [0, 0],
              [5, 0],
              [5, 5],
              [0, 5],
              [0, 0],
            ],
          ],
        },
        speed_limit_mps: 0.45,
        risk_level: 0.01,
        rule_refs: ['esd_protected_payload'],
      },
    ],
    route_graph: { nodes: [], edges: [] },
    docks: ['HOME', 'CHARGER', 'P-IC', 'P-SENSOR', 'P-LAB'].map((id, index) => ({
      id,
      label: id === 'P-LAB' ? '实验室交接点' : id,
      pose: { x: 18 + index, y: 2.5, yaw: Math.PI / 2 },
      node_id: id,
      kind: id === 'HOME' ? 'home' : 'human_handoff',
      capabilities: ['navigate', 'human_handoff'],
    })),
    affordances: [],
    dynamic_overlays: [],
    provenance: {
      home_dock_id: 'HOME',
      robot: { id: 'MB-R01', payload_kg: 18, reserve_pct: 15, battery_pct: 82 },
    },
  }
}
function plan(id = 'SM-1'): MissionPlan {
  return {
    schema_version: 1,
    mission_id: id,
    map_id: 'MB-EMB-LAB-03',
    map_revision: revision,
    profile: 'fastest',
    status: 'READY',
    stops: [],
    ordered_goal_ids: ['P-IC', 'P-SENSOR', 'P-LAB'],
    completed_goal_ids: [],
    constraints: { payload_capacity_kg: 18, battery_pct: 82, battery_reserve_pct: 15 },
    solver: { name: 'ortools', method: 'constrained_routing', optimality_proven: false },
    metrics: { distance_m: 42.75, eta_s: 123.5, energy_wh: 2.2, min_clearance_m: 0.14 },
    objective_terms: {},
    segments: [],
    violations: [],
  }
}
function projection(status = 'READY', sequence = 0, id = 'SM-1'): SpatialMissionProjection {
  const value: SpatialMissionProjection = {
    mission_id: id,
    conversation_id: 'conversation-1',
    execution_boundary: 'ros2_nav2_simulation',
    inventory_written: false,
    hardware_control: false,
    mission_plan: plan(id),
    execution: null,
    task_graph: {
      schema_version: 1,
      task_id: 'task-1',
      conversation_id: 'conversation-1',
      map_id: 'MB-EMB-LAB-03',
      map_revision: revision,
      mission_id: id,
      nodes: [
        {
          id: 'nav-IC',
          skill: 'navigate_to',
          args: { goal_id: 'P-IC' },
          dependencies: [],
          status: status === 'READY' ? 'PENDING' : 'RUNNING',
          attempts: 0,
          idempotency_key: 'node-op-1',
        },
        {
          id: 'handoff-IC',
          skill: 'human_handoff',
          args: { goal_id: 'P-IC' },
          dependencies: ['nav-IC'],
          status: 'PENDING',
          attempts: 0,
          idempotency_key: 'node-op-2',
        },
      ],
      current_skill: status === 'READY' ? null : 'navigate_to',
      completed_goal_ids: [],
      remaining_goal_ids: ['P-IC', 'P-SENSOR', 'P-LAB'],
      robot_state: { instruction: '先送IC和传感器，再到实验室交接，完成后回待命点。' },
      recovery: null,
      events: [],
      last_valid_plan: plan(id),
      status,
    },
  }
  if (status !== 'READY') {
    value.execution = {
      mission_id: id,
      map_id: 'MB-EMB-LAB-03',
      map_revision: revision,
      status,
      current_goal_id: 'P-IC',
      current_pose: { x: 2, y: 3, yaw: 0.5 },
      completed_goal_ids: [],
      remaining_goal_ids: ['P-IC', 'P-SENSOR', 'P-LAB'],
      robot_state: { battery_pct: 79.5, payload_kg: 0.8, v_mps: 0.31 },
      events: [],
      last_sequence: sequence,
      metrics: {
        distance_m: 7.4,
        elapsed_s: 19.5,
        collision_count: 0,
        min_clearance_m: 0.128,
        bt_recovery_count: 1,
        action_feedback_messages: 9,
      },
      execution_boundary: 'ros2_nav2_simulation',
      hardware_control: false,
    }
    value.task_graph.events = [
      {
        event_id: `event-${sequence}`,
        sequence,
        type: 'feedback',
        timestamp: '2026-10-04T12:00:00Z',
        goal_id: 'P-IC',
        details: { distance_remaining_m: 4.7 },
      },
    ]
  }
  return value
}
function navigationHealth(): SpatialNavigationHealth {
  return {
    ready: true,
    map_id: 'MB-EMB-LAB-03',
    map_revision: revision,
    build_sha: 'candidate-sha',
    ros_distro: 'jazzy',
    nav2_version: 'installed-version',
    active_plugins: { planner: 'observed::Lattice', controller: 'observed::MPPI' },
    lifecycle: { planner_server: 'active', controller_server: 'active' },
    actions: { navigate_to_pose: true },
    topic_message_counts: { '/odom': 28, '/scan': 31 },
    observed_tf: [['warehouse_map', 'odom']],
    robot_state: { pose: { x: 18, y: 2.5, yaw: 1.5 }, v_mps: 0 },
    execution_boundary: 'ros2_nav2_simulation',
    hardware_control: false,
  }
}
function benchmark(): WarehouseBenchmark {
  return {
    benchmark: 'WarehouseBench',
    captured_at: '2026-10-04T13:00:00Z',
    source_sha: 'source-bound-sha',
    map_id: 'MB-EMB-LAB-03',
    map_revision: revision,
    seed: 34017,
    repetitions: 1,
    execution_boundary: 'deterministic_plan_verification',
    measurement: { mission_time: 'planned estimate', success: 'independent geometry audit' },
    algorithm_availability: [
      { algorithm: 'heading_grid_astar', status: 'EXECUTED' },
      { algorithm: 'semantic_graph_ortools', status: 'EXECUTED' },
      { algorithm: 'nav2_state_lattice', status: 'NOT_RUN' },
    ],
    summaries: {
      'semantic_graph_ortools:fastest': {
        executed_episodes: 12,
        skipped_episodes: 0,
        success_rate: 0.75,
        decision_correct_rate: 1,
        planning_p50_ms: 321.5,
        planning_p95_ms: 437.7,
        mean_path_length_m: 54.4,
        min_clearance_m: null,
        collision_rate: 0,
        constraint_violation_rate: 0,
        recovery_success_rate: null,
        mean_mission_completion_time_s: 125.8,
      },
    },
  }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((accept) => {
    resolve = accept
  })
  return { promise, resolve }
}
const detaches: (() => void)[] = []
function mockReads() {
  return vi.spyOn(api, 'get').mockImplementation(async (url: string) => {
    if (url.includes('/snapshot')) return { data: map() }
    if (url.endsWith('/health')) return { data: navigationHealth() }
    if (url.endsWith('/benchmarks')) return { data: benchmark() }
    if (url.endsWith('/contracts'))
      return {
        data: {
          ObservationFrame: {},
          MapDeltaProposal: {},
          TrajectorySegment: {},
          training_executed: false,
          automatic_map_write: false,
          multi_robot_runtime: false,
        },
      }
    return { data: projection('NAVIGATING', 2) }
  })
}
function router() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/warehouse-twin', component: { template: '<div />' } },
    ],
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.restoreAllMocks()
  setActivePinia(createPinia())
  resetWarehouseAgentSession()
})
afterEach(() => {
  for (const detach of detaches.splice(0)) detach()
  resetSpatialMissionSession()
  vi.useRealTimers()
})

describe('server projection, planning boundary, and request epochs', () => {
  it('uses the canonical registered HOME pose and robot budget without copying coordinates', () => {
    const snapshot = map()
    snapshot.docks[0].pose = { x: 44, y: 22, yaw: -0.7 }
    const request = defaultMissionRequest(snapshot)
    expect(request.start_pose).toEqual(snapshot.docks[0].pose)
    expect(request.constraints).toEqual({
      battery_pct: 82,
      battery_reserve_pct: 15,
      payload_capacity_kg: 18,
      robot_class: 'MB-R01',
    })
    expect(request.goal_ids).toEqual(['P-IC', 'P-SENSOR', 'P-LAB'])
    expect(request.return_home).toBe(true)
    expect(() => defaultMissionRequest({ ...snapshot, provenance: {} })).toThrow('缺少')
    expect(() => defaultMissionRequest(snapshot, { goalIds: ['unregistered'] })).toThrow('注册')
  })
  it('previews a route through the planning API without creating or starting execution', async () => {
    mockReads()
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: plan() })
    const state = useSpatialMission()
    await state.loadSnapshot()
    await state.planMission()
    expect(post).toHaveBeenCalledOnce()
    expect(post).toHaveBeenCalledWith(
      '/spatial/missions/plan',
      expect.objectContaining({ map_revision: revision }),
      { timeout: 90_000 },
    )
    expect(state.previewPlan.value?.status).toBe('READY')
    expect(state.mission.value).toBeNull()
  })
  it('keeps a single poll request and publishes the same server graph to both surfaces', async () => {
    const pending = deferred<{ data: SpatialMissionProjection }>()
    const get = vi.spyOn(api, 'get').mockReturnValue(pending.promise)
    const card = useSpatialMission(),
      workbench = useSpatialMission()
    const listener = vi.fn()
    detaches.push(subscribeSpatialMission(listener))
    acceptSpatialMission(projection('NAVIGATING', 1))
    const first = card.pollMission(),
      second = workbench.pollMission()
    expect(get).toHaveBeenCalledOnce()
    const update = projection('AWAITING_HANDOFF', 2)
    pending.resolve({ data: update })
    await Promise.all([first, second])
    expect(card.mission.value).toEqual(update)
    expect(card.mission).toBe(workbench.mission)
    expect(listener).toHaveBeenLastCalledWith(update)
    expect(card.mission.value?.task_graph.completed_goal_ids).toEqual([])
  })
  it('ignores older navigation sequences and another conversation projection', () => {
    acceptSpatialMission(projection('NAVIGATING', 8))
    expect(acceptSpatialMission(projection('AWAITING_HANDOFF', 7))).toBe(false)
    expect(acceptSpatialMission(projection('NAVIGATING', 9), 'another-conversation')).toBe(false)
    expect(useSpatialMission().mission.value?.execution?.last_sequence).toBe(8)
  })
  it('rejects mismatched revisions and hardware or inventory boundaries', () => {
    const stale = projection()
    stale.mission_plan!.map_revision = 'stale'
    expect(acceptSpatialMission(stale)).toBe(false)
    const invalid = {
      ...projection(),
      inventory_written: true,
    } as unknown as SpatialMissionProjection
    expect(acceptSpatialMission(invalid)).toBe(false)
    expect(useSpatialMission().mission.value).toBeNull()
  })
  it('clears account and new-conversation state and ignores late polling', async () => {
    const pending = deferred<{ data: SpatialMissionProjection }>()
    const get = vi.spyOn(api, 'get').mockReturnValue(pending.promise)
    acceptSpatialMission(projection('NAVIGATING', 1))
    const state = useSpatialMission(),
      poll = state.pollMission()
    useWarehouseAgent().newConversation()
    pending.resolve({ data: projection('COMPLETED', 20) })
    await poll
    expect(state.mission.value).toBeNull()
    await vi.advanceTimersByTimeAsync(5000)
    expect(get).toHaveBeenCalledOnce()
  })
  it('does not refill map, health or benchmark after an account reset', async () => {
    const pending = deferred<{
      data: SpatialMapSnapshot | SpatialNavigationHealth | WarehouseBenchmark
    }>()
    vi.spyOn(api, 'get').mockReturnValue(pending.promise)
    const state = useSpatialMission()
    const requests = [state.loadSnapshot(), state.loadHealth(), state.loadBenchmark()]
    resetWarehouseAgentSession()
    pending.resolve({ data: map() })
    await Promise.all(requests)
    expect(state.snapshot.value).toBeNull()
    expect(state.health.value).toBeNull()
    expect(state.benchmark.value).toBeNull()
    expect(state.loadingSnapshot.value).toBe(false)
  })
  it('deduplicates cold map and health reads across card initialization', async () => {
    const get = mockReads()
    await Promise.all([useSpatialMission().initialize(), useSpatialMission().initialize()])
    expect(get.mock.calls.filter(([url]) => String(url).includes('/snapshot'))).toHaveLength(1)
    expect(get.mock.calls.filter(([url]) => String(url).endsWith('/health'))).toHaveLength(1)
  })
  it('invalidates a delayed plan when planning controls change', async () => {
    mockReads()
    const pending = deferred<{ data: MissionPlan }>()
    vi.spyOn(api, 'post').mockReturnValue(pending.promise)
    const state = useSpatialMission()
    await state.loadSnapshot()
    const request = state.planMission()
    state.setProfile('esd_safe')
    pending.resolve({ data: plan() })
    await request
    expect(state.previewPlan.value).toBeNull()
  })
  it('keeps the same create operation ID on retry and preserves the business error', async () => {
    mockReads()
    const post = vi
      .spyOn(api, 'post')
      .mockRejectedValueOnce({
        response: {
          status: 409,
          data: { code: 'SPATIAL_BUSY', message: '任务状态刚刚改变，请重试。' },
        },
      })
      .mockResolvedValueOnce({ data: projection() })
    const state = useSpatialMission()
    await state.loadSnapshot()
    setSpatialConversationId('conversation-1')
    await state.createMission()
    expect(state.error.value).toBe('任务状态刚刚改变，请重试。')
    expect(state.errorCode.value).toBe('SPATIAL_BUSY')
    await state.createMission()
    expect(post.mock.calls[0][1]).toEqual(post.mock.calls[1][1])
    expect(post.mock.calls[1][1]).toMatchObject({ conversation_id: 'conversation-1' })
    expect(post.mock.calls.every(([url]) => !String(url).endsWith('/start'))).toBe(true)
  })
  it('does not accept a create response received after logout', async () => {
    mockReads()
    const pending = deferred<{ data: SpatialMissionProjection }>()
    vi.spyOn(api, 'post').mockReturnValue(pending.promise)
    const state = useSpatialMission()
    await state.loadSnapshot()
    const operation = state.createMission()
    resetWarehouseAgentSession()
    pending.resolve({ data: projection() })
    await operation
    expect(state.mission.value).toBeNull()
    expect(state.creating.value).toBe(false)
  })
  it('cannot roll back command feedback with a poll that began before the command', async () => {
    const pending = deferred<{ data: SpatialMissionProjection }>()
    vi.spyOn(api, 'get').mockReturnValue(pending.promise)
    const update = projection('NAVIGATING', 3)
    update.task_graph.completed_goal_ids = ['P-IC']
    update.task_graph.remaining_goal_ids = ['P-SENSOR', 'P-LAB']
    vi.spyOn(api, 'post').mockResolvedValue({ data: update })
    acceptSpatialMission(projection('NAVIGATING', 1))
    const state = useSpatialMission(),
      poll = state.pollMission()
    await state.replan('safest')
    pending.resolve({ data: projection('NAVIGATING', 2) })
    await poll
    expect(state.mission.value?.execution?.last_sequence).toBe(3)
    expect(state.mission.value?.task_graph.completed_goal_ids).toEqual(['P-IC'])
  })
  it('does not interpolate pose or mark stations completed as time passes', async () => {
    const update = projection('NAVIGATING', 1)
    vi.spyOn(api, 'get').mockResolvedValue({ data: update })
    acceptSpatialMission(update)
    await vi.advanceTimersByTimeAsync(9000)
    expect(useSpatialMission().mission.value?.execution?.current_pose).toEqual(
      update.execution?.current_pose,
    )
    expect(useSpatialMission().mission.value?.task_graph.completed_goal_ids).toEqual([])
  })
  it('preserves an edited profile and plan preview across unchanged execution polling', async () => {
    mockReads()
    const observed = projection('NAVIGATING', 2)
    vi.spyOn(api, 'post').mockResolvedValue({ data: { ...plan('preview'), profile: 'safest' } })
    acceptSpatialMission(observed)
    const state = useSpatialMission()
    await state.initialize()
    state.setProfile('safest')
    await state.planMission()
    await state.pollMission()
    expect(state.selectedProfile.value).toBe('safest')
    expect(state.previewPlan.value?.mission_id).toBe('preview')
  })
  it('removes each canonical scenario with its own physical and spatial identity', async () => {
    mockReads()
    const observed = projection('NAVIGATING', 2)
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: observed })
    acceptSpatialMission(observed)
    await useSpatialMission().removeObstacle('isolated-dock')
    await useSpatialMission().removeObstacle('blocked-crossing')
    expect(post.mock.calls[0][1]).toEqual({
      operation: 'remove',
      id: 'isolated-dock',
      scenario_id: 'isolated-dock',
    })
    expect(post.mock.calls[1][1]).toEqual({
      operation: 'remove',
      id: 'blocked-crossing',
      scenario_id: 'blocked-crossing',
    })
  })
  it('publishes selected layers, server plan and observed pose and detaches scene listeners', async () => {
    mockReads()
    const listener = vi.fn()
    const detach = subscribeSpatialScene(listener)
    await useSpatialMission().loadSnapshot()
    const observed = projection('NAVIGATING', 1)
    acceptSpatialMission(observed)
    for (const layer of ['semantics', 'rules', 'cost', 'dynamic'] as const)
      useSpatialMission().toggleLayer(layer)
    expect(listener).toHaveBeenLastCalledWith(
      expect.objectContaining({
        layers: ['geometry', 'trajectory', 'semantics', 'rules', 'cost', 'dynamic'],
        execution: observed.execution,
        mission: observed.mission_plan,
      }),
    )
    const count = listener.mock.calls.length
    detach()
    useSpatialMission().toggleLayer('trajectory')
    expect(listener).toHaveBeenCalledTimes(count)
  })
  it('updates shared Agent results from polling only inside the active conversation', async () => {
    const response: AgentQueryResponse = {
      answer: '空间任务已建立',
      narrative: '',
      intent: 'spatial_mission',
      entities: { spatial_mission: projection() },
      grounded_facts: [],
      tool_events: [],
      ui_actions: [],
      proposal_ids: [],
      telemetry: [],
      execution_mode: 'deterministic',
      model_call_count: 0,
      request_id: 'request-1',
      conversation_id: 'conversation-1',
    }
    vi.spyOn(api, 'post').mockResolvedValue({ data: response })
    const state = useWarehouseAgent()
    await state.submit('先送IC和传感器，再到实验室交接')
    const observed = projection('NAVIGATING', 4)
    acceptSpatialMission(observed)
    expect(state.result.value?.entities.spatial_mission).toEqual(observed)
    expect(state.conversation.value[0].response?.entities.spatial_mission).toEqual(observed)
    state.newConversation()
    expect(useSpatialMission().mission.value).toBeNull()
  })
})

describe('spatial mission cards and observability', () => {
  it.each([
    ['replay', SpatialReplayDrawer],
    ['benchmark', SpatialBenchmarkDrawer],
  ] as const)(
    'opens the %s dialog above the shell and returns keyboard focus',
    async (_name, component) => {
      mockReads()
      const opener = document.createElement('button')
      document.body.append(opener)
      opener.focus()
      const wrapper = mount(component, { props: { open: true } })
      await flushPromises()
      const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!
      expect(dialog.parentElement?.parentElement).toBe(document.body)
      expect(document.activeElement).toBe(dialog)
      dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      expect(wrapper.emitted('close')).toHaveLength(1)
      await wrapper.setProps({ open: false })
      expect(document.activeElement).toBe(opener)
      wrapper.unmount()
      opener.remove()
    },
  )
  it('keeps scan labels unique across compact cards for the same mission', async () => {
    mockReads()
    acceptSpatialMission(projection('AWAITING_HANDOFF', 4))
    const wrapper = mount(
      {
        components: { SpatialMissionCard },
        template: '<SpatialMissionCard compact /><SpatialMissionCard compact />',
      },
      { global: { plugins: [router()] } },
    )
    await flushPromises()
    const inputs = wrapper.findAll('input')
    const labels = wrapper.findAll('label')
    expect(inputs).toHaveLength(2)
    expect(new Set(inputs.map((input) => input.attributes('id'))).size).toBe(2)
    expect(labels.map((label) => label.attributes('for'))).toEqual(
      inputs.map((input) => input.attributes('id')),
    )
    wrapper.unmount()
  })
  it('renders the shared live graph in the floating conversation and keeps historical turns separate', async () => {
    mockReads()
    const state = useWarehouseAgent()
    const previous = projection('INFEASIBLE', 0, 'SM-LOW')
    const current = projection('AWAITING_HANDOFF', 4)
    for (const [id, mission] of [
      [1, previous],
      [2, current],
    ] as const) {
      state.conversation.value.push({
        id: String(id),
        question: '只规划 V4 空间任务',
        pending: false,
        error: '',
        response: {
          answer: '任务状态',
          narrative: '',
          intent: 'spatial_mission',
          entities: { spatial_mission: mission },
          grounded_facts: [],
          tool_events: [],
          ui_actions: [],
          proposal_ids: [],
          telemetry: [],
          execution_mode: 'deterministic',
          model_call_count: 0,
          request_id: `request-${id}`,
          conversation_id: 'conversation-1',
        },
      })
    }
    acceptSpatialMission(current)
    const wrapper = mount(FloatingAgentConversation, {
      global: { plugins: [router()], stubs: { 'el-button': true, 'el-icon': true } },
    })
    await flushPromises()
    const cards = wrapper.findAllComponents(SpatialMissionCard)
    expect(cards).toHaveLength(2)
    expect(cards[0]!.props('mission')?.mission_id).toBe('SM-LOW')
    expect(cards[1]!.props('mission')?.mission_id).toBe('SM-1')
    const completed = projection('COMPLETED', 8)
    completed.task_graph.completed_goal_ids = ['P-IC', 'P-SENSOR', 'P-LAB']
    completed.task_graph.remaining_goal_ids = []
    acceptSpatialMission(completed)
    await flushPromises()
    expect(cards[1]!.text()).toContain('3 / 3 交接')
    expect(cards[0]!.text()).toContain('0 / 3 交接')
    wrapper.unmount()
  })
  it('shows server status and keeps handoff progress independent of navigation arrival', async () => {
    mockReads()
    const observed = projection('AWAITING_HANDOFF', 4)
    acceptSpatialMission(observed)
    const wrapper = mount(SpatialMissionCard, {
      props: { mission: observed },
      global: { plugins: [router()] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('等待扫码交接')
    expect(wrapper.text()).toContain('0 / 3 交接')
    expect(wrapper.text()).toContain('79.5')
    expect(wrapper.find('[role="progressbar"]').attributes('aria-valuenow')).toBe('0')
    expect(wrapper.find('canvas').exists()).toBe(false)
    wrapper.unmount()
  })
  it('does not progress on a wrong scan and displays the server rejection event', async () => {
    mockReads()
    const awaiting = projection('AWAITING_HANDOFF', 4)
    acceptSpatialMission(awaiting)
    const rejected = projection('AWAITING_HANDOFF', 5)
    rejected.task_graph.events.push({
      event_id: 'scan-5',
      sequence: 5,
      type: 'scan_rejected',
      timestamp: '2026-10-04T12:01:00Z',
      goal_id: 'P-IC',
      details: {},
    })
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: rejected })
    const wrapper = mount(SpatialMissionCard, { global: { plugins: [router()] } })
    await flushPromises()
    await wrapper.find('input').setValue('错误货架扫码')
    await wrapper.find('input').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(post).toHaveBeenCalledWith(
      '/spatial/missions/SM-1/handoff',
      { goal_id: 'P-IC', scan_code: '错误货架扫码' },
      { timeout: 30_000 },
    )
    expect(wrapper.text()).toContain('扫码不匹配')
    expect(wrapper.find('[role="progressbar"]').attributes('aria-valuenow')).toBe('0')
    expect(useSpatialMission().mission.value?.task_graph.completed_goal_ids).toEqual([])
    wrapper.unmount()
  })
  it('renders recovery and preserved completed stations from the graph', async () => {
    mockReads()
    const value = projection('BLOCKED', 7)
    value.task_graph.completed_goal_ids = ['P-IC']
    value.task_graph.remaining_goal_ids = ['P-SENSOR', 'P-LAB']
    value.task_graph.recovery = { action: 'replan_remaining', code: 'ROUTE_BLOCKED' }
    acceptSpatialMission(value)
    const wrapper = mount(SpatialMissionCard, {
      props: { compact: true },
      global: { plugins: [router()] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('保留 1 个已交接站点')
    expect(wrapper.text()).toContain('1 / 3 交接')
    expect(wrapper.find('.spatial-graph').attributes('open')).toBeUndefined()
    wrapper.unmount()
  })
  it('shows observed plugins, lifecycle, collision and clearance without relabeling a plan estimate', async () => {
    mockReads()
    const state = useSpatialMission()
    await state.initialize()
    acceptSpatialMission(projection('NAVIGATING', 3))
    const wrapper = mount(SpatialNavInspector)
    expect(wrapper.text()).toContain('observed::Lattice')
    expect(wrapper.text()).toContain('observed::MPPI')
    expect(wrapper.text()).toContain('0.128')
    expect(wrapper.text()).toContain('9 条反馈')
    expect(wrapper.text()).toContain('精密防静电区')
    expect(wrapper.text()).toContain('0.45 m/s')
    expect(wrapper.text()).toContain('planner_server')
    wrapper.unmount()
  })
  it('keeps execution metrics unknown before the first actual execution', async () => {
    mockReads()
    await useSpatialMission().initialize()
    acceptSpatialMission(projection())
    const wrapper = mount(SpatialNavInspector)
    expect(wrapper.text()).toContain('任务实测指标保持未知')
    expect(wrapper.findAll('.spatial-metrics strong').map((item) => item.text())).toEqual([
      '— m',
      '— m',
      '—',
      '— s',
    ])
    wrapper.unmount()
  })
  it('separates low-battery planning constraints from observed mission battery and explicit start', async () => {
    mockReads()
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: plan() })
    acceptSpatialMission(projection('NAVIGATING', 2))
    const wrapper = mount(SpatialObservability, { global: { plugins: [router()] } })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '任务 / Nav2')!
      .trigger('click')
    await wrapper.find('select[aria-label="空间任务场景"]').setValue('low-battery')
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '预览路线')!
      .trigger('click')
    await flushPromises()
    expect(post).toHaveBeenCalledWith(
      '/spatial/missions/plan',
      expect.objectContaining({ constraints: expect.objectContaining({ battery_pct: 12 }) }),
      { timeout: 90_000 },
    )
    expect(useSpatialMission().mission.value?.execution?.robot_state.battery_pct).toBe(79.5)
    expect(post.mock.calls.some(([url]) => String(url).endsWith('/start'))).toBe(false)
    expect(wrapper.findAll('.spatial-layer-controls button')).toHaveLength(6)
    wrapper.unmount()
  })
  it('loads real benchmark values with the cold timeout and keeps Nav2 NOT_RUN and null metrics visible', async () => {
    const get = mockReads()
    const wrapper = mount(SpatialBenchmarkDrawer, {
      props: { open: true },
      global: { stubs: { teleport: true } },
    })
    await flushPromises()
    expect(get).toHaveBeenCalledWith('/spatial/benchmarks', { timeout: 180_000 })
    expect(wrapper.text()).toContain('Nav2 State Lattice')
    expect(wrapper.text()).toContain('未运行')
    expect(wrapper.text()).toContain('75.0%')
    expect(wrapper.text()).toContain('321.5 / 437.7')
    expect(wrapper.text()).toContain('规划估算')
    expect(wrapper.findAll('tbody td').map((item) => item.text())).toContain('—')
    expect(wrapper.text()).toContain('source-bound-sha')
    wrapper.unmount()
  })
  it('deduplicates an expensive benchmark request and excludes late-account results', async () => {
    const pending = deferred<{ data: WarehouseBenchmark }>()
    const get = vi.spyOn(api, 'get').mockReturnValue(pending.promise)
    const state = useSpatialMission()
    const requests = [state.loadBenchmark(), state.loadBenchmark()]
    expect(get).toHaveBeenCalledOnce()
    resetWarehouseAgentSession()
    pending.resolve({ data: benchmark() })
    await Promise.all(requests)
    expect(state.benchmark.value).toBeNull()
  })
  it('keeps unknown numeric fields distinct from measured zero and maps registered polygons', () => {
    expect(formatMetric(undefined)).toBe('—')
    expect(formatMetric(NaN)).toBe('—')
    expect(formatMetric(0)).toBe('0.0')
    expect(formatPercent(null)).toBe('—')
    expect(currentZones(map(), { x: 2, y: 3, yaw: 0 }).map((zone) => zone.id)).toEqual(['ESD'])
    expect(currentZones(map(), { x: 0, y: 0, yaw: 0 }).map((zone) => zone.id)).toEqual(['ESD'])
    const snapshot = map()
    snapshot.zones[0].polygon.coordinates.push([
      [1, 1],
      [3, 1],
      [3, 4],
      [1, 4],
      [1, 1],
    ])
    expect(currentZones(snapshot, { x: 2, y: 3, yaw: 0 })).toEqual([])
  })
})
