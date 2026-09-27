<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Collection, Folder, Plus, Search } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'
import type { Material } from '../types'
import { formatQuantity } from '../utils/format'
import { materialPrimaryIdentity, materialSecondaryIdentity } from '../utils/materialIdentity'

interface Category {
  id: number
  parent_id: number | null
  name: string
  code: string
  sort_order: number
  is_active: boolean
}

interface CategoryNode extends Category {
  children: CategoryNode[]
}

interface MaterialResponse {
  items: Material[]
  total: number
  page: number
  page_size: number
  summary: {
    quantity: string
    reserved_quantity: string
    available_quantity: string
    low_stock_count: number
  }
}

const router = useRouter()
const auth = useAuthStore()
const categories = ref<Category[]>([])
const materials = ref<MaterialResponse>({
  items: [], total: 0, page: 1, page_size: 20,
  summary: { quantity: '0', reserved_quantity: '0', available_quantity: '0', low_stock_count: 0 },
})
const selectedMode = ref<'all' | 'uncategorized' | 'category'>('all')
const selectedCategoryId = ref<number | null>(null)
const includeDescendants = ref(true)
const categorySearch = ref('')
const materialSearch = ref('')
const loading = ref(false)
const page = reactive({ page: 1, page_size: 20 })
const dialog = ref(false)
const editingId = ref<number | null>(null)
const categoryForm = reactive({
  code: '', name: '', parent_id: null as number | null, sort_order: 0, is_active: true,
})

function buildTree(rows: Category[]): CategoryNode[] {
  const nodes = new Map<number, CategoryNode>()
  rows.forEach((row) => nodes.set(row.id, { ...row, children: [] }))
  const roots: CategoryNode[] = []
  nodes.forEach((node) => {
    const parent = node.parent_id ? nodes.get(node.parent_id) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  })
  const sortNodes = (items: CategoryNode[]) => {
    items.sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
    items.forEach((item) => sortNodes(item.children))
  }
  sortNodes(roots)
  return roots
}

function filterTree(nodes: CategoryNode[], keyword: string): CategoryNode[] {
  const term = keyword.trim().toLowerCase()
  if (!term) return nodes
  return nodes.flatMap((node) => {
    const children = filterTree(node.children, term)
    const matched = node.name.toLowerCase().includes(term) || node.code.toLowerCase().includes(term)
    return matched || children.length ? [{ ...node, children }] : []
  })
}

const treeData = computed(() => buildTree(categories.value))
const visibleTree = computed(() => filterTree(treeData.value, categorySearch.value))
const selectedCategory = computed(() => categories.value.find((item) => item.id === selectedCategoryId.value) ?? null)
const selectedTitle = computed(() => {
  if (selectedMode.value === 'all') return '全部器件'
  if (selectedMode.value === 'uncategorized') return '未分类器件'
  return selectedCategory.value?.name ?? '分类器件'
})
const selectedPath = computed(() => {
  if (!selectedCategory.value) return selectedTitle.value
  const names: string[] = []
  let current: Category | undefined = selectedCategory.value
  const visited = new Set<number>()
  while (current && !visited.has(current.id)) {
    visited.add(current.id)
    names.unshift(current.name)
    current = current.parent_id ? categories.value.find((item) => item.id === current?.parent_id) : undefined
  }
  return names.join(' / ')
})
const parentOptions = computed(() => categories.value.filter((item) => item.id !== editingId.value))

async function loadCategories() {
  categories.value = (await api.get<Category[]>('/categories')).data
}

async function loadMaterials() {
  loading.value = true
  try {
    const params: Record<string, unknown> = {
      page: page.page,
      page_size: page.page_size,
      q: materialSearch.value.trim(),
    }
    if (selectedMode.value === 'category' && selectedCategoryId.value) {
      params.category_id = selectedCategoryId.value
      params.include_descendants = includeDescendants.value
    } else if (selectedMode.value === 'uncategorized') {
      params.uncategorized = true
    }
    materials.value = (await api.get<MaterialResponse>('/materials', { params })).data
  } finally {
    loading.value = false
  }
}

function chooseAll() {
  selectedMode.value = 'all'
  selectedCategoryId.value = null
  page.page = 1
  void loadMaterials()
}

function chooseUncategorized() {
  selectedMode.value = 'uncategorized'
  selectedCategoryId.value = null
  page.page = 1
  void loadMaterials()
}

