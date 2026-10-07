import { computed, shallowReadonly, ref, shallowRef, watch } from 'vue'
import { api, uuidKey } from '../api/client'
import { apiErrorMessage } from '../utils/apiError'
import { finite, record } from './presentation'
import type {
  MissionPlan,
  MissionRequest,
  RouteProfile,
  SpatialLayer,
  SpatialMapSnapshot,
  SpatialMissionProjection,
  SpatialNavigationHealth,
  SpatialRecord,
  SpatialSceneProjection,
  WarehouseBenchmark,
} from './contracts'

const snapshot = shallowRef<SpatialMapSnapshot | null>(null)
const mission = shallowRef<SpatialMissionProjection | null>(null)
const previewPlan = shallowRef<MissionPlan | null>(null)
const health = shallowRef<SpatialNavigationHealth | null>(null)
const benchmark = shallowRef<WarehouseBenchmark | null>(null)
const episode = shallowRef<SpatialRecord | null>(null)
const readiness = shallowRef<SpatialRecord | null>(null)
const layers = ref<SpatialLayer[]>(['geometry', 'trajectory'])
const selectedProfile = ref<RouteProfile>('fastest')
const selectedGoalIds = ref<string[]>(['P-IC', 'P-SENSOR', 'P-LAB'])
const planningBatteryPct = ref<number | null>(null)
const error = ref('')
const errorCode = ref('')
const loadingSnapshot = ref(false)
const loadingHealth = ref(false)
const planning = ref(false)
const creating = ref(false)
const commandBusy = ref<string | null>(null)
const benchmarkLoading = ref(false)
const episodeLoading = ref(false)
const readinessLoading = ref(false)
let epoch = 0
let projectionVersion = 0
let conversationId: string | null = null
let pollTimer: ReturnType<typeof setTimeout> | null = null
let pollPromise: Promise<SpatialMissionProjection | null> | null = null
let snapshotPromise: Promise<SpatialMapSnapshot | null> | null = null
let healthPromise: Promise<SpatialNavigationHealth | null> | null = null
let benchmarkPromise: Promise<WarehouseBenchmark | null> | null = null
let healthReadAt = 0
let createOperation: { signature: string; id: string } | null = null
const missionListeners = new Set<(value: SpatialMissionProjection | null) => void>()
const sceneListeners = new Set<(value: SpatialSceneProjection) => void>()
const terminalStatuses = new Set(['COMPLETED', 'CANCELLED', 'FAILED'])

function clearError() {
  error.value = ''
  errorCode.value = ''
}
function reportError(caught: unknown) {
  const body = record(record(record(caught).response).data)
  errorCode.value = typeof body.code === 'string' ? body.code : ''
  error.value = typeof body.message === 'string' ? body.message : apiErrorMessage(caught)
}
function stopTimer() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
}
function schedulePoll() {
  stopTimer()
  const value = mission.value
  if (!value || terminalStatuses.has(value.task_graph.status) || !value.execution) return
  pollTimer = setTimeout(() => {
    pollTimer = null
    void pollMission()
  }, 1500)
}
function notifyMission() {
  for (const listener of missionListeners) listener(mission.value)
}
function sceneProjection(): SpatialSceneProjection {
  return {
    snapshot: snapshot.value,
    layers: [...layers.value],
    mission: previewPlan.value || mission.value?.mission_plan || null,
    execution: mission.value?.execution || null,
  }
}
watch(
  [snapshot, mission, previewPlan, layers],
  () => {
    const value = sceneProjection()
    for (const listener of sceneListeners) listener(value)
  },
  { flush: 'sync', deep: false },
)

