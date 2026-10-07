<script setup lang="ts">
import { computed, onMounted, ref, useId } from 'vue'
import { useRouter } from 'vue-router'
import type { SpatialMissionProjection } from './contracts'
import {
  eventLabel,
  formatMetric,
  goalLabel,
  profileLabels,
  skillLabel,
  statusLabel,
  textValue,
} from './presentation'
import { useSpatialMission } from './useSpatialMission'
import SpatialReplayDrawer from './SpatialReplayDrawer.vue'
import './spatial.css'
const props = withDefaults(
  defineProps<{ mission?: SpatialMissionProjection | null; compact?: boolean }>(),
  { mission: null, compact: false },
)
const state = useSpatialMission()
const { mission: sharedMission, snapshot, health, busy, canStart, error, commandBusy } = state
const router = useRouter()
const replayOpen = ref(false)
const scanCode = ref('')
const scanInputId = `spatial-scan-${useId()}`
const isCurrent = computed(
  () =>
    !!sharedMission.value &&
    (!props.mission ||
      (props.mission.mission_id === sharedMission.value.mission_id &&
        props.mission.conversation_id === sharedMission.value.conversation_id)),
)
const visibleMission = computed(() => (isCurrent.value ? sharedMission.value : props.mission))
const graph = computed(() => visibleMission.value?.task_graph)
const plan = computed(() => visibleMission.value?.mission_plan)
const execution = computed(() => visibleMission.value?.execution)
const observed = computed(() => execution.value?.robot_state || {})
const done = computed(() => graph.value?.completed_goal_ids.length || 0)
const total = computed(() => done.value + (graph.value?.remaining_goal_ids.length || 0))
const currentGoal = computed(() => execution.value?.current_goal_id || '')
const recentEvents = computed(() => (graph.value?.events || []).slice(-5).reverse())
const instruction = computed(() =>
  textValue(graph.value?.robot_state.instruction, '注册站点运输任务'),
)
const recovery = computed(() => graph.value?.recovery)
const canCancel = computed(
  () => graph.value && !['COMPLETED', 'CANCELLED', 'FAILED'].includes(graph.value.status),
)
onMounted(() => {
  if (isCurrent.value) void state.initialize()
})
function openTwin() {
  void router.push({ path: '/warehouse-twin', query: { workspace: 'robot-lab' } })
}
async function handoff() {
  if (!currentGoal.value || !scanCode.value.trim()) return
  const goal = currentGoal.value
  const response = await state.handoff(goal, scanCode.value.trim())
  if (response?.task_graph.completed_goal_ids.includes(goal)) scanCode.value = ''
}
</script>
<template>
  <section
    v-if="visibleMission && graph"
    class="spatial-mission-card"
    :class="{ compact }"
    data-testid="spatial-mission-card"
  >
    <header class="spatial-card-header">
      <div>
        <small>Mission / Task Graph</small>
        <h3>{{ statusLabel(graph.status) }}</h3>
      </div>
      <span
        class="spatial-status"
        :class="{
          warning: ['BLOCKED', 'FAILED', 'TRANSPORT_PAUSED', 'BLOCKED_LOW_BATTERY'].includes(
            graph.status,
          ),
        }"
        >{{ done }} / {{ total }} 交接</span
      >
    </header>
    <p class="spatial-mission-goal">{{ instruction }}</p>
    <div
      class="spatial-progress"
      role="progressbar"
      aria-label="已验证交接站点"
      :aria-valuenow="done"
      :aria-valuemax="total"
      aria-valuemin="0"
    >
      <span :style="{ width: `${total ? (done / total) * 100 : 0}%` }" />
    </div>
    <div class="spatial-mission-stage">
      <span
        >当前技能 <b>{{ skillLabel(graph.current_skill) }}</b></span
      ><span v-if="currentGoal"
        >站点 <b>{{ goalLabel(snapshot, currentGoal) }}</b></span
      >
    </div>
    <p v-if="recovery" class="spatial-warning" role="status">
      恢复：{{ textValue(recovery.action) }} · {{ textValue(recovery.code)
      }}<span v-if="done"> · 保留 {{ done }} 个已交接站点</span>
    </p>
    <div v-if="plan" class="spatial-metrics">
      <div>
        <small>计划距离</small
        ><strong>{{ formatMetric(plan.metrics.distance_m) }} <em>m</em></strong>
      </div>
      <div>
        <small>计划 ETA</small><strong>{{ formatMetric(plan.metrics.eta_s) }} <em>s</em></strong>
      </div>
      <div>
        <small>{{ execution ? '仿真电量' : '规划电量' }}</small
        ><strong
          >{{ formatMetric(execution ? observed.battery_pct : plan.constraints.battery_pct) }}
          <em>%</em></strong
        >
      </div>
      <div>
        <small>{{ execution ? '仿真载荷' : '载荷上限' }}</small
        ><strong
          >{{
            formatMetric(execution ? observed.payload_kg : plan.constraints.payload_capacity_kg)
          }}
          <em>kg</em></strong
        >
      </div>
    </div>
    <p v-if="plan" class="spatial-caption">
      {{ profileLabels[plan.profile] }} · {{ textValue(plan.solver.name) }} /
      {{ textValue(plan.solver.method)
      }}<span v-if="plan.solver.optimality_proven === true"> · 此求解范围最优已证明</span>
    </p>
    <div class="spatial-goal-order" aria-label="服务器规划站点顺序">
      <span
        v-for="id in plan?.ordered_goal_ids || []"
        :key="id"
        :class="{ done: graph.completed_goal_ids.includes(id) }"
        >{{ goalLabel(snapshot, id) }}<b v-if="graph.completed_goal_ids.includes(id)"> ✓</b></span
      >
    </div>
    <details class="spatial-graph" :open="!compact">
      <summary>
        技能图 · {{ graph.nodes.filter((node) => node.status === 'SUCCEEDED').length }} /
        {{ graph.nodes.length }} 节点
      </summary>
      <ol>
        <li v-for="node in graph.nodes" :key="node.id" :data-status="node.status">
          <span class="spatial-node-dot" />
          <div>
            <b>{{ skillLabel(node.skill) }}</b
            ><small v-if="node.args.goal_id">{{
              goalLabel(snapshot, textValue(node.args.goal_id))
            }}</small
            ><small v-if="node.failure_code" class="spatial-warning">{{ node.failure_code }}</small>
          </div>
          <span>{{ statusLabel(node.status) }}</span>
        </li>
      </ol>
    </details>
    <div
      v-if="isCurrent && graph.status === 'AWAITING_HANDOFF' && currentGoal"
      class="spatial-handoff"
    >
      <label :for="scanInputId"
        >站点扫码 <code>{{ currentGoal }}</code></label
      >
      <div>
        <input
          :id="scanInputId"
          v-model="scanCode"
          placeholder="输入扫描到的站点码"
          :disabled="busy"
          @keydown.enter="handoff"
        /><button :disabled="busy || !scanCode.trim()" @click="handoff">扫码并确认交接</button>
      </div>
      <small>服务器验证通过后记为仿真交接。</small>
    </div>
    <ul v-if="recentEvents.length" class="spatial-event-list" aria-label="最新服务器事件">
      <li v-for="event in recentEvents" :key="event.event_id">
        <small>#{{ event.sequence }}</small
        ><span>{{ eventLabel(event.type) }}</span
        ><code v-if="event.goal_id">{{ event.goal_id }}</code>
      </li>
    </ul>
    <p v-if="isCurrent && error" class="spatial-error" role="alert">{{ error }}</p>
    <p v-if="isCurrent && !execution" class="spatial-caption">
      路线已规划。点击“启动导航仿真”后执行；当前没有执行反馈。
    </p>
    <p v-if="isCurrent && !health?.ready" class="spatial-caption">
      Nav2 就绪状态未确认，启动前请刷新导航状态。
    </p>
    <footer class="spatial-card-footer">
      <span class="spatial-caption">ROS2 导航仿真 · 库存写入无</span>
      <div class="spatial-actions">
        <button v-if="isCurrent && !execution" :disabled="!canStart" @click="state.start">
          {{ commandBusy === 'start' ? '正在启动…' : '启动导航仿真' }}
        </button>
        <button
          v-else-if="isCurrent && graph.status === 'TRANSPORT_PAUSED'"
          :disabled="!canStart"
          @click="state.start"
        >
          重试连接并继续
        </button>
        <button v-if="isCurrent && canCancel" :disabled="busy" @click="state.cancel">
          取消任务
        </button>
        <button v-if="isCurrent" :disabled="busy" @click="state.pollMission">刷新</button>
        <button v-if="isCurrent" @click="replayOpen = true">回放</button>
        <button @click="openTwin">查看实验仓 ↗</button>
      </div>
    </footer>
    <SpatialReplayDrawer v-if="isCurrent" :open="replayOpen" @close="replayOpen = false" />
  </section>
