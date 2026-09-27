<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api/client'
import WarehouseRouteMap, {
  type WarehouseRouteView,
  type WarehouseMapView,
} from './WarehouseRouteMap.vue'

interface WarehouseMapSummary {
  id: number
  warehouse_location_id: number
  code: string
  name: string
  version: string
  status: 'draft' | 'active' | 'archived'
  calibration_status: 'demo_synthetic' | 'measured' | 'verified'
  coordinate_unit: string
  width_m: string | number
  height_m: string | number
  default_start_node_code: string
  default_end_node_code: string
  graph_hash: string
  geometry_note: string
  verified_by_id: number | null
  verified_at: string | null
}

interface WarehouseMapDetail extends WarehouseMapSummary, Omit<WarehouseMapView, 'version'> {}
type WarehouseMapEditor = Omit<WarehouseMapDetail, 'width_m' | 'height_m'> & {
  width_m: number
  height_m: number
}

const props = defineProps<{
  warehouseId: number
  canManage: boolean
}>()

const loading = ref(false)
const saving = ref(false)
const maps = ref<WarehouseMapSummary[]>([])
const selectedMapId = ref<number | null>(null)
const selectedMap = ref<WarehouseMapDetail | null>(null)
const editing = ref(false)
const editor = ref<WarehouseMapEditor | null>(null)
const preview = ref<WarehouseRouteView | null>(null)
const previewLocationIds = ref<number[]>([])
const previewOptions = ref<Array<{ id: number; code: string }>>([])
async function previewRoute() {
  if (!selectedMapId.value || !previewLocationIds.value.length) return
  preview.value = (
    await api.post<WarehouseRouteView>(`/warehouse-maps/${selectedMapId.value}/route-preview`, {
      location_ids: previewLocationIds.value,
    })
  ).data
}

const selectedSummary = computed(
  () => maps.value.find((item) => item.id === selectedMapId.value) ?? null,
)
const canEditSelected = computed(
  () =>
    props.canManage &&
    selectedSummary.value?.status === 'draft' &&
    selectedMap.value?.id === selectedMapId.value,
)
let detailRequest = 0

function calibrationLabel(value: WarehouseMapSummary['calibration_status']) {
  if (value === 'verified') return '已验证'
  if (value === 'measured') return '已测量 · 待验证'
  return '示例数据 · 非实测'
}

function statusLabel(value: WarehouseMapSummary['status']) {
  if (value === 'active') return '当前启用'
  if (value === 'archived') return '历史归档'
  return '草稿'
}

async function loadMaps(preferId?: number) {
  loading.value = true
  try {
    const { data } = await api.get<WarehouseMapSummary[]>('/warehouse-maps', {
      params: { warehouse_id: props.warehouseId },
    })
    maps.value = data
    const preferred = preferId ?? selectedMapId.value
    selectedMapId.value =
      (preferred && data.some((item) => item.id === preferred) ? preferred : null) ??
      data.find((item) => item.status === 'active')?.id ??
      data[0]?.id ??
      null
    await loadSelected()
  } finally {
    loading.value = false
  }
}

async function loadSelected() {
  const request = ++detailRequest
  const mapId = selectedMapId.value
  selectedMap.value = null
  editor.value = null
  preview.value = null
  previewLocationIds.value = []
  if (!mapId) return
  const { data } = await api.get<WarehouseMapDetail>(`/warehouse-maps/${mapId}`)
  if (request !== detailRequest || selectedMapId.value !== mapId) return
  const locations = (await api.get<Array<{ id: number; code: string }>>('/locations')).data
  if (request !== detailRequest || selectedMapId.value !== mapId) return
  selectedMap.value = data
  previewOptions.value = locations.filter((l) =>
    data.organizer_bindings.some((b) => b.location_code === l.code),
  )
  if (!editing.value) editor.value = null
}

function startEditing() {
  if (!selectedMap.value || !canEditSelected.value) return
  const copy = JSON.parse(JSON.stringify(selectedMap.value)) as WarehouseMapDetail
  editor.value = { ...copy, width_m: Number(copy.width_m), height_m: Number(copy.height_m) }
  editing.value = true
}

function cancelEditing() {
  editing.value = false
  editor.value = null
}

