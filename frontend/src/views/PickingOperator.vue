<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import WarehouseTwin from './WarehouseTwin.vue'

interface Allocation {
  id: number
  location_id: number
  location_code: string
  full_path: string
  material_code: string
  material_name: string
  mpn: string
  unit: string
  quantity_precision: number
  planned_quantity: string
  picked_quantity: string
  remaining_quantity: string
  route_sequence: number
  route_node_code: string
  status: string
}
interface StationGroup {
  route_sequence: number
  route_node_code: string
  station_index: number
  status: 'completed' | 'current' | 'pending'
  completed_allocations: number
  remaining_allocations: number
  allocations: Allocation[]
}
interface Task {
  id: number
  pick_task_no: string
  status: string
  warehouse_map_id: number | null
  warehouse_graph_hash: string
  route_distance_m: string | number | null
  route_constraints: { closed_edge_codes?: string[] }
  allocations: Allocation[]
}
interface OperatorState {
  task: Task
  station_groups: StationGroup[]
  current_station: StationGroup | null
  current_allocation: Allocation | null
  progress: {
    stations_total: number
    stations_completed: number
    allocations_total: number
    allocations_completed: number
  }
  events: Array<{
    id: number
    action: string
    success: boolean
    created_at: string
    after: Record<string, unknown>
  }>
}
interface ScanResult {
  allocation_id: number
  location_match: boolean
  material_match: boolean | null
  ready: boolean
  expected_location_code: string
  expected_material_code: string
  material_name: string
  unit: string
  quantity_precision: number
  remaining_quantity: string
}

const route = useRoute()
const router = useRouter()
const taskId = computed(() => Number(route.params.taskId))
const state = ref<OperatorState | null>(null)
const loading = ref(false)
const busy = ref(false)
const scanText = ref('')
const scannerInput = ref<{ focus: () => void } | null>(null)
const scanStage = ref<'location' | 'material' | 'ready'>('location')
const locationToken = ref('')
const materialToken = ref('')
const quantity = ref(0)
const confirmKey = ref('')
const scanError = ref('')
const confirmationMethod = ref<'barcode' | 'manual'>('barcode')
const manualOverrideReason = ref('')
const closedEdges = ref('')
const issueType = ref('location_blocked')
const issueNotes = ref('')

const current = computed(() => state.value?.current_allocation || null)
const currentStation = computed(() => state.value?.current_station || null)
const task = computed(() => state.value?.task || null)
const complete = computed(() => task.value?.status === 'completed')
const quantityPrecision = computed(() => current.value?.quantity_precision ?? 4)
const quantityStep = computed(() => 10 ** -quantityPrecision.value)
const stationPercent = computed(() => {
  const progress = state.value?.progress
  if (!progress?.stations_total) return 0
  return Math.round((progress.stations_completed / progress.stations_total) * 100)
})
const scanPrompt = computed(() => {
  if (!current.value) return complete.value ? '任务已完成' : '没有待处理分配'
  if (scanStage.value === 'location') return `扫描库位：${current.value.location_code}`
  if (scanStage.value === 'material') return `扫描物料：${current.value.material_code}`
  if (confirmationMethod.value === 'manual') return '人工核对已记录，可以确认取料'
  return '扫描已核对，可以确认取料'
})
const stageLabel = computed(() => {
  if (scanStage.value === 'location') return '1 · 库位'
  if (scanStage.value === 'material') return '2 · 物料'
  return '3 · 数量确认'
})

function operationKey() {
  return crypto.randomUUID()
}
function normalizeEdges() {
  return closedEdges.value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}
