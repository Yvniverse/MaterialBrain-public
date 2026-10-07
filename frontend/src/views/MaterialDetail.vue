<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'
import type { ComponentRelation, Material, Page } from '../types'
import { formatQuantity, formatSignedQuantity } from '../utils/format'
import { formatBusinessText } from '../utils/businessCopy'
import { materialPartNumberText } from '../utils/materialIdentity'
import MaterialEvidencePanel from '../components/evidence/MaterialEvidencePanel.vue'
interface Movement {
  id: number
  movement_no: string
  operation_type: string
  quantity_delta: string
  before_quantity: string
  after_quantity: string
  reason: string
  created_at: string
}
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const material = ref<Material | null>(null)
const movements = ref<Movement[]>([])
const relations = ref<ComponentRelation[]>([])
const deleting = ref(false)
const reviewBusyId = ref<number | null>(null)
const displayTags = computed(() => material.value?.tags || [])
async function loadRelations() {
  relations.value = (
    await api.get<{ items: ComponentRelation[] }>(`/materials/${route.params.id}/relations`)
  ).data.items
}
onMounted(async () => {
  const [materialResponse, movementResponse] = await Promise.all([
    api.get<Material>(`/materials/${route.params.id}`),
    api.get<Page<Movement>>('/stock-movements', {
      params: { material_id: route.params.id, page_size: 50 },
    }),
  ])
  material.value = materialResponse.data
  movements.value = movementResponse.data.items
  await loadRelations()
})
const relationLabels: Record<ComponentRelation['relation_type'], string> = {
  similar_to: '相似器件',
  electrical_compatible: '电气兼容',
  pin_compatible: '引脚兼容',
  same_footprint: '相同封装',
}
function relatedMaterial(relation: ComponentRelation) {
  return relation.source_material.id === material.value?.id
    ? relation.target_material
    : relation.source_material
}
async function validateRelation(relation: ComponentRelation) {
  reviewBusyId.value = relation.id
  try {
    await api.post(`/component-relations/${relation.id}/validate`)
    ElMessage.success('工程关系已验证')
    await loadRelations()
  } finally {
    reviewBusyId.value = null
  }
}
async function rejectRelation(relation: ComponentRelation) {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(
      '请填写拒绝原因，决定将写入审计日志。',
      '拒绝候选关系',
      {
        confirmButtonText: '拒绝',
        cancelButtonText: '取消',
        inputValidator: (value) => Boolean(value.trim()) || '拒绝原因不能为空',
      },
    )
    reason = result.value
  } catch {
    return
  }
  reviewBusyId.value = relation.id
  try {
    await api.post(`/component-relations/${relation.id}/reject`, { reason })
    ElMessage.success('候选关系已拒绝')
    await loadRelations()
  } finally {
    reviewBusyId.value = null
  }
}
async function revokeRelation(relation: ComponentRelation) {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(
      '请填写撤销原因。历史验证记录会保留，但不再作为当前可用知识。',
      '撤销已验证关系',
      {
        confirmButtonText: '撤销',
        cancelButtonText: '取消',
        inputValidator: (value) => Boolean(value.trim()) || '撤销原因不能为空',
      },
    )
    reason = result.value
  } catch {
    return
  }
  reviewBusyId.value = relation.id
  try {
    await api.post(`/component-relations/${relation.id}/revoke`, { reason })
    ElMessage.success('工程关系已撤销')
    await loadRelations()
  } finally {
    reviewBusyId.value = null
  }
}
async function removeMaterial() {
  if (!material.value || deleting.value) return
  const row = material.value
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
  deleting.value = true
  try {
    await api.delete(`/materials/${row.id}`)
    ElMessage.success(`“${label}”已删除`)
    await router.push('/materials')
  } catch {
    // 具体错误由统一请求拦截器显示。
  } finally {
    deleting.value = false
  }
}
</script>
<template>
  <div v-if="material" class="page">
    <div class="page-header">
      <div>
        <div class="code">{{ material.code }}</div>
        <h1 class="page-title">{{ material.name }}</h1>
        <div class="page-subtitle">
          {{ formatBusinessText(material.manufacturer) }} · {{ materialPartNumberText(material) }} ·
          {{ material.package || '未设置封装' }}
        </div>
      </div>
      <div>
        <el-button
          v-if="auth.can('material:manage')"
          type="danger"
          plain
          :loading="deleting"
          @click="removeMaterial"
          >删除物料</el-button
        ><el-button
          v-if="auth.can('material:manage')"
          @click="$router.push(`/materials/${material.id}/edit`)"
          >编辑资料</el-button
        ><el-button
          type="primary"
          @click="$router.push({ path: '/inventory', query: { material_id: material.id } })"
          >库存操作</el-button
        >
      </div>
    </div>
    <section class="stock">
      <div>
        <span>当前库存</span><b>{{ formatQuantity(material.quantity) }}</b
        ><small>{{ material.unit }}</small>
      </div>
      <div>
        <span>项目预留</span><b>{{ formatQuantity(material.reserved_quantity) }}</b
        ><small>{{ material.unit }}</small>
      </div>
      <div>
        <span>可用库存</span
        ><b
          :class="
            Number(material.available_quantity) <= Number(material.safety_stock)
              ? 'danger-number'
              : 'success-number'
          "
          >{{ formatQuantity(material.available_quantity) }}</b
        ><small>{{ material.unit }}</small>
      </div>
      <div>
        <span>安全库存</span><b>{{ formatQuantity(material.safety_stock) }}</b
        ><small>{{ material.unit }}</small>
      </div>
    </section>
    <el-card class="card"
      ><el-tabs
        ><el-tab-pane label="技术资料"
          ><el-descriptions :column="3" border
            ><el-descriptions-item label="规格值">{{
              material.specification || '—'
            }}</el-descriptions-item
            ><el-descriptions-item label="封装">{{ material.package || '—' }}</el-descriptions-item
            ><el-descriptions-item label="Footprint">{{
              material.footprint || '—'
            }}</el-descriptions-item
            ><el-descriptions-item label="单价">¥ {{ material.unit_price }}</el-descriptions-item
            ><el-descriptions-item label="RoHS">{{ material.rohs_status }}</el-descriptions-item
            ><el-descriptions-item label="生命周期">{{
              material.lifecycle_status
            }}</el-descriptions-item
            ><el-descriptions-item label="条形码">{{
              material.barcode || '—'
            }}</el-descriptions-item
            ><el-descriptions-item label="数据手册"
              ><a v-if="material.datasheet_url" :href="material.datasheet_url" target="_blank"
                >打开链接</a
              ><span v-else>—</span></el-descriptions-item
            ><el-descriptions-item label="标签"
              ><el-tag v-for="tag in displayTags" :key="tag" size="small">{{
                tag
              }}</el-tag></el-descriptions-item
            ><el-descriptions-item label="备注" :span="3">{{
              formatBusinessText(material.notes) || '—'
            }}</el-descriptions-item></el-descriptions
          ></el-tab-pane
        ><el-tab-pane label="工程证据"
          ><MaterialEvidencePanel :material-id="material.id" /></el-tab-pane
        ><el-tab-pane label="工程关系"
          ><div class="relation-intro">
            工程关系来自人工审核记录。相似器件不代表可直接替代；只有明确验证且有支持证据的“引脚兼容”记录才可这样表述。
          </div>
          <div v-if="relations.length" class="relation-list">
            <article
              v-for="relation in relations"
              :key="relation.id"
              class="relation-card"
              data-testid="component-relation-card"
            >
              <header>
                <div>
                  <b>{{ relatedMaterial(relation).mpn || relatedMaterial(relation).code }}</b
                  ><span>{{ relatedMaterial(relation).name }}</span>
                </div>
                <el-tag
                  :type="
                    relation.status === 'validated'
                      ? 'success'
                      : relation.status === 'candidate'
                        ? 'warning'
                        : 'info'
                  "
                  >{{
                    relation.status === 'validated'
                      ? '当前已验证'
                      : relation.status === 'revoked'
                        ? '历史已验证，现已撤销'
                        : relation.status === 'rejected'
                          ? '已拒绝'
                          : '待审核'
                  }}</el-tag
                >
              </header>
              <dl>
                <div>
                  <dt>关系</dt>
                  <dd>{{ relationLabels[relation.relation_type] }}</dd>
                </div>
                <div>
                  <dt>证据摘要</dt>
                  <dd>{{ formatBusinessText(relation.evidence_summary) || '尚未提供' }}</dd>
                </div>
                <div v-if="relation.confidence_note">
                  <dt>置信说明</dt>
                  <dd>{{ formatBusinessText(relation.confidence_note) }}</dd>
                </div>
                <div v-if="relation.evidence_citations?.length">
                  <dt>页级引用</dt>
                  <dd>
                    <span
                      v-for="citation in relation.evidence_citations"
                      :key="citation.anchor_id"
                      class="citation-chip"
                      >{{ citation.document_revision }} · p.{{ citation.page }} ·
                      {{ citation.section }}</span
                    >
                  </dd>
                </div>
              </dl>
              <p v-if="relation.relation_type === 'similar_to'" class="scope-warning">
                “相似”不等于已批准替代，也不代表引脚兼容。
              </p>
              <p v-if="relation.unavailable_reasons?.length" class="scope-warning">
                当前不可用：{{ relation.unavailable_reasons.join('；') }}
              </p>
              <p v-if="relation.rejected_reason" class="scope-warning">
                拒绝原因：{{ relation.rejected_reason }}
              </p>
              <p v-if="relation.revoked_reason" class="scope-warning">
                撤销原因：{{ relation.revoked_reason }}
              </p>
              <footer v-if="relation.status === 'candidate' && auth.can('component:validate')">
                <el-button
                  type="success"
                  plain
                  :loading="reviewBusyId === relation.id"
                  @click="validateRelation(relation)"
                  >验证关系</el-button
                ><el-button
                  type="danger"
                  plain
                  :loading="reviewBusyId === relation.id"
                  @click="rejectRelation(relation)"
                  >拒绝</el-button
                >
              </footer>
              <footer v-if="relation.status === 'validated' && auth.can('component:validate')">
                <el-button
                  type="danger"
                  plain
                  :loading="reviewBusyId === relation.id"
                  @click="revokeRelation(relation)"
                  >撤销验证</el-button
                >
              </footer>
            </article>
          </div>
          <el-empty v-else description="尚无工程关系记录" /></el-tab-pane
        ><el-tab-pane label="库存流水"
          ><el-table :data="movements"
            ><el-table-column prop="movement_no" label="流水号" width="200" /><el-table-column
              prop="operation_type"
              label="类型"
              width="130" /><el-table-column label="变化" width="100"
              ><template #default="{ row }">{{
                formatSignedQuantity(row.quantity_delta)
              }}</template></el-table-column
            ><el-table-column label="操作前"
              ><template #default="{ row }">{{
                formatQuantity(row.before_quantity)
              }}</template></el-table-column
            ><el-table-column label="操作后"
              ><template #default="{ row }">{{
                formatQuantity(row.after_quantity)
              }}</template></el-table-column
            ><el-table-column label="原因" min-width="180"
              ><template #default="{ row }">{{
                formatBusinessText(row.reason)
              }}</template></el-table-column
            ><el-table-column prop="created_at" label="时间" width="180" /></el-table></el-tab-pane
        ><el-tab-pane label="附件"
          ><el-empty description="附件管理功能将在此区域提供" /></el-tab-pane></el-tabs
    ></el-card>
  </div>
  <div v-else class="page"><el-skeleton :rows="8" animated /></div>
