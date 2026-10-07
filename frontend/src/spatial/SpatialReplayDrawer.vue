<script setup lang="ts">
import { computed, watch } from 'vue'
import {
  eventLabel,
  formatMetric,
  record,
  skillLabel,
  statusLabel,
  textValue,
} from './presentation'
import { useSpatialMission } from './useSpatialMission'
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()
const { episode, episodeLoading, mission, loadEpisode, error } = useSpatialMission()
watch(
  () => props.open,
  (value) => {
    if (value) void loadEpisode()
  },
  { immediate: true },
)
const steps = computed(() =>
  Array.isArray(episode.value?.steps) ? episode.value.steps.map(record) : [],
)
const finalState = computed(() => record(episode.value?.final_state))
const metrics = computed(() => record(episode.value?.final_metrics))
function download() {
  if (!episode.value) return
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(episode.value, null, 2)], { type: 'application/json' }),
  )
  const link = document.createElement('a')
  link.href = url
  link.download = `spatial-episode-${mission.value?.mission_id || 'record'}.json`
  link.click()
  URL.revokeObjectURL(url)
}
</script>
<template>
  <div
    v-if="open"
    class="spatial-drawer-backdrop"
    @click.self="emit('close')"
    @keydown.esc="emit('close')"
  >
    <aside
      class="spatial-drawer"
      role="dialog"
      aria-modal="true"
      aria-label="空间任务回放"
      tabindex="-1"
    >
      <header>
        <div>
          <small>Observed execution</small>
          <h3>任务回放</h3>
        </div>
        <button aria-label="关闭任务回放" @click="emit('close')">关闭</button>
      </header>
      <p v-if="episodeLoading" role="status">正在读取服务器记录…</p>
      <p v-else-if="!episode" role="status">{{ error || '尚未读取任务记录。' }}</p>
      <template v-else>
        <p class="spatial-caption">
          {{ textValue(episode.mission_id) }} · {{ statusLabel(textValue(finalState.status)) }}
        </p>
        <div class="spatial-metrics">
          <div>
            <small>实际交接</small><strong>{{ formatMetric(metrics.completed_stops, 0) }}</strong>
          </div>
          <div>
            <small>观测事件</small
            ><strong>{{ formatMetric(metrics.observed_event_count, 0) }}</strong>
          </div>
          <div>
            <small>实测距离</small
            ><strong>{{ formatMetric(metrics.observed_distance_m) }} m</strong>
          </div>
          <div>
            <small>实测碰撞</small><strong>{{ formatMetric(metrics.collision_count, 0) }}</strong>
          </div>
        </div>
        <p class="spatial-caption">
          地图修订 {{ textValue(episode.navigation_revision).slice(0, 16) }} · {{ episode.source }}
        </p>
        <p v-if="episode.trace_truncated" class="spatial-warning">
          早期记录已超出对话窗口，导出标记为截断。
        </p>
        <ol class="spatial-replay-steps">
          <li v-for="(step, index) in steps" :key="String(step.sequence ?? index)">
            <span>{{ step.sequence }}</span>
            <div>
              <b>{{ eventLabel(textValue(record(step.outcome).type, '记录')) }}</b
              ><small>{{ skillLabel(textValue(record(step.chosen_skill).name, '')) }}</small>
            </div>
          </li>
        </ol>
        <p v-if="!steps.length" class="spatial-caption">尚无执行事件；规划结果不会记为完成。</p>
        <footer>
          <small
            >ROS2 导航仿真 · 库存写入
            {{ episode.inventory_written === false ? '无' : '未提供' }}</small
          ><button @click="download">导出 JSON</button>
        </footer>
      </template>
    </aside>
  </div>
</template>
<style scoped>
.spatial-replay-steps {
  padding: 0;
  list-style: none;
  max-height: 360px;
  overflow: auto;
}
.spatial-replay-steps li {
  display: flex;
  gap: 12px;
  padding: 9px 0;
  border-bottom: 1px solid var(--g-line, #dbe7ed);
  font-size: 13px;
}
.spatial-replay-steps li > span {
  min-width: 30px;
  color: var(--g-muted, #607d8e);
}
.spatial-replay-steps small {
  display: block;
  margin-top: 4px;
}
</style>