/** Call only after the shared Agent request's account epoch has been checked. */
export function acceptSpatialMission(
  value: SpatialMissionProjection,
  expectedConversationId?: string | null,
): boolean {
  if (expectedConversationId && value.conversation_id !== expectedConversationId) return false
  const graph = value.task_graph
  if (
    !graph ||
    graph.mission_id !== value.mission_id ||
    graph.conversation_id !== value.conversation_id ||
    value.execution_boundary !== 'ros2_nav2_simulation' ||
    value.inventory_written !== false ||
    value.hardware_control !== false
  )
    return false
  if (
    value.mission_plan &&
    (value.mission_plan.map_revision !== graph.map_revision ||
      value.mission_plan.mission_id !== value.mission_id)
  )
    return false
  if (
    value.execution &&
    (value.execution.map_revision !== graph.map_revision ||
      value.execution.mission_id !== value.mission_id ||
      value.execution.hardware_control !== false)
  )
    return false
  // Ignore delayed feedback from a previous navigation sequence.
  if (
    mission.value?.mission_id === value.mission_id &&
    (value.execution?.last_sequence ?? -1) < (mission.value.execution?.last_sequence ?? -1)
  )
    return false
  projectionVersion += 1
  const previous = mission.value
  const previousId = previous?.mission_id
  const planChanged = JSON.stringify(previous?.mission_plan) !== JSON.stringify(value.mission_plan)
  conversationId = value.conversation_id
  mission.value = JSON.parse(JSON.stringify(value)) as SpatialMissionProjection
  if (
    previousId !== value.mission_id ||
    previous?.mission_plan?.profile !== value.mission_plan?.profile
  )
    selectedProfile.value = value.mission_plan?.profile || selectedProfile.value
  if (previousId !== value.mission_id || planChanged) previewPlan.value = null
  if (previousId !== value.mission_id) episode.value = null
  notifyMission()
  schedulePoll()
  return true
}
export function setSpatialConversationId(value: string | null) {
  conversationId = value
}
export function subscribeSpatialMission(
  listener: (value: SpatialMissionProjection | null) => void,
): () => void {
  missionListeners.add(listener)
  listener(mission.value)
  return () => {
    missionListeners.delete(listener)
  }
}
export function subscribeSpatialScene(
  listener: (value: SpatialSceneProjection) => void,
): () => void {
  sceneListeners.add(listener)
  listener(sceneProjection())
  return () => {
    sceneListeners.delete(listener)
  }
}
export function resetSpatialMissionSession() {
  epoch += 1
  projectionVersion += 1
  stopTimer()
  pollPromise = null
  snapshotPromise = null
  healthPromise = null
  benchmarkPromise = null
  healthReadAt = 0
  conversationId = null
  createOperation = null
  snapshot.value = null
  mission.value = null
  previewPlan.value = null
  health.value = null
  benchmark.value = null
  episode.value = null
  readiness.value = null
  layers.value = ['geometry', 'trajectory']
  selectedProfile.value = 'fastest'
  selectedGoalIds.value = ['P-IC', 'P-SENSOR', 'P-LAB']
  planningBatteryPct.value = null
  loadingSnapshot.value = false
  loadingHealth.value = false
  planning.value = false
  creating.value = false
  commandBusy.value = null
  benchmarkLoading.value = false
  episodeLoading.value = false
  readinessLoading.value = false
  clearError()
  notifyMission()
}

