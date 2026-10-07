<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api/client'
import { mountProductionLabApp } from './production-runtime.mjs'
import world from '../scene/world.v3.json'
import botUrl from '../assets/materialbrain-bot-v3.png'
import '../styles/lab.css'
import { attachNavigationSession, takeNavigationSession } from '../navigationSession'
import SpatialObservability from '../../spatial/SpatialObservability.vue'
import { subscribeSpatialScene } from '../../spatial/useSpatialMission'
const props = withDefaults(defineProps<{ useServer?: boolean }>(), { useServer: true })
const host = ref<HTMLElement | null>(null)
const assetsOpen = ref(false)
const simulationOpen = ref(false)
const router = useRouter()
const error = ref('')
const notice = ref('')
let disposed = false
let app: ReturnType<typeof mountProductionLabApp> | null = null
let detachSession: (() => void) | null = null
let detachSpatial: (() => void) | null = null
let layoutObserver: ResizeObserver | null = null
let layoutFrame = 0
function scheduleLayoutMeasure() {
  cancelAnimationFrame(layoutFrame)
  layoutFrame = requestAnimationFrame(() => {
    if (disposed || !host.value) return
    const layout = host.value.querySelector<HTMLElement>('.twin-layout')
    if (!layout) return
    const minimum = window.innerWidth <= 600 ? 420 : 480
    const height = Math.max(minimum, window.innerHeight - layout.getBoundingClientRect().top - 16)
    host.value.style.setProperty('--embodied-stage-height', `${height}px`)
  })
}
watch(notice, scheduleLayoutMeasure)
const diagnosticRequest = () =>
  window.dispatchEvent(
    new CustomEvent('embodied-lab:diagnostics', { detail: app?.diagnostics() || null }),
  )
function go(target: string) {
  const routes: Record<string, string> = {
    'warehouse-live': '/warehouse-twin',
    locations: '/locations',
    materials: '/materials',
    projects: '/projects',
    inventory: '/inventory',
    'floating-agent': '/agent',
  }
  if (routes[target]) void router.push(routes[target])
}
async function openLocation(selection: { reference_code?: string; slot?: string }) {
  type Location = {
    id: number
    code: string
    name?: string
    parent_id: number | null
    row_no?: number
    col_no?: number
    organizer_style?: string
  }
  try {
    const rows = (await api.get<Location[]>('/locations')).data
    const owner = rows.find((row) => row.code === selection.reference_code)
    if (!owner) {
      notice.value = '此设备为新增实验模型，没有同名真实库位。其他真实设备仍可正常打开。'
      return
    }
    const slot = selection.slot || ''
    const key = slot.split('/')[0].trim()
    const match = /^([A-G])(\d{2})$/.exec(key)
    const child = rows.find(
      (row) =>
        row.parent_id === owner.id &&
        (row.code === key ||
          row.code.endsWith('-' + key) ||
          row.name === key ||
          (match &&
            (owner.organizer_style === 'drawer_rack_100'
              ? row.row_no === Number(match[2]) && row.col_no === match[1].charCodeAt(0) - 64
              : row.row_no === match[1].charCodeAt(0) - 64 && row.col_no === Number(match[2])))),
    )
    if (!disposed)
      void router.push({
        path: '/locations',
        query: { focus: child?.id || owner.id, return_to: router.currentRoute.value.fullPath },
      })
  } catch {
    notice.value = '真实库位暂未读取，可以继续查看实验仓。'
  }
}

onMounted(async () => {
  if (!host.value) return
  try {
    const data = props.useServer ? (await api.get('/navigation-lab/world')).data : world
    if (disposed || !host.value) return
    const restored = takeNavigationSession(
      typeof router.currentRoute.value.query.nav_goals === 'string',
    )
    app = mountProductionLabApp(host.value, data, {
      botUrl,
      showNavigation: false,
      showBot: false,
      onNavigate: go,
      onOpenLocation: openLocation,
      initialExecution: restored?.execution,
      initialGoalIds:
        restored?.goals ||
        (typeof router.currentRoute.value.query.nav_goals === 'string'
          ? (router.currentRoute.value.query.nav_goals as string).split(',').filter(Boolean)
          : null),
      initialScenario:
        restored?.scenario ||
        (typeof router.currentRoute.value.query.nav_scenario === 'string'
          ? (router.currentRoute.value.query.nav_scenario as string)
          : 'baseline'),
      planningClient: props.useServer
        ? async (request: unknown) =>
            (await api.post('/navigation-lab/plan', request, { timeout: 90_000 })).data
        : null,
    })
    const mounted = app
    detachSession = attachNavigationSession(() => mounted.captureExecution())
    if (props.useServer)
      detachSpatial = subscribeSpatialScene(({ snapshot, layers, mission, execution }) => {
        mounted.setSpatialLayers(snapshot, layers, mission, execution)
      })
    window.addEventListener('embodied-lab:diagnostics-request', diagnosticRequest)
    window.addEventListener('resize', scheduleLayoutMeasure)
    if (typeof ResizeObserver !== 'undefined') {
      layoutObserver = new ResizeObserver(scheduleLayoutMeasure)
      layoutObserver.observe(host.value)
      if (host.value.parentElement) layoutObserver.observe(host.value.parentElement)
    }
    scheduleLayoutMeasure()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '实验仓加载失败'
  }
})
onBeforeUnmount(() => {
  disposed = true
  detachSession?.()
  detachSpatial?.()
  window.removeEventListener('embodied-lab:diagnostics-request', diagnosticRequest)
  window.removeEventListener('resize', scheduleLayoutMeasure)
  cancelAnimationFrame(layoutFrame)
  layoutObserver?.disconnect()
  app?.dispose()
  app = null
})
defineExpose({ diagnostics: () => app?.diagnostics() || null })
</script>
<template>
  <div v-if="error" class="embodied-load-error">
    <strong>实验仓暂未加载</strong>
    <p>{{ error }}</p>
    <p>真实仓库与其他业务页面继续可用。</p>
  </div>
  <p v-if="notice" class="embodied-notice" role="status">{{ notice }}</p>
  <section
    class="embodied-workspace"
    :class="{ 'show-assets': assetsOpen, 'show-simulation': simulationOpen }"
    data-testid="embodied-workspace"
  >
    <div class="embodied-workspace-tools" aria-label="实验室面板">
      <button class="g-btn" :aria-expanded="assetsOpen" @click="assetsOpen = !assetsOpen">
        设备与区域
      </button>
      <button
        class="g-btn"
        :aria-expanded="simulationOpen"
        @click="simulationOpen = !simulationOpen"
      >
        仿真任务
      </button>
    </div>
    <div class="embodied-spatial-controls">
      <SpatialObservability v-if="useServer && !error" />
    </div>
    <div ref="host" class="embodied-integrated-host" />
  </section>
