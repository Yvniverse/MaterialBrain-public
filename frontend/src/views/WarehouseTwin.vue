<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import { mountWarehouseUI } from '../components/locations/digitalTwin/ui.js'
import type { TwinSnapshot, TwinController, TwinRoute, TwinTask } from '../components/locations/digitalTwin/types'
import '../components/locations/digitalTwin/twin.css'

const props = withDefaults(
  defineProps<{
    taskId?: number | null
    focusLocationId?: number | null
    embedded?: boolean
  }>(),
  { taskId: null, focusLocationId: null, embedded: false },
)

const route = useRoute()
const router = useRouter()
const host = ref<HTMLDivElement | null>(null)
const error = ref('')
const loading = ref(false)
let view: TwinController | null = null
let generation = 0
let closedEdgeCodes: string[] = []
defineExpose({ getDiagnostics: () => view?.stats() ?? null })
interface MapOption { id: number; code: string; status: string }
interface LocationOption { id: number; type: string }
function positiveId(raw: unknown): number | null {
  const value = Number(raw)
  return Number.isSafeInteger(value) && value > 0 ? value : null
}
async function refresh() {
  const token = ++generation
  view?.dispose()
  view = null
  loading.value = true
  error.value = ''
  try {
    const taskId = positiveId(props.taskId) || positiveId(route.query.task)
    const task = taskId ? (await api.get<TwinTask>(`/pick-tasks/${taskId}`)).data : null
    if (token !== generation) return
    let mapId = task ? task.warehouse_map_id : positiveId(route.query.map)
    if (task && !mapId) throw new Error('该拣货任务尚未关联仓库地图。')
    if (!mapId) {
      const locations = (await api.get<LocationOption[]>('/locations')).data
      const warehouse = positiveId(route.query.warehouse) || locations.find(item => item.type === 'warehouse')?.id
      if (!warehouse) throw new Error('尚未找到仓库。')
      const maps = (await api.get<MapOption[]>('/warehouse-maps', { params: { warehouse_id: warehouse } })).data
      mapId = (maps.find(item => item.code === 'WH-RD-TWIN-V4') || maps.find(item => item.code === 'WH-RD-TWIN-V3') || maps.find(item => item.status === 'active') || maps[0])?.id || null
    }
    if (token !== generation) return
    if (!mapId) throw new Error('当前仓库尚未配置地图。')
    const snapshot = (await api.get<TwinSnapshot>(`/warehouse-maps/${mapId}/twin-snapshot`)).data
    if (token !== generation) return
    if (task) {
      if (task.warehouse_graph_hash && task.warehouse_graph_hash !== snapshot.map.graph_hash)
        throw new Error('任务路线和地图版本不一致。请从拣货任务页面检查路线。')
      snapshot.task = task
      snapshot.route = task.route_plan
    } else {
      const demoCodes = [
        'PORT-IC-100',
        'PORT-LCSC-CONN-100',
        'PORT-PWR-6',
        'PORT-LCSC-CABLE-56',
        'PORT-PASSIVE-56',
        'PORT-LCSC-MODULE-56',
      ]
      const ids = demoCodes
        .map(code => snapshot.assets.find(item => item.code === code)?.location_id)
        .filter((id): id is number => !!id)
      const closures = snapshot.map.code === 'WH-RD-TWIN-V4' ? closedEdgeCodes : []
      if (ids.length) {
        snapshot.route = (
          await api.post<TwinRoute>(`/warehouse-maps/${mapId}/route-preview`, {
            location_ids: ids,
            closed_edge_codes: closures,
          })
        ).data
        snapshot.closed_edge_codes = closures
      }
    }
    if (token !== generation || !host.value) return
    view = mountWarehouseUI(host.value, snapshot, {
      initialMode: route.query.mode === '2d' ? '2d' : '3d',
      focusLocationId: positiveId(props.focusLocationId) || positiveId(route.query.focus),
      onRefresh: () => { void refresh() },
      closureActive: closedEdgeCodes.length > 0,
      onToggleClosure:
        snapshot.map.code === 'WH-RD-TWIN-V4' && !task
          ? () => {
              closedEdgeCodes = closedEdgeCodes.length ? [] : ['G31--G41']
              void refresh()
            }
          : undefined,
      onOpenLocation: id => { if (id) void router.push({ path: '/locations', query: { focus: id, return_to: route.fullPath } }) },
      onOpenTask: id => { if (id) void router.push({ path: '/projects', query: { task: id } }) },
    })
  } catch (cause) {
    if (token !== generation) return
    view?.dispose()
    view = null
    error.value = cause instanceof Error ? cause.message : '仓库数据加载失败。'
  } finally {
    if (token === generation) loading.value = false
  }
}
onMounted(() => { void refresh() })
watch(
  [() => route.fullPath, () => props.taskId, () => props.focusLocationId],
  () => { void refresh() },
)
onBeforeUnmount(() => { generation++; view?.dispose(); view = null })
</script>

<template>
  <section
    class="warehouse-twin-page"
    :class="{ 'is-embedded': embedded }"
    :aria-busy="loading"
    data-testid="warehouse-twin-page"
  >
    <div v-if="error" class="twin-load-error" role="alert">{{ error }} <button @click="refresh">重试</button></div>
    <div v-if="loading" class="twin-loading" role="status">正在读取仓库数据…</div>
    <div ref="host"></div>
  </section>
</template>

<style scoped>
.warehouse-twin-page { position: relative; min-width: 0; margin: -4px; }
.twin-loading { position: absolute; top: 0; right: 0; z-index: 4; padding: 7px 12px; color: #60798e; background: #f8fafddc; font-size: 12px; border-radius: 6px; }
.twin-load-error { padding: 14px; color: #9a5e39; background: #fff8ef; border: 1px solid #eedac5; border-radius: 8px; }
.twin-load-error button { margin-left: 10px; }
.warehouse-twin-page.is-embedded { margin: 0; min-height: 0; height: 100%; }
.warehouse-twin-page.is-embedded :deep(.tw-header),
.warehouse-twin-page.is-embedded :deep(.tw-side),
.warehouse-twin-page.is-embedded :deep(.tw-meta) { display: none; }
.warehouse-twin-page.is-embedded :deep(.tw-workspace) { grid-template-columns: minmax(0, 1fr); }
.warehouse-twin-page.is-embedded :deep(.tw-stage) { min-height: 540px; }
</style>
