<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'
import type { BuildPlan, BuildReadinessEntity, AgentActionProposal } from '../types'
import BuildReadinessCard from './agent/BuildReadinessCard.vue'
import BuildPlanPreview from './agent/BuildPlanPreview.vue'
import WarehouseRouteMap, {
  type WarehouseMapView,
  type WarehouseRouteView,
} from './locations/WarehouseRouteMap.vue'

interface ProductionPlan extends BuildPlan {
  production_stage: string
  pick_task_id: number | null
}
interface ProjectOption {
  id: number
  code: string
  name: string
  product_revision_id: number | null
}
interface PickingFacts {
  material_id: number
  code: string
  name: string
  required_total: string
  project_reserved: string
  book_quantity: string
  locatable_quantity: string
  location_shortage: string
  allocatable_now: string
}
interface Readiness {
  executable: boolean
  preview_only: boolean
  items: PickingFacts[]
}
interface Allocation {
  id: number
  location_id: number
  location_code: string
  full_path: string
  material_code: string
  material_name: string
  mpn: string
  planned_quantity: string
  picked_quantity: string
  remaining_quantity: string
  route_sequence: number
  route_node_code: string
  status: string
}
interface Task {
  id: number
  pick_task_no: string
  status: string
  warehouse_map_id: number | null
  warehouse_graph_hash: string
  route_plan: WarehouseRouteView
  allocations: Allocation[]
  next_stop: Allocation | null
}
const auth = useAuthStore(),
  route = useRoute(),
  router = useRouter()
const projects = ref<ProjectOption[]>([]),
  plans = ref<ProductionPlan[]>([])
const projectId = ref<number | null>(Number(route.query.project) || null)
const quantity = ref(Math.max(1, Number(route.query.quantity) || 1)),
  status = ref(''),
  planNo = ref('')
const productFilter = ref(''),
  revisionFilter = ref('')
const visiblePlans = computed(() =>
  plans.value.filter(
    (plan) =>
      `${plan.product.code} ${plan.product.name}`
        .toLowerCase()
        .includes(productFilter.value.toLowerCase()) &&
      plan.revision.revision.toLowerCase().includes(revisionFilter.value.toLowerCase()),
  ),
)
const selected = ref<ProductionPlan | null>(null),
  readiness = ref<Readiness | null>(null)
const buildReadiness = ref<BuildReadinessEntity | null>(null)
const task = ref<Task | null>(null),
  map = ref<WarehouseMapView | null>(null)
const current = ref<Allocation | null>(null),
  confirmOpen = ref(false)
const locationToken = ref(''),
  materialToken = ref(''),
  pickQuantity = ref(1)
const method = ref<'manual' | 'barcode' | 'qr'>('barcode'),
  busy = ref(false)
const confirmKey = ref(''),
  closedEdges = ref('')
const operation = () => crypto.randomUUID()

