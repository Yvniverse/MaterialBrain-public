<script setup lang="ts">
import { computed } from 'vue'
import type { SpatialPose } from './contracts'
import { currentZones, finite, formatMetric, record, textValue } from './presentation'
import { useSpatialMission } from './useSpatialMission'
const { mission, snapshot, health, loadingHealth, loadHealth } = useSpatialMission()
const execution = computed(() => mission.value?.execution)
const metrics = computed(() => execution.value?.metrics || {})
const robot = computed(() => execution.value?.robot_state || health.value?.robot_state || {})
const plugins = computed(() => health.value?.active_plugins || {})
const pose = computed(() => {
  const value = execution.value?.current_pose || record(robot.value.pose)
  return finite(value.x) && finite(value.y) && finite(value.yaw) ? (value as SpatialPose) : null
})
const zones = computed(() => currentZones(snapshot.value, pose.value))
const speedLimit = computed(() => {
  const speeds = zones.value.map((zone) => zone.speed_limit_mps).filter(finite)
  return speeds.length ? Math.min(...speeds) : null
})
const restrictions = computed(() => [
  ...new Set(zones.value.flatMap((zone) => zone.rule_refs || [])),
])
const feedback = computed(() =>
  [...(mission.value?.task_graph.events || [])]
    .reverse()
    .find((event) => event.type === 'feedback'),
)
const mapMatches = computed(
  () => !!snapshot.value && health.value?.map_revision === snapshot.value.revision,
)
</script>
<template>
  <section class="spatial-nav-inspector" data-testid="spatial-nav-inspector">
    <header>
      <h3>导航与安全</h3>
      <button :disabled="loadingHealth" @click="loadHealth">
        {{ loadingHealth ? '读取中…' : '刷新 Nav2' }}
      </button>
    </header>
    <p class="spatial-nav-ready" :class="{ warning: !health?.ready || !mapMatches }">
      {{
        health?.ready && mapMatches
          ? 'Nav2 就绪 · 地图修订一致'
          : health?.ready
            ? '地图修订待核对'
            : 'Nav2 就绪未确认'
      }}
    </p>
    <dl class="spatial-nav-facts">
      <dt>全局规划器</dt>
      <dd>{{ textValue(plugins.planner) }}</dd>
      <dt>局部控制器</dt>
      <dd>{{ textValue(plugins.controller) }}</dd>
      <dt>行为树反馈</dt>
      <dd>
        {{ formatMetric(metrics.bt_recovery_count, 0) }} 次恢复 ·
        {{ formatMetric(metrics.action_feedback_messages, 0) }} 条反馈
      </dd>
      <dt>距离目标</dt>
      <dd>{{ formatMetric(feedback?.details.distance_remaining_m) }} m <small>动作反馈</small></dd>
      <dt>当前位姿</dt>
      <dd>
        {{
          pose
            ? `${formatMetric(pose.x)}, ${formatMetric(pose.y)} m · ${formatMetric(pose.yaw, 2)} rad`
            : '未收到里程计'
        }}
      </dd>
      <dt>实际速度</dt>
      <dd>{{ formatMetric(robot.v_mps, 2) }} m/s</dd>
    </dl>
    <div class="spatial-metrics">
      <div>
        <small>实测距离</small><strong>{{ formatMetric(metrics.distance_m) }} <em>m</em></strong>
      </div>
      <div>
        <small>实测最小净空</small
        ><strong>{{ formatMetric(metrics.min_clearance_m, 3) }} <em>m</em></strong>
      </div>
      <div>
        <small>实测碰撞</small><strong>{{ formatMetric(metrics.collision_count, 0) }}</strong>
      </div>
      <div>
        <small>实际耗时</small><strong>{{ formatMetric(metrics.elapsed_s) }} <em>s</em></strong>
      </div>
    </div>
    <p v-if="!execution" class="spatial-caption">尚未执行任务，任务实测指标保持未知。</p>
    <dl class="spatial-nav-facts">
      <dt>位姿所在区域</dt>
      <dd>
        {{
          !pose
            ? '等待位姿'
            : !snapshot
              ? '等待读取地图'
              : zones.length
                ? zones.map((zone) => zone.name || zone.id).join(' / ')
                : '通行区域'
        }}
      </dd>
      <dt>地图限速</dt>
      <dd>{{ formatMetric(speedLimit, 2) }} m/s</dd>
      <dt>当前规则</dt>
      <dd>
        {{
          !pose || !snapshot
            ? '等待观测与地图'
            : restrictions.length
              ? restrictions.join(' / ')
              : '未命中区域规则'
        }}
      </dd>
    </dl>
    <details>
      <summary>生命周期与话题</summary>
      <dl class="spatial-nav-facts">
        <template v-for="(status, name) in health?.lifecycle || {}" :key="name"
          ><dt>{{ name }}</dt>
          <dd>{{ status }}</dd></template
        ><template v-for="(count, name) in health?.topic_message_counts || {}" :key="name"
          ><dt>{{ name }}</dt>
          <dd>{{ formatMetric(count, 0) }} 条</dd></template
        >
      </dl>
      <p v-if="!health" class="spatial-caption">尚未收到导航服务健康数据。</p>
      <p class="spatial-caption">
        ROS {{ health?.ros_distro || '—' }} · Nav2 {{ health?.nav2_version || '—' }} · build
        {{ health?.build_sha?.slice(0, 12) || '—' }}
      </p>
    </details>
  </section>
</template>
<style scoped>
.spatial-nav-inspector {
  padding: 13px;
  border: 1px solid #dce8ed;
  border-radius: 12px;
  background: #fbfdfe;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
h3 {
  margin: 0;
  font-size: 14px;
}
.spatial-nav-ready {
  display: inline-block;
  margin: 9px 0;
  padding: 5px 8px;
  border-radius: 6px;
  background: #e8f5ef;
  color: #4b8172;
  font-size: 12px;
}
.spatial-nav-ready.warning {
  color: #8b6b45;
  background: #fff5e7;
}
.spatial-nav-facts {
  display: grid;
  grid-template-columns: 91px minmax(0, 1fr);
  gap: 7px 9px;
  font-size: 12px;
  line-height: 1.5;
  margin: 10px 0;
}
dt {
  color: #65808e;
  overflow-wrap: anywhere;
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
}
small {
  color: #65808e;
  font-size: 11px;
}
.spatial-metrics {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.spatial-metrics strong {
  font-size: 16px;
}
details {
  margin-top: 12px;
  font-size: 12px;
}
summary {
  cursor: pointer;
}
</style>
