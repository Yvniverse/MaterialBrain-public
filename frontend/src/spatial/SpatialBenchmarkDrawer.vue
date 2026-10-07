<script setup lang="ts">
import { computed, watch } from 'vue'
import { formatMetric, formatPercent, profileLabels } from './presentation'
import type { RouteProfile } from './contracts'
import { useSpatialMission } from './useSpatialMission'
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()
const { benchmark, benchmarkLoading, error, loadBenchmark } = useSpatialMission()
const algorithmLabels: Record<string, string> = {
  heading_grid_astar: 'V3 航向栅格 A*',
  semantic_graph_ortools: '语义图 + OR-Tools',
  nav2_state_lattice: 'Nav2 State Lattice',
}
const rows = computed(() =>
  Object.entries(benchmark.value?.summaries || {}).map(([key, values]) => {
    const [algorithm, profile] = key.split(':')
    return {
      key,
      algorithm: algorithmLabels[algorithm] || algorithm,
      profile: profileLabels[profile as RouteProfile] || profile,
      values,
    }
  }),
)
watch(
  () => props.open,
  (value) => {
    if (value) void loadBenchmark()
  },
  { immediate: true },
)
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
      aria-label="WarehouseBench 基准"
      tabindex="-1"
    >
      <header>
        <div>
          <small>WarehouseBench</small>
          <h3>规划基准比较</h3>
        </div>
        <button aria-label="关闭基准" @click="emit('close')">关闭</button>
      </header>
      <p v-if="benchmarkLoading" role="status">正在运行服务器基准，首次生成可能需要约一分钟…</p>
      <div v-else-if="!benchmark">
        <p role="status">{{ error || '尚未生成基准结果。' }}</p>
        <button :disabled="benchmarkLoading" @click="loadBenchmark">重试读取</button>
      </div>
      <template v-else>
        <p class="spatial-caption">
          规划可行率来自独立几何 / 资源审计；任务时间和能耗为规划估算。导航实测证据单独导入。
        </p>
        <ul class="spatial-algorithm-status">
          <li v-for="item in benchmark.algorithm_availability" :key="item.algorithm">
            <b>{{ algorithmLabels[item.algorithm] || item.algorithm }}</b
            ><span :class="{ pending: item.status !== 'EXECUTED' }">{{
              item.status === 'EXECUTED'
                ? '已运行'
                : item.status === 'NOT_RUN'
                  ? '未运行'
                  : item.status
            }}</span>
          </li>
        </ul>
        <div class="spatial-bench-table">
          <table>
            <thead>
              <tr>
                <th>算法 / 策略</th>
                <th>规划可行率</th>
                <th>决策正确率</th>
                <th>P50 / P95 (ms)</th>
                <th>路径 (m)</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in rows" :key="row.key">
                <th>
                  {{ row.algorithm
                  }}<small
                    >{{ row.profile }} · {{ formatMetric(row.values.executed_episodes, 0) }} 执行 /
                    {{ formatMetric(row.values.skipped_episodes, 0) }} 跳过</small
                  >
                </th>
                <td>{{ formatPercent(row.values.success_rate) }}</td>
                <td>{{ formatPercent(row.values.decision_correct_rate) }}</td>
                <td>
                  {{ formatMetric(row.values.planning_p50_ms) }} /
                  {{ formatMetric(row.values.planning_p95_ms) }}
                </td>
                <td>{{ formatMetric(row.values.mean_path_length_m) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <details class="spatial-bench-details">
          <summary>安全审计与恢复指标</summary>
          <div class="spatial-bench-table">
            <table>
              <thead>
                <tr>
                  <th>算法 / 策略</th>
                  <th>几何碰撞率</th>
                  <th>约束违反率</th>
                  <th>净空 (m)</th>
                  <th>恢复率</th>
                  <th>估算时间 (s)</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in rows" :key="row.key">
                  <th>
                    {{ row.algorithm }}<small>{{ row.profile }}</small>
                  </th>
                  <td>{{ formatPercent(row.values.collision_rate) }}</td>
                  <td>{{ formatPercent(row.values.constraint_violation_rate) }}</td>
                  <td>{{ formatMetric(row.values.min_clearance_m, 3) }}</td>
                  <td>{{ formatPercent(row.values.recovery_success_rate) }}</td>
                  <td>{{ formatMetric(row.values.mean_mission_completion_time_s) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
        <dl class="spatial-bench-provenance">
          <dt>执行边界</dt>
          <dd>{{ benchmark.execution_boundary }}</dd>
          <dt>地图修订</dt>
          <dd>{{ benchmark.map_revision }}</dd>
          <dt>源版本</dt>
          <dd>{{ benchmark.source_sha || '未绑定' }}</dd>
          <dt>采集时间</dt>
          <dd>{{ benchmark.captured_at }}</dd>
          <dt>随机种子 / 重复</dt>
          <dd>{{ benchmark.seed }} / {{ benchmark.repetitions }}</dd>
          <dt>事件文件校验</dt>
          <dd>{{ benchmark.artifacts?.episodes_jsonl_sha256 || '未提供' }}</dd>
        </dl>
        <details class="spatial-bench-details">
          <summary>测量口径</summary>
          <dl class="spatial-bench-provenance">
            <template v-for="(value, key) in benchmark.measurement" :key="key"
              ><dt>{{ key }}</dt>
              <dd>{{ value }}</dd></template
            >
          </dl>
        </details>
      </template>
    </aside>
  </div>
</template>
<style scoped>
.spatial-algorithm-status {
  padding: 0;
  list-style: none;
  margin: 15px 0;
}
.spatial-algorithm-status li {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin: 7px 0;
  font-size: 13px;
}
.spatial-algorithm-status span {
  color: #3f806f;
}
.spatial-algorithm-status span.pending {
  color: #8c6e43;
}
.spatial-bench-table {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  text-align: left;
}
th,
td {
  padding: 10px 8px;
  border-bottom: 1px solid #dce8ed;
  white-space: nowrap;
}
thead {
  background: #edf5f8;
}
tbody th {
  font-weight: 500;
}
tbody small {
  display: block;
  margin-top: 5px;
  font-size: 11px;
}
.spatial-bench-details {
  margin-top: 18px;
  font-size: 13px;
}
summary {
  cursor: pointer;
}
.spatial-bench-provenance {
  display: grid;
  grid-template-columns: 100px minmax(0, 1fr);
  gap: 8px;
  font-size: 12px;
  margin-top: 18px;
}
dt {
  color: #647f8f;
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
}
</style>
