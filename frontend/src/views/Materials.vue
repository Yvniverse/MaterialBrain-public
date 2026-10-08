<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type UploadFile } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'
import type { Material, Page } from '../types'
import { formatQuantity } from '../utils/format'
import { formatBusinessText } from '../utils/businessCopy'
import { materialPrimaryIdentity, materialSecondaryIdentity } from '../utils/materialIdentity'

interface Category {
  id: number
  parent_id: number | null
  name: string
}

type ImportRow = Record<string, unknown>
type ImportError = { row: number; message: string }

const importColumnLabels: Record<string, string> = {
  code: '物料编码',
  name: '名称',
  mpn: '商品型号 / MPN',
  manufacturer: '品牌 / 厂家',
  package: '封装规格',
  quantity: '购买数量 / 初始库存',
  unit_price: '商品单价（元）',
  supplier_part_number: '立创商品编号',
  unit: '单位',
}

const auth = useAuthStore()
const loading = ref(false)
const data = ref<Page<Material>>({ items: [], total: 0, page: 1, page_size: 20 })
const filter = reactive({ q: '', low_stock: false, page: 1, page_size: 20 })
const categories = ref<Category[]>([])
const savingCategoryIds = ref<number[]>([])
const deletingMaterialIds = ref<number[]>([])

const importOpen = ref(false)
const selectedImportFile = ref<File | null>(null)
const importRows = ref<ImportRow[]>([])
const importErrors = ref<ImportError[]>([])
const importTotal = ref(0)
const detectedImportFormat = ref('')
const parsingImport = ref(false)
const committingImport = ref(false)
const importPreviewRows = computed(() => importRows.value.slice(0, 100))
const importColumns = Object.keys(importColumnLabels)

const categoryPaths = computed(() => {
  const byId = new Map(categories.value.map((item) => [item.id, item]))
  const paths = new Map<number, string>()
  categories.value.forEach((item) => {
    const names: string[] = []
    const visited = new Set<number>()
    let current: Category | undefined = item
    while (current && !visited.has(current.id)) {
      visited.add(current.id)
      names.unshift(current.name)
      current = current.parent_id ? byId.get(current.parent_id) : undefined
    }
    paths.set(item.id, names.join(' / '))
  })
  return paths
})

function categoryPath(categoryId: number | null) {
  return categoryId ? categoryPaths.value.get(categoryId) || '分类已不存在' : '未分类'
}

function resetImportState() {
  selectedImportFile.value = null
  importRows.value = []
  importErrors.value = []
  importTotal.value = 0
  detectedImportFormat.value = ''
}

function toggleImport() {
  if (importOpen.value) resetImportState()
  importOpen.value = !importOpen.value
}

function chooseImportFile(file: UploadFile) {
  selectedImportFile.value = file.raw || null
  importRows.value = []
  importErrors.value = []
  importTotal.value = 0
  detectedImportFormat.value = ''
}

function removeImportFile() {
  resetImportState()
}

async function previewImport() {
  if (!selectedImportFile.value) return
  parsingImport.value = true
  try {
    const body = new FormData()
    body.append('file', selectedImportFile.value)
    const { data: preview } = await api.post<{
      rows: ImportRow[]
      errors: ImportError[]
      total: number
      detected_format: string
    }>('/imports/materials/preview', body)
    importRows.value = preview.rows
    importErrors.value = preview.errors
    importTotal.value = preview.total
    detectedImportFormat.value = preview.detected_format
    if (preview.errors.length) {
      ElMessage.warning(`文件已解析，但有 ${preview.errors.length} 行需要修正`)
    } else {
      ElMessage.success(`已识别 ${preview.total} 条物料数据`)
    }
  } finally {
    parsingImport.value = false
  }
}

async function commitImport() {
  if (!importRows.value.length || importErrors.value.length) return
  committingImport.value = true
  try {
    const { data: result } = await api.post<{ created: number; skipped: number }>(
      '/imports/materials/commit',
      { rows: importRows.value },
    )
    ElMessage.success(`导入完成：新增 ${result.created} 条，跳过重复 ${result.skipped} 条`)
    resetImportState()
    importOpen.value = false
    filter.page = 1
    await load()
  } finally {
    committingImport.value = false
  }
}

