<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { RouteProfile, SpatialLayer } from './contracts'
import {
  formatMetric,
  goalLabel,
  layerLabels,
  profileLabels,
  record,
  statusLabel,
  textValue,
} from './presentation'
import { useSpatialMission } from './useSpatialMission'
import SpatialMissionCard from './SpatialMissionCard.vue'
import SpatialNavInspector from './SpatialNavInspector.vue'
import SpatialBenchmarkDrawer from './SpatialBenchmarkDrawer.vue'
import './spatial.css'
const state = useSpatialMission()
const {
  snapshot,
  mission,
  previewPlan,
  health,
  layers,
  selectedProfile,
  selectedGoalIds,
  planningBatteryPct,
  loadingSnapshot,
  planning,
  creating,
  commandBusy,
  busy,
  error,
  readiness,
  readinessLoading,
} = state
const expanded = ref(false)
const benchmarkOpen = ref(false)
const scenario = ref<'baseline' | 'blocked-crossing' | 'isolated-dock' | 'low-battery'>('baseline')
const availableGoals = computed(
  () => snapshot.value?.docks.filter((dock) => !['HOME', 'CHARGER'].includes(dock.id)) || [],
)
const active = computed(
  () =>
    mission.value &&
    !['COMPLETED', 'CANCELLED', 'FAILED'].includes(mission.value.task_graph.status),
)
const canCreate = computed(
  () =>
    previewPlan.value?.status === 'READY' &&
    (!active.value || !mission.value?.execution) &&
    !busy.value,
)
onMounted(() => {
  void state.initialize()
})
function profileChange(event: Event) {
  state.setProfile((event.target as HTMLSelectElement).value as RouteProfile)
}
function toggleGoal(id: string) {
  state.setGoalIds(
    selectedGoalIds.value.includes(id)
      ? selectedGoalIds.value.filter((goal) => goal !== id)
      : [...selectedGoalIds.value, id],
  )
}
function batteryChange(event: Event) {
  const value = (event.target as HTMLInputElement).value
  state.setPlanningBattery(value ? Number(value) : null)
}
function selectScenario(event: Event) {
  scenario.value = (event.target as HTMLSelectElement).value as typeof scenario.value
  const canonical = record(record(snapshot.value?.provenance).robot).battery_pct
  state.setPlanningBattery(
    scenario.value === 'low-battery' ? 12 : typeof canonical === 'number' ? canonical : null,
  )
}
function injectScenario() {
  if (scenario.value === 'blocked-crossing' || scenario.value === 'isolated-dock')
    void state.addObstacle(scenario.value)
}
function violationText(value: unknown) {
  if (typeof value === 'string') return value
  const violation = record(value)
  return textValue(violation.message, textValue(violation.reason, textValue(violation.code)))
}
</script>
<template>
  <section class="spatial-observability" data-testid="spatial-observability">
    <header class="spatial-toolbar">
      <div class="spatial-layer-controls" aria-label="空间图层">
        <button
          v-for="(label, key) in layerLabels"
          :key="key"
          :aria-pressed="layers.includes(key as SpatialLayer)"
          :title="key === 'geometry' ? '仅显示原始几何' : label"
          :class="{ selected: layers.includes(key as SpatialLayer) }"
          @click="state.toggleLayer(key as SpatialLayer)"
        >
          {{ label }}
        </button>
      </div>
      <div class="spatial-toolbar-end">
        <label
          >路线策略
          <select
            aria-label="路线策略"
            :value="selectedProfile"
            :disabled="busy"
            @change="profileChange"
          >
            <option v-for="(label, value) in profileLabels" :key="value" :value="value">
              {{ label }}
            </option>
          </select></label
        ><button :aria-expanded="expanded" @click="expanded = !expanded">
          {{ expanded ? '收起任务 / Nav2' : '任务 / Nav2' }}</button
        ><button @click="benchmarkOpen = true">WarehouseBench</button>
      </div>
    </header>
    <div v-if="expanded" class="spatial-observability-body">
      <div class="spatial-planning-panel">
        <header>
          <h3>空间任务</h3>
          <span class="spatial-caption">{{ snapshot?.map_id || '读取地图…' }}</span>
        </header>
        <div class="spatial-goal-selector" aria-label="注册交接站点">
          <label v-for="dock in availableGoals" :key="dock.id"
            ><input
              type="checkbox"
              :checked="selectedGoalIds.includes(dock.id)"
              :disabled="busy"
              @change="toggleGoal(dock.id)"
            />{{ dock.label || dock.id }}</label
          >
        </div>
        <div class="spatial-planning-constraints">
          <label
            >场景
            <select
              aria-label="空间任务场景"
              :value="scenario"
              :disabled="busy"
              @change="selectScenario"
            >
              <option value="baseline">正常通行</option>
              <option value="blocked-crossing">中央通道受阻</option>
              <option value="isolated-dock">站点隔离</option>
              <option value="low-battery">低电量规划</option>
            </select></label
          ><label
            >规划电量
            <input
              aria-label="规划电量"
              type="number"
              min="0"
              max="100"
              :value="planningBatteryPct ?? ''"
              :disabled="busy"
              @input="batteryChange"
            />
            %</label
          >
        </div>
        <p class="spatial-caption">电量输入只用于规划约束。执行位置、电量与进度来自服务器反馈。</p>
        <p v-if="['blocked-crossing', 'isolated-dock'].includes(scenario)" class="spatial-caption">
          建立任务后点击“应用所选障碍”，服务器才会更新临时环境约束。
        </p>
        <div class="spatial-actions">
          <button
            :disabled="loadingSnapshot || busy || !snapshot || !selectedGoalIds.length"
            @click="state.planMission"
          >
            {{ planning ? '正在规划…' : '预览路线' }}</button
          ><button :disabled="!canCreate" @click="state.createMission">
            {{ creating ? '正在建立…' : '建立任务' }}</button
          ><button v-if="active" :disabled="busy" @click="state.replan(selectedProfile)">
            {{ commandBusy === 'replan' ? '重规划中…' : '重规划剩余任务' }}</button
          ><button :disabled="loadingSnapshot" @click="state.loadSnapshot(true)">刷新地图</button>
        </div>
        <div v-if="previewPlan" class="spatial-plan-preview" data-testid="spatial-plan-preview">
          <h4>{{ statusLabel(previewPlan.status) }} · {{ profileLabels[previewPlan.profile] }}</h4>
          <p>
            {{
              previewPlan.ordered_goal_ids.map((id) => goalLabel(snapshot, id)).join(' → ') ||
              '没有可执行顺序'
            }}
          </p>
          <div class="spatial-metrics">
            <div>
              <small>计划距离</small
              ><strong>{{ formatMetric(previewPlan.metrics.distance_m) }} m</strong>
            </div>
            <div>
              <small>计划 ETA</small
              ><strong>{{ formatMetric(previewPlan.metrics.eta_s) }} s</strong>
            </div>
            <div>
              <small>计划净空</small
              ><strong>{{ formatMetric(previewPlan.metrics.min_clearance_m, 3) }} m</strong>
            </div>
            <div>
              <small>估算能耗</small
              ><strong>{{ formatMetric(previewPlan.metrics.energy_wh) }} Wh</strong>
            </div>
          </div>
          <p class="spatial-caption">
            {{ textValue(previewPlan.solver.name) }} / {{ textValue(previewPlan.solver.method) }} ·
            仅规划
          </p>
          <ul v-if="previewPlan.violations.length" class="spatial-violations">
            <li v-for="(item, index) in previewPlan.violations" :key="index">
              {{ violationText(item) }}
            </li>
          </ul>
        </div>
        <SpatialMissionCard v-if="mission" compact />
        <p v-else class="spatial-caption">
          从注册待命点 HOME 出发，选择站点后预览路线。建立任务不会自动启动。
        </p>
        <div v-if="active" class="spatial-scenario-actions">
          <small>临时环境约束</small>
          <div class="spatial-actions">
            <button
              :disabled="busy || !['blocked-crossing', 'isolated-dock'].includes(scenario)"
              @click="injectScenario"
            >
              应用所选障碍</button
            ><button
              :disabled="busy"
              @click="
                state.removeObstacle(
                  scenario === 'isolated-dock' ? 'isolated-dock' : 'blocked-crossing',
                )
              "
            >
              移除所选障碍
            </button>
          </div>
          <small>{{ snapshot?.dynamic_overlays.length ?? '—' }} 个注册动态覆盖层</small>
        </div>
        <p v-if="error" role="alert" class="spatial-error">{{ error }}</p>
        <details class="spatial-readiness">
          <summary @click="state.loadReadiness">研究接口</summary>
          <p v-if="readinessLoading" class="spatial-caption">读取接口…</p>
          <template v-else-if="readiness"
            ><p class="spatial-caption">
              {{
                ['ObservationFrame', 'MapDeltaProposal', 'TrajectorySegment']
                  .filter((name) => readiness?.[name])
                  .join(' · ')
              }}
            </p>
            <p class="spatial-caption">
              训练执行
              {{
                readiness.training_executed === true
                  ? '是'
                  : readiness.training_executed === false
                    ? '否'
                    : '未知'
              }}
              · 多机器人运行
              {{
                readiness.multi_robot_runtime === true
                  ? '是'
                  : readiness.multi_robot_runtime === false
                    ? '否'
                    : '未知'
              }}
              · 自动地图写入
              {{
                readiness.automatic_map_write === true
                  ? '是'
                  : readiness.automatic_map_write === false
                    ? '否'
                    : '未知'
              }}
            </p></template
          >
        </details>
      </div>
      <SpatialNavInspector />
    </div>
    <p v-if="!expanded && error" class="spatial-error" role="alert">{{ error }}</p>
    <p v-if="!expanded" class="spatial-observability-summary">
      <span>{{
        mission
          ? `${statusLabel(mission.task_graph.status)} · ${mission.task_graph.completed_goal_ids.length} 个已交接站点`
          : '语义图层 / 任务规划'
      }}</span
      ><span>{{ health?.ready ? 'Nav2 服务就绪' : 'Nav2 状态待确认' }}</span
      ><span v-if="snapshot">布局 {{ snapshot.revision.slice(0, 10) }}</span>
    </p>
    <SpatialBenchmarkDrawer :open="benchmarkOpen" @close="benchmarkOpen = false" />
  </section>
