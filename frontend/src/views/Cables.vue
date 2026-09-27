<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Delete, Edit, Minus, Plus, Refresh, Search, Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, uuidKey } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { formatQuantity } from '../utils/format'
import type {
  Cable,
  CableDirection,
  CableEndStyle,
  CableImportPreview,
  CableImportResult,
  CableImportRow,
  CableKind,
  CablePage,
} from '../types'

interface CableForm {
  name: string
  model: string
  cable_kind: CableKind
  end_style: CableEndStyle
  connector_a: string
  connector_b: string
  connector_pitch_mm: string
  direction: CableDirection
  length_cm: string
  pin_count: string
  pin_count_b: string
  pin_layout: string
  quantity: number
  storage_location: string
  notes: string
}

const pitchPresets = ['0.8', '1.0', '1.25', '2.0', '2.54']
const lengthPresets = ['10', '15', '20', '30', '50']
const pinPresets = [4, 6, 8, 10, 12, 14, 16, 20, 24, 30, 40, 50]
const auth = useAuthStore()
const route = useRoute()
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const importDialogVisible = ref(false)
const analyzingImport = ref(false)
const committingImport = ref(false)
const importFileInput = ref<HTMLInputElement | null>(null)
const importPreview = ref<CableImportPreview | null>(null)
const editingId = ref<number | null>(null)
const adjustingIds = ref<number[]>([])
const focusedCableId = ref<number | null>(null)
const filters = reactive({
  q: '',
  cable_kind: '' as '' | CableKind,
  end_style: '' as '' | CableEndStyle,
  connector_pitch_mm: '',
  direction: '' as '' | CableDirection,
  length_cm: '',
  pin_count: null as number | null,
  page: 1,
  page_size: 50,
})
const data = reactive<CablePage>({
  items: [],
  total: 0,
  page: 1,
  page_size: 50,
  summary: {
    quantity: '0',
    available_quantity: '0',
    in_stock_types: 0,
    pitch_count: 0,
  },
  facets: {
    connector_pitches: [],
    lengths: [],
    pin_counts: [],
    cable_kinds: [],
    end_styles: [],
  },
})
const form = reactive<CableForm>({
  name: '',
  model: '',
  cable_kind: 'terminal',
  end_style: 'double',
  connector_a: '',
  connector_b: '',
  connector_pitch_mm: '1.0',
  direction: 'same',
  length_cm: '20',
  pin_count: '20',
  pin_count_b: '',
  pin_layout: '',
  quantity: 0,
  storage_location: '',
  notes: '',
})

const canManage = computed(() => auth.can('material:manage'))
const canOperate = computed(() => auth.can('inventory:operate'))
const canImport = computed(() => canManage.value && canOperate.value && auth.can('import:manage'))
const editing = computed(() => editingId.value !== null)
const pitchOptions = computed(() =>
  [...new Set([...pitchPresets, ...data.facets.connector_pitches])].sort(
    (left, right) => Number(left) - Number(right),
  ),
)
const lengthOptions = computed(() =>
  [...new Set([...lengthPresets, ...data.facets.lengths])].sort(
    (left, right) => Number(left) - Number(right),
  ),
)
const pinOptions = computed(() =>
  [...new Set([...pinPresets, ...data.facets.pin_counts])].sort((left, right) => left - right),
)
const hasFilters = computed(
  () =>
    Boolean(filters.q) ||
    Boolean(filters.cable_kind) ||
    Boolean(filters.end_style) ||
    Boolean(filters.connector_pitch_mm) ||
    Boolean(filters.direction) ||
    Boolean(filters.length_cm) ||
    filters.pin_count !== null,
)
const selectedImportRows = computed(
  () => importPreview.value?.rows.filter((row) => row.selected) ?? [],
)
const readyImportRows = computed(() =>
  selectedImportRows.value.filter((row) => importRowReady(row)),
)
const selectedImportQuantity = computed(() =>
  readyImportRows.value.reduce((total, row) => total + Number(row.quantity || 0), 0),
)

function decimalLabel(value: string | null): string {
  if (!value) return '—'
  const number = Number(value)
  if (!Number.isFinite(number)) return value
  return Number.isInteger(number) ? String(number) : String(number)
}

function directionLabel(direction: CableDirection): string {
  if (direction === 'same') return '同向'
  if (direction === 'reverse') return '反向'
  return '未注明'
}

function cableKindLabel(kind: CableKind): string {
  if (kind === 'flat_flex') return 'FPC/FFC 软排线'
  if (kind === 'micro_coax') return 'FPC 极细同轴'
  if (kind === 'rf_coax') return 'IPEX 射频同轴'
  return '端子线'
}

function endStyleLabel(style: CableEndStyle): string {
  if (style === 'single') return '单头'
  if (style === 'single_tinned') return '单头沾锡'
  if (style === 'male_female_pair') return '公母一套'
  if (style === 'unspecified') return '端头未注明'
  return '双头'
}

function directionForKind(row: Pick<Cable, 'direction' | 'cable_kind'>): string {
  if (row.direction === 'unspecified') return '方向未注明'
  if (row.cable_kind === 'flat_flex' || row.cable_kind === 'micro_coax') {
    return row.direction === 'same' ? '同面 / A型' : '反面 / B型'
  }
  return directionLabel(row.direction)
}

function pinLabel(row: Pick<Cable, 'pin_count' | 'pin_count_b' | 'pin_layout'>): string {
  if (row.pin_count_b > 0) return `${row.pin_count} → ${row.pin_count_b}`
  if (row.pin_count <= 0) return '—'
  return row.pin_layout ? `${row.pin_layout} / ${row.pin_count}` : String(row.pin_count)
}

function requiresPitchAndPins(kind: CableKind): boolean {
  return kind !== 'rf_coax'
}

function handleKindChange(kind: CableKind) {
  if (kind === 'rf_coax') {
    form.end_style = 'double'
    form.direction = 'unspecified'
    if (!form.connector_a) form.connector_a = 'IPEX'
    if (!form.connector_b) form.connector_b = 'IPEX'
    return
  }
  if (kind === 'flat_flex' || kind === 'micro_coax') {
    form.end_style = 'double'
  }
  if (form.direction === 'unspecified') form.direction = 'same'
}

function importActionLabel(action: CableImportRow['import_action']): string {
  if (action === 'increase') return '增加现有库存'
  if (action === 'skip') return '已导入，跳过'
  return '新建线缆'
}

function confidenceLabel(confidence: CableImportRow['confidence']): string {
  if (confidence === 'high') return '高可信'
  if (confidence === 'medium') return '需留意'
  return '待补充'
}

function importRowKey(row: CableImportRow): string {
  return row.source_items[0]?.key ?? row.source_rows.join('-')
}

function importRowCancelled(row: CableImportRow): boolean {
  return row.issues.some((issue) => issue.includes('订单状态为'))
}