function chooseCategory(node: CategoryNode) {
  selectedMode.value = 'category'
  selectedCategoryId.value = node.id
  page.page = 1
  void loadMaterials()
}

function openCategory(row?: Category, parentId?: number | null) {
  Object.assign(categoryForm, {
    code: row?.code ?? '',
    name: row?.name ?? '',
    parent_id: row?.parent_id ?? parentId ?? null,
    sort_order: row?.sort_order ?? 0,
    is_active: row?.is_active ?? true,
  })
  editingId.value = row?.id ?? null
  dialog.value = true
}

async function saveCategory() {
  if (editingId.value) await api.put(`/categories/${editingId.value}`, categoryForm)
  else await api.post('/categories', categoryForm)
  ElMessage.success(editingId.value ? '分类已更新' : '分类已创建')
  dialog.value = false
  await loadCategories()
}

async function removeSelectedCategory() {
  if (!selectedCategory.value) return
  await ElMessageBox.confirm(
    `确认删除“${selectedCategory.value.name}”？存在下级分类或器件时将不允许删除。`,
    '删除分类',
    { type: 'warning' },
  )
  await api.delete(`/categories/${selectedCategory.value.id}`)
  ElMessage.success('分类已删除')
  selectedMode.value = 'all'
  selectedCategoryId.value = null
  await Promise.all([loadCategories(), loadMaterials()])
}

function createMaterial() {
  const query: Record<string, string> = { return_to: 'categories' }
  if (selectedMode.value === 'category' && selectedCategoryId.value) {
    query.category_id = String(selectedCategoryId.value)
  }
  router.push({ path: '/materials/new', query })
}

function editMaterial(material: Material) {
  router.push({ path: `/materials/${material.id}/edit`, query: { return_to: 'categories' } })
}

function inventoryOperation(material: Material, type: 'inbound' | 'outbound' | 'adjust') {
  router.push({ path: '/inventory', query: { material_id: material.id, type } })
}

onMounted(async () => {
  await Promise.all([loadCategories(), loadMaterials()])
})
</script>