export function defaultMissionRequest(
  map: SpatialMapSnapshot,
  options: { profile?: RouteProfile; goalIds?: string[]; batteryPct?: number | null } = {},
): MissionRequest {
  const metadata = record(map.provenance),
    robot = record(metadata.robot)
  const home = map.docks.find((dock) => dock.id === (metadata.home_dock_id || 'HOME'))
  const capacity = robot.payload_kg,
    reserve = robot.reserve_pct,
    battery = options.batteryPct ?? robot.battery_pct
  if (
    !home ||
    !finite(home.pose.x) ||
    !finite(home.pose.y) ||
    !finite(home.pose.yaw) ||
    !finite(capacity) ||
    !finite(reserve) ||
    !finite(battery) ||
    typeof robot.id !== 'string'
  )
    throw new Error('注册地图缺少待命位姿或机器人约束，请刷新地图。')
  const goals = options.goalIds || ['P-IC', 'P-SENSOR', 'P-LAB']
  if (
    !goals.length ||
    goals.some(
      (id) => !map.docks.some((dock) => dock.id === id && !['HOME', 'CHARGER'].includes(id)),
    )
  )
    throw new Error('请选择注册交接站点。')
  if (battery < 0 || battery > 100) throw new Error('规划电量必须在 0–100% 之间。')
  return {
    map_id: map.map_id,
    map_revision: map.revision,
    profile: options.profile || 'fastest',
    start_pose: { ...home.pose },
    goal_ids: [...new Set(goals)],
    completed_goal_ids: [],
    stops: [],
    constraints: {
      payload_capacity_kg: capacity,
      battery_pct: battery,
      battery_reserve_pct: reserve,
      robot_class: robot.id,
    },
    return_home: true,
    dynamic_overlays: map.dynamic_overlays,
  }
}
function requestFromControls(): MissionRequest {
  if (!snapshot.value) throw new Error('请先加载注册地图。')
  return defaultMissionRequest(snapshot.value, {
    profile: selectedProfile.value,
    goalIds: selectedGoalIds.value,
    batteryPct: planningBatteryPct.value,
  })
}
function localInputError(caught: unknown) {
  error.value = caught instanceof Error ? caught.message : '请检查任务条件。'
  errorCode.value = 'SPATIAL_INPUT_REQUIRED'
}
async function loadSnapshot(force = false): Promise<SpatialMapSnapshot | null> {
  if (snapshotPromise) return snapshotPromise
  if (snapshot.value && !force) return snapshot.value
  const requestEpoch = epoch
  loadingSnapshot.value = true
  const pending = (async () => {
    try {
      const response = await api.get<SpatialMapSnapshot>('/spatial/maps/MB-EMB-LAB-03/snapshot')
      if (requestEpoch !== epoch) return null
      snapshot.value = response.data
      if (planningBatteryPct.value === null) {
        const value = record(record(response.data.provenance).robot).battery_pct
        planningBatteryPct.value = finite(value) ? value : null
      }
      return response.data
    } catch (caught) {
      if (requestEpoch === epoch) reportError(caught)
      return null
    } finally {
      if (requestEpoch === epoch) {
        loadingSnapshot.value = false
        snapshotPromise = null
      }
    }
  })()
  snapshotPromise = pending
  return pending
}
async function loadHealth(): Promise<SpatialNavigationHealth | null> {
  if (healthPromise) return healthPromise
  const requestEpoch = epoch
  loadingHealth.value = true
  const pending = (async () => {
    try {
      const response = await api.get<SpatialNavigationHealth>('/spatial/navigation/health')
      if (requestEpoch !== epoch) return null
      health.value = response.data
      healthReadAt = Date.now()
      return response.data
    } catch (caught) {
      if (requestEpoch === epoch) {
        health.value = null
        reportError(caught)
      }
      return null
    } finally {
      if (requestEpoch === epoch) {
        loadingHealth.value = false
        healthPromise = null
      }
    }
  })()
  healthPromise = pending
  return pending
}
async function pollMission(): Promise<SpatialMissionProjection | null> {
  if (pollPromise) return pollPromise
  if (!mission.value || commandBusy.value) {
    schedulePoll()
    return null
  }
  const requestEpoch = epoch,
    version = projectionVersion,
    id = mission.value.mission_id
  const pending = (async () => {
    try {
      const response = await api.get<SpatialMissionProjection>(
        `/spatial/missions/${encodeURIComponent(id)}`,
      )
      if (
        requestEpoch !== epoch ||
        version !== projectionVersion ||
        mission.value?.mission_id !== id
      )
        return null
      acceptSpatialMission(response.data, conversationId)
      if (Date.now() - healthReadAt >= 10_000) void loadHealth()
      return response.data
    } catch (caught) {
      if (requestEpoch === epoch && version === projectionVersion) reportError(caught)
      return null
    } finally {
      if (requestEpoch === epoch) {
        pollPromise = null
        schedulePoll()
      }
    }
  })()
  pollPromise = pending
  return pending
}
async function planMission(): Promise<MissionPlan | null> {
  if (planning.value || creating.value || commandBusy.value) return null
  let request: MissionRequest
  clearError()
  try {
    request = requestFromControls()
  } catch (caught) {
    localInputError(caught)
    return null
  }
  const requestEpoch = epoch,
    signature = JSON.stringify(request)
  planning.value = true
  try {
    const response = await api.post<MissionPlan>('/spatial/missions/plan', request, {
      timeout: 90_000,
    })
    if (requestEpoch !== epoch || signature !== JSON.stringify(requestFromControls())) return null
    previewPlan.value = response.data
    return response.data
  } catch (caught) {
    if (requestEpoch === epoch) reportError(caught)
    return null
  } finally {
    if (requestEpoch === epoch) planning.value = false
  }
}
async function createMission(): Promise<SpatialMissionProjection | null> {
  if (creating.value || planning.value || commandBusy.value) return null
  let request: MissionRequest
  clearError()
  try {
    request = requestFromControls()
  } catch (caught) {
    localInputError(caught)
    return null
  }
  const requestEpoch = epoch,
    version = projectionVersion,
    signature = JSON.stringify([conversationId, request])
  if (!createOperation || createOperation.signature !== signature)
    createOperation = { signature, id: uuidKey() }
  creating.value = true
  try {
    const response = await api.post<SpatialMissionProjection>(
      '/spatial/missions',
      { request, conversation_id: conversationId, client_operation_id: createOperation.id },
      { timeout: 90_000 },
    )
    if (requestEpoch !== epoch || version !== projectionVersion) return null
    if (!acceptSpatialMission(response.data, conversationId)) return null
    createOperation = null
    return response.data
  } catch (caught) {
    if (requestEpoch === epoch) reportError(caught)
    return null
  } finally {
    if (requestEpoch === epoch) creating.value = false
  }
}
async function command(
  action: string,
  body: SpatialRecord = {},
): Promise<SpatialMissionProjection | null> {
  const value = mission.value
  if (!value || commandBusy.value || planning.value || creating.value) return null
  clearError()
  stopTimer()
  projectionVersion += 1
  const requestEpoch = epoch,
    version = projectionVersion,
    id = value.mission_id
  commandBusy.value = action
  try {
    const response = await api.post<SpatialMissionProjection>(
      `/spatial/missions/${encodeURIComponent(id)}/${action}`,
      body,
      { timeout: action === 'replan' ? 90_000 : 30_000 },
    )
    if (requestEpoch !== epoch || version !== projectionVersion || mission.value?.mission_id !== id)
      return null
    if (!acceptSpatialMission(response.data, conversationId)) return null
    if (action === 'obstacles') await loadSnapshot(true)
    return response.data
  } catch (caught) {
    if (requestEpoch === epoch && version === projectionVersion) reportError(caught)
    return null
  } finally {
    if (requestEpoch === epoch && mission.value?.mission_id === id) {
      commandBusy.value = null
      schedulePoll()
    }
  }
}
async function loadBenchmark(): Promise<WarehouseBenchmark | null> {
  if (benchmarkPromise) return benchmarkPromise
  if (benchmark.value) return benchmark.value
  const requestEpoch = epoch
  benchmarkLoading.value = true
  clearError()
  const pending = (async () => {
    try {
      const response = await api.get<WarehouseBenchmark>('/spatial/benchmarks', {
        timeout: 180_000,
      })
      if (requestEpoch !== epoch) return null
      benchmark.value = response.data
      return response.data
    } catch (caught) {
      if (requestEpoch === epoch) reportError(caught)
      return null
    } finally {
      if (requestEpoch === epoch) {
        benchmarkLoading.value = false
        benchmarkPromise = null
      }
    }
  })()
  benchmarkPromise = pending
  return pending
}
async function loadEpisode(): Promise<SpatialRecord | null> {
  if (!mission.value || episodeLoading.value) return null
  const requestEpoch = epoch,
    id = mission.value.mission_id
  episodeLoading.value = true
  clearError()
  try {
    const response = await api.get<SpatialRecord>(
      `/spatial/missions/${encodeURIComponent(id)}/episode`,
    )
    if (requestEpoch !== epoch || mission.value?.mission_id !== id) return null
    episode.value = response.data
    return response.data
  } catch (caught) {
    if (requestEpoch === epoch) reportError(caught)
    return null
  } finally {
    if (requestEpoch === epoch) episodeLoading.value = false
  }
}
async function loadReadiness(): Promise<SpatialRecord | null> {
  if (readiness.value) return readiness.value
  if (readinessLoading.value) return null
  const requestEpoch = epoch
  readinessLoading.value = true
  clearError()
  try {
    const response = await api.get<SpatialRecord>('/spatial/readiness/contracts')
    if (requestEpoch !== epoch) return null
    readiness.value = response.data
    return response.data
  } catch (caught) {
    if (requestEpoch === epoch) reportError(caught)
    return null
  } finally {
    if (requestEpoch === epoch) readinessLoading.value = false
  }
}
function setProfile(value: RouteProfile) {
  selectedProfile.value = value
  previewPlan.value = null
}
function setGoalIds(value: string[]) {
  selectedGoalIds.value = [...value]
  previewPlan.value = null
}
function setPlanningBattery(value: number | null) {
  planningBatteryPct.value = value
  previewPlan.value = null
}
function toggleLayer(value: SpatialLayer) {
  if (value === 'geometry') {
    layers.value = ['geometry']
    return
  }
  layers.value = layers.value.includes(value)
    ? layers.value.filter((layer) => layer !== value)
    : [...layers.value, value]
}
const busy = computed(() => planning.value || creating.value || !!commandBusy.value)
const canStart = computed(
  () =>
    !!mission.value &&
    mission.value.mission_plan?.status === 'READY' &&
    ['READY', 'TRANSPORT_PAUSED', 'BLOCKED', 'BLOCKED_LOW_BATTERY'].includes(
      mission.value.task_graph.status,
    ) &&
    health.value?.ready === true &&
    health.value.map_revision === mission.value.task_graph.map_revision &&
    !busy.value,
)