</template>
<style scoped>
.code {
  font-size: 12px;
  color: #3478c5;
  font-weight: 700;
  letter-spacing: 1px;
  margin-bottom: 5px;
}
.stock {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  background: #112c52;
  color: white;
  border-radius: 14px;
  padding: 24px;
  margin-bottom: 16px;
}
.stock div {
  padding: 0 24px;
  border-right: 1px solid #ffffff20;
}
.stock div:last-child {
  border: 0;
}
.stock span {
  display: block;
  color: #9fb6d3;
  font-size: 13px;
}
.stock b {
  font-size: 30px;
  display: inline-block;
  margin-top: 8px;
}
.stock small {
  margin-left: 6px;
  color: #9fb6d3;
}
.relation-intro {
  margin-bottom: 14px;
  padding: 12px;
  border-radius: 10px;
  background: #fff7e8;
  color: #795a25;
  line-height: 1.6;
}
.relation-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 12px;
}
.relation-card {
  display: grid;
  gap: 11px;
  padding: 15px;
  border: 1px solid #d8e4ed;
  border-radius: 12px;
}
.relation-card header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.relation-card header div {
  display: grid;
  gap: 3px;
}
.relation-card header span {
  color: #6d8295;
  font-size: 13px;
}
.relation-card dl {
  display: grid;
  gap: 8px;
  margin: 0;
}
.relation-card dl div {
  display: grid;
  grid-template-columns: 82px 1fr;
  gap: 8px;
}
.relation-card dt {
  color: #71869a;
}
.relation-card dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.citation-chip {
  display: inline-block;
  margin: 0 8px 5px 0;
  padding: 4px 7px;
  border-radius: 7px;
  background: #eef7fc;
  color: #2d6d97;
  font-size: 12px;
}
.relation-card footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.scope-warning {
  margin: 0;
  color: #8a6327;
  font-size: 13px;
}
@media (max-width: 700px) {
  .stock {
    grid-template-columns: 1fr 1fr;
    gap: 20px;
  }
  .stock div {
    border: 0;
    padding: 0;
  }
  .relation-list {
    grid-template-columns: 1fr;
  }
}
</style>