async function focusScanner() {
  await nextTick()
  scannerInput.value?.focus()
}
function resetScan() {
  scanText.value = ''
  locationToken.value = ''
  materialToken.value = ''
  scanStage.value = 'location'
  scanError.value = ''
  quantity.value = Number(current.value?.remaining_quantity || 0)
  confirmationMethod.value = 'barcode'
  manualOverrideReason.value = ''
  confirmKey.value = current.value ? operationKey() : ''
  void focusScanner()
}
async function load() {
  if (!Number.isSafeInteger(taskId.value) || taskId.value <= 0) {
    state.value = null
    return
  }
  loading.value = true
  try {
    state.value = (await api.get<OperatorState>(`/pick-tasks/${taskId.value}/operator-state`)).data
    closedEdges.value = state.value.task.route_constraints?.closed_edge_codes?.join(', ') || ''
  } finally {
    loading.value = false
  }
}
async function handleScan() {
  const token = scanText.value.trim()
  scanText.value = ''
  scanError.value = ''
  if (!token || !current.value || busy.value) return
  busy.value = true
  try {
    if (scanStage.value === 'location') {
      const result = (
        await api.post<ScanResult>(`/pick-allocations/${current.value.id}/validate-scan`, {
          location_token: token,
          material_token: '',
        })
      ).data
      if (!result.location_match) {
        scanError.value = `库位不匹配。当前任务要求 ${result.expected_location_code}`
        ElMessage.error('库位扫描不匹配，未发生库存写入')
        return
      }
      locationToken.value = token
      scanStage.value = 'material'
      ElMessage.success('库位核对通过')
      return
    }
    if (scanStage.value === 'material') {
      const result = (
        await api.post<ScanResult>(`/pick-allocations/${current.value.id}/validate-scan`, {
          location_token: locationToken.value,
          material_token: token,
        })
      ).data
      if (!result.ready) {
        scanError.value = `物料不匹配。当前任务要求 ${result.expected_material_code}`
        ElMessage.error('物料扫描不匹配，未发生库存写入')
        return
      }
      materialToken.value = token
      quantity.value = Number(result.remaining_quantity)
      scanStage.value = 'ready'
      ElMessage.success('物料核对通过')
    }
  } finally {
    busy.value = false
    void focusScanner()
  }
}
async function confirmPick() {
  if (!current.value || busy.value || scanStage.value !== 'ready') return
  busy.value = true
  try {
    // Keep the same idempotency key so a network retry cannot duplicate settlement.
    await api.post(`/pick-allocations/${current.value.id}/confirm`, {
      quantity: quantity.value,
      idempotency_key: confirmKey.value,
      confirmation_method: confirmationMethod.value,
      location_token: locationToken.value,
      material_token: materialToken.value,
      notes: 'Guided Picking 操作员确认',
      manual_override_reason: manualOverrideReason.value,
    })
    ElMessage.success('取料已确认，库存与项目预留已同步更新')
    await load()
    resetScan()
  } finally {
    busy.value = false
  }
}
async function manualFillExpected() {
  if (!current.value || busy.value) return
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(
      '人工核对会跳过物理扫码。请现场核对库位和物料标签，并填写原因；系统会记录审计日志。',
      '人工核对覆盖扫码',
      {
        confirmButtonText: '我已现场核对',
        cancelButtonText: '取消',
        inputPlaceholder: '例如：扫码枪故障，已由操作员目视核对标签',
        inputValidator: (value: string) => value.trim().length >= 2 || '请至少填写 2 个字符的原因',
      },
    )
    reason = String(result.value || '').trim()
  } catch {
    return
  }
  manualOverrideReason.value = reason
  confirmationMethod.value = 'manual'
  // Explicitly use the task's authoritative expected tokens only after the
  // operator has attested to the manual verification and supplied a reason.
  locationToken.value = current.value.location_code
  materialToken.value = current.value.material_code
  scanStage.value = 'ready'
  scanError.value = '人工核对模式：未使用物理扫码；原因将写入审计日志。'
  quantity.value = Number(current.value.remaining_quantity)
  await focusScanner()
}
async function replan() {
  if (!task.value || busy.value) return
  await ElMessageBox.confirm('按当前封闭通道重新规划剩余取料？已完成的取料历史不会改变。', '重新规划')
  busy.value = true
  try {
    await api.post(`/pick-tasks/${task.value.id}/replan`, {
      client_operation_id: operationKey(),
      reason: 'Guided Picking 操作员重新规划',
      closed_edge_codes: normalizeEdges(),
    })
    ElMessage.success('剩余路线已重新规划')
    await load()
    resetScan()
  } finally {
    busy.value = false
  }
}
async function reportIssue() {
  if (!task.value || issueNotes.value.trim().length < 2 || busy.value) return
  busy.value = true
  try {
    const { data } = await api.post<{ recommended_action: string }>(
      `/pick-tasks/${task.value.id}/issues`,
      {
        issue_type: issueType.value,
        allocation_id: current.value?.id || null,
        notes: issueNotes.value.trim(),
      },
    )
    issueNotes.value = ''
    ElMessage.success(
      data.recommended_action === 'replan' ? '异常已记录，可重新规划剩余路线' : '异常已记录',
    )
    await load()
  } finally {
    busy.value = false
  }
}
function actionLabel(action: string) {
  return (
    {
      'picking.create': '创建任务',
      'picking.confirm': '确认取料',
      'picking.replan': '重新规划',
      'picking.issue': '报告异常',
      'picking.cancel': '取消任务',
    } as Record<string, string>
  )[action] || action
}