function importRowReady(row: CableImportRow): boolean {
  const pitch = row.connector_pitch_mm === null ? Number.NaN : Number(row.connector_pitch_mm)
  const length = Number(row.length_cm)
  const price = row.unit_price === '' ? 0 : Number(row.unit_price)
  return (
    row.name.trim().length > 0 &&
    (!requiresPitchAndPins(row.cable_kind) || (Number.isFinite(pitch) && pitch > 0)) &&
    Number.isFinite(length) &&
    length > 0 &&
    (!requiresPitchAndPins(row.cable_kind) ||
      (Number.isInteger(Number(row.pin_count)) && Number(row.pin_count) > 0)) &&
    Number.isInteger(Number(row.quantity)) &&
    Number(row.quantity) > 0 &&
    Number.isFinite(price) &&
    price >= 0 &&
    ['same', 'reverse', 'unspecified'].includes(row.direction) &&
    !importRowCancelled(row)
  )
}

function currentImportIssue(row: CableImportRow): string {
  if (importRowCancelled(row)) {
    return row.issues.find((issue) => issue.includes('订单状态为')) ?? ''
  }
  if (!row.name.trim()) return '请填写线缆名称'
  if (requiresPitchAndPins(row.cable_kind) && !(Number(row.connector_pitch_mm) > 0)) {
    return '请填写正确的接头间距'
  }
  if (
    requiresPitchAndPins(row.cable_kind) &&
    (!Number.isInteger(Number(row.pin_count)) || Number(row.pin_count) <= 0)
  ) {
    return '请填写正确的 Pin 数'
  }
  if (!(Number(row.length_cm) > 0)) return '请填写正确的长度'
  if (!Number.isInteger(Number(row.quantity)) || Number(row.quantity) <= 0) {
    return '请填写正确的入库数量'
  }
  if (row.unit_price && !(Number(row.unit_price) >= 0)) return '请填写正确的单价'
  return ''
}

function triggerImportFile() {
  importFileInput.value?.click()
}

async function handleImportFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || analyzingImport.value) return
  const suffix = file.name.split('.').pop()?.toLowerCase()
  if (!suffix || !['xlsx', 'xls', 'csv'].includes(suffix)) {
    ElMessage.warning('请选择 XLSX、XLS 或 CSV 订单表格')
    return
  }
  analyzingImport.value = true
  try {
    const body = new FormData()
    body.append('file', file)
    importPreview.value = (await api.post<CableImportPreview>('/cables/import/preview', body)).data
    importDialogVisible.value = true
    const summary = importPreview.value.summary
    ElMessage.success(
      `已识别 ${summary.source_rows} 条订单明细，整理为 ${summary.spec_count} 种线缆`,
    )
  } finally {
    analyzingImport.value = false
  }
}

async function commitCableImport() {
  if (!importPreview.value || committingImport.value) return
  if (!selectedImportRows.value.length) {
    ElMessage.warning('请至少选择一项需要导入的线缆')
    return
  }
  const invalid = selectedImportRows.value.filter((row) => !importRowReady(row))
  if (invalid.length) {
    ElMessage.warning(`还有 ${invalid.length} 项规格未填写完整，请先修正红色提示`)
    return
  }
  committingImport.value = true
  try {
    const rows = readyImportRows.value.map((row) => ({
      name: row.name,
      model: row.model,
      cable_kind: row.cable_kind,
      end_style: row.end_style,
      connector_a: row.connector_a,
      connector_b: row.connector_b,
      connector_pitch_mm:
        row.connector_pitch_mm === null || row.connector_pitch_mm === ''
          ? null
          : Number(row.connector_pitch_mm),
      direction: row.direction,
      length_cm: Number(row.length_cm),
      pin_count: Number(row.pin_count),
      pin_count_b: Number(row.pin_count_b || 0),
      pin_layout: row.pin_layout,
      quantity: Number(row.quantity),
      unit_price: row.unit_price === '' ? null : Number(row.unit_price),
      storage_location: row.storage_location,
      notes: row.notes,
      shop: row.shop,
      raw_product_name: row.raw_product_name,
      raw_variant: row.raw_variant,
      source_rows: row.source_rows,
      source_items: row.source_items,
    }))
    const result = (
      await api.post<CableImportResult>('/cables/import/commit', {
        rows,
        idempotency_key: uuidKey(),
      })
    ).data
    ElMessage.success(
      `导入完成：新建 ${result.created} 种、补充 ${result.increased} 种，共入库 ${result.quantity_added} 条`,
    )
    importDialogVisible.value = false
    importPreview.value = null
    await load()
  } finally {
    committingImport.value = false
  }
}

function resetForm() {
  Object.assign(form, {
    name: '',
    model: '',
    cable_kind: 'terminal',
    end_style: 'double',
    connector_a: '',
    connector_b: '',
    connector_pitch_mm: '1.0',
    direction: 'same',
    length_cm: '20',
    pin_count: '20',
    pin_count_b: '',
    pin_layout: '',
    quantity: 0,
    storage_location: '',
    notes: '',
  } satisfies CableForm)
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, string | number> = {
      q: filters.q,
      page: filters.page,
      page_size: filters.page_size,
    }
    if (filters.connector_pitch_mm) {
      params.connector_pitch_mm = filters.connector_pitch_mm
    }
    if (filters.direction) params.direction = filters.direction
    if (filters.cable_kind) params.cable_kind = filters.cable_kind
    if (filters.end_style) params.end_style = filters.end_style
    if (filters.length_cm) params.length_cm = filters.length_cm
    if (filters.pin_count !== null) params.pin_count = filters.pin_count
    Object.assign(data, (await api.get<CablePage>('/cables', { params })).data)
  } finally {
    loading.value = false
  }
}

function cableRowClass({ row }: { row: Cable }) {
  return row.id === focusedCableId.value ? 'focus-row' : ''
}

async function revealFocusedCable() {
  const id = Number(route.query.focus)
  if (!Number.isInteger(id) || id <= 0) {
    focusedCableId.value = null
    await load()
    return
  }
  focusedCableId.value = id
  try {
    const cable = (await api.get<Cable>(`/cables/${id}`)).data
    filters.q = cable.code
    filters.page = 1
    await load()
    await nextTick()
    document.querySelector('.cable-table .focus-row')?.scrollIntoView({
      block: 'center',
      behavior: 'smooth',
    })
  } catch {
    ElMessage.warning('未找到要定位的线缆')
    await load()
  }
}

function applyFilters() {
  filters.page = 1
  void load()
}

function clearFilters() {
  Object.assign(filters, {
    q: '',
    cable_kind: '',
    end_style: '',
    connector_pitch_mm: '',
    direction: '',
    length_cm: '',
    pin_count: null,
    page: 1,
  })
  void load()
}