<template>
  <div class="page category-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">分类器件工作台</h1>
        <div class="page-subtitle">按多级分类浏览器件与库存，并直接执行入库、出库、盘点和资料维护</div>
      </div>
      <div class="header-actions">
        <el-button :icon="Plus" @click="openCategory(undefined, null)">新增一级分类</el-button>
        <el-button v-if="auth.can('material:manage')" type="primary" @click="createMaterial">新增器件</el-button>
      </div>
    </div>

    <div class="workspace">
      <el-card class="card category-panel" shadow="never">
        <template #header>
          <div class="panel-title"><div><b>器件分类</b><span>点击分类查看库存</span></div></div>
        </template>
        <el-input v-model="categorySearch" :prefix-icon="Search" clearable placeholder="搜索分类名称或编码" />
        <div class="root-menu">
          <button :class="{ active: selectedMode === 'all' }" @click="chooseAll">
            <el-icon><Collection /></el-icon><span>全部器件</span>
          </button>
          <button :class="{ active: selectedMode === 'uncategorized' }" @click="chooseUncategorized">
            <el-icon><Folder /></el-icon><span>未分类</span>
          </button>
        </div>
        <el-tree
          :data="visibleTree"
          node-key="id"
          :props="{ label: 'name', children: 'children' }"
          :current-node-key="selectedMode === 'category' ? selectedCategoryId : undefined"
          highlight-current
          class="category-tree"
          empty-text="没有匹配的分类"
          @node-click="chooseCategory"
        >
          <template #default="{ data }">
            <div class="tree-node" :class="{ disabled: !data.is_active }">
              <el-icon><Folder /></el-icon>
              <span>{{ data.name }}</span>
            </div>
          </template>
        </el-tree>
        <div v-if="selectedCategory" class="category-actions">
          <el-button size="small" @click="openCategory(undefined, selectedCategory.id)">新增下级</el-button>
          <el-button size="small" @click="openCategory(selectedCategory)">编辑</el-button>
          <el-button size="small" type="danger" plain @click="removeSelectedCategory">删除</el-button>
        </div>
      </el-card>

      <section class="content-panel">
        <div class="content-head">
          <div>
            <div class="eyebrow">{{ selectedPath }}</div>
            <h2>{{ selectedTitle }}</h2>
            <span v-if="selectedMode === 'category' && includeDescendants">包含该分类的全部下级分类</span>
            <span v-else-if="selectedMode === 'category'">仅显示直接归属此分类的器件</span>
            <span v-else>显示当前范围内的器件与实时库存</span>
          </div>
          <el-switch
            v-if="selectedMode === 'category'"
            v-model="includeDescendants"
            active-text="包含下级分类"
            @change="page.page = 1; loadMaterials()"
          />
        </div>

        <div class="stats">
          <div><span>器件种类</span><b>{{ materials.total }}</b><small>种</small></div>
          <div><span>当前库存</span><b>{{ formatQuantity(materials.summary.quantity) }}</b><small>件</small></div>
          <div><span>可用库存</span><b>{{ formatQuantity(materials.summary.available_quantity) }}</b><small>件</small></div>
          <div><span>低库存</span><b :class="{ warning: materials.summary.low_stock_count > 0 }">{{ materials.summary.low_stock_count }}</b><small>项</small></div>
        </div>

        <el-card class="card material-card" shadow="never">
          <div class="material-toolbar">
            <el-input
              v-model="materialSearch"
              :prefix-icon="Search"
              clearable
              placeholder="搜索型号、编码或名称"
              @keyup.enter="page.page = 1; loadMaterials()"
              @clear="page.page = 1; loadMaterials()"
            />
            <el-button @click="page.page = 1; loadMaterials()">查询</el-button>
          </div>
          <el-table v-loading="loading" :data="materials.items" stripe height="calc(100vh - 390px)" empty-text="该分类下暂无器件">
            <el-table-column label="型号 / 名称" min-width="220" fixed>
              <template #default="{ row }">
                <router-link :to="`/materials/${row.id}`" class="material-name">
                  <b>{{ materialPrimaryIdentity(row) }}</b><span v-if="materialSecondaryIdentity(row)">{{ materialSecondaryIdentity(row) }}</span>
                </router-link>
              </template>
            </el-table-column>
            <el-table-column prop="code" label="物料编码" width="130" show-overflow-tooltip />
            <el-table-column prop="manufacturer" label="厂家" min-width="125" show-overflow-tooltip />
            <el-table-column prop="package" label="封装" width="115" show-overflow-tooltip />
            <el-table-column label="当前" width="80" align="right"><template #default="{ row }">{{ formatQuantity(row.quantity) }}</template></el-table-column>
            <el-table-column label="预留" width="75" align="right"><template #default="{ row }">{{ formatQuantity(row.reserved_quantity) }}</template></el-table-column>
            <el-table-column label="可用" width="80" align="right">
              <template #default="{ row }"><b :class="Number(row.available_quantity) <= Number(row.safety_stock) ? 'danger-number' : 'success-number'">{{ formatQuantity(row.available_quantity) }}</b></template>
            </el-table-column>
            <el-table-column label="操作" width="178" fixed="right">
              <template #default="{ row }">
                <div class="row-actions">
                  <template v-if="auth.can('inventory:operate')">
                    <el-button size="small" plain type="success" @click="inventoryOperation(row, 'inbound')">入库</el-button>
                    <el-button size="small" plain type="danger" @click="inventoryOperation(row, 'outbound')">出库</el-button>
                    <el-button size="small" plain @click="inventoryOperation(row, 'adjust')">盘点</el-button>
                  </template>
                  <el-button v-if="auth.can('material:manage')" size="small" plain type="primary" @click="editMaterial(row)">编辑</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-model:current-page="page.page"
            v-model:page-size="page.page_size"
            :total="materials.total"
            layout="total, sizes, prev, pager, next"
            class="pager"
            @change="loadMaterials"
          />
        </el-card>
      </section>
    </div>

    <el-dialog v-model="dialog" :title="editingId ? '编辑分类' : '新增分类'" width="520px">
      <el-form label-position="top">
        <div class="two">
          <el-form-item label="分类编码" required><el-input v-model="categoryForm.code" placeholder="例如 CONN-WIRE" /></el-form-item>
          <el-form-item label="分类名称" required><el-input v-model="categoryForm.name" /></el-form-item>
        </div>
        <el-form-item label="上级分类">
          <el-select v-model="categoryForm.parent_id" clearable filterable placeholder="不选择则作为一级分类">
            <el-option v-for="item in parentOptions" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="排序"><el-input-number v-model="categoryForm.sort_order" :min="0" :precision="0" /></el-form-item>
        <el-switch v-model="categoryForm.is_active" active-text="启用" inactive-text="停用" />
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :disabled="!categoryForm.code || !categoryForm.name" @click="saveCategory">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.category-page{max-width:1540px}.header-actions{display:flex;flex-wrap:wrap;gap:8px}.workspace{display:grid;grid-template-columns:240px minmax(0,1fr);align-items:start;gap:18px;min-width:0}.category-panel{position:sticky;top:88px;min-width:0;overflow:hidden}.category-panel :deep(.el-card__body){padding:16px;overflow:hidden}.panel-title>div{display:flex;flex-direction:column;gap:4px}.panel-title span{color:#8795a8;font-size:11px;line-height:1.45}.root-menu{display:grid;gap:5px;margin:14px 0 8px}.root-menu button{width:100%;display:grid;grid-template-columns:22px minmax(0,1fr);align-items:center;padding:10px;border:0;border-radius:8px;background:transparent;color:#465b74;text-align:left;cursor:pointer}.root-menu button span{overflow-wrap:anywhere}.root-menu button:hover,.root-menu button.active{background:#eaf3ff;color:#246dbb}.category-tree{max-height:min(56vh,520px);overflow:auto;background:transparent}.category-tree :deep(.el-tree-node__content){height:auto;min-height:40px;padding-top:5px;padding-bottom:5px;border-radius:8px;margin:2px 0}.tree-node{display:grid;grid-template-columns:20px minmax(0,1fr);align-items:center;width:100%;min-width:0;padding-right:8px;gap:5px}.tree-node span{min-width:0;overflow-wrap:anywhere;white-space:normal;line-height:1.35}.tree-node.disabled{opacity:.5}.category-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px;padding-top:14px;border-top:1px solid #edf1f5}.category-actions .el-button{margin:0}.content-panel{min-width:0}.content-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:14px}.content-head>div{min-width:0}.content-head h2{margin:4px 0;color:#213752;font-size:24px;overflow-wrap:anywhere}.content-head span,.eyebrow{color:#8291a4;font-size:12px;line-height:1.5;overflow-wrap:anywhere}.content-head .el-switch{flex:0 0 auto}.eyebrow{color:#3976bb}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:14px}.stats>div{min-width:0;padding:16px 18px;border:1px solid #e5ebf3;border-radius:12px;background:#fff}.stats span{display:block;color:#7e8da1;font-size:12px}.stats b{display:inline-block;max-width:100%;margin-top:8px;color:#263d59;font-size:clamp(20px,2vw,24px);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.stats small{margin-left:5px;color:#94a0b1}.stats .warning{color:#d2534d}.material-card{min-width:0;overflow:hidden}.material-card :deep(.el-card__body){min-width:0;padding:16px;overflow:hidden}.material-card :deep(.el-table){width:100%;min-width:0}.material-toolbar{display:flex;gap:8px;margin-bottom:14px}.material-toolbar .el-input{min-width:0;max-width:360px}.material-name{display:flex;flex-direction:column;min-width:0;padding:3px 0;color:#2363ad;line-height:1.35}.material-name b,.material-name span{white-space:normal;overflow-wrap:anywhere}.material-name span{margin-top:3px;color:#8492a6;font-size:12px}.row-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.row-actions .el-button{width:100%;margin:0;padding-left:7px;padding-right:7px}.pager{justify-content:flex-end;margin-top:14px}.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}:deep(.el-dialog .el-select),:deep(.el-dialog .el-input-number){width:100%}@media(max-width:1050px){.workspace{grid-template-columns:200px minmax(0,1fr)}.stats{grid-template-columns:repeat(2,1fr)}}@media(max-width:760px){.workspace{grid-template-columns:1fr}.category-panel{position:static}.category-tree{max-height:320px}.content-head{align-items:flex-start;flex-direction:column}.stats{grid-template-columns:repeat(2,1fr)}.header-actions{width:100%}.header-actions .el-button{flex:1}.material-toolbar{align-items:stretch;flex-direction:column}.material-toolbar .el-input,.material-toolbar .el-button{width:100%;max-width:none}.two{grid-template-columns:1fr}}
</style>