watch(current, (next, previous) => {
  if (next?.id !== previous?.id) resetScan()
})
onMounted(async () => {
  await load()
  resetScan()
})
</script>

<template>
  <section class="operator-page" data-testid="picking-operator-page" :aria-busy="loading">
    <header class="operator-header">
      <div>
        <div class="eyebrow">MATERIALBRAIN / PICKING</div>
        <h1>Guided Picking · 拣货执行</h1>
        <p v-if="task">{{ task.pick_task_no }} · {{ task.status }}</p>
      </div>
      <div class="header-actions">
        <el-button @click="router.push({ path: '/projects', query: { task: taskId } })">返回生产任务</el-button>
        <el-button
          v-if="task?.warehouse_map_id"
          @click="router.push({ path: '/warehouse-twin', query: { task: taskId } })"
        >打开完整数字孪生</el-button>
      </div>
    </header>

    <section v-if="state" class="progress-card" data-testid="operator-progress">
      <div class="progress-copy">
        <strong>{{ state.progress.stations_completed }} / {{ state.progress.stations_total }} 站</strong>
        <span>{{ state.progress.allocations_completed }} / {{ state.progress.allocations_total }} 格口完成</span>
      </div>
      <el-progress :percentage="stationPercent" :stroke-width="10" />
      <div class="station-strip" aria-label="拣货站点进度">
        <button
          v-for="station in state.station_groups"
          :key="`${station.route_sequence}-${station.route_node_code}`"
          type="button"
          :class="['station-pill', station.status]"
          :data-testid="`operator-station-${station.station_index}`"
        >
          <b>{{ station.station_index }}</b>
          <span>{{ station.route_node_code || '层级路线' }}</span>
          <small>{{ station.completed_allocations }}/{{ station.allocations.length }}</small>
        </button>
      </div>
    </section>

    <div v-if="state" class="operator-grid">
      <section class="twin-panel" data-testid="operator-twin">
        <WarehouseTwin
          :task-id="taskId"
          :focus-location-id="current?.location_id || null"
          embedded
        />
      </section>

      <aside class="operator-panel">
        <section class="panel-card current-card" data-testid="operator-current-allocation">
          <div class="section-label">当前取料</div>
          <template v-if="current">
            <h2>{{ current.material_name }}</h2>
            <div class="code">{{ current.material_code }} · {{ current.mpn }}</div>
            <dl>
              <div><dt>库位</dt><dd>{{ current.location_code }}</dd></div>
              <div><dt>路径</dt><dd>{{ current.full_path }}</dd></div>
              <div><dt>剩余</dt><dd>{{ current.remaining_quantity }} {{ current.unit }}</dd></div>
              <div><dt>当前站</dt><dd>{{ currentStation?.station_index }} / {{ state.progress.stations_total }}</dd></div>
            </dl>
            <div v-if="currentStation && currentStation.allocations.length > 1" class="same-station" data-testid="operator-same-station">
              <b>同一设备内还有 {{ currentStation.remaining_allocations }} 个格口待取</b>
              <span>当前设备完成后才进入下一步行路线。</span>
              <div class="mini-allocations">
                <span
                  v-for="allocation in currentStation.allocations"
                  :key="allocation.id"
                  :class="allocation.status"
                >{{ allocation.location_code }} · {{ allocation.remaining_quantity }}</span>
              </div>
            </div>
          </template>
          <el-result v-else-if="complete" icon="success" title="拣货任务已完成" sub-title="库存、项目预留与流水已完成结算" />
          <el-empty v-else description="没有待处理分配" />
        </section>

        <section v-if="current" class="panel-card scanner-card">
          <div class="scanner-heading">
            <span class="section-label">扫码确认</span>
            <b>{{ stageLabel }}</b>
          </div>
          <div :class="['scan-state', scanStage]" data-testid="operator-scan-stage">{{ scanPrompt }}</div>
          <el-input
            ref="scannerInput"
            v-model="scanText"
            size="large"
            clearable
            autocomplete="off"
            data-testid="operator-scanner-input"
            placeholder="扫描条码后回车，也可以手工输入"
            @keyup.enter="handleScan"
          />
          <p v-if="scanError" class="scan-error" role="alert">{{ scanError }}</p>
          <div class="scan-verified">
            <span :class="{ ok: !!locationToken }">库位 {{ locationToken ? '✓' : '待扫描' }}</span>
            <span :class="{ ok: !!materialToken }">物料 {{ materialToken ? '✓' : '待扫描' }}</span>
          </div>
          <div class="quantity-row">
            <span>实际取料数量</span>
            <el-input-number
              v-model="quantity"
              :min="quantityStep"
              :max="Number(current.remaining_quantity)"
              :step="quantityStep"
              :precision="quantityPrecision"
              :disabled="scanStage !== 'ready'"
              data-testid="operator-quantity"
            />
          </div>
          <div class="scan-actions">
            <el-button @click="resetScan">重新扫描</el-button>
            <el-button data-testid="operator-manual-override" @click="manualFillExpected">人工核对</el-button>
            <el-button
              type="primary"
              :loading="busy"
              :disabled="scanStage !== 'ready' || quantity <= 0"
              data-testid="operator-confirm-pick"
              @click="confirmPick"
            >确认取料</el-button>
          </div>
        </section>

        <section v-if="task && !complete" class="panel-card exception-card">
          <div class="section-label">异常与重规划</div>
          <el-select v-model="issueType" aria-label="异常类型" data-testid="operator-issue-type">
            <el-option value="location_blocked" label="库位 / 通道受阻" />
            <el-option value="stock_shortage" label="现场库存不足" />
            <el-option value="label_unreadable" label="标签无法识别" />
            <el-option value="damaged_material" label="物料损坏" />
            <el-option value="other" label="其他" />
          </el-select>
          <el-input v-model="issueNotes" type="textarea" :rows="2" placeholder="描述现场异常" data-testid="operator-issue-notes" />
          <el-button :disabled="issueNotes.trim().length < 2" data-testid="operator-report-issue" @click="reportIssue">记录异常</el-button>
          <div class="replan-row">
            <el-input v-model="closedEdges" placeholder="封闭通道编码，逗号分隔" data-testid="operator-closed-edges" />
            <el-button type="warning" plain data-testid="operator-replan" @click="replan">重新规划剩余路线</el-button>
          </div>
        </section>

        <details class="panel-card timeline-card" data-testid="operator-timeline">
          <summary>任务记录</summary>
          <ol>
            <li v-for="event in state.events.slice(0, 12)" :key="event.id">
              <b>{{ actionLabel(event.action) }}</b>
              <time>{{ new Date(event.created_at).toLocaleString() }}</time>
            </li>
          </ol>
        </details>
      </aside>
    </div>

    <el-result v-else-if="!loading" icon="error" title="无法读取拣货任务" />
  </section>