function openCreate() {
  editingId.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: Cable) {
  editingId.value = row.id
  Object.assign(form, {
    name: row.custom_name,
    model: row.model,
    cable_kind: row.cable_kind,
    end_style: row.end_style,
    connector_a: row.connector_a,
    connector_b: row.connector_b,
    connector_pitch_mm: row.connector_pitch_mm,
    direction: row.direction,
    length_cm: row.length_cm,
    pin_count: String(row.pin_count),
    pin_count_b: row.pin_count_b > 0 ? String(row.pin_count_b) : '',
    pin_layout: row.pin_layout,
    quantity: Number(row.quantity),
    storage_location: row.storage_location,
    notes: row.notes,
  } satisfies CableForm)
  dialogVisible.value = true
}

function validateForm(): boolean {
  const pitch = Number(form.connector_pitch_mm)
  const length = Number(form.length_cm)
  const pins = Number(form.pin_count)
  if (requiresPitchAndPins(form.cable_kind) && (!Number.isFinite(pitch) || pitch <= 0)) {
    ElMessage.warning('请输入正确的接头间距')
    return false
  }
  if (!Number.isFinite(length) || length <= 0) {
    ElMessage.warning('请输入正确的线缆长度')
    return false
  }
  if (requiresPitchAndPins(form.cable_kind) && (!Number.isInteger(pins) || pins <= 0)) {
    ElMessage.warning('Pin 数必须是大于 0 的整数')
    return false
  }
  return true
}