function addNode() {
  if (!editor.value) return
  const index = editor.value.nodes.length + 1
  editor.value.nodes.push({
    code: `NODE-${String(index).padStart(2, '0')}`,
    node_type: 'aisle',
    x_m: 1,
    y_m: 1,
    label: '新节点',
  })
}

function addEdge() {
  if (!editor.value || editor.value.nodes.length < 2) return
  const index = editor.value.edges.length + 1
  editor.value.edges.push({
    code: `EDGE-${String(index).padStart(2, '0')}`,
    from_node: editor.value.nodes[0].code,
    to_node: editor.value.nodes[1].code,
    distance_m: 1,
    bidirectional: true,
    enabled: true,
  })
}

function removeNode(index: number) {
  if (!editor.value) return
  const code = editor.value.nodes[index]?.code
  if (!code) return
  if (
    editor.value.edges.some((item) => item.from_node === code || item.to_node === code) ||
    editor.value.organizer_bindings.some((item) => item.pick_node_code === code)
  ) {
    ElMessage.warning('该节点仍被路线边或库位绑定引用，请先解除引用。')
    return
  }
  editor.value.nodes.splice(index, 1)
}

function removeEdge(index: number) {
  editor.value?.edges.splice(index, 1)
}

async function saveDraft() {
  if (!editor.value || editor.value.id !== selectedMapId.value || !canEditSelected.value) return
  saving.value = true
  try {
    const payload = {
      name: editor.value.name,
      width_m: Number(editor.value.width_m),
      height_m: Number(editor.value.height_m),
      default_start_node: editor.value.default_start_node_code,
      default_end_node: editor.value.default_end_node_code || null,
      geometry_note: editor.value.geometry_note,
      nodes: editor.value.nodes.map((item) => ({ ...item, is_active: true })),
      edges: editor.value.edges,
      organizer_bindings: editor.value.organizer_bindings,
    }
    await api.put(`/warehouse-maps/${selectedMapId.value}/definition`, payload)
    editing.value = false
    editor.value = null
    await loadMaps(selectedMapId.value)
    ElMessage.success('地图草稿已保存；几何变更需要重新测量/验证后才能生产启用。')
  } finally {
    saving.value = false
  }
}

async function cloneDraft() {
  if (!selectedMap.value) return
  const { value: version } = await ElMessageBox.prompt('输入新地图版本，例如 2.0.0', '复制为草稿', {
    inputValue: `${Number.parseInt(selectedMap.value.version, 10) + 1 || 2}.0.0`,
    confirmButtonText: '下一步',
    cancelButtonText: '取消',
  })
  const safeVersion = version.trim()
  if (!safeVersion) return
  const code = `${selectedMap.value.code.split('-V')[0]}-V${safeVersion.replace(/[^0-9A-Za-z]+/g, '-')}`
  const { data } = await api.post<WarehouseMapSummary>(
    `/warehouse-maps/${selectedMap.value.id}/clone-draft`,
    {
      code,
      version: safeVersion,
      name: `${selectedMap.value.name.replace(/ v\d+(?:\.\d+)*/i, '')} ${safeVersion} 草稿`,
    },
  )
  await loadMaps(data.id)
  startEditing()
  ElMessage.success('已创建不可影响当前生产地图的新草稿版本。')
}

async function validateSelected() {
  if (!selectedMapId.value) return
  const { data } = await api.post(`/warehouse-maps/${selectedMapId.value}/validate`)
  ElMessage.success(
    `地图验证通过：${data.node_count} 节点 / ${data.edge_count} 边 / ${data.binding_count} 个设备绑定`,
  )
}

async function setCalibration(status: 'measured' | 'verified') {
  if (!selectedMapId.value) return
  const copy =
    status === 'verified'
      ? '确认现场尺寸、通道连接、设备位置和取料面均已复核？验证后仍需单独执行“启用”。'
      : '确认已经根据现场测量更新尺寸和坐标？'
  await ElMessageBox.confirm(copy, status === 'verified' ? '验证地图' : '标记已测量', {
    type: 'warning',
  })
  await api.put(`/warehouse-maps/${selectedMapId.value}/calibration`, {
    calibration_status: status,
    geometry_note: selectedMap.value?.geometry_note || '',
  })
  await loadMaps(selectedMapId.value)
}