</template>
<style scoped>
.spatial-mission-card {
  min-width: 0;
  padding: 18px;
  border: 1px solid var(--g-line, #dbe7ed);
  border-radius: 14px;
  background: var(--g-paper, #fff);
  color: var(--g-ink, #213d50);
}
.spatial-mission-card.compact {
  padding: 13px;
}
.spatial-card-header,
.spatial-card-footer,
.spatial-mission-stage {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 9px;
}
h3 {
  margin: 4px 0 0;
  font-size: 17px;
}
.spatial-card-header small {
  font-size: 12px;
  color: var(--g-muted, #647f8e);
}
.spatial-status {
  padding: 5px 8px;
  border-radius: 7px;
  background: #e6f4ef;
  color: #397b6e;
  font-size: 12px;
}
.spatial-status.warning {
  background: #fff2df;
  color: #8d6330;
}
.spatial-mission-goal {
  margin: 12px 0;
  font-size: 14px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.spatial-progress {
  height: 4px;
  background: #e8f0f3;
  border-radius: 4px;
  overflow: hidden;
}
.spatial-progress span {
  display: block;
  height: 100%;
  background: #72b5a8;
}
.spatial-mission-stage {
  margin: 12px 0;
  font-size: 12px;
  color: var(--g-muted, #647f8e);
}
.spatial-mission-stage b {
  color: var(--g-ink, #213d50);
  font-weight: 600;
}
.spatial-goal-order {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
  margin: 9px 0;
}
.spatial-goal-order span {
  padding: 5px 7px;
  font-size: 12px;
  border: 1px solid #dce9ed;
  border-radius: 6px;
  background: #f5f9fb;
}
.spatial-goal-order span.done {
  background: #e8f4ee;
  color: #417867;
}
.spatial-graph {
  margin: 10px 0;
  font-size: 12px;
}
.spatial-graph summary {
  cursor: pointer;
  padding: 6px 0;
}
.spatial-graph ol {
  list-style: none;
  padding: 0;
  margin: 7px 0;
  max-height: 265px;
  overflow: auto;
}
.spatial-graph li {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 8px 0;
  border-top: 1px solid #e5edf1;
}
.spatial-graph li > div {
  flex: 1;
}
.spatial-graph li small {
  display: block;
  margin-top: 3px;
}
.spatial-node-dot {
  width: 7px;
  height: 7px;
  margin-top: 4px;
  background: #b9cdd5;
  border-radius: 50%;
  flex: none;
}
[data-status='RUNNING'] .spatial-node-dot,
[data-status='RECOVERING'] .spatial-node-dot {
  background: #64aac2;
}
[data-status='SUCCEEDED'] .spatial-node-dot {
  background: #7bb5a4;
}
[data-status='FAILED'] .spatial-node-dot,
[data-status='BLOCKED'] .spatial-node-dot {
  background: #c99b69;
}
.spatial-handoff {
  padding: 10px;
  border-radius: 8px;
  background: #edf6f8;
  font-size: 12px;
}
.spatial-handoff > div {
  display: flex;
  gap: 6px;
  margin: 8px 0;
  flex-wrap: wrap;
}
.spatial-handoff input {
  width: 150px;
  flex: 1;
  min-width: 100px;
}
.spatial-event-list {
  list-style: none;
  padding: 0;
  margin: 10px 0;
}
.spatial-event-list li {
  display: flex;
  gap: 8px;
  padding: 4px 0;
  font-size: 12px;
}
.spatial-event-list small {
  color: var(--g-muted, #647f8e);
  min-width: 25px;
}
.spatial-card-footer {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid #e3edf0;
}
@media (max-width: 430px) {
  .spatial-mission-card {
    padding: 12px;
  }
}
</style>
