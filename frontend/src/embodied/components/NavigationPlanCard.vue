<script setup lang="ts">
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { queueNavigationPlan } from '../navigationSession'
import type { AgentNavigationPlan } from '../../types'
const props = defineProps<{ plan: AgentNavigationPlan }>()
const router = useRouter()
const labels = {
  READY: '路线已准备',
  BLOCKED: '停靠点受阻',
  NEEDS_CHARGE: '先安排充电',
  SPLIT_REQUIRED: '需要分批运输',
  CAPABILITY_MISMATCH: '需要人工或其他设备',
  CLARIFICATION: '需要当前任务状态',
}
const fmt = (n: number | undefined, d = 1) =>
  typeof n === 'number' && Number.isFinite(n) ? n.toFixed(d) : '—'
function open() {
  if (!queueNavigationPlan(props.plan)) {
    ElMessage.warning('任务状态已变化，请重新暂停并规划剩余任务。')
    return
  }
  void router.push({
    path: '/warehouse-twin',
    query: {
      workspace: 'robot-lab',
      nav_goals: (props.plan.requested_goal_ids || props.plan.goal_ids || []).join(','),
      nav_scenario: props.plan.scenario_id || 'baseline',
      nav_request: String(Date.now()),
    },
  })
}
</script>
<template>
  <section class="nav-result" data-testid="agent-navigation-plan">
    <header>
      <strong>{{ labels[props.plan.status] || props.plan.status }}</strong
      ><span>机器人实验仓</span>
    </header>
    <div class="nav-result-grid">
      <div>
        <small>路线长度</small><b>{{ fmt(plan.distance_m) }} <em>m</em></b>
      </div>
      <div>
        <small>预计用时</small
        ><b>{{ fmt(plan.eta_s === undefined ? undefined : plan.eta_s / 60) }} <em>min</em></b>
      </div>
      <div>
        <small>最小外廓净空</small
        ><b
          >{{ fmt(plan.min_clearance_m === undefined ? undefined : plan.min_clearance_m * 100) }}
          <em>cm</em></b
        >
      </div>
    </div>
    <p v-if="plan.reason">{{ plan.reason }}</p>
    <p v-else>
      {{
        plan.resumed
          ? '保留已交接站点和载荷，从当前暂停位姿继续仿真。'
          : '按停靠点和机器人能力生成路线，在三维仓库从待命点复核路线或开始仿真。'
      }}
    </p>
    <footer>
      <small>合成场景 · 不改变实际库存</small
      ><button :disabled="plan.status === 'CLARIFICATION'" @click="open">带此任务查看仓库 ↗</button>
    </footer>
  </section>
</template>
<style scoped>
.nav-result {
  padding: 18px;
  border: 1px solid var(--g-line);
  border-radius: 16px;
  background: var(--g-paper);
  color: var(--g-ink);
}
header,
footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
header span,
small {
  font-size: 13px;
  color: var(--g-muted);
}
header strong {
  font-size: 17px;
}
.nav-result-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 16px 0;
}
.nav-result-grid > div {
  background: var(--mb-surface-subtle);
  padding: 12px;
  border-radius: 12px;
}
.nav-result-grid b {
  display: block;
  font-size: 22px;
  margin-top: 6px;
}
.nav-result-grid em {
  font-size: 12px;
  font-style: normal;
  font-weight: 400;
}
p {
  font-size: 14px;
  line-height: 1.6;
}
button {
  background: var(--g-ice);
  color: var(--g-accent-strong);
  padding: 10px 14px;
  border: 1px solid var(--g-line);
  border-radius: 10px;
  cursor: pointer;
}
@media (max-width: 430px) {
  .nav-result-grid {
    grid-template-columns: 1fr 1fr;
  }
  .nav-result {
    padding: 12px;
  }
}
</style>