async function saveCable() {
  if (!validateForm() || saving.value) return
  saving.value = true
  try {
    const payload = {
      name: form.name,
      model: form.model,
      cable_kind: form.cable_kind,
      end_style: form.end_style,
      connector_a: form.connector_a,
      connector_b: form.connector_b,
      connector_pitch_mm: requiresPitchAndPins(form.cable_kind)
        ? Number(form.connector_pitch_mm)
        : null,
      direction: form.direction,
      length_cm: Number(form.length_cm),
      pin_count: requiresPitchAndPins(form.cable_kind) ? Number(form.pin_count) : 0,
      pin_count_b: Number(form.pin_count_b || 0),
      pin_layout: form.pin_layout,
      quantity: canOperate.value ? form.quantity : editing.value ? form.quantity : 0,
      storage_location: form.storage_location,
      notes: form.notes,
      idempotency_key: uuidKey(),
    }
    if (editingId.value !== null) {
      await api.put(`/cables/${editingId.value}`, payload)
      ElMessage.success('线缆资料已更新')
    } else {
      await api.post('/cables', payload)
      ElMessage.success('线缆已入库建档')
    }
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function adjustQuantity(row: Cable, delta: number) {
  if (adjustingIds.value.includes(row.id)) return
  adjustingIds.value.push(row.id)
  try {
    await api.post(`/cables/${row.id}/quantity`, {
      delta,
      idempotency_key: uuidKey(),
    })
    await load()
  } finally {
    adjustingIds.value = adjustingIds.value.filter((id) => id !== row.id)
  }
}

async function removeCable(row: Cable) {
  if (Number(row.quantity) > 0 || Number(row.reserved_quantity) > 0) {
    ElMessage.warning('请先把该线缆库存调整为 0，再删除记录')
    return
  }
  await ElMessageBox.confirm(`确认删除“${row.name}”？该操作会保留历史库存流水。`, '删除线缆', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
  await api.delete(`/cables/${row.id}`)
  ElMessage.success('线缆记录已删除')
  await load()
}

onMounted(revealFocusedCable)
watch(() => route.query.focus, revealFocusedCable)
</script>

<template>
  <div class="page cable-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">线缆管理</h1>
        <div class="page-subtitle">统一管理端子线、单头沾锡、FPC/FFC 排线与同轴线缆</div>
      </div>
      <div class="page-actions">
        <input
          ref="importFileInput"
          class="hidden-file-input"
          type="file"
          accept=".xlsx,.xls,.csv"
          @change="handleImportFile"
        />
        <el-button
          v-if="canImport"
          :icon="Upload"
          :loading="analyzingImport"
          @click="triggerImportFile"
        >
          导入订单表格
        </el-button>
        <el-button v-if="canManage" type="primary" :icon="Plus" @click="openCreate">
          新增线缆
        </el-button>
      </div>
    </div>

    <section class="summary-grid" aria-label="线缆库存概况">
      <article>
        <span>筛选结果</span>
        <b>{{ data.total }}</b>
        <small>种线缆规格</small>
      </article>
      <article>
        <span>库存总数</span>
        <b>{{ data.summary.quantity }}</b>
        <small>条</small>
      </article>
      <article>
        <span>有库存规格</span>
        <b>{{ data.summary.in_stock_types }}</b>
        <small>种</small>
      </article>
      <article>
        <span>接头间距</span>
        <b>{{ data.summary.pitch_count }}</b>
        <small>种规格</small>
      </article>
    </section>

    <el-card class="card cable-card">
      <div class="filter-grid">
        <el-input
          v-model="filters.q"
          :prefix-icon="Search"
          clearable
          placeholder="搜索编号、名称、型号、Pin 数、位置或备注"
          @keyup.enter="applyFilters"
          @clear="applyFilters"
        />
        <el-select
          v-model="filters.cable_kind"
          clearable
          placeholder="线缆类型"
          @change="applyFilters"
        >
          <el-option label="端子线" value="terminal" />
          <el-option label="FPC/FFC 软排线" value="flat_flex" />
          <el-option label="FPC 极细同轴" value="micro_coax" />
          <el-option label="IPEX 射频同轴" value="rf_coax" />
        </el-select>
        <el-select
          v-model="filters.end_style"
          clearable
          placeholder="端头形式"
          @change="applyFilters"
        >
          <el-option label="双头" value="double" />
          <el-option label="单头" value="single" />
          <el-option label="单头沾锡" value="single_tinned" />
          <el-option label="公母一套" value="male_female_pair" />
          <el-option label="端头未注明" value="unspecified" />
        </el-select>
        <el-select
          v-model="filters.connector_pitch_mm"
          clearable
          placeholder="接头间距"
          @change="applyFilters"
        >
          <el-option
            v-for="item in pitchOptions"
            :key="item"
            :label="`${decimalLabel(item)} mm`"
            :value="item"
          />
        </el-select>
        <el-select
          v-model="filters.direction"
          clearable
          placeholder="线缆方向"
          @change="applyFilters"
        >
          <el-option label="同向" value="same" />
          <el-option label="反向" value="reverse" />
          <el-option label="方向未注明" value="unspecified" />
        </el-select>
        <el-select
          v-model="filters.length_cm"
          clearable
          placeholder="线缆长度"
          @change="applyFilters"
        >
          <el-option
            v-for="item in lengthOptions"
            :key="item"
            :label="`${decimalLabel(item)} cm`"
            :value="item"
          />
        </el-select>
        <el-select
          v-model="filters.pin_count"
          clearable
          placeholder="Pin 数"
          @change="applyFilters"
        >
          <el-option v-for="item in pinOptions" :key="item" :label="`${item} Pin`" :value="item" />
        </el-select>
        <el-button :icon="Refresh" :disabled="!hasFilters" @click="clearFilters"> 重置 </el-button>
      </div>

      <div class="result-note">
        <span
          >共找到 <b>{{ data.total }}</b> 种线缆</span
        >
        <small>常用规格可直接选择，也可在新增窗口输入自定义数值</small>
      </div>

      <el-table
        v-loading="loading"
        :data="data.items"
        row-key="id"
        height="calc(100vh - 410px)"
        stripe
        class="cable-table"
        :row-class-name="cableRowClass"
      >
        <el-table-column label="线缆" min-width="330" fixed>
          <template #default="{ row }">
            <div class="cable-identity">
              <div
                class="cable-visual"
                :class="[row.cable_kind, row.end_style, row.direction]"
                :title="`${cableKindLabel(row.cable_kind)} · ${endStyleLabel(row.end_style)}`"
              >
                <template v-if="row.cable_kind === 'terminal'">
                  <span class="connector connector-left">
                    <i v-for="index in 5" :key="`left-${index}`" />
                  </span>
                  <span class="cable-line"><i /></span>
                  <span v-if="row.end_style === 'single_tinned'" class="tinned-tip" />
                  <span v-else-if="row.end_style === 'single'" class="bare-tip" />
                  <span v-else class="connector connector-right">
                    <i v-for="index in 5" :key="`right-${index}`" />
                  </span>
                </template>
                <template v-else-if="row.cable_kind === 'flat_flex'">
                  <span class="flat-contact" />
                  <span class="flat-ribbon" />
                  <span class="flat-contact" />
                </template>
                <template v-else-if="row.cable_kind === 'micro_coax'">
                  <span class="micro-contact" />
                  <span class="micro-bundle" />
                  <span class="micro-contact" />
                </template>
                <template v-else>
                  <span class="rf-head" />
                  <span class="rf-wire" />
                  <span class="rf-head" />
                </template>
              </div>
              <div class="cable-copy">
                <b>{{ row.name }}</b>
                <span
                  >{{ row.code }}<template v-if="row.model"> · {{ row.model }}</template></span
                >
                <div class="cable-badges">
                  <small>{{ cableKindLabel(row.cable_kind) }}</small>
                  <small>{{ endStyleLabel(row.end_style) }}</small>
                </div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="两端接头" min-width="155">
          <template #default="{ row }">
            <div class="endpoint-copy">
              <span>{{ row.connector_a || '接头 A 未填写' }}</span>
              <i>→</i>
              <span>{{ row.connector_b || '接头 B 未填写' }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="接头间距" width="100" align="center">
          <template #default="{ row }">
            <strong class="spec-number">{{ decimalLabel(row.connector_pitch_mm) }}</strong>
            <small v-if="row.connector_pitch_mm" class="spec-unit">mm</small>
          </template>
        </el-table-column>
        <el-table-column label="Pin 数" width="105" align="center">
          <template #default="{ row }">
            <strong class="spec-number">{{ pinLabel(row) }}</strong>
            <small v-if="row.pin_count > 0" class="spec-unit">
              {{ row.pin_count_b > 0 ? '两端 Pin' : 'Pin' }}
            </small>
          </template>
        </el-table-column>
        <el-table-column label="方向" width="110" align="center">
          <template #default="{ row }">
            <el-tag
              :type="
                row.direction === 'same'
                  ? 'primary'
                  : row.direction === 'reverse'
                    ? 'warning'
                    : 'info'
              "
              effect="light"
            >
              {{ directionForKind(row) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="长度" width="95" align="center">
          <template #default="{ row }">
            <strong class="spec-number">{{ decimalLabel(row.length_cm) }}</strong>
            <small class="spec-unit">cm</small>
          </template>
        </el-table-column>
        <el-table-column label="采购单价" width="105" align="center">
          <template #default="{ row }">
            <span v-if="Number(row.unit_price) > 0" class="price-value">
              ¥{{ decimalLabel(row.unit_price) }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="当前库存" width="150" align="center">
          <template #default="{ row }">
            <div class="stock-control">
              <button
                v-if="canOperate"
                type="button"
                title="出库 1 条"
                :disabled="Number(row.available_quantity) <= 0 || adjustingIds.includes(row.id)"
                @click="adjustQuantity(row, -1)"
              >
                <el-icon><Minus /></el-icon>
              </button>
              <b>{{ formatQuantity(row.quantity) }}</b>
              <button
                v-if="canOperate"
                type="button"
                title="入库 1 条"
                :disabled="adjustingIds.includes(row.id)"
                @click="adjustQuantity(row, 1)"
              >
                <el-icon><Plus /></el-icon>
              </button>
            </div>
            <small v-if="Number(row.reserved_quantity)" class="reserved-tip">
              已预留 {{ formatQuantity(row.reserved_quantity) }}
            </small>
          </template>
        </el-table-column>
        <el-table-column label="实际库位" min-width="285">
          <template #default="{ row }">
            <div class="location-truth" data-testid="cable-location-truth">
              <span v-if="row.actual_locations.length" class="location-warehouse">
                仓库：{{ row.actual_locations[0].warehouse }}
              </span>
              <span v-else class="location-unallocated">实际库位：尚未分配</span>
              <RouterLink
                v-for="location in row.actual_locations"
                :key="location.location_id"
                class="actual-location-link"
                data-testid="cable-actual-location-link"
                :to="{ path: '/locations', query: { focus: location.location_id } }"
              >
                实际库位：{{ location.full_path }}
              </RouterLink>
              <small v-if="row.storage_location" class="legacy-location-note">
                备注位置：{{ row.storage_location }}
              </small>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="备注" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ muted: !row.notes }">{{ row.notes || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="canManage" label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button link :icon="Edit" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" :icon="Delete" @click="removeCable(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="hasFilters ? '没有符合条件的线缆' : '还没有录入线缆'">
            <el-button v-if="canManage && !hasFilters" type="primary" @click="openCreate">
              录入第一条线缆
            </el-button>
            <el-button v-else-if="hasFilters" @click="clearFilters">清除筛选</el-button>
          </el-empty>
        </template>
      </el-table>

      <el-pagination
        v-model:current-page="filters.page"
        v-model:page-size="filters.page_size"
        :total="data.total"
        :page-sizes="[20, 50, 100, 200]"
        layout="total, sizes, prev, pager, next"
        class="pager"
        @change="load"
      />
    </el-card>

    <el-dialog
      v-model="importDialogVisible"
      title="导入线缆订单"
      width="94vw"
      top="3vh"
      destroy-on-close
      append-to-body
      class="cable-import-dialog"
    >
      <div v-if="importPreview" class="import-preview">
        <div class="import-summary">
          <div class="import-file-copy">
            <span>{{ importPreview.detected_format }}</span>
            <b>{{ importPreview.filename }}</b>
            <small>
              {{
                importPreview.summary.shops.length
                  ? importPreview.summary.shops.join('、')
                  : '未识别店铺'
              }}
            </small>
          </div>
          <div class="import-stat">
            <span>订单明细</span>
            <b>{{ importPreview.summary.source_rows }}</b>
            <small>条</small>
          </div>
          <div class="import-stat">
            <span>线缆规格</span>
            <b>{{ importPreview.summary.spec_count }}</b>
            <small>种</small>
          </div>
          <div class="import-stat">
            <span>识别数量</span>
            <b>{{ importPreview.summary.quantity }}</b>
            <small>条</small>
          </div>
          <div class="import-stat">
            <span>合并重复明细</span>
            <b>{{ importPreview.summary.merged_rows }}</b>
            <small>条</small>
          </div>
        </div>

        <div class="import-guidance">
          <div>
            <b>规格已经自动分析，可在下表直接修改后再入库</b>
            <span>
              已支持单头沾锡、公母对接、FPC/FFC、22→15 Pin 转接排线、极细同轴和 IPEX
              射频线；同规格会自动合并。
            </span>
          </div>
          <el-button :icon="Upload" :loading="analyzingImport" @click="triggerImportFile">
            重新选择文件
          </el-button>
        </div>

        <el-table
          :data="importPreview.rows"
          :row-key="importRowKey"
          height="58vh"
          border
          class="import-table"
        >
          <el-table-column label="导入" width="62" fixed="left" align="center">
            <template #default="{ row }">
              <el-checkbox
                v-model="row.selected"
                :disabled="row.import_action === 'skip' || importRowCancelled(row)"
              />
            </template>
          </el-table-column>
          <el-table-column label="来源" width="116" fixed="left">
            <template #default="{ row }">
              <div class="source-cell">
                <b>第 {{ row.source_rows.join('、') }} 行</b>
                <span v-if="row.source_line_count > 1"> 合并 {{ row.source_line_count }} 条 </span>
                <el-tooltip
                  :content="`${row.raw_product_name}\n${row.raw_variant}`"
                  placement="top"
                >
                  <small>{{ row.raw_variant }}</small>
                </el-tooltip>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="处理方式" width="126">
            <template #default="{ row }">
              <el-tag
                :type="
                  row.import_action === 'create'
                    ? 'success'
                    : row.import_action === 'increase'
                      ? 'primary'
                      : 'info'
                "
                effect="light"
              >
                {{ importActionLabel(row.import_action) }}
              </el-tag>
              <small v-if="row.existing_cable_code" class="existing-code">
                {{ row.existing_cable_code }}
              </small>
            </template>
          </el-table-column>
          <el-table-column label="类型 / 端头" width="176">
            <template #default="{ row }">
              <div class="stacked-inputs">
                <el-select v-model="row.cable_kind" size="small">
                  <el-option label="端子线" value="terminal" />
                  <el-option label="FPC/FFC 软排线" value="flat_flex" />
                  <el-option label="FPC 极细同轴" value="micro_coax" />
                  <el-option label="IPEX 射频同轴" value="rf_coax" />
                </el-select>
                <el-select v-model="row.end_style" size="small">
                  <el-option label="双头" value="double" />
                  <el-option label="单头" value="single" />
                  <el-option label="单头沾锡" value="single_tinned" />
                  <el-option label="公母一套" value="male_female_pair" />
                  <el-option label="端头未注明" value="unspecified" />
                </el-select>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="名称 / 型号" width="260">
            <template #default="{ row }">
              <div class="stacked-inputs">
                <el-input v-model="row.name" size="small" maxlength="200" placeholder="线缆名称" />
                <el-input v-model="row.model" size="small" maxlength="200" placeholder="型号" />
              </div>
            </template>
          </el-table-column>
          <el-table-column label="接头 A / B" width="190">
            <template #default="{ row }">
              <div class="stacked-inputs">
                <el-input
                  v-model="row.connector_a"
                  size="small"
                  maxlength="100"
                  placeholder="接头 A"
                />
                <el-input
                  v-model="row.connector_b"
                  size="small"
                  maxlength="100"
                  placeholder="接头 B"
                />
              </div>
            </template>
          </el-table-column>
          <el-table-column label="接头间距" width="112">
            <template #default="{ row }">
              <el-input v-model="row.connector_pitch_mm" size="small" inputmode="decimal">
                <template #suffix>mm</template>
              </el-input>
            </template>
          </el-table-column>
          <el-table-column label="Pin A / B" width="136">
            <template #default="{ row }">
              <div class="stacked-inputs">
                <el-input-number
                  v-model="row.pin_count"
                  size="small"
                  :min="0"
                  :max="1000"
                  :precision="0"
                  :controls="false"
                  placeholder="A 端"
                />
                <el-input-number
                  v-model="row.pin_count_b"
                  size="small"
                  :min="0"
                  :max="1000"
                  :precision="0"
                  :controls="false"
                  placeholder="B 端选填"
                />
                <el-input
                  v-model="row.pin_layout"
                  size="small"
                  maxlength="50"
                  placeholder="排布，如 2×5"
                />
              </div>
            </template>
          </el-table-column>
          <el-table-column label="长度" width="108">
            <template #default="{ row }">
              <el-input v-model="row.length_cm" size="small" inputmode="decimal">
                <template #suffix>cm</template>
              </el-input>
            </template>
          </el-table-column>
          <el-table-column label="方向" width="100">
            <template #default="{ row }">
              <el-select v-model="row.direction" size="small" placeholder="请选择">
                <el-option label="同向" value="same" />
                <el-option label="反向" value="reverse" />
                <el-option label="未注明" value="unspecified" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="入库数量" width="108">
            <template #default="{ row }">
              <el-input-number
                v-model="row.quantity"
                size="small"
                :min="1"
                :max="1000000000"
                :precision="0"
                :controls="false"
              />
            </template>
          </el-table-column>
          <el-table-column label="订单单价" width="108">
            <template #default="{ row }">
              <el-input v-model="row.unit_price" size="small" inputmode="decimal">
                <template #prefix>¥</template>
              </el-input>
            </template>
          </el-table-column>
          <el-table-column label="备注位置" width="160">
            <template #default="{ row }">
              <el-input
                v-model="row.storage_location"
                size="small"
                maxlength="200"
                placeholder="选填"
              />
            </template>
          </el-table-column>
          <el-table-column label="备注" width="190">
            <template #default="{ row }">
              <el-input v-model="row.notes" size="small" maxlength="2000" placeholder="选填" />
            </template>
          </el-table-column>
          <el-table-column label="识别检查" width="176" fixed="right">
            <template #default="{ row }">
              <div class="import-check">
                <el-tag
                  :type="importRowReady(row) ? 'success' : 'danger'"
                  size="small"
                  effect="plain"
                >
                  {{ importRowReady(row) ? confidenceLabel(row.confidence) : '需要修正' }}
                </el-tag>
                <small
                  v-if="currentImportIssue(row)"
                  class="row-error"
                  :title="currentImportIssue(row)"
                >
                  {{ currentImportIssue(row) }}
                </small>
                <small
                  v-else-if="row.warnings.length"
                  class="row-warning"
                  :title="row.warnings.join('；')"
                >
                  {{ row.warnings.join('；') }}
                </small>
                <small v-else class="row-ready">规格完整，可直接导入</small>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <div class="import-footnote">
          系统会记录每一条入库流水；重复上传同一订单时，已导入明细会自动跳过。
        </div>
      </div>
      <template #footer>
        <div class="import-footer">
          <div>
            已选择 <b>{{ readyImportRows.length }}</b> 种线缆，共
            <b>{{ selectedImportQuantity }}</b> 条
            <span v-if="selectedImportRows.length !== readyImportRows.length">
              · {{ selectedImportRows.length - readyImportRows.length }} 项待修正
            </span>
          </div>
          <div>
            <el-button @click="importDialogVisible = false">取消</el-button>
            <el-button
              type="primary"
              :loading="committingImport"
              :disabled="!readyImportRows.length"
              @click="commitCableImport"
            >
              确认导入并入库
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑线缆' : '新增线缆'"
      width="820px"
      destroy-on-close
      append-to-body
    >
      <el-form label-position="top" class="cable-form">
        <div class="form-grid">
          <el-form-item label="自定义名称（选填）" class="wide-field">
            <el-input v-model="form.name" maxlength="200" placeholder="留空时按规格自动生成名称" />
          </el-form-item>
          <el-form-item label="线缆类型">
            <el-select v-model="form.cable_kind" @change="handleKindChange">
              <el-option label="端子线" value="terminal" />
              <el-option label="FPC/FFC 软排线" value="flat_flex" />
              <el-option label="FPC 极细同轴" value="micro_coax" />
              <el-option label="IPEX 射频同轴" value="rf_coax" />
            </el-select>
          </el-form-item>
          <el-form-item label="端头形式">
            <el-select v-model="form.end_style">
              <el-option label="双头" value="double" />
              <el-option label="单头" value="single" />
              <el-option label="单头沾锡" value="single_tinned" />
              <el-option label="公母一套" value="male_female_pair" />
              <el-option label="端头未注明" value="unspecified" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="requiresPitchAndPins(form.cable_kind)" label="接头间距">
            <el-select
              v-model="form.connector_pitch_mm"
              filterable
              allow-create
              default-first-option
              placeholder="选择或输入间距"
            >
              <el-option
                v-for="item in pitchOptions"
                :key="item"
                :label="`${decimalLabel(item)} mm`"
                :value="item"
              />
            </el-select>
            <small class="field-help">内置 0.8、1.0、1.25、2.0、2.54 mm，也可直接输入</small>
          </el-form-item>
          <el-form-item v-if="requiresPitchAndPins(form.cable_kind)" label="A 端 Pin 数">
            <el-select
              v-model="form.pin_count"
              filterable
              allow-create
              default-first-option
              placeholder="选择或输入 Pin 数"
            >
              <el-option
                v-for="item in pinOptions"
                :key="item"
                :label="`${item} Pin`"
                :value="String(item)"
              />
            </el-select>
            <small class="field-help">可输入任意大于 0 的整数</small>
          </el-form-item>
          <el-form-item v-if="requiresPitchAndPins(form.cable_kind)" label="B 端 Pin 数（选填）">
            <el-input
              v-model="form.pin_count_b"
              inputmode="numeric"
              placeholder="转接排线可填写另一端 Pin 数"
            />
            <small class="field-help">例如 22 Pin 转 15 Pin：A 端填 22，B 端填 15</small>
          </el-form-item>
          <el-form-item v-if="requiresPitchAndPins(form.cable_kind)" label="Pin 排布（选填）">
            <el-input v-model="form.pin_layout" maxlength="50" placeholder="例如 双排、2×4、2×5" />
          </el-form-item>
          <el-form-item label="线缆方向">
            <el-radio-group v-model="form.direction" class="direction-options">
              <el-radio-button value="same">同向 / 同面</el-radio-button>
              <el-radio-button value="reverse">反向 / 反面</el-radio-button>
              <el-radio-button value="unspecified">未注明</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="线缆长度">
            <el-select
              v-model="form.length_cm"
              filterable
              allow-create
              default-first-option
              placeholder="选择或输入长度"
            >
              <el-option
                v-for="item in lengthOptions"
                :key="item"
                :label="`${decimalLabel(item)} cm`"
                :value="item"
              />
            </el-select>
            <small class="field-help">内置 10、15、20、30、50 cm，也可直接输入</small>
          </el-form-item>
          <el-form-item label="型号 / 厂商编号（选填）">
            <el-input v-model="form.model" maxlength="200" placeholder="例如 FFC-A-20P" />
          </el-form-item>
          <el-form-item label="接头 A（选填）">
            <el-input
              v-model="form.connector_a"
              maxlength="100"
              placeholder="例如 SH1.0、22 Pin FPC、IPEX 1代"
            />
          </el-form-item>
          <el-form-item label="接头 B（选填）">
            <el-input
              v-model="form.connector_b"
              maxlength="100"
              placeholder="例如 沾锡线端、15 Pin FPC、母头"
            />
          </el-form-item>
          <el-form-item label="当前库存">
            <el-input-number
              v-model="form.quantity"
              :min="0"
              :max="1000000000"
              :precision="0"
              :disabled="!canOperate"
              controls-position="right"
            />
            <small class="field-help">
              {{ canOperate ? '修改数量会自动产生库存流水' : '当前角色没有库存操作权限' }}
            </small>
          </el-form-item>
          <el-form-item label="备注位置（选填，不代表实际库位）">
            <el-input
              v-model="form.storage_location"
              maxlength="200"
              placeholder="例如 研发仓库；实际库位请通过库存操作分配"
            />
          </el-form-item>
          <el-form-item label="备注（选填）" class="wide-field">
            <el-input
              v-model="form.notes"
              type="textarea"
              :rows="3"
              maxlength="2000"
              show-word-limit
              placeholder="用途、适配设备、颜色或其他识别信息"
            />
          </el-form-item>
        </div>
      </el-form>
      <div class="dialog-tip">
        保存后自动生成线缆编号；线缆类型、端头形式、间距、方向、长度和 Pin
        数均可组合筛选。射频同轴线无需填写间距和 Pin 数。
      </div>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveCable">
          {{ editing ? '保存修改' : '保存并入库' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.cable-page {
  max-width: 1680px;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}
.summary-grid article {
  position: relative;
  min-width: 0;
  padding: 17px 19px;
  border: 1px solid #dfe8f2;
  border-radius: 14px;
  background: linear-gradient(145deg, #fff, #f7faff);
  box-shadow: 0 8px 24px #29496b0a;
  overflow: hidden;
}
.summary-grid article:after {
  position: absolute;
  right: -18px;
  bottom: -30px;
  width: 82px;
  height: 82px;
  border-radius: 50%;
  content: '';
  background: #dbeeff66;
}
.summary-grid span {
  display: block;
  color: #7d8ea2;
  font-size: 11px;
}
.summary-grid b {
  display: inline-block;
  margin-top: 7px;
  color: #234c72;
  font-size: 28px;
  line-height: 1;
}
.summary-grid small {
  margin-left: 5px;
  color: #8c9aaa;
  font-size: 10px;
}
.cable-card {
  overflow: hidden;
}
.filter-grid {
  display: grid;
  grid-template-columns: minmax(260px, 1.7fr) repeat(4, minmax(125px, 0.72fr)) auto;
  gap: 10px;
}
.result-note {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 16px 0 10px;
  padding-top: 13px;
  border-top: 1px solid #edf1f6;
  color: #718398;
  font-size: 12px;
}
.result-note b {
  color: #2d6495;
}
.result-note small {
  color: #9aa6b4;
}
.cable-identity {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 13px;
}
.cable-visual {
  display: flex;
  align-items: center;
  flex: 0 0 88px;
  width: 88px;
  height: 40px;
}
.connector {
  display: grid;
  grid-template-columns: repeat(5, 2px);
  align-content: center;
  justify-content: center;
  width: 19px;
  height: 30px;
  gap: 1px;
  border: 2px solid #7d98b3;
  border-radius: 4px;
  background: #dce9f5;
  box-shadow: 0 3px 0 #9eb4c8;
}
.connector i {
  width: 2px;
  height: 12px;
  border-radius: 1px;
  background: #d49d34;
}
.cable-line {
  position: relative;
  flex: 1;
  height: 13px;
  border-top: 1px solid #9fc1df;
  border-bottom: 1px solid #9fc1df;
  background: repeating-linear-gradient(90deg, #d8eaf8 0 4px, #bdd8ed 4px 5px);
}
.cable-line i {
  position: absolute;
  top: 2px;
  right: 2px;
  bottom: 2px;
  left: 2px;
  border-top: 1px solid #fff9;
}
.cable-visual.reverse .connector-right {
  transform: rotate(180deg);
  background: #ffedcf;
  border-color: #c6964b;
}
.cable-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
}
.cable-copy b {
  max-width: 100%;
  color: #24415f;
  font-size: 13px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.cable-copy span {
  margin-top: 4px;
  color: #8292a5;
  font-size: 10px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.spec-number {
  color: #285b86;
  font-size: 15px;
}
.spec-unit {
  display: block;
  margin-top: 2px;
  color: #94a1af;
  font-size: 9px;
}
.stock-control {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.stock-control b {
  min-width: 30px;
  color: #187457;
  font-size: 19px;
  font-variant-numeric: tabular-nums;
}
.stock-control button {
  display: grid;
  place-items: center;
  width: 25px;
  height: 25px;
  padding: 0;
  border: 1px solid #d5e0eb;
  border-radius: 7px;
  background: #f7fafc;
  color: #4c7194;
  cursor: pointer;
}
.stock-control button:hover:not(:disabled) {
  border-color: #7ba9d3;
  background: #eaf5ff;
  color: #1e6bad;
}
.stock-control button:disabled {
  cursor: not-allowed;
  opacity: 0.38;
}
.reserved-tip {
  display: block;
  margin-top: 3px;
  color: #b17b27;
  font-size: 9px;
}
.pager {
  justify-content: flex-end;
  margin-top: 16px;
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 18px;
}
.wide-field {
  grid-column: 1/-1;
}
.cable-form :deep(.el-select),
.cable-form :deep(.el-input-number),
.direction-options {
  width: 100%;
}
.direction-options :deep(.el-radio-button) {
  flex: 1;
}
.direction-options :deep(.el-radio-button__inner) {
  width: 100%;
}
.field-help {
  display: block;
  width: 100%;
  margin-top: 5px;
  color: #8a99aa;
  font-size: 10px;
  line-height: 1.4;
}
.dialog-tip {
  padding: 10px 12px;
  border-radius: 9px;
  background: #f2f7fc;
  color: #688098;
  font-size: 11px;
}
@media (max-width: 1250px) {
  .filter-grid {
    grid-template-columns: minmax(260px, 2fr) repeat(2, minmax(130px, 1fr));
  }
  .filter-grid > .el-button {
    width: 100%;
  }
}
@media (max-width: 900px) {
  .summary-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .result-note {
    align-items: flex-start;
    flex-direction: column;
    gap: 5px;
  }
}
@media (max-width: 760px) {
  .summary-grid {
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .summary-grid article {
    padding: 14px;
  }
  .summary-grid b {
    font-size: 23px;
  }
  .filter-grid {
    grid-template-columns: 1fr 1fr;
  }
  .filter-grid > .el-input {
    grid-column: 1/-1;
  }
  .form-grid {
    grid-template-columns: 1fr;
  }
  .wide-field {
    grid-column: auto;
  }
  .result-note small {
    line-height: 1.5;
  }
}
@media (max-width: 480px) {
  .summary-grid,
  .filter-grid {
    grid-template-columns: 1fr;
  }
  .filter-grid > .el-input {
    grid-column: auto;
  }
}
.page-actions {
  display: flex;
  align-items: center;
  gap: 9px;
}
.hidden-file-input {
  display: none;
}
.price-value {
  color: #8a6322;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.import-preview {
  min-width: 0;
}
.import-summary {
  display: grid;
  grid-template-columns: minmax(260px, 1.8fr) repeat(4, minmax(110px, 0.6fr));
  gap: 10px;
  margin-bottom: 12px;
}
.import-file-copy,
.import-stat {
  min-width: 0;
  padding: 13px 15px;
  border: 1px solid #dce7f1;
  border-radius: 11px;
  background: #f8fbfe;
}
.import-file-copy {
  display: flex;
  flex-direction: column;
}
.import-file-copy span,
.import-stat span {
  color: #7d8da0;
  font-size: 10px;
}
.import-file-copy b {
  margin-top: 5px;
  color: #294b69;
  font-size: 13px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.import-file-copy small {
  margin-top: 5px;
  color: #8b99a8;
  font-size: 10px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.import-stat b {
  display: inline-block;
  margin: 7px 4px 0 0;
  color: #245b88;
  font-size: 24px;
  line-height: 1;
}
.import-stat small {
  color: #8b99aa;
  font-size: 10px;
}
.import-guidance {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
  padding: 11px 13px;
  border: 1px solid #d9e9f7;
  border-radius: 10px;
  background: #f2f8fd;
}
.import-guidance > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}
.import-guidance b {
  color: #345b7c;
  font-size: 12px;
}
.import-guidance span {
  color: #7c8fa1;
  font-size: 10px;
  line-height: 1.5;
}
.import-table :deep(.el-input-number),
.import-table :deep(.el-select) {
  width: 100%;
}
.import-table :deep(.el-table__cell) {
  padding: 7px 0;
}
.stacked-inputs {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.source-cell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}
.source-cell b {
  color: #385875;
  font-size: 11px;
}
.source-cell span {
  color: #3b7f64;
  font-size: 9px;
}
.source-cell small {
  max-width: 100%;
  color: #8b99a7;
  font-size: 9px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
  cursor: help;
}
.existing-code {
  display: block;
  margin: 5px 0 0 !important;
  color: #8493a2;
  font-size: 9px;
}
.import-check {
  display: flex;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 5px;
}
.import-check small {
  display: block;
  max-width: 100%;
  font-size: 9px;
  line-height: 1.35;
}
.row-error {
  color: #c64d4d;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.row-warning {
  color: #b37a28;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.row-ready {
  color: #6c8a7e;
}
.import-footnote {
  margin-top: 10px;
  color: #8493a2;
  font-size: 10px;
}
.import-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  width: 100%;
}
.import-footer > div:first-child {
  color: #77889a;
  font-size: 11px;
}
.import-footer b {
  color: #26628f;
  font-size: 14px;
}
.import-footer span {
  color: #c26c45;
}
:global(.cable-import-dialog .el-dialog__body) {
  padding-top: 8px;
  overflow: hidden;
}
:global(.cable-import-dialog .el-dialog__footer) {
  padding-top: 10px;
  border-top: 1px solid #edf1f5;
}
@media (max-width: 980px) {
  .page-actions {
    align-items: stretch;
    flex-direction: column;
  }
  .import-summary {
    grid-template-columns: repeat(2, 1fr);
  }
  .import-file-copy {
    grid-column: 1/-1;
  }
  .import-guidance {
    align-items: flex-start;
    flex-direction: column;
  }
  .import-footer {
    align-items: flex-start;
    flex-direction: column;
  }
  .import-footer > div:last-child {
    display: flex;
    justify-content: flex-end;
    width: 100%;
  }
}
.filter-grid {
  grid-template-columns: minmax(250px, 1.65fr) repeat(6, minmax(116px, 0.72fr)) auto;
}
.cable-visual {
  flex-basis: 96px;
  width: 96px;
}
.tinned-tip {
  width: 14px;
  height: 5px;
  border-radius: 0 5px 5px 0;
  background: linear-gradient(90deg, #c99848, #f0ce78);
  box-shadow: inset 0 1px #fff8;
}
.bare-tip {
  width: 14px;
  height: 5px;
  border-radius: 0 4px 4px 0;
  background: #7f9ab3;
  box-shadow: inset 0 1px #dce8f2;
}
.flat-contact {
  width: 17px;
  height: 30px;
  border: 1px solid #c59137;
  border-radius: 3px;
  background: repeating-linear-gradient(90deg, #f1c65f 0 2px, #fff0aa 2px 3px);
  box-shadow: 0 2px 0 #b9a063;
}
.flat-ribbon {
  flex: 1;
  height: 24px;
  border-top: 1px solid #76a6c8;
  border-bottom: 1px solid #76a6c8;
  background: repeating-linear-gradient(90deg, #d8f0ff 0 4px, #9ac7e3 4px 5px);
}
.cable-visual.flat_flex.reverse .flat-contact:last-child {
  transform: rotate(180deg);
  background: #ffd8a2;
}
.micro-contact {
  width: 17px;
  height: 25px;
  border: 2px solid #778da3;
  border-radius: 3px;
  background: repeating-linear-gradient(90deg, #d7b05d 0 2px, #edf2f5 2px 3px);
}
.micro-bundle {
  flex: 1;
  height: 14px;
  border-radius: 7px;
  background: repeating-linear-gradient(90deg, #485f76 0 2px, #7e9bb5 2px 4px);
  box-shadow: inset 0 2px #b7cad9;
}
.rf-head {
  width: 17px;
  height: 17px;
  border: 3px solid #c0a15d;
  border-radius: 50%;
  background: #f7df9c;
  box-shadow: inset 0 0 0 3px #fff;
}
.rf-wire {
  flex: 1;
  height: 6px;
  border-radius: 6px;
  background: #222f3d;
  box-shadow: inset 0 2px #6f8294;
}
.cable-badges {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 6px;
}
.cable-badges small {
  margin: 0;
  padding: 2px 6px;
  border: 1px solid #d8e5ef;
  border-radius: 10px;
  background: #f4f8fb;
  color: #5f7890;
  font-size: 9px;
}
.endpoint-copy {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 14px minmax(0, 1fr);
  align-items: center;
  gap: 4px;
  color: #526d86;
  font-size: 10px;
}
.endpoint-copy span {
  min-width: 0;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.endpoint-copy i {
  color: #9cadba;
  font-style: normal;
  text-align: center;
}
.location-truth {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  line-height: 1.45;
}
.location-warehouse {
  color: #526d86;
}
.actual-location-link {
  color: #216fb6;
  font-weight: 650;
  overflow-wrap: anywhere;
  text-decoration: none;
}
.actual-location-link:hover {
  text-decoration: underline;
}
.location-unallocated {
  color: #a25d24;
  font-weight: 650;
}
.legacy-location-note {
  color: #8293a5;
  font-size: 12px;
  overflow-wrap: anywhere;
}
:deep(.cable-table .focus-row > td.el-table__cell) {
  background: #fff4cf !important;
  transition: background-color 0.25s ease;
}
@media (max-width: 1500px) {
  .filter-grid {
    grid-template-columns: minmax(250px, 2fr) repeat(3, minmax(130px, 1fr));
  }
  .filter-grid > .el-button {
    width: 100%;
  }
}
@media (max-width: 1100px) {
  .filter-grid {
    grid-template-columns: 1fr 1fr;
  }
  .filter-grid > .el-input {
    grid-column: 1/-1;
  }
}
@media (max-width: 760px) {
  .filter-grid {
    grid-template-columns: 1fr 1fr;
  }
  .filter-grid > .el-input {
    grid-column: 1/-1;
  }
}
@media (max-width: 480px) {
  .filter-grid {
    grid-template-columns: 1fr;
  }
  .filter-grid > .el-input {
    grid-column: auto;
  }
}
</style>