</template>

<style scoped>
.operator-page {
  --panel-border: #dbe5ec;
  min-width: 0;
  padding: 8px 10px 18px;
  color: #21374a;
}
.operator-header,
.progress-card,
.panel-card {
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: #fff;
}
.operator-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 14px 16px;
  margin-bottom: 10px;
}
.operator-header h1 { margin: 2px 0; font-size: 24px; }
.operator-header p { margin: 0; color: #72869a; font-size: 13px; }
.eyebrow,
.section-label { color: #66839a; font-size: 12px; font-weight: 800; letter-spacing: .08em; }
.header-actions { display: flex; gap: 8px; }
.progress-card { padding: 12px 14px; margin-bottom: 10px; }
.progress-copy { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 13px; }
.station-strip { display: flex; gap: 7px; overflow-x: auto; margin-top: 10px; padding-bottom: 2px; }
.station-pill {
  min-width: 118px;
  display: grid;
  grid-template-columns: 24px 1fr auto;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid #dbe5ec;
  border-radius: 9px;
  color: #60788b;
  background: #f7fafc;
}
.station-pill.current { color: #155f8e; border-color: #4aa7dd; background: #eef8fe; }
.station-pill.completed { color: #2c7759; border-color: #b8dfcf; background: #f0faf6; }
.station-pill b { display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; background: #e3edf3; }
.station-pill small { font-size: 11px; }
.operator-grid { display: grid; grid-template-columns: minmax(0, 1fr) 370px; gap: 10px; min-height: 650px; }
.twin-panel { min-width: 0; overflow: hidden; border: 1px solid var(--panel-border); border-radius: 12px; background: #eef4f8; }
.operator-panel { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.panel-card { padding: 14px; }
.current-card h2 { margin: 6px 0 2px; font-size: 19px; }
.code { color: #6c8295; font-size: 12px; margin-bottom: 10px; }
dl { margin: 0; display: grid; gap: 6px; }
dl div { display: grid; grid-template-columns: 76px 1fr; gap: 8px; align-items: start; }
dt { color: #7e91a1; font-size: 12px; }
dd { margin: 0; font-size: 13px; font-weight: 700; overflow-wrap: anywhere; }
.same-station { margin-top: 12px; padding: 10px; border-radius: 9px; background: #f4f8fb; }
.same-station b, .same-station span { display: block; font-size: 12px; }
.same-station span { color: #718698; margin-top: 3px; }
.mini-allocations { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
.mini-allocations span { padding: 4px 6px; border: 1px solid #dce6ed; border-radius: 6px; background: #fff; }
.mini-allocations span.picked { color: #2d775a; text-decoration: line-through; }
.scanner-heading { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.scan-state { margin: 8px 0 10px; padding: 10px; border-radius: 8px; background: #eff6fa; font-weight: 800; font-size: 14px; }
.scan-state.ready { color: #246c50; background: #edf8f3; }
.scan-error { color: #b64637; margin: 8px 0; font-size: 12px; }
.scan-verified { display: flex; gap: 8px; margin-top: 8px; }
.scan-verified span { padding: 4px 7px; border-radius: 6px; background: #f1f4f6; color: #8796a2; font-size: 12px; }
.scan-verified span.ok { color: #28765a; background: #eaf7f1; }
.quantity-row { display: flex; align-items: center; justify-content: space-between; margin: 12px 0; font-size: 13px; }
.scan-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 7px; }
.exception-card { display: grid; gap: 8px; }
.replan-row { display: grid; grid-template-columns: 1fr auto; gap: 8px; }
.timeline-card summary { cursor: pointer; font-weight: 800; }
.timeline-card ol { margin: 10px 0 0; padding-left: 18px; }
.timeline-card li { margin: 5px 0; font-size: 12px; }
.timeline-card time { display: block; color: #8495a2; font-size: 11px; }
@media (max-width: 1180px) {
  .operator-grid { grid-template-columns: 1fr; }
  .operator-panel { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .current-card, .scanner-card { min-height: 280px; }
}
</style>