</template>
<style scoped>
.embodied-notice {
  padding: 10px 15px;
  margin-bottom: 12px;
  background: #e6f0f5;
  color: #5c7d8d;
  border-radius: 9px;
}
.embodied-integrated-host {
  min-width: 0;
  min-height: 0;
  padding: 0;
}
.embodied-workspace {
  position: relative;
  min-width: 0;
  padding: 0 26px 26px;
}
.embodied-workspace-tools {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-bottom: 10px;
}
.embodied-spatial-controls {
  display: flow-root;
}
.embodied-load-error {
  padding: 24px;
  border: 1px solid #d5e6ee;
  border-radius: 16px;
  background: #fff;
  color: #526f80;
}
:deep(.topbar) {
  display: none;
}
:deep(.twin-layout) {
  height: var(--embodied-stage-height, calc(100dvh - 340px));
  min-height: 420px;
  grid-template-columns: 190px minmax(0, 1fr) 280px;
}
:deep(.mb-lab .page-heading) {
  display: none;
}
:deep(.mb-lab .bottom-strip) {
  font-size: 13px;
}
:deep(.mb-lab .stage-footer) {
  flex-wrap: wrap;
}
:deep(.mb-lab .stage-info span),
:deep(.mb-lab .legend),
:deep(.mb-lab .camera-tabs button),
:deep(.mb-lab .asset-row small),
:deep(.mb-lab .robot-card small),
:deep(.mb-lab .small),
:deep(.mb-lab .metrics small) {
  font-size: 13px;
}
:deep(.mb-lab button),
:deep(.mb-lab select),
:deep(.mb-lab input) {
  font-size: 14px;
}
:deep(.mb-lab label),
:deep(.mb-lab .g-goals span),
:deep(.mb-lab .chip) {
  font-size: 13px;
}
:deep(.mb-lab .asset-row strong) {
  font-size: 14px;
}
:deep(.mb-lab) {
  min-height: 0;
}
:deep(.page) {
  padding: 0;
}
@media (max-width: 1199px) {
  :deep(.twin-layout) {
    display: block;
    position: relative;
    height: var(--embodied-stage-height, calc(100dvh - 372px));
    min-height: 420px;
  }
  :deep(.mb-lab .stage-panel) {
    height: 100%;
  }
  :deep(.mb-lab .assets-panel),
  :deep(.mb-lab .mission-panel) {
    display: none;
  }
  .show-assets :deep(.mb-lab .assets-panel),
  .show-simulation :deep(.mb-lab .mission-panel) {
    display: flex;
    position: absolute;
    z-index: 12;
    top: 0;
    bottom: 0;
    width: min(310px, 90%);
    box-shadow: 0 8px 28px #315f7526;
  }
  .show-assets :deep(.mb-lab .assets-panel) {
    left: 0;
  }
  .show-simulation :deep(.mb-lab .mission-panel) {
    right: 0;
  }
}
@media (max-width: 600px) {
  .embodied-workspace {
    padding: 0 12px 20px;
  }
  :deep(.mb-lab .stage-toolbar) {
    flex-wrap: wrap;
    left: 10px;
    right: 10px;
    top: 10px;
  }
  :deep(.mb-lab .stage-info) {
    max-width: 100%;
  }
  :deep(.mb-lab .stage-footer) {
    left: 8px;
    right: 8px;
    bottom: 8px;
  }
  :deep(.mb-lab .legend) {
    font-size: 13px;
  }
  :deep(.mb-lab .camera-tabs button) {
    padding: 6px 7px;
    font-size: 14px;
  }
}
</style>
