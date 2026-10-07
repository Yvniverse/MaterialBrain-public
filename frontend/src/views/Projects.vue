<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import BomProductionTabs from '../components/BomProductionTabs.vue'
import ProductionWorkspace from '../components/ProductionWorkspace.vue'
import type { Material, Page, User } from '../types'
import { formatBusinessText } from '../utils/businessCopy'
import { useAuthStore } from '../stores/auth'
interface Project {
  id: number
  code: string
  name: string
  manager_id: number
  status: string
  start_date: string | null
  end_date: string | null
  notes: string
  members: number[]
  product_revision_id: number | null
  linked_product_revision?: {
    id: number
    product_id: number
    product_code: string
    product_name: string
    revision: string
    status: string
    bom_hash: string | null
  } | null
  execution_domain?: {
    label: string
    build_plan_source: string
    legacy_bom_policy: string
  }
  bom_domain_warning?: {
    has_warning: boolean
    message: string
    reasons: string[]
  } | null
  build_plan?: { id: number; plan_no: string; status: string; build_quantity: number } | null
  bom?: Bom[]
}
interface Bom {
  id: number
  version: string
  material_id: number
  required_quantity: string
  notes: string
}
const items = ref<Project[]>([])
const auth = useAuthStore()
const canManage = computed(() => auth.can('project:manage'))
const users = ref<User[]>([])
const materials = ref<Material[]>([])
const dialog = ref(false)
const detail = ref<Project | null>(null)
const bomDialog = ref(false)
const form = reactive({
  code: '',
  name: '',
  manager_id: null as number | null,
  status: 'planning',
  start_date: null as string | null,
  end_date: null as string | null,
  notes: '',
  members: [] as number[],
})
const bom = reactive({
  material_id: null as number | null,
  required_quantity: 1,
  version: 'V1',
  notes: '',
})
async function load() {
  items.value = (await api.get<Project[]>('/projects')).data.map(presentProject)
  users.value = (await api.get<User[]>('/users').catch(() => ({ data: [] }))).data
  materials.value = auth.can('material:view')
    ? (await api.get<Page<Material>>('/materials', { params: { page_size: 200 } })).data.items
    : []
}
async function save() {
  await api.post('/projects', form)
  ElMessage.success('项目已创建')
  dialog.value = false
  load()
}
async function openDetail(row: Project) {
  detail.value = presentProject((await api.get<Project>(`/projects/${row.id}`)).data)
}