/** One module instance, one poller, and no client mission runner. */
export function useSpatialMission() {
  return {
    snapshot: shallowReadonly(snapshot),
    mission: shallowReadonly(mission),
    previewPlan: shallowReadonly(previewPlan),
    health: shallowReadonly(health),
    benchmark: shallowReadonly(benchmark),
    episode: shallowReadonly(episode),
    readiness: shallowReadonly(readiness),
    layers: shallowReadonly(layers),
    selectedProfile: shallowReadonly(selectedProfile),
    selectedGoalIds: shallowReadonly(selectedGoalIds),
    planningBatteryPct: shallowReadonly(planningBatteryPct),
    error: shallowReadonly(error),
    errorCode: shallowReadonly(errorCode),
    loadingSnapshot: shallowReadonly(loadingSnapshot),
    loadingHealth: shallowReadonly(loadingHealth),
    planning: shallowReadonly(planning),
    creating: shallowReadonly(creating),
    commandBusy: shallowReadonly(commandBusy),
    benchmarkLoading: shallowReadonly(benchmarkLoading),
    episodeLoading: shallowReadonly(episodeLoading),
    readinessLoading: shallowReadonly(readinessLoading),
    busy,
    canStart,
    initialize: () => Promise.all([loadSnapshot(), loadHealth()]),
    loadSnapshot,
    loadHealth,
    pollMission,
    planMission,
    createMission,
    loadBenchmark,
    loadEpisode,
    loadReadiness,
    setProfile,
    setGoalIds,
    setPlanningBattery,
    toggleLayer,
    clearError,
    start: () => command('start'),
    cancel: () => command('cancel'),
    replan: (profile: RouteProfile = selectedProfile.value) => command('replan', { profile }),
    handoff: (goalId: string, scanCode: string) =>
      command('handoff', { goal_id: goalId, scan_code: scanCode }),
    addObstacle: (scenarioId: 'blocked-crossing' | 'isolated-dock' = 'blocked-crossing') =>
      command('obstacles', { operation: 'add', id: scenarioId, scenario_id: scenarioId }),
    removeObstacle: (id: 'blocked-crossing' | 'isolated-dock' = 'blocked-crossing') =>
      command('obstacles', { operation: 'remove', id, scenario_id: id }),
  }
}