async function load() {
  plans.value = (
    await api.get<ProductionPlan[]>('/build-plans', {
      params: {
        project_id: projectId.value || undefined,
        status: status.value || undefined,
        plan_no: planNo.value,
      },
    })
  ).data
}
async function selectPlan(plan: ProductionPlan) {
  selected.value = plan
  readiness.value = auth.can('picking:view')
    ? (await api.get<Readiness>(`/picking/readiness/${plan.id}`)).data
    : null
  task.value = null
  map.value = null
  if (plan.pick_task_id && auth.can('picking:view')) await openTask(plan.pick_task_id)
}
async function analyze() {
  const project = projects.value.find((p) => p.id === projectId.value)
  if (!project?.product_revision_id) return
  buildReadiness.value = (
    await api.post<BuildReadinessEntity>(
      `/product-revisions/${project.product_revision_id}/build-readiness`,
      { project_id: project.id, build_quantity: quantity.value },
    )
  ).data
}
async function createPlan() {
  const project = projects.value.find((p) => p.id === projectId.value)
  if (!project?.product_revision_id) return
  const { data } = await api.post<ProductionPlan>('/build-plans', {
    product_revision_id: project.product_revision_id,
    project_id: project.id,
    build_quantity: quantity.value,
    client_operation_id: operation(),
  })
  await load()
  await selectPlan(data)
}
async function reserve() {
  if (!selected.value) return
  const { data } = await api.post<{
    fully_reserved: boolean
    proposal: AgentActionProposal | null
  }>(`/build-plans/${selected.value.id}/reservation-proposal`, {
    client_operation_id: operation(),
    reason: '生产计划备料预留',
  })
  if (data.proposal) {
    let approved = false
    try {
      await ElMessageBox.confirm('确认批准本生产计划的物料预留？这将占用账面库存。', '审批预留', {
        type: 'warning',
      })
      approved = true
    } catch {
      // Keep the pending proposal accessible after cancellation or refresh.
    }
    if (approved) await api.post(`/agent/proposals/${data.proposal.id}/approve`)
  }
  const id = selected.value.id
  await load()
  const refreshed = plans.value.find((p) => p.id === id)
  if (refreshed) await selectPlan(refreshed)
}
async function createTask() {
  if (!selected.value) return
  const { data } = await api.post<Task>('/pick-tasks', {
    build_plan_id: selected.value.id,
    build_plan_snapshot_hash: selected.value.snapshot_hash,
    client_operation_id: operation(),
    closed_edge_codes: [],
  })
  await openTask(data.id)
  await load()
}
async function openTask(id: number) {
  task.value = (await api.get<Task>(`/pick-tasks/${id}`)).data
  const requested = Number(route.query.allocation)
  current.value = task.value.allocations.find((a) => a.id === requested) || task.value.next_stop
  map.value = task.value.warehouse_map_id
    ? (await api.get<WarehouseMapView>(`/warehouse-maps/${task.value.warehouse_map_id}`)).data
    : null
  await router.replace({ path: '/projects', query: { ...route.query, task: id } })
}
function showConfirm(a: Allocation) {
  current.value = a
  locationToken.value = ''
  materialToken.value = ''
  pickQuantity.value = Number(a.remaining_quantity)
  confirmKey.value = operation()
  confirmOpen.value = true
}
async function confirmPick() {
  if (!current.value || !task.value || busy.value) return
  busy.value = true
  try {
    await api.post(`/pick-allocations/${current.value.id}/confirm`, {
      quantity: pickQuantity.value,
      idempotency_key: confirmKey.value,
      confirmation_method: method.value,
      location_token: locationToken.value,
      material_token: materialToken.value,
    })
    confirmOpen.value = false
  } finally {
    busy.value = false
    await openTask(task.value.id)
    await load()
  }
}
async function replan() {
  if (!task.value) return
  await api.post(`/pick-tasks/${task.value.id}/replan`, {
    client_operation_id: operation(),
    reason: '操作员重新规划剩余取料',
    closed_edge_codes: closedEdges.value
      .split(',')
      .map((v) => v.trim())
      .filter(Boolean),
  })
  await openTask(task.value.id)
}
async function cancelTask() {
  if (!task.value) return
  await ElMessageBox.confirm('取消未完成取料分配？已取库存不回滚，项目预留保留。', '取消拣货任务')
  await api.post(`/pick-tasks/${task.value.id}/cancel`, { reason: '操作员取消剩余取料' })
  await openTask(task.value.id)
  await load()
}
function openLocation(a: Allocation) {
  router.push({
    path: '/locations',
    query: { focus: a.location_id, task: task.value?.id, allocation: a.id },
  })
}
onMounted(async () => {
  projects.value = (await api.get<ProjectOption[]>('/projects')).data
  await load()
  if (route.query.task && auth.can('picking:view')) await openTask(Number(route.query.task))
})
</script>

