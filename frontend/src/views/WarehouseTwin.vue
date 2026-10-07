<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import GlacierWarehouse from '../glacier/components/GlacierWarehouse.vue'
import EmbodiedTwin from '../embodied/components/EmbodiedTwin.vue'
import type { TwinSnapshot, TwinRoute, TwinTask } from '../components/locations/digitalTwin/types'
const props = withDefaults(
  defineProps<{ taskId?: number | null; focusLocationId?: number | null; embedded?: boolean }>(),
  { taskId: null, focusLocationId: null, embedded: false },
)
const route = useRoute(),
  router = useRouter(),
  snapshot = ref<TwinSnapshot | null>(null),
  loading = ref(false),
  error = ref(''),
  routeBusy = ref(false),
  closedEdges = ref<string[]>([])
const view = ref<{ diagnostics: () => Record<string, unknown> | null } | null>(null)
let generation = 0,
  routeGeneration = 0
const focus = computed(
  () => positiveId(props.focusLocationId) || positiveId(route.query.focus) || undefined,
)
const taskId = computed(() => positiveId(props.taskId) || positiveId(route.query.task))
const isRobotLab = computed(
  () => route.query.workspace === 'robot-lab' && !taskId.value && !props.embedded,
)
const closures = computed(() => snapshot.value?.map.edges || [])
function positiveId(raw: unknown) {
  const n = Number(raw)
  return Number.isSafeInteger(n) && n > 0 ? n : null
}
function loadScene() {
  return import('../components/locations/digitalTwin/scene.js')
}
defineExpose({ getDiagnostics: () => view.value?.diagnostics() ?? null })
async function refresh() {
  const token = ++generation
  ++routeGeneration
  routeBusy.value = false
  loading.value = true
  error.value = ''
  try {
    const task = taskId.value ? (await api.get<TwinTask>(`/pick-tasks/${taskId.value}`)).data : null
    if (token !== generation) return
    let mapId = task?.warehouse_map_id || positiveId(route.query.map)
    if (task && !mapId) throw new Error('此任务尚未关联仓库地图。')
    if (!mapId) {
      const locations = (await api.get<{ id: number; type: string }[]>('/locations')).data
      if (token !== generation) return
      const warehouse =
        positiveId(route.query.warehouse) || locations.find((x) => x.type === 'warehouse')?.id
      if (!warehouse) throw new Error('尚未配置仓库。')
      const maps = (
        await api.get<{ id: number; status: string; code: string }[]>('/warehouse-maps', {
          params: { warehouse_id: warehouse },
        })
      ).data
      mapId = (maps.find((x) => x.code === 'WH-RD-TWIN-V4') || maps.find((x) => x.code === 'WH-RD-TWIN-V3') || maps.find((x) => x.status === 'active') || maps[0])?.id
    }
    if (token !== generation) return
    if (!mapId) throw new Error('当前仓库尚未配置地图。')
    const fresh = (await api.get<TwinSnapshot>(`/warehouse-maps/${mapId}/twin-snapshot`)).data
    if (token !== generation) return
    // Keep identity, geometry and physical numbering exactly as provided by the API.
    fresh.task = task
    fresh.route = null
    if (task) {
      if (task.warehouse_graph_hash && task.warehouse_graph_hash !== fresh.map.graph_hash)
        error.value = '任务路线需要更新；仓库地图仍可查看。'
      else fresh.route = task.route_plan
    }
    closedEdges.value = closedEdges.value.filter((code) =>
      fresh.map.edges.some((e) => e.code === code),
    )
    fresh.closed_edge_codes = [...closedEdges.value]
    snapshot.value = fresh
  } catch (e) {
    if (token === generation)
      error.value = e instanceof Error ? e.message : '仓库未能刷新，已保留上次视图。'
  } finally {
    if (token === generation) loading.value = false
  }
}
async function previewRoute(ids: number[]) {
  const current = snapshot.value
  if (!current?.map.id || routeBusy.value || taskId.value) return
  const mapGeneration = generation,
    token = ++routeGeneration
  routeBusy.value = true
  try {
    const response = await api.post<TwinRoute>(`/warehouse-maps/${current.map.id}/route-preview`, {
      location_ids: ids,
      closed_edge_codes: closedEdges.value,
    })
    if (mapGeneration !== generation || token !== routeGeneration) return
    snapshot.value = { ...current, route: response.data, closed_edge_codes: [...closedEdges.value] }
    error.value = ''
  } catch {
    if (token === routeGeneration) error.value = '路线暂未生成；可以继续查看设备和库位。'
  } finally {
    if (token === routeGeneration) routeBusy.value = false
  }
}
function openLocation(id: number) {
  void router.push({ path: '/locations', query: { focus: id, return_to: route.fullPath } })
}
function openTask() {
  if (taskId.value) void router.push({ path: '/projects', query: { task: taskId.value } })
}
function switchWorkspace(lab: boolean) {
  const query = { ...route.query }
  delete query.workspace
  delete query.nav_goals
  delete query.nav_scenario
  if (lab) query.workspace = 'robot-lab'
  void router.push({ path: '/warehouse-twin', query })
}
function closuresChanged() {
  ++routeGeneration
  routeBusy.value = false
  if (snapshot.value)
    snapshot.value = { ...snapshot.value, route: null, closed_edge_codes: [...closedEdges.value] }
}
onMounted(() => {
  if (!isRobotLab.value) void refresh()
})
watch(isRobotLab, (lab) => {
  if (!lab && !snapshot.value) void refresh()
})
watch([() => route.query.map, () => route.query.warehouse, taskId], () => {
  snapshot.value = null
  closedEdges.value = []
  void refresh()
})
onBeforeUnmount(() => {
  generation++
  routeGeneration++
})
</script>
<template>
  <header v-if="!embedded && !taskId" class="g-page-heading warehouse-workspace-heading">
    <div>
      <h1>{{ isRobotLab ? '具身智能实验室' : '数字孪生仓库' }}<span class="g-title-dot">.</span></h1>
      <p class="g-muted">{{ isRobotLab ? '让空间任务在真实三维场景中走通。' : '看见每一个库位，让每一次移动都有依据。' }}</p>
    </div>
    <div class="g-segmented" role="group" aria-label="仓库模式">
      <button :class="{ active: !isRobotLab }" :aria-pressed="!isRobotLab" data-testid="warehouse-mode-live" @click="switchWorkspace(false)">数字孪生仓库</button>
      <button :class="{ active: isRobotLab }" :aria-pressed="isRobotLab" data-testid="warehouse-mode-lab" @click="switchWorkspace(true)">具身智能实验室</button>
    </div>
  </header>
  <EmbodiedTwin v-if="isRobotLab" :key="route.fullPath" />
  <section
    v-else
    class="g-warehouse-live"
    :class="{ 'is-embedded': embedded }"
    :aria-busy="loading"
    data-testid="warehouse-twin-page"
  >
    <div v-if="error" class="g-inline-notice" role="alert">
      <span>{{ error }}</span
      ><button class="g-text-btn" @click="refresh">重试</button
      ><button v-if="taskId" class="g-text-btn" @click="openTask">查看任务</button>
    </div>
    <div v-if="loading" class="g-inline-notice" role="status">正在读取仓库数据…</div>
    <div v-if="snapshot && !embedded" class="g-map-actions">
      <template v-if="!taskId"
        ><span>通道临时封闭</span
        ><el-select
          v-model="closedEdges"
          multiple
          collapse-tags
          placeholder="全部通道可用"
          style="width: 260px"
          @change="closuresChanged"
          ><el-option
            v-for="edge in closures"
            :key="edge.code"
            :value="edge.code"
            :label="edge.from_node + ' → ' + edge.to_node" /></el-select></template
      ><button v-if="taskId" class="g-btn" @click="openTask">查看拣货任务</button
      ><span v-if="routeBusy" role="status">正在规划路线…</span>
    </div>
    <GlacierWarehouse
      v-if="snapshot"
      ref="view"
      :snapshot="snapshot"
      :load-scene="loadScene"
      :initial-focus-id="focus"
      :initial-mode="route.query.mode === '2d' ? '2d' : '3d'"
      :embedded="embedded"
      :hide-heading="!embedded && !taskId"
      :allow-route-preview="!taskId"
      @open-location="openLocation"
      @preview-route="previewRoute"
      @refresh="refresh"
    />
    <div v-else-if="!loading" class="g-card g-brain-empty">
      <h2>从真实库位建立仓库</h2>
      <p>保留已有设备和编号，配置地图后即可查看。</p>
      <button class="g-btn" @click="router.push('/locations')">打开可视化库位</button>
    </div>
  </section>
</template>

<style scoped>
.warehouse-workspace-heading { padding: 22px 26px 8px; margin-bottom: 10px; }
.g-map-actions { padding: 10px 26px 14px; }
.warehouse-workspace-heading p { margin-top: 8px; font-size: 14px; }
@media (max-width: 1024px) { .warehouse-workspace-heading { align-items: flex-start; flex-wrap: wrap; } }
@media (max-width: 600px) {
  .warehouse-workspace-heading { padding: 18px 16px 6px; }
  .g-map-actions { padding: 8px 16px 12px; }
  .warehouse-workspace-heading h1 { font-size: 25px; }
  .warehouse-workspace-heading .g-segmented { width: 100%; }
  .warehouse-workspace-heading .g-segmented button { flex: 1; padding-inline: 9px; white-space: normal; }
}
</style>