function presentProject(item: Project): Project {
  return {
    ...item,
    notes: formatBusinessText(item.notes),
    bom: item.bom?.map((row) => ({ ...row, notes: formatBusinessText(row.notes) })),
  }
}
async function addBom() {
  if (!detail.value) return
  await api.post(`/projects/${detail.value.id}/bom`, bom)
  bomDialog.value = false
  openDetail(detail.value)
  ElMessage.success('BOM 项已添加')
}
onMounted(load)
</script>
<template>
  <div class="page">
    <BomProductionTabs />
    <ProductionWorkspace />
    <div class="page-header">
      <div>
        <h1 class="page-title">项目 / 生产任务</h1>
        <div class="page-subtitle">项目执行需求、构建计划、物料预留和实际领用保持关联</div>
      </div>
      <el-button v-if="canManage" type="primary" @click="dialog = true">创建项目</el-button>
    </div>
    <div class="project-grid">
      <el-card v-for="p in items" :key="p.id" class="card project" @click="openDetail(p)"
        ><div class="project-top">
          <span>{{ p.code }}</span
          ><el-tag>{{ p.status }}</el-tag>
        </div>
        <h3>{{ p.name }}</h3>
        <small v-if="p.linked_product_revision" class="project-link">
          产品定义：{{ p.linked_product_revision.product_code }} ·
          {{ p.linked_product_revision.revision }}
        </small>
        <p>{{ p.notes || '暂无项目说明' }}</p>
        <div class="project-foot">
          <span>负责人 ID {{ p.manager_id }}</span
          ><span>{{ p.start_date || '未定' }} → {{ p.end_date || '未定' }}</span>
        </div></el-card
      ><el-empty v-if="!items.length" class="project-empty" description="暂无项目" />
    </div>
    <el-drawer v-model="detail" title="项目详情" size="62%"
      ><template v-if="detail"
        ><el-descriptions :column="2" border
          ><el-descriptions-item label="项目编号">{{ detail.code }}</el-descriptions-item
          ><el-descriptions-item label="项目状态">{{ detail.status }}</el-descriptions-item
          ><el-descriptions-item label="项目名称" :span="2">{{ detail.name }}</el-descriptions-item
          ><el-descriptions-item label="产品定义 / 版本 BOM" :span="2">
            <span v-if="detail.linked_product_revision">
              {{ detail.linked_product_revision.product_code }} ·
              {{ detail.linked_product_revision.product_name }} ·
              {{ detail.linked_product_revision.revision }}
            </span>
            <span v-else>未关联 ProductRevision</span>
          </el-descriptions-item>
          ><el-descriptions-item label="计划周期"
            >{{ detail.start_date }} 至 {{ detail.end_date }}</el-descriptions-item
          ><el-descriptions-item label="成员数">{{
            detail.members.length
          }}</el-descriptions-item></el-descriptions
        >
        <el-alert
          v-if="detail.bom_domain_warning?.has_warning"
          :title="detail.bom_domain_warning.message"
          type="warning"
          :description="detail.bom_domain_warning.reasons.join('；')"
          :closable="false"
          class="domain-warning" />
        <el-alert
          v-if="detail.linked_product_revision"
          title="产品版本 BOM 是单台工程设计源；项目执行请通过 BuildPlan。"
          description="下方 Project BomItem 仍保留用于临时、维修或历史需求，不会自动覆盖产品版本 BOM。"
          type="info"
          :closable="false"
          class="domain-warning" />
        <div class="section-head">
          <h3>临时 / 历史项目需求（Project BomItem）</h3>
          <el-button
            v-if="canManage && !detail.product_revision_id"
            type="primary"
            @click="bomDialog = true"
            >添加 BOM 项</el-button
          >
        </div>
        <el-table :data="detail.bom"
          ><el-table-column prop="version" label="版本" /><el-table-column
            prop="material_id"
            label="物料 ID" /><el-table-column
            prop="required_quantity"
            label="需求数量" /><el-table-column prop="notes" label="备注" /></el-table
        ><el-alert
          title="项目预留、取消预留与预留转出库请在“库存操作台”选择本项目执行。"
          type="info"
          :closable="false" /></template></el-drawer
    ><el-dialog v-model="dialog" title="创建项目" width="600px"
      ><el-form label-position="top"
        ><div class="two">
          <el-form-item label="项目编号"><el-input v-model="form.code" /></el-form-item
          ><el-form-item label="项目名称"><el-input v-model="form.name" /></el-form-item
          ><el-form-item label="项目负责人"
            ><el-select v-model="form.manager_id"
              ><el-option
                v-for="u in users"
                :key="u.id"
                :label="u.full_name"
                :value="u.id" /></el-select></el-form-item
          ><el-form-item label="状态"
            ><el-select v-model="form.status"
              ><el-option label="规划中" value="planning" /><el-option
                label="进行中"
                value="active" /><el-option label="暂停" value="paused" /><el-option
                label="已完成"
                value="completed" /></el-select></el-form-item
          ><el-form-item label="开始日期"
            ><el-date-picker v-model="form.start_date" value-format="YYYY-MM-DD" /></el-form-item
          ><el-form-item label="结束日期"
            ><el-date-picker v-model="form.end_date" value-format="YYYY-MM-DD"
          /></el-form-item>
        </div>
        <el-form-item label="项目说明"
          ><el-input v-model="form.notes" type="textarea" /></el-form-item></el-form
      ><template #footer
        ><el-button @click="dialog = false">取消</el-button
        ><el-button
          type="primary"
          :disabled="!form.code || !form.name || !form.manager_id"
          @click="save"
          >创建</el-button
        ></template
      ></el-dialog
    ><el-dialog v-model="bomDialog" title="添加 BOM 项" width="500px"
      ><el-form label-position="top"
        ><el-form-item label="物料"
          ><el-select v-model="bom.material_id" filterable
            ><el-option
              v-for="m in materials"
              :key="m.id"
              :label="`${m.code} · ${m.name}`"
              :value="m.id" /></el-select
        ></el-form-item>
        <div class="two">
          <el-form-item label="需求数量"
            ><el-input-number v-model="bom.required_quantity" :min="0.0001" /></el-form-item
          ><el-form-item label="BOM 版本"
            ><el-input v-model="bom.version"
          /></el-form-item></div></el-form
      ><template #footer
        ><el-button @click="bomDialog = false">取消</el-button
        ><el-button type="primary" @click="addBom">添加</el-button></template
      ></el-dialog
    >
  </div>
</template>
<style scoped>
.project-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}
.project-empty {
  grid-column: 1/-1;
  width: 100%;
  min-height: 360px;
  box-sizing: border-box;
}
.project {
  cursor: pointer;
  transition: transform 0.15s;
}
.project:hover {
  transform: translateY(-2px);
}
.project-top,
.project-foot {
  display: flex;
  justify-content: space-between;
  color: #8190a3;
  font-size: 12px;
}
.project-top span {
  color: #3375c1;
  font-weight: 700;
  letter-spacing: 1px;
}
.project h3 {
  font-size: 18px;
  margin: 18px 0 8px;
}
.project p {
  color: #6e8096;
  min-height: 42px;
}
.project-link {
  display: block;
  margin-top: -2px;
  color: #4c7897;
  font-size: var(--mb-font-secondary);
}
.domain-warning {
  margin-top: 14px;
}
.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 24px;
}
.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
:deep(.el-select),
:deep(.el-date-editor) {
  width: 100%;
}
@media (max-width: 900px) {
  .project-grid {
    grid-template-columns: 1fr 1fr;
  }
}
@media (max-width: 600px) {
  .project-grid {
    grid-template-columns: 1fr;
  }
  .project-empty {
    min-height: 280px;
  }
}
</style>