async function activateSelected() {
  if (!selectedMapId.value) return
  await ElMessageBox.confirm(
    '启用后，旧 active 地图会归档，新建 PickTask 将快照本地图 graph hash。历史 PickTask 仍保留旧路线快照。确认启用？',
    '启用生产路线地图',
    { type: 'warning', confirmButtonText: '确认启用' },
  )
  await api.post(`/warehouse-maps/${selectedMapId.value}/activate`, {
    confirm_verified_geometry: true,
  })
  await loadMaps(selectedMapId.value)
  ElMessage.success('新的 verified 地图已启用；历史任务未被改写。')
}

async function deleteDraft() {
  if (!selectedMapId.value || selectedSummary.value?.status !== 'draft') return
  await ElMessageBox.confirm(
    '仅删除当前草稿，不影响 active/archived 地图。确认？',
    '删除地图草稿',
    {
      type: 'warning',
    },
  )
  await api.delete(`/warehouse-maps/${selectedMapId.value}`)
  selectedMapId.value = null
  await loadMaps()
}

watch(
  () => props.warehouseId,
  () => loadMaps(),
)
watch(selectedMapId, () => {
  editing.value = false
  editor.value = null
  loadSelected()
})
onMounted(() => loadMaps())
</script>

<template>
  <section class="warehouse-map-admin" v-loading="loading" data-testid="warehouse-map-admin">
    <aside class="version-panel">
      <div class="panel-title">
        <div>
          <span>地图版本</span>
          <b>{{ maps.length }}</b>
        </div>
        <el-button size="small" @click="loadMaps()">刷新</el-button>
      </div>
      <button
        v-for="item in maps"
        :key="item.id"
        type="button"
        class="version-card"
        :class="{ active: selectedMapId === item.id }"
        @click="selectedMapId = item.id"
      >
        <span>{{ item.code }}</span>
        <b>{{ item.version }} · {{ statusLabel(item.status) }}</b>
        <small>{{ calibrationLabel(item.calibration_status) }}</small>
        <code>{{ item.graph_hash.slice(0, 10) }}</code>
      </button>
      <el-empty v-if="!maps.length" description="当前仓库还没有路线地图" />
    </aside>

    <main v-if="selectedMap" class="map-workspace">
      <header class="map-toolbar">
        <div>
          <span>{{ selectedMap.code }} · {{ selectedMap.version }}</span>
          <h2>{{ selectedMap.name }}</h2>
          <small>
            {{ statusLabel(selectedMap.status) }} ·
            {{ calibrationLabel(selectedMap.calibration_status) }} · Graph
            {{ selectedMap.graph_hash.slice(0, 12) }}
          </small>
        </div>
        <div class="toolbar-actions">
          <el-button v-if="canManage" @click="validateSelected">验证连通性</el-button>
          <el-button v-if="canManage" @click="cloneDraft">复制为新草稿</el-button>
          <el-button v-if="canEditSelected && !editing" type="primary" @click="startEditing">
            编辑草稿
          </el-button>
          <el-button
            v-if="canEditSelected && selectedMap.calibration_status === 'demo_synthetic'"
            @click="setCalibration('measured')"
          >
            标记已测量
          </el-button>
          <el-button
            v-if="canEditSelected && selectedMap.calibration_status === 'measured'"
            type="warning"
            @click="setCalibration('verified')"
          >
            现场复核通过
          </el-button>
          <el-button
            v-if="canEditSelected && selectedMap.calibration_status === 'verified'"
            type="success"
            @click="activateSelected"
          >
            启用生产地图
          </el-button>
          <el-button v-if="canEditSelected" type="danger" plain @click="deleteDraft"
            >删除草稿</el-button
          >
        </div>
      </header>

      <el-alert
        v-if="selectedMap.calibration_status === 'demo_synthetic'"
        title="当前是示例合成几何，只用于功能演示。生产使用前必须按现场实测坐标修改，并完成 measured → verified → active。"
        type="warning"
        :closable="false"
        show-icon
      />

      <template v-if="editing && editor">
        <div class="editor-toolbar">
          <el-input v-model="editor.name" placeholder="地图名称" />
          <el-input-number v-model="editor.width_m" :min="0.1" :step="0.1" />
          <span>m ×</span>
          <el-input-number v-model="editor.height_m" :min="0.1" :step="0.1" />
          <span>m</span>
          <el-select v-model="editor.default_start_node_code" placeholder="起点">
            <el-option
              v-for="node in editor.nodes"
              :key="node.code"
              :label="node.code"
              :value="node.code"
            />
          </el-select>
          <el-button @click="cancelEditing">取消</el-button>
          <el-button type="primary" :loading="saving" @click="saveDraft">保存草稿</el-button>
        </div>

        <div class="editor-grid">
          <section>
            <div class="section-title">
              <b>通行节点</b><el-button size="small" @click="addNode">新增节点</el-button>
            </div>
            <el-table :data="editor.nodes" size="small" max-height="310">
              <el-table-column label="Code" min-width="120"
                ><template #default="{ row }"><el-input v-model="row.code" /></template
              ></el-table-column>
              <el-table-column label="类型" min-width="110"
                ><template #default="{ row }"
                  ><el-select v-model="row.node_type"
                    ><el-option
                      v-for="kind in ['packing', 'entrance', 'aisle', 'intersection', 'pick_face']"
                      :key="kind"
                      :label="kind"
                      :value="kind" /></el-select></template
              ></el-table-column>
              <el-table-column label="X(m)" width="105"
                ><template #default="{ row }"
                  ><el-input-number v-model="row.x_m" :step="0.1" /></template
              ></el-table-column>
              <el-table-column label="Y(m)" width="105"
                ><template #default="{ row }"
                  ><el-input-number v-model="row.y_m" :step="0.1" /></template
              ></el-table-column>
              <el-table-column label="说明" min-width="120"
                ><template #default="{ row }"><el-input v-model="row.label" /></template
              ></el-table-column>
              <el-table-column width="70"
                ><template #default="{ $index }"
                  ><el-button link type="danger" @click="removeNode($index)"
                    >删</el-button
                  ></template
                ></el-table-column
              >
            </el-table>
          </section>

          <section>
            <div class="section-title">
              <b>可通行边</b><el-button size="small" @click="addEdge">新增边</el-button>
            </div>
            <el-table :data="editor.edges" size="small" max-height="310">
              <el-table-column label="Code" min-width="120"
                ><template #default="{ row }"><el-input v-model="row.code" /></template
              ></el-table-column>
              <el-table-column label="From" min-width="120"
                ><template #default="{ row }"
                  ><el-select v-model="row.from_node"
                    ><el-option
                      v-for="node in editor.nodes"
                      :key="node.code"
                      :label="node.code"
                      :value="node.code" /></el-select></template
              ></el-table-column>
              <el-table-column label="To" min-width="120"
                ><template #default="{ row }"
                  ><el-select v-model="row.to_node"
                    ><el-option
                      v-for="node in editor.nodes"
                      :key="node.code"
                      :label="node.code"
                      :value="node.code" /></el-select></template
              ></el-table-column>
              <el-table-column label="距离(m)" width="120"
                ><template #default="{ row }"
                  ><el-input-number v-model="row.distance_m" :min="0.001" :step="0.1" /></template
              ></el-table-column>
              <el-table-column label="启用" width="75"
                ><template #default="{ row }"><el-switch v-model="row.enabled" /></template
              ></el-table-column>
              <el-table-column width="70"
                ><template #default="{ $index }"
                  ><el-button link type="danger" @click="removeEdge($index)"
                    >删</el-button
                  ></template
                ></el-table-column
              >
            </el-table>
          </section>
        </div>

        <section class="binding-editor">
          <div class="section-title">
            <b>设备/货架几何与取料面绑定</b><small>子抽屉/格口自动继承所属设备取料面。</small>
          </div>
          <el-table :data="editor.organizer_bindings" size="small" max-height="340">
            <el-table-column prop="location_code" label="设备" min-width="150" />
            <el-table-column label="取料面" min-width="150"
              ><template #default="{ row }"
                ><el-select v-model="row.pick_node_code"
                  ><el-option
                    v-for="node in editor.nodes.filter((item) => item.node_type === 'pick_face')"
                    :key="node.code"
                    :label="node.code"
                    :value="node.code" /></el-select></template
            ></el-table-column>
            <el-table-column label="X" width="100"
              ><template #default="{ row }"
                ><el-input-number v-model="row.x_m" :step="0.1" /></template
            ></el-table-column>
            <el-table-column label="Y" width="100"
              ><template #default="{ row }"
                ><el-input-number v-model="row.y_m" :step="0.1" /></template
            ></el-table-column>
            <el-table-column label="宽" width="100"
              ><template #default="{ row }"
                ><el-input-number v-model="row.width_m" :min="0.1" :step="0.1" /></template
            ></el-table-column>
            <el-table-column label="深" width="100"
              ><template #default="{ row }"
                ><el-input-number v-model="row.depth_m" :min="0.1" :step="0.1" /></template
            ></el-table-column>
            <el-table-column label="朝向" width="110"
              ><template #default="{ row }"
                ><el-select v-model="row.facing"
                  ><el-option label="东" value="east" /><el-option
                    label="西"
                    value="west" /><el-option label="南" value="south" /><el-option
                    label="北"
                    value="north" /></el-select></template
            ></el-table-column>
          </el-table>
        </section>

        <el-input
          v-model="editor.geometry_note"
          type="textarea"
          :rows="3"
          placeholder="记录测量方法、基准点、日期、现场偏差和验证说明"
        />
        <WarehouseRouteMap :map="editor" />
      </template>

      <template v-else>
        <div class="toolbar-actions">
          <el-select
            v-model="previewLocationIds"
            multiple
            placeholder="选择取料设备预览路线"
            style="min-width: 300px"
            ><el-option v-for="l in previewOptions" :key="l.id" :label="l.code" :value="l.id"
          /></el-select>
          <el-button @click="previewRoute">预览配置图路线</el-button>
        </div>
        <WarehouseRouteMap :map="selectedMap" :route="preview" />
      </template>
    </main>

    <el-empty v-else description="请选择或创建仓库地图版本" class="empty-map" />
  </section>