</template>
<style scoped>
.spatial-observability {
  margin: 0 24px 16px;
  padding: 12px 14px;
  border: 1px solid #d5e5ec;
  border-radius: 12px;
  background: #f5fafc;
  color: #2b4b5d;
}
.spatial-toolbar,
.spatial-toolbar-end,
.spatial-layer-controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
}
.spatial-toolbar {
  justify-content: space-between;
}
.spatial-layer-controls button {
  padding: 6px 8px;
  background: #fafdfe;
}
.spatial-layer-controls button.selected {
  color: #35796e;
  background: #e5f2ed;
  border-color: #bbd9cf;
}
.spatial-toolbar-end label {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 12px;
}
.spatial-toolbar-end select {
  padding: 6px 7px;
}
.spatial-observability-body {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(280px, 1fr);
  gap: 13px;
  margin-top: 14px;
  align-items: start;
}
.spatial-planning-panel {
  min-width: 0;
}
.spatial-planning-panel > header {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
}
h3 {
  font-size: 15px;
  margin: 0;
}
h4 {
  font-size: 13px;
  margin: 0 0 8px;
}
.spatial-goal-selector {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 13px;
  margin: 12px 0;
}
.spatial-goal-selector label {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  font-size: 12px;
}
.spatial-goal-selector input {
  accent-color: #589484;
}
.spatial-planning-constraints {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: center;
}
.spatial-planning-constraints label {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 12px;
}
.spatial-planning-constraints input {
  width: 55px;
}
.spatial-plan-preview {
  margin: 12px 0;
  padding: 12px;
  border: 1px solid #d3e5ec;
  border-radius: 10px;
  background: #fafdfe;
}
.spatial-plan-preview p {
  font-size: 12px;
  line-height: 1.6;
}
.spatial-plan-preview .spatial-metrics {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.spatial-plan-preview strong {
  font-size: 16px;
}
.spatial-planning-panel > .spatial-mission-card {
  margin-top: 13px;
}
.spatial-scenario-actions {
  padding-top: 12px;
  margin-top: 12px;
  border-top: 1px solid #dae8ed;
}
.spatial-scenario-actions small {
  display: block;
  font-size: 12px;
  margin: 7px 0;
  color: #647e8b;
}
.spatial-readiness {
  font-size: 12px;
  margin-top: 12px;
}
summary {
  cursor: pointer;
}
.spatial-violations {
  padding-left: 18px;
  color: #976e3f;
  font-size: 12px;
}
.spatial-observability-summary {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin: 10px 0 0;
  font-size: 12px;
  color: #647e8b;
}
@media (max-width: 960px) {
  .spatial-observability-body {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 720px) {
  .spatial-observability {
    margin: 0 12px 12px;
    padding: 12px;
  }
  .spatial-toolbar {
    gap: 12px;
  }
  .spatial-layer-controls {
    gap: 5px;
  }
  .spatial-layer-controls button {
    font-size: 11px;
  }
}
</style>
