<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import BomProductionTabs from '../components/BomProductionTabs.vue'
import type {
  BuildReadinessEntity,
  ProductBomEntity,
  ProductBomAlternate,
  ProductRevisionSummary,
  ProductSummary,
} from '../types'
import { formatQuantity } from '../utils/format'
import { formatBusinessText } from '../utils/businessCopy'
import BuildReadinessCard from '../components/agent/BuildReadinessCard.vue'
import { useAuthStore } from '../stores/auth'

interface LinkedProject {
  id: number
  code: string
  name: string
  product_revision_id: number | null
}

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const products = ref<ProductSummary[]>([])
const selected = ref<ProductSummary | null>(null)
const selectedRevisionId = ref<number | null>(null)
const bom = ref<ProductBomEntity | null>(null)
const alternatesByBomItem = ref<Record<number, ProductBomAlternate[]>>({})
const alternateReviewBusyId = ref<number | null>(null)
const projects = ref<LinkedProject[]>([])
const buildQuantity = ref(1)
const projectId = ref<number | null>(null)
const readiness = ref<BuildReadinessEntity | null>(null)
const loading = ref(false)
const analyzing = ref(false)

const revisions = computed(() => selected.value?.revisions || [])
const selectedRevision = computed(
  () => revisions.value.find((revision) => revision.id === selectedRevisionId.value) || null,
)
const matchingProjects = computed(() =>
  projects.value.filter((project) => project.product_revision_id === selectedRevisionId.value),
)

function revisionLabel(revision: ProductRevisionSummary) {
  const state =
    revision.status === 'released' ? '已发布' : revision.status === 'draft' ? '草稿' : '已停用'
  return `${revision.revision} · ${state}${revision.is_default ? ' · 默认' : ''}`
}

async function loadBom() {
  readiness.value = null
  projectId.value = null
  if (!selectedRevisionId.value) {
    bom.value = null
    alternatesByBomItem.value = {}
    return
  }
  bom.value = (
    await api.get<ProductBomEntity>(`/product-revisions/${selectedRevisionId.value}/bom`)
  ).data
  const rows = await Promise.all(
    bom.value.items
      .filter((item) => item.id)
      .map(
        async (item) =>
          [
            item.id as number,
            (
              await api.get<{ items: ProductBomAlternate[] }>(
                `/product-bom-items/${item.id}/alternates`,
              )
            ).data.items,
          ] as const,
      ),
  )
  alternatesByBomItem.value = Object.fromEntries(rows)
}

async function reloadAlternates(itemId: number) {
  alternatesByBomItem.value = {
    ...alternatesByBomItem.value,
    [itemId]: (
      await api.get<{ items: ProductBomAlternate[] }>(`/product-bom-items/${itemId}/alternates`)
    ).data.items,
  }
}

async function approveAlternate(alternate: ProductBomAlternate) {
  alternateReviewBusyId.value = alternate.id
  try {
    await api.post(`/product-bom-alternates/${alternate.id}/approve`)
    ElMessage.success('此产品版本的 BOM 位备选已批准')
    await reloadAlternates(alternate.product_bom_item_id)
  } finally {
    alternateReviewBusyId.value = null
  }
}