</template>

<style scoped>
.warehouse-map-admin {
  min-height: 620px;
  display: grid;
  grid-template-columns: 230px minmax(0, 1fr);
  gap: 16px;
}
.version-panel {
  border-right: 1px solid #e1e9ef;
  padding-right: 14px;
}
.panel-title,
.map-toolbar,
.editor-toolbar,
.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.panel-title div,
.map-toolbar > div:first-child {
  display: grid;
  gap: 3px;
}
.panel-title span,
.map-toolbar span,
.map-toolbar small,
.version-card small {
  color: #6c8191;
  font-size: 12px;
}
.version-card {
  width: 100%;
  margin-top: 8px;
  padding: 10px;
  display: grid;
  gap: 4px;
  text-align: left;
  border: 1px solid #dbe5ec;
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
}
.version-card.active {
  border-color: #4c88b3;
  box-shadow: 0 0 0 2px #e6f1f8;
}
.version-card code {
  color: #7a91a1;
  font-size: 10px;
}
.map-workspace {
  min-width: 0;
  display: grid;
  align-content: start;
  gap: 14px;
}
.map-toolbar {
  align-items: flex-start;
}
.map-toolbar h2 {
  margin: 0;
}
.toolbar-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 7px;
}
.editor-toolbar {
  justify-content: flex-start;
  padding: 10px;
  background: #f6f9fb;
  border-radius: 10px;
}
.editor-toolbar .el-input {
  max-width: 300px;
}
.editor-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.editor-grid section,
.binding-editor {
  border: 1px solid #e1e9ef;
  border-radius: 10px;
  padding: 10px;
}
.section-title {
  margin-bottom: 8px;
}
.section-title small {
  color: #718698;
}
.empty-map {
  grid-column: 2;
}
@media (max-width: 1050px) {
  .warehouse-map-admin,
  .editor-grid {
    grid-template-columns: 1fr;
  }
  .version-panel {
    border-right: 0;
    border-bottom: 1px solid #e1e9ef;
    padding: 0 0 12px;
  }
  .empty-map {
    grid-column: 1;
  }
}
</style>