function downloadMaterialTemplate() {
  const headers = [
    '物料编码',
    '物料名称',
    '型号',
    '规格',
    '封装',
    '厂家',
    '供应商料号',
    '单位',
    '单价',
    '初始库存',
    '安全库存',
    '目标库存',
  ]
  const example = [
    'MAT-0001',
    '示例电阻',
    'RC0402FR-0710KL',
    '10kΩ ±1%',
    '0402',
    'Yageo',
    '',
    'pcs',
    '0.01',
    '100',
    '0',
    '0',
  ]
  const content = `\ufeff${headers.join(',')}\r\n${example.join(',')}\r\n`
  const url = URL.createObjectURL(new Blob([content], { type: 'text/csv;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = '物料导入模板.csv'
  anchor.click()
  URL.revokeObjectURL(url)
}

async function changeCategory(row: Material, value: number | undefined) {
  if (savingCategoryIds.value.includes(row.id)) return
  const categoryId = value ?? null
  if (categoryId === row.category_id) return
  savingCategoryIds.value.push(row.id)
  try {
    await api.put(`/materials/${row.id}`, { category_id: categoryId })
    row.category_id = categoryId
    ElMessage.success(`“${row.mpn || row.name}”的分类已更新`)
  } finally {
    savingCategoryIds.value = savingCategoryIds.value.filter((id) => id !== row.id)
  }
}

async function removeMaterial(row: Material) {
  if (deletingMaterialIds.value.includes(row.id)) return
  const label = row.mpn || row.name
  if (Number(row.quantity) !== 0 || Number(row.reserved_quantity) !== 0) {
    ElMessage.warning(`“${label}”仍有库存或预留，请先完成出库或取消预留并清零后再删除`)
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认删除“${label}”？删除后将从物料列表移除，历史库存流水仍会保留。`,
      '删除物料',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  deletingMaterialIds.value.push(row.id)
  try {
    await api.delete(`/materials/${row.id}`)
    ElMessage.success(`“${label}”已删除`)
    if (data.value.items.length === 1 && filter.page > 1) filter.page -= 1
    await load()
  } catch {
    // 具体错误由统一请求拦截器显示。
  } finally {
    deletingMaterialIds.value = deletingMaterialIds.value.filter((id) => id !== row.id)
  }
}

async function load() {
  loading.value = true
  try {
    const response = (await api.get<Page<Material>>('/materials', { params: filter })).data
    data.value = {
      ...response,
      items: response.items.map((item) => ({
        ...item,
        manufacturer: formatBusinessText(item.manufacturer),
      })),
    }
  } finally {
    loading.value = false
  }
}

async function loadCategories() {
  categories.value = (await api.get<Category[]>('/categories')).data
}

onMounted(() => Promise.all([load(), loadCategories()]))
</script>

<template>
  <div class="page materials-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">物料管理</h1>
        <div class="page-subtitle">统一查询型号、技术参数、分类、位置和实时库存</div>
      </div>
      <div class="header-actions">
        <el-button
          v-if="auth.can('import:manage')"
          :type="importOpen ? 'primary' : 'default'"
          :plain="importOpen"
          data-testid="toggle-material-import"
          @click="toggleImport"
        >
          {{ importOpen ? '收起导入' : '导入物料' }}
        </el-button>
        <el-button
          v-if="auth.can('material:manage')"
          type="primary"
          @click="$router.push('/materials/new')"
        >
          新增物料
        </el-button>
      </div>
    </div>

    <el-card v-if="importOpen" class="card import-panel" data-testid="material-import-panel">
      <div class="import-panel-head">
        <div>
          <span>BATCH MATERIAL IMPORT</span>
          <h2>批量导入物料</h2>
          <p>支持立创商城购物车及通用物料表，导入数量会自动生成初始库存流水。</p>
        </div>
        <el-button text @click="toggleImport">关闭</el-button>
      </div>

      <div class="import-onboarding">
        <el-upload
          class="import-uploader"
          drag
          :auto-upload="false"
          accept=".csv,.xlsx,.xls"
          :limit="1"
          @change="chooseImportFile"
          @remove="removeImportFile"
        >
          <div class="upload-symbol">⇧</div>
          <div class="upload-copy">
            <b>拖放立创购物车或通用物料表到这里</b>
            <span>也可以点击选择 CSV、XLSX 或 XLS 文件</span>
          </div>
        </el-upload>

        <aside class="import-notes" aria-label="导入规则">
          <div><span>支持格式</span><b>CSV · XLSX · XLS</b></div>
          <div><span>单次上限</span><b>5,000 行</b></div>
          <div><span>重复物料</span><b>按物料编码自动跳过</b></div>
        </aside>
      </div>

      <div class="import-actions">
        <el-button @click="downloadMaterialTemplate">下载 CSV 模板</el-button>
        <el-button
          type="primary"
          :loading="parsingImport"
          :disabled="!selectedImportFile"
          @click="previewImport"
        >
          解析并预览
        </el-button>
      </div>

      <el-alert
        v-if="importErrors.length"
        :title="`${importErrors.length} 行存在错误：${importErrors[0]?.message}`"
        type="error"
        show-icon
        :closable="false"
      />

      <template v-if="importRows.length">
        <div class="preview-head">
          <div>
            <span>IMPORT PREVIEW</span>
            <b>数据预览</b>
            <small>
              已识别 {{ detectedImportFormat }} · 共 {{ importTotal }} 行
              <template v-if="importTotal > 100">，下方显示前 100 行</template>
            </small>
          </div>
          <el-button
            type="success"
            :loading="committingImport"
            :disabled="importErrors.length > 0"
            data-testid="commit-material-import"
            @click="commitImport"
          >
            确认导入全部 {{ importTotal }} 行
          </el-button>
        </div>
        <div class="import-table-wrap">
          <el-table :data="importPreviewRows" max-height="420" stripe>
            <el-table-column
              v-for="key in importColumns"
              :key="key"
              :prop="key"
              :label="importColumnLabels[key]"
              min-width="145"
              show-overflow-tooltip
            />
          </el-table>
        </div>
      </template>
    </el-card>

    <el-card class="card material-card">
      <div class="toolbar material-toolbar">
        <el-input
          v-model="filter.q"
          :prefix-icon="Search"
          clearable
          placeholder="编码 / 型号 / 名称"
          @keyup.enter="load"
          @clear="load"
        />
        <el-checkbox v-model="filter.low_stock" border @change="load">仅看低库存</el-checkbox>
        <el-button @click="load">查询</el-button>
      </div>
      <el-table v-loading="loading" :data="data.items" height="calc(100vh - 280px)" stripe>
        <el-table-column prop="code" label="物料编码" width="145" fixed />
        <el-table-column label="型号 / 名称" min-width="220">
          <template #default="{ row }">
            <router-link :to="`/materials/${row.id}`" class="material-link">
              <b>{{ materialPrimaryIdentity(row) }}</b>
              <span v-if="materialSecondaryIdentity(row)">{{ materialSecondaryIdentity(row) }}</span>
            </router-link>
          </template>
        </el-table-column>
        <el-table-column label="分类" min-width="200">
          <template #default="{ row }">
            <el-select
              class="category-select"
              :model-value="row.category_id"
              :loading="savingCategoryIds.includes(row.id)"
              :disabled="savingCategoryIds.includes(row.id)"
              filterable
              clearable
              placeholder="未分类"
              @change="changeCategory(row, $event)"
            >
              <el-option
                v-for="item in categories"
                :key="item.id"
                :label="categoryPath(item.id)"
                :value="item.id"
              />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column prop="package" label="封装" width="110" />
        <el-table-column label="厂家" width="150">
          <template #default="{ row }">{{
            formatBusinessText(row.manufacturer) || '—'
          }}</template>
        </el-table-column>
        <el-table-column label="当前库存" width="110" align="right">
          <template #default="{ row }">
            <span class="number">{{ formatQuantity(row.quantity) }} {{ row.unit }}</span>
          </template>
        </el-table-column>
        <el-table-column label="已预留" width="100" align="right">
          <template #default="{ row }">
            <span class="number muted">{{ formatQuantity(row.reserved_quantity) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="可用库存" width="110" align="right">
          <template #default="{ row }">
            <span
              :class="
                Number(row.available_quantity) <= Number(row.safety_stock)
                  ? 'danger-number'
                  : 'success-number'
              "
            >
              {{ formatQuantity(row.available_quantity) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="95">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" effect="light">
              {{ row.is_active ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="185" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="$router.push(`/materials/${row.id}`)">
              详情
            </el-button>
            <template v-if="auth.can('material:manage')">
              <el-button link @click="$router.push(`/materials/${row.id}/edit`)">编辑</el-button>
              <el-button
                link
                type="danger"
                :loading="deletingMaterialIds.includes(row.id)"
                @click="removeMaterial(row)"
              >
                删除
              </el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="filter.page"
        v-model:page-size="filter.page_size"
        :total="data.total"
        layout="total, sizes, prev, pager, next"
        class="pager"
        @change="load"
      />
    </el-card>
  </div>
</template>

<style scoped>
.materials-page {
  max-width: 1680px;
}
.header-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.header-actions .el-button {
  margin: 0;
}
.import-panel {
  margin-bottom: 18px;
  overflow: hidden;
}
.import-panel:deep(.el-card__body) {
  padding: 21px;
}
.import-panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 17px;
}
.import-panel-head > div {
  min-width: 0;
}
.import-panel-head span,
.preview-head > div > span {
  color: #3479c8;
  font-size: 10px;
  font-weight: 800;
  line-height: 16px;
  letter-spacing: 1.4px;
}
.import-panel-head h2 {
  margin: 3px 0 0;
  color: #223b58;
  font-size: 21px;
  line-height: 29px;
}
.import-panel-head p {
  margin: 5px 0 0;
  color: #78899e;
  font-size: 12px;
  line-height: 19px;
}
.import-onboarding {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(280px, 0.65fr);
  align-items: stretch;
  gap: 14px;
}
.import-uploader {
  min-width: 0;
}
.import-uploader:deep(.el-upload),
.import-uploader:deep(.el-upload-dragger) {
  width: 100%;
  height: 100%;
}
.import-uploader:deep(.el-upload-dragger) {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 142px;
  padding: 24px;
  border: 1px dashed #abc5df;
  border-radius: 14px;
  background: linear-gradient(135deg, #f7fbff, #f1f7fd);
}
.upload-symbol {
  display: grid;
  place-items: center;
  flex: 0 0 44px;
  width: 44px;
  height: 44px;
  margin-right: 14px;
  border-radius: 13px;
  background: #e3f0ff;
  color: #2f79c7;
  font-size: 24px;
  font-weight: 700;
}
.upload-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  text-align: left;
}
.upload-copy b {
  color: #324d69;
  font-size: 14px;
  line-height: 21px;
}
.upload-copy span {
  margin-top: 4px;
  color: #8393a5;
  font-size: 11px;
  line-height: 17px;
}
.import-notes {
  display: grid;
  grid-template-columns: 1fr;
  gap: 7px;
  padding: 12px;
  border: 1px solid #e2e9f1;
  border-radius: 14px;
  background: #fafbfd;
}
.import-notes > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 8px;
  border-bottom: 1px solid #edf1f5;
}
.import-notes > div:last-child {
  border-bottom: 0;
}
.import-notes span {
  color: #8897a9;
  font-size: 10px;
  line-height: 16px;
}
.import-notes b {
  color: #3a536e;
  font-size: 11px;
  line-height: 17px;
  text-align: right;
}
.import-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin: 14px 0;
}
.import-actions .el-button {
  margin: 0;
}
.preview-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin: 19px 0 12px;
  padding-top: 17px;
  border-top: 1px solid #e9eef4;
}
.preview-head > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
}
.preview-head b {
  color: #273f5a;
  font-size: 16px;
  line-height: 23px;
}
.preview-head small {
  margin-top: 2px;
  color: #8795a6;
  font-size: 11px;
  line-height: 17px;
}
.import-table-wrap {
  width: 100%;
  overflow: hidden;
  border: 1px solid #e7edf3;
  border-radius: 11px;
}
.material-card {
  min-width: 0;
  overflow: hidden;
}
.material-toolbar .el-input {
  width: 300px;
}
.material-link {
  display: flex;
  flex-direction: column;
  color: #235fa9;
}
.material-link span {
  margin-top: 3px;
  color: #8492a6;
  font-size: 12px;
}
.category-select {
  width: 100%;
}
.pager {
  justify-content: flex-end;
  margin-top: 16px;
}
@media (max-width: 1000px) {
  .import-onboarding {
    grid-template-columns: 1fr;
  }
  .import-notes {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .import-notes > div {
    align-items: flex-start;
    flex-direction: column;
    border-right: 1px solid #edf1f5;
    border-bottom: 0;
  }
  .import-notes > div:last-child {
    border-right: 0;
  }
  .import-notes b {
    text-align: left;
  }
}
@media (max-width: 760px) {
  .header-actions {
    width: 100%;
  }
  .header-actions .el-button {
    flex: 1;
  }
  .import-panel:deep(.el-card__body) {
    padding: 16px;
  }
  .import-uploader:deep(.el-upload-dragger) {
    align-items: center;
    min-height: 170px;
    flex-direction: column;
    padding: 20px;
  }
  .upload-symbol {
    margin: 0 0 11px;
  }
  .upload-copy {
    text-align: center;
  }
  .import-notes {
    grid-template-columns: 1fr;
  }
  .import-notes > div {
    align-items: center;
    flex-direction: row;
    border-right: 0;
    border-bottom: 1px solid #edf1f5;
  }
  .preview-head {
    align-items: stretch;
    flex-direction: column;
  }
  .preview-head .el-button {
    width: 100%;
  }
  .material-toolbar {
    align-items: stretch;
    flex-direction: column;
  }
  .material-toolbar .el-input,
  .material-toolbar .el-button {
    width: 100%;
  }
}
</style>