async function rejectAlternate(alternate: ProductBomAlternate) {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(
      '请填写拒绝原因，决定将写入审计日志。',
      '拒绝候选备选料',
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
  alternateReviewBusyId.value = alternate.id
  try {
    await api.post(`/product-bom-alternates/${alternate.id}/reject`, { reason })
    ElMessage.success('候选备选料已拒绝')
    await reloadAlternates(alternate.product_bom_item_id)
  } finally {
    alternateReviewBusyId.value = null
  }
}

async function revokeAlternate(alternate: ProductBomAlternate) {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(
      '请填写撤销原因。历史批准会保留，但该备选不再是当前可用批准。',
      '撤销产品备选批准',
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
  alternateReviewBusyId.value = alternate.id
  try {
    await api.post(`/product-bom-alternates/${alternate.id}/revoke`, { reason })
    ElMessage.success('产品备选批准已撤销')
    await reloadAlternates(alternate.product_bom_item_id)
  } finally {
    alternateReviewBusyId.value = null
  }
}

async function openProduct(product: ProductSummary, revisionId?: number | null) {
  loading.value = true
  try {
    selected.value = presentProduct((await api.get<ProductSummary>(`/products/${product.id}`)).data)
    const requested = selected.value.revisions?.find((item) => item.id === revisionId)
    const fallback =
      selected.value.revisions?.find((item) => item.is_default && item.status === 'released') ||
      selected.value.revisions?.find((item) => item.status === 'released') ||
      selected.value.revisions?.[0]
    selectedRevisionId.value = requested?.id || fallback?.id || null
    await loadBom()
    await router.replace({
      path: '/products',
      query: {
        product: String(product.id),
        ...(selectedRevisionId.value ? { revision: String(selectedRevisionId.value) } : {}),
      },
    })
  } finally {
    loading.value = false
  }
}

async function chooseRevision(revisionId: number) {
  selectedRevisionId.value = revisionId
  await loadBom()
  await router.replace({
    path: '/products',
    query: { product: String(selected.value?.id), revision: String(revisionId) },
  })
}

async function analyze() {
  if (!selectedRevisionId.value || buildQuantity.value < 1) return
  analyzing.value = true
  try {
    readiness.value = (
      await api.post<BuildReadinessEntity>(
        `/product-revisions/${selectedRevisionId.value}/build-readiness`,
        { build_quantity: buildQuantity.value, project_id: projectId.value },
      )
    ).data
  } finally {
    analyzing.value = false
  }
}

async function openProduction() {
  await router.push({
    path: '/projects',
    query: {
      ...(projectId.value ? { project: projectId.value } : {}),
      quantity: buildQuantity.value,
    },
  })
}

async function load() {
  products.value = (await api.get<ProductSummary[]>('/products')).data.map(presentProduct)
  projects.value = (
    await api.get<LinkedProject[]>('/projects').catch(() => ({ data: [] as LinkedProject[] }))
  ).data
  const requestedId = Number(route.query.product)
  const requestedRevision = Number(route.query.revision)
  const initial = products.value.find((item) => item.id === requestedId) || products.value[0]
  if (initial) await openProduct(initial, requestedRevision || null)
}

function presentProduct(product: ProductSummary): ProductSummary {
  return {
    ...product,
    description: formatBusinessText(product.description),
    revisions: product.revisions?.map((revision) => ({
      ...revision,
      notes: formatBusinessText(revision.notes),
    })),
  }
}

watch(
  () => route.query,
  async (query) => {
    const productId = Number(query.product)
    const revisionId = Number(query.revision)
    if (productId && productId !== selected.value?.id) {
      const product = products.value.find((item) => item.id === productId)
      if (product) await openProduct(product, revisionId || null)
    } else if (revisionId && revisionId !== selectedRevisionId.value) {
      await chooseRevision(revisionId)
    }
  },
)

watch([buildQuantity, projectId], () => {
  readiness.value = null
})

onMounted(load)
</script>

<template>
  <div class="page products-page">
    <BomProductionTabs />
    <div class="page-header">
      <div>
        <h1 class="page-title">产品定义 / 版本 BOM</h1>
        <div class="page-subtitle">按产品版本核对单台用量，并只读分析“做 N 台够不够”</div>
      </div>
      <el-tag type="success" effect="plain">构建分析不会修改库存</el-tag>
    </div>

    <div class="product-layout">
      <aside class="product-list card" aria-label="产品列表">
        <button
          v-for="product in products"
          :key="product.id"
          type="button"
          :class="{ active: selected?.id === product.id }"
          @click="openProduct(product, product.default_revision?.id)"
        >
          <b>{{ product.name }}</b>
          <span>{{ product.code }}</span>
          <small>{{ product.revision_count }} 个产品版本</small>
        </button>
        <el-empty v-if="!products.length" description="暂无产品" />
      </aside>

      <main v-if="selected" v-loading="loading" class="product-detail">
        <section class="product-hero card">
          <div>
            <span class="eyebrow">{{ selected.code }}</span>
            <h2>{{ selected.name }}</h2>
            <p>
              {{
                selected.code === 'PROD-ATLAS-AMR'
                  ? '机器人移动底盘产品'
                  : selected.description || '暂无产品说明'
              }}
            </p>
          </div>
          <el-tag :type="selected.lifecycle_status === 'archived' ? 'info' : 'success'">
            {{ selected.lifecycle_status === 'archived' ? '已归档' : '在用' }}
          </el-tag>
        </section>

        <section class="revision-card card">
          <header>
            <div>
              <span>产品版本</span>
              <b>{{ selectedRevision ? revisionLabel(selectedRevision) : '暂无版本' }}</b>
            </div>
            <el-select
              v-if="revisions.length"
              :model-value="selectedRevisionId"
              aria-label="选择产品版本"
              @change="chooseRevision"
            >
              <el-option
                v-for="revision in revisions"
                :key="revision.id"
                :label="revisionLabel(revision)"
                :value="revision.id"
              />
            </el-select>
          </header>
          <p v-if="selectedRevision?.notes">{{ formatBusinessText(selectedRevision.notes) }}</p>
        </section>

        <section v-if="bom" class="bom-card card">
          <header>
            <div>
              <h3>单台 BOM</h3>
              <span>{{ bom.count }} 类物料</span>
            </div>
            <small>这里是每台用量，不是项目总需求</small>
          </header>
          <el-table :data="bom.items" stripe>
            <el-table-column type="expand" width="48">
              <template #default="{ row }">
                <div class="alternate-panel" data-testid="product-bom-alternates">
                  <header>
                    <div>
                      <b>此 BOM 位的工程备选</b>
                      <small
                        >仅适用于 {{ selected?.code }} ·
                        {{ selectedRevision?.revision }}；不会自动参与备料计算。</small
                      >
                    </div>
                  </header>
                  <article
                    v-for="alternate in alternatesByBomItem[row.id] || []"
                    :key="alternate.id"
                    class="alternate-card"
                  >
                    <div>
                      <b>{{
                        alternate.alternate_material.mpn || alternate.alternate_material.code
                      }}</b>
                      <span>{{ alternate.alternate_material.name }}</span>
                    </div>
                    <el-tag
                      :type="
                        alternate.status === 'approved'
                          ? 'success'
                          : alternate.status === 'candidate'
                            ? 'warning'
                            : 'info'
                      "
                    >
                      {{
                        alternate.status === 'approved'
                          ? '此 BOM 位当前已批准'
                          : alternate.status === 'revoked'
                            ? '历史已批准，现已撤销'
                            : alternate.status === 'rejected'
                              ? '已拒绝'
                              : '待审核'
                      }}
                    </el-tag>
                    <p>{{ formatBusinessText(alternate.engineering_note) || '暂无工程说明' }}</p>
                    <small
                      >使用条件：{{
                        formatBusinessText(alternate.usage_condition) || '未注明'
                      }}</small
                    >
                    <small v-if="alternate.unavailable_reasons?.length" class="alternate-warning"
                      >当前不可用：{{ alternate.unavailable_reasons.join('；') }}</small
                    >
                    <div v-if="alternate.evidence_citations?.length" class="alternate-citations">
                      <span
                        v-for="citation in alternate.evidence_citations"
                        :key="citation.anchor_id"
                      >
                        {{ citation.document_revision }} · p.{{ citation.page }} ·
                        {{ citation.section }}
                      </span>
                    </div>
                    <small v-if="alternate.revoked_reason" class="alternate-warning"
                      >撤销原因：{{ alternate.revoked_reason }}</small
                    >
                    <footer
                      v-if="alternate.status === 'candidate' && auth.can('component:validate')"
                    >
                      <el-button
                        type="success"
                        plain
                        :loading="alternateReviewBusyId === alternate.id"
                        @click="approveAlternate(alternate)"
                        >批准此 BOM 位</el-button
                      >
                      <el-button
                        type="danger"
                        plain
                        :loading="alternateReviewBusyId === alternate.id"
                        @click="rejectAlternate(alternate)"
                        >拒绝</el-button
                      >
                    </footer>
                    <footer
                      v-if="alternate.status === 'approved' && auth.can('component:validate')"
                    >
                      <el-button
                        type="danger"
                        plain
                        :loading="alternateReviewBusyId === alternate.id"
                        @click="revokeAlternate(alternate)"
                        >撤销批准</el-button
                      >
                    </footer>
                  </article>
                  <el-empty
                    v-if="!(alternatesByBomItem[row.id] || []).length"
                    description="此 BOM 位尚无备选记录"
                  />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="物料" min-width="190">
              <template #default="{ row }">
                <b>{{ row.name }}</b
                ><br /><small>{{ row.code }}</small>
              </template>
            </el-table-column>
            <el-table-column prop="mpn" label="MPN" min-width="150" />
            <el-table-column label="单台用量" width="110">
              <template #default="{ row }"
                >{{ formatQuantity(row.quantity_per_unit) }} {{ row.unit }}</template
              >
            </el-table-column>
            <el-table-column label="当前可用" width="105">
              <template #default="{ row }">{{ formatQuantity(row.available_quantity) }}</template>
            </el-table-column>
            <el-table-column label="安全库存" width="105">
              <template #default="{ row }">{{ formatQuantity(row.safety_stock) }}</template>
            </el-table-column>
          </el-table>
        </section>

        <section v-if="selectedRevision" class="build-panel card">
          <header>
            <div>
              <h3>备料情况</h3>
              <span>确定性只读计算</span>
            </div>
          </header>
          <div class="build-form">
            <label>
              <span>计划构建</span>
              <el-input-number v-model="buildQuantity" :min="1" :step="1" :precision="0" />
              <span>台</span>
            </label>
            <label v-if="matchingProjects.length">
              <span>结合项目预留</span>
              <el-select v-model="projectId" clearable placeholder="不合并项目预留">
                <el-option
                  v-for="project in matchingProjects"
                  :key="project.id"
                  :label="`${project.code} · ${project.name}`"
                  :value="project.id"
                />
              </el-select>
            </label>
            <el-button type="primary" :loading="analyzing" @click="analyze">检查是否够料</el-button>
          </div>
          <BuildReadinessCard
            v-if="readiness"
            :result="readiness"
            generate-label="前往项目 / 生产任务"
            @generate-plan="openProduction"
          />
        </section>
      </main>
    </div>
  </div>
</template>

<style scoped>
.products-page {
  max-width: 1480px;
}
.product-layout {
  display: grid;
  grid-template-columns: minmax(230px, 280px) minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}
.product-list {
  display: grid;
  gap: 6px;
  padding: 10px;
  position: sticky;
  top: 12px;
}
.product-list button {
  display: grid;
  gap: 4px;
  min-height: 78px;
  padding: 12px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: #34536d;
  cursor: pointer;
  text-align: left;
}
.product-list button:hover,
.product-list button.active {
  border-color: #abd0e8;
  background: #edf7fd;
}
.product-list button span,
.product-list button small,
.product-hero p,
.revision-card p,
.bom-card small,
.build-panel header span {
  color: #6d8295;
  font-size: var(--mb-font-secondary);
}
.product-detail {
  display: grid;
  gap: 15px;
  min-width: 0;
}
.product-hero,
.revision-card,
.bom-card,
.build-panel {
  padding: 20px;
}
.product-hero,
.revision-card > header,
.bom-card > header,
.build-panel > header {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: flex-start;
}
.eyebrow {
  color: #2376a6;
  font-weight: 750;
  letter-spacing: 0.06em;
}
.product-hero h2,
.bom-card h3,
.build-panel h3 {
  margin: 4px 0 5px;
}
.product-hero h2 {
  font-size: 25px;
}
.revision-card header > div,
.bom-card header > div,
.build-panel header > div {
  display: grid;
  gap: 4px;
}
.revision-card header > div > span {
  color: #71879b;
  font-size: var(--mb-font-secondary);
}
.revision-card header > div > b {
  font-size: 18px;
}
.bom-card :deep(.el-table) {
  margin-top: 14px;
}
.alternate-panel {
  display: grid;
  gap: 10px;
  padding: 14px 18px;
  background: #f7fafc;
}
.alternate-panel header div {
  display: grid;
  gap: 3px;
}
.alternate-panel header small,
.alternate-card small {
  color: #6d8295;
}
.alternate-card {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) auto;
  gap: 7px 14px;
  padding: 13px;
  border: 1px solid #d6e3ec;
  border-radius: 10px;
  background: #fff;
}
.alternate-card > div {
  display: grid;
  gap: 3px;
}
.alternate-card > div span {
  color: #6d8295;
  font-size: 13px;
}
.alternate-card p,
.alternate-card small,
.alternate-card footer {
  grid-column: 1/-1;
  margin: 0;
}
.alternate-card footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.alternate-card :deep(.el-tag) {
  align-self: start;
}
.build-form {
  display: flex;
  flex-wrap: wrap;
  gap: 13px;
  align-items: end;
  margin: 15px 0;
}
.build-form label {
  display: flex;
  gap: 8px;
  align-items: center;
  color: #46637a;
}
.build-form label:nth-child(2) :deep(.el-select) {
  width: 260px;
}
@media (max-width: 880px) {
  .product-layout {
    grid-template-columns: 1fr;
  }
  .product-list {
    grid-auto-flow: column;
    grid-auto-columns: minmax(210px, 1fr);
    overflow-x: auto;
    position: static;
  }
}
@media (max-width: 600px) {
  .product-hero,
  .revision-card > header,
  .bom-card > header {
    display: grid;
  }
  .build-form,
  .build-form label {
    align-items: stretch;
    flex-direction: column;
  }
  .build-form label:nth-child(2) :deep(.el-select) {
    width: 100%;
  }
}
</style>