<template>
  <section class="production-workspace card" data-testid="production-workspace">
    <h2>生产计划与拣货执行</h2>
    <p>产品单台 BOM → 生产需求快照 → 项目预留 → 实际库位拣货</p>
    <div class="controls">
      <el-select v-model="projectId" clearable placeholder="选择项目" @change="load"
        ><el-option
          v-for="p in projects"
          :key="p.id"
          :value="p.id"
          :label="`${p.code} · ${p.name}`"
      /></el-select>
      <el-input-number v-model="quantity" :min="1" :max="10000" aria-label="生产台数" />
      <el-button @click="analyze">分析备料（不预留）</el-button>
      <el-button v-if="auth.can('project:manage')" @click="createPlan">生成生产计划</el-button>
    </div>
    <BuildReadinessCard
      v-if="buildReadiness"
      :result="buildReadiness"
      @generate-plan="createPlan"
    />
    <div class="controls">
      <el-input v-model="planNo" placeholder="计划编号" />
      <el-input v-model="productFilter" placeholder="筛选产品名称 / 编码" />
      <el-input v-model="revisionFilter" placeholder="筛选产品版本" />
      <el-select v-model="status" clearable placeholder="执行阶段"
        ><el-option
          v-for="s in [
            'ready',
            'reservation_pending',
            'reserved',
            'in_progress',
            'completed',
            'needs_replan',
            'stale',
            'cancelled',
          ]"
          :key="s"
          :value="s"
          :label="s"
      /></el-select>
      <el-button @click="load">筛选计划</el-button>
    </div>
    <el-table :data="visiblePlans" @row-click="selectPlan" highlight-current-row>
      <el-table-column prop="plan_no" label="生产计划" min-width="210" />
      <el-table-column prop="project.name" label="项目" />
      <el-table-column prop="product.name" label="产品" />
      <el-table-column prop="revision.revision" label="版本" />
      <el-table-column prop="build_quantity" label="台数" width="70" />
      <el-table-column prop="production_stage" label="执行阶段" />
    </el-table>
    <template v-if="selected">
      <h3>{{ selected.plan_no }}</h3>
      <BuildPlanPreview :plan="selected" read-only />
      <el-button
        v-if="
          auth.can('project:manage') &&
          auth.can('inventory:operate') &&
          ['ready', 'reservation_pending'].includes(selected.status)
        "
        @click="reserve"
        >申请 / 审批预留</el-button
      >
      <section v-if="readiness" data-testid="picking-readiness">
        <h3>拣货准备度 · {{ readiness.executable ? '可执行' : '预览 / 尚不可执行' }}</h3>
        <p>账面库存不等于可定位库存。没有 InventoryLot 的数量不生成取料位置。</p>
        <el-table :data="readiness.items">
          <el-table-column prop="code" label="物料" /><el-table-column
            prop="required_total"
            label="需求"
          />
          <el-table-column prop="project_reserved" label="项目预留" /><el-table-column
            prop="book_quantity"
            label="账面"
          />
          <el-table-column prop="locatable_quantity" label="实际可定位" /><el-table-column
            prop="allocatable_now"
            label="当前可分配"
          />
          <el-table-column prop="location_shortage" label="库位缺口" />
        </el-table>
        <el-button
          v-if="auth.can('picking:operate')"
          :disabled="!readiness.executable"
          type="primary"
          @click="createTask"
          >生成拣货任务</el-button
        >
      </section>
    </template>
    <section v-if="task" data-testid="pick-task-detail">
      <h2>{{ task.pick_task_no }} · {{ task.status }}</h2>
      <p v-if="map">
        仓库图 {{ map.code }} · 版本 {{ map.version }} · Graph {{ task.warehouse_graph_hash }}
      </p>
      <el-button
        v-if="task?.warehouse_map_id"
        type="primary"
        plain
        @click="router.push({ path: '/warehouse-twin', query: { task: task.id } })"
      >
        打开数字孪生
      </el-button>
      <el-button
        v-if="task && auth.can('picking:operate')"
        type="primary"
        @click="router.push(`/picking/operator/${task.id}`)"
      >
        进入 Guided Picking
      </el-button>
      <WarehouseRouteMap
        v-if="map"
        :map="map"
        :route="task.route_plan"
        :current-node-code="current?.route_node_code"
        @select-binding="
          (b) => {
            current = task?.allocations.find((a) => a.route_node_code === b.pick_node_code) || null
          }
        "
      />
      <p v-else>未配置仓库地图：按库位层级排序，不代表物理最短路线。</p>
      <div v-if="auth.can('picking:operate')" class="controls">
        <el-input v-model="closedEdges" placeholder="临时关闭的通道编码，逗号分隔" />
        <el-button @click="replan">重新规划剩余</el-button
        ><el-button @click="cancelTask">取消任务</el-button>
      </div>
      <article
        v-for="a in task.allocations"
        :key="a.id"
        class="pick-stop"
        :class="{ current: current?.id === a.id }"
        @click="current = a"
      >
        <b>站点 {{ a.route_sequence }} · {{ a.full_path }} · {{ a.location_code }}</b>
        <p>{{ a.material_code }} · {{ a.material_name }} · {{ a.mpn }}</p>
        <p>
          计划 {{ a.planned_quantity }} · 已取 {{ a.picked_quantity }} · 剩余
          {{ a.remaining_quantity }} · {{ a.status }}
        </p>
        <el-button @click.stop="openLocation(a)">打开库位</el-button>
        <el-button
          v-if="auth.can('picking:operate') && ['pending', 'partial'].includes(a.status)"
          :disabled="!['ready', 'in_progress'].includes(task.status)"
          type="primary"
          @click.stop="showConfirm(a)"
          >扫描 / 人工确认</el-button
        >
      </article>
    </section>
    <el-dialog v-model="confirmOpen" title="确认实际取料" width="540px">
      <p>预期库位 {{ current?.location_code }}；物料 {{ current?.material_code }}</p>
      <el-form label-position="top">
        <el-form-item label="确认方式"
          ><el-select v-model="method"
            ><el-option label="扫码" value="barcode" /><el-option
              label="人工输入核对"
              value="manual" /></el-select
        ></el-form-item>
        <el-form-item label="库位编码"
          ><el-input v-model="locationToken" aria-label="库位编码"
        /></el-form-item>
        <el-form-item label="物料编码 / 条码"
          ><el-input v-model="materialToken" aria-label="物料编码"
        /></el-form-item>
        <el-form-item label="实际取料数量"
          ><el-input-number
            v-model="pickQuantity"
            :min="0.0001"
            :max="Number(current?.remaining_quantity || 0)"
        /></el-form-item>
      </el-form>
      <template #footer
        ><el-button @click="confirmOpen = false">取消</el-button
        ><el-button type="primary" :loading="busy" @click="confirmPick"
          >确认取料并扣减库存</el-button
        ></template
      >
    </el-dialog>
  </section>
</template>

<style scoped>
.production-workspace {
  padding: 20px;
  margin-bottom: 20px;
}
.controls {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin: 16px 0;
}
.controls .el-select,
.controls .el-input {
  width: 240px;
}
.pick-stop {
  border: 1px solid #d9e3eb;
  border-radius: 10px;
  padding: 16px;
  margin: 12px 0;
}
.pick-stop.current {
  border-color: #287daf;
  background: #f1f8fc;
}
</style>
