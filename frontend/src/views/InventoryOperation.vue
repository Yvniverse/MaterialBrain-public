<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute } from 'vue-router'
import { api, uuidKey } from '../api/client'
import type { Material, Page } from '../types'
import { formatQuantity } from '../utils/format'

interface Named {
  id: number
  name?: string
  code?: string
  full_path?: string
}

const operationTypes = [
  { value: 'inbound', label: '入库', description: '增加在库数量', tone: 'positive' },
  { value: 'outbound', label: '出库', description: '扣减可用库存', tone: 'negative' },
  { value: 'scrap', label: '报废', description: '报废并扣减库存', tone: 'negative' },
  { value: 'refund', label: '退料', description: '退回供应渠道', tone: 'negative' },
  { value: 'reserve', label: '项目预留', description: '锁定项目用量', tone: 'project' },
  { value: 'cancel-reservation', label: '取消预留', description: '释放项目预留', tone: 'project' },
  { value: 'reservation-to-outbound', label: '预留转出库', description: '预留转为领用', tone: 'project' },
  { value: 'adjust', label: '盘点修正', description: '按实盘数修正', tone: 'advanced' },
  { value: 'transfer', label: '库位转移', description: '变更存放库位', tone: 'advanced' },
  { value: 'reverse', label: '流水冲正', description: '冲正错误流水', tone: 'advanced' },
] as const

type OperationType = (typeof operationTypes)[number]['value']

const route = useRoute()
const requestedType = String(route.query.type || '')
const initialType = operationTypes.some((item) => item.value === requestedType)
  ? requestedType as OperationType
  : 'inbound'
const materials = ref<Material[]>([])
const projects = ref<Named[]>([])
const locations = ref<Named[]>([])
const submitting = ref(false)
const result = ref<Record<string, unknown> | null>(null)

const form = reactive({
  type: initialType,
  material_id: (Number(route.query.material_id) || null) as number | null,
  quantity: 1,
  actual_quantity: 0,
  reason: '',
  notes: '',
  project_id: null as number | null,
  source_location_id: null as number | null,
  target_location_id: null as number | null,
  movement_id: null as number | null,
})

const selected = computed(() => materials.value.find((item) => item.id === form.material_id))
const currentType = computed(
  () => operationTypes.find((item) => item.value === form.type) ?? operationTypes[0],
)
const isAdjust = computed(() => form.type === 'adjust')
const quantitySummary = computed(() => (isAdjust.value ? form.actual_quantity : form.quantity))
const selectedMaterialLabel = computed(() =>
  selected.value ? `${selected.value.code} · ${selected.value.name}` : '尚未选择',
)
const canSubmit = computed(() => {
  if (!form.material_id || !form.reason.trim()) return false
  if (isAdjust.value ? form.actual_quantity < 0 : form.quantity <= 0) return false
  if (['reserve', 'cancel-reservation', 'reservation-to-outbound'].includes(form.type)) {
    return Boolean(form.project_id)
  }
  if (form.type === 'transfer') {
    return Boolean(
      form.source_location_id &&
        form.target_location_id &&
        form.source_location_id !== form.target_location_id,
    )
  }
  if (form.type === 'reverse') return Boolean(form.movement_id)
  return true
})

function selectType(type: OperationType) {
  form.type = type
  result.value = null
}

onMounted(async () => {
  const [materialResponse, projectResponse, locationResponse] = await Promise.all([
    api.get<Page<Material>>('/materials', { params: { page_size: 200 } }),
    api.get<Named[]>('/projects').catch(() => ({ data: [] })),
    api.get<Named[]>('/locations'),
  ])
  materials.value = materialResponse.data.items
  projects.value = projectResponse.data
  locations.value = locationResponse.data
})

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  result.value = null
  try {
    const payload = { ...form, idempotency_key: uuidKey() }
    delete (payload as Partial<typeof payload>).type
    if (isAdjust.value) delete (payload as Partial<typeof payload>).quantity
    else delete (payload as Partial<typeof payload>).actual_quantity
    const { data } = await api.post<Record<string, unknown>>(`/inventory/${form.type}`, payload)
    result.value = data
    ElMessage.success('库存操作已完成')
    if (form.material_id) {
      const fresh = (await api.get<Material>(`/materials/${form.material_id}`)).data
      const index = materials.value.findIndex((item) => item.id === fresh.id)
      if (index >= 0) materials.value[index] = fresh
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="page op-page">
    <div class="page-header">
      <div class="page-heading-copy">
        <div class="title-row">
          <h1 class="page-title">库存操作台</h1>
          <el-tag effect="plain" type="success">事务级安全</el-tag>
        </div>
        <div class="page-subtitle">
          每次操作均由服务端校验并生成不可变流水，重复请求不会重复扣减库存
        </div>
      </div>
    </div>

    <div class="op-grid">
      <el-card class="card form-card" shadow="never">
        <el-form :model="form" label-position="top" size="large" class="operation-form">
          <section class="form-section first-section">
            <div class="section-heading">
              <span class="step">01</span>
              <div>
                <h2>选择操作类型</h2>
                <p>先明确库存变化的业务场景</p>
              </div>
            </div>
            <div class="operation-picker" role="radiogroup" aria-label="操作类型">
              <button
                v-for="item in operationTypes"
                :key="item.value"
                type="button"
                role="radio"
                :aria-checked="form.type === item.value"
                class="operation-option"
                :class="[{ active: form.type === item.value }, `tone-${item.tone}`]"
                @click="selectType(item.value)"
              >
                <span class="operation-label">{{ item.label }}</span>
                <span class="operation-description">{{ item.description }}</span>
              </button>
            </div>
          </section>

          <section class="form-section">
            <div class="section-heading">
              <span class="step">02</span>
              <div>
                <h2>选择物料</h2>
                <p>确认物料身份和实时库存水位</p>
              </div>
            </div>
            <el-form-item label="物料" required>
              <el-select
                v-model="form.material_id"
                filterable
                placeholder="搜索物料编码、名称或 MPN"
                no-data-text="暂无可选物料"
              >
                <el-option
                  v-for="material in materials"
                  :key="material.id"
                  :label="`${material.code} · ${material.name}${material.mpn ? ` · ${material.mpn}` : ''}`"
                  :value="material.id"
                />
              </el-select>
            </el-form-item>
            <div v-if="selected" class="stock-preview" aria-label="当前库存概览">
              <div>
                <span>当前库存</span>
                <b>{{ formatQuantity(selected.quantity) }}</b>
              </div>
              <div>
                <span>项目预留</span>
                <b>{{ formatQuantity(selected.reserved_quantity) }}</b>
              </div>
              <div>
                <span>可用库存</span>
                <b>{{ formatQuantity(selected.available_quantity) }}</b>
              </div>
              <div>
                <span>安全库存</span>
                <b>{{ formatQuantity(selected.safety_stock) }}</b>
              </div>
            </div>
          </section>

          <section class="form-section last-section">
            <div class="section-heading">
              <span class="step">03</span>
              <div>
                <h2>填写业务信息</h2>
                <p>带星号内容将写入审计记录</p>
              </div>
            </div>

            <div class="conditional-grid">
              <el-form-item v-if="isAdjust" label="实际盘点数量" required>
                <el-input-number v-model="form.actual_quantity" :min="0" :step="1" :precision="0" />
              </el-form-item>
              <el-form-item v-else label="操作数量" required>
                <el-input-number v-model="form.quantity" :min="1" :step="1" :precision="0" />
              </el-form-item>

              <el-form-item
                v-if="['reserve', 'cancel-reservation', 'reservation-to-outbound'].includes(form.type)"
                label="关联项目"
                required
              >
                <el-select v-model="form.project_id" placeholder="选择关联项目">
                  <el-option
                    v-for="project in projects"
                    :key="project.id"
                    :label="`${project.code} · ${project.name}`"
                    :value="project.id"
                  />
                </el-select>
              </el-form-item>

              <template v-if="form.type === 'transfer'">
                <el-form-item label="来源库位" required>
                  <el-select v-model="form.source_location_id" placeholder="选择来源库位">
                    <el-option
                      v-for="location in locations"
                      :key="location.id"
                      :label="location.full_path"
                      :value="location.id"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item label="目标库位" required>
                  <el-select v-model="form.target_location_id" placeholder="选择目标库位">
                    <el-option
                      v-for="location in locations"
                      :key="location.id"
                      :label="location.full_path"
                      :value="location.id"
                    />
                  </el-select>
                </el-form-item>
              </template>

              <el-form-item v-if="form.type === 'reverse'" label="原流水 ID" required>
                <el-input-number v-model="form.movement_id" :min="1" />
              </el-form-item>
            </div>

            <el-form-item label="操作原因" required>
              <el-input
                v-model="form.reason"
                maxlength="200"
                show-word-limit
                placeholder="请填写可审计的业务原因"
              />
            </el-form-item>
            <el-form-item label="备注">
              <el-input
                v-model="form.notes"
                type="textarea"
                :rows="3"
                maxlength="500"
                show-word-limit
                placeholder="可补充单据编号、领用人或其他说明"
              />
            </el-form-item>
          </section>
        </el-form>
      </el-card>

      <aside class="op-aside">
        <el-card v-if="result" class="card result-card" shadow="never">
          <el-result icon="success" title="操作成功" sub-title="最新库存已由服务端返回">
            <template #extra>
              <el-descriptions :column="1" border size="small">
                <el-descriptions-item label="流水号">{{ result.movement_no }}</el-descriptions-item>
                <el-descriptions-item label="当前库存">{{ formatQuantity(result.quantity) }}</el-descriptions-item>
                <el-descriptions-item label="已预留">{{ formatQuantity(result.reserved_quantity) }}</el-descriptions-item>
                <el-descriptions-item label="可用库存">{{ formatQuantity(result.available_quantity) }}</el-descriptions-item>
              </el-descriptions>
            </template>
          </el-result>
        </el-card>

        <el-card class="card summary-card" shadow="never">
          <template #header>
            <div class="summary-head">
              <div>
                <b>提交摘要</b>
                <span>确认无误后执行</span>
              </div>
              <el-tag size="small" effect="plain">{{ currentType.label }}</el-tag>
            </div>
          </template>
          <div class="summary-list">
            <div class="summary-row">
              <span>操作类型</span>
              <strong>{{ currentType.label }}</strong>
            </div>
            <div class="summary-row">
              <span>业务影响</span>
              <strong>{{ currentType.description }}</strong>
            </div>
            <div class="summary-row material-summary">
              <span>目标物料</span>
              <strong>{{ selectedMaterialLabel }}</strong>
            </div>
            <div class="summary-row">
              <span>{{ isAdjust ? '实盘数量' : '操作数量' }}</span>
              <strong class="quantity-summary">{{ quantitySummary }}</strong>
            </div>
          </div>
          <div class="readiness" :class="{ ready: canSubmit }">
            <span class="readiness-dot"></span>
            {{ canSubmit ? '必填信息已完整，可以提交' : '请完成左侧必填信息' }}
          </div>
          <el-button
            type="primary"
            size="large"
            class="submit"
            :loading="submitting"
            :disabled="!canSubmit"
            @click="submit"
          >
            确认并提交
          </el-button>
        </el-card>

        <el-card class="card notice" shadow="never">
          <template #header><b>数据安全规则</b></template>
          <ul>
            <li>服务端事务内锁定物料记录</li>
            <li>实时校验可用库存与项目预留</li>
            <li>每次提交生成独立幂等键</li>
            <li>成功操作写入库存流水与审计上下文</li>
            <li>错误操作只能通过反向流水冲正</li>
          </ul>
        </el-card>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.op-page {
  max-width: 1280px;
}

.page-heading-copy,
.op-grid,
.form-card,
.form-section,
.op-aside {
  min-width: 0;
}

.title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.page-subtitle {
  max-width: 760px;
  line-height: 1.65;
}

.op-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  align-items: start;
  gap: 20px;
}

.form-card :deep(.el-card__body) {
  padding: 0;
}

.operation-form {
  width: 100%;
}

.form-section {
  padding: 26px 28px 28px;
  border-top: 1px solid #e8eef6;
}

.first-section {
  border-top: 0;
}

.last-section {
  padding-bottom: 30px;
}

.section-heading {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 20px;
}

.section-heading .step {
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  color: #2f70bb;
  background: #eaf3ff;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: .4px;
}

.section-heading h2 {
  margin: 0;
  color: #20344f;
  font-size: 17px;
  line-height: 1.25;
}

.section-heading p {
  margin: 5px 0 0;
  color: #8392a6;
  font-size: 12px;
  line-height: 1.45;
}

.operation-picker {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.operation-option {
  position: relative;
  min-width: 0;
  min-height: 72px;
  padding: 12px 14px 11px 17px;
  overflow: hidden;
  text-align: left;
  border: 1px solid #dbe5f1;
  border-radius: 11px;
  background: #fff;
  color: #31445e;
  cursor: pointer;
  transition: border-color .16s, box-shadow .16s, transform .16s, background .16s;
}

.operation-option::before {
  content: '';
  position: absolute;
  top: 10px;
  bottom: 10px;
  left: 0;
  width: 3px;
  border-radius: 0 4px 4px 0;
  background: #9aaac0;
}

.operation-option:hover {
  transform: translateY(-1px);
  border-color: #9fc2e9;
  box-shadow: 0 7px 18px #264e7d12;
}

.operation-option.active {
  border-color: #4a91dd;
  background: #f2f8ff;
  box-shadow: 0 0 0 2px #4a91dd1a;
}

.operation-option.tone-positive::before {
  background: #35a77d;
}

.operation-option.tone-negative::before {
  background: #d46a62;
}

.operation-option.tone-project::before {
  background: #4a83cf;
}

.operation-option.tone-advanced::before {
  background: #8a72c2;
}

.operation-label,
.operation-description {
  display: block;
  max-width: 100%;
  overflow-wrap: anywhere;
}

.operation-label {
  font-size: 14px;
  font-weight: 750;
  line-height: 1.35;
}

.operation-description {
  margin-top: 5px;
  color: #8291a5;
  font-size: 11px;
  line-height: 1.35;
}

.form-card :deep(.el-form-item) {
  min-width: 0;
  margin-bottom: 20px;
}

.form-card :deep(.el-form-item__label) {
  height: auto;
  padding-bottom: 8px;
  white-space: normal;
  color: #465a73;
  font-weight: 650;
  line-height: 1.4;
}

.form-card :deep(.el-select),
.form-card :deep(.el-input-number),
.form-card :deep(.el-date-editor) {
  width: 100%;
  min-width: 0;
}

.form-card :deep(.el-input__inner),
.form-card :deep(.el-textarea__inner) {
  min-width: 0;
}

.stock-preview {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: -2px 0 2px;
  overflow: hidden;
  border: 1px solid #dfe9f4;
  border-radius: 12px;
  background: #f5f8fc;
}

.stock-preview div {
  min-width: 0;
  padding: 14px 15px;
  border-right: 1px solid #dfe9f4;
}

.stock-preview div:last-child {
  border-right: 0;
}

.stock-preview span {
  display: block;
  color: #7c8da1;
  font-size: 11px;
  line-height: 1.35;
}

.stock-preview b {
  display: block;
  margin-top: 5px;
  overflow-wrap: anywhere;
  color: #243b58;
  font-size: clamp(16px, 2vw, 20px);
  font-variant-numeric: tabular-nums;
  line-height: 1.25;
}

.conditional-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 0 14px;
}

.op-aside {
  position: sticky;
  top: 92px;
  display: grid;
  gap: 16px;
}

.summary-card :deep(.el-card__header),
.notice :deep(.el-card__header) {
  padding: 18px 20px;
}

.summary-card :deep(.el-card__body) {
  padding: 18px 20px 20px;
}

.summary-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.summary-head > div {
  min-width: 0;
}

.summary-head b,
.summary-head span {
  display: block;
}

.summary-head b {
  color: #263a55;
  font-size: 16px;
}

.summary-head span {
  margin-top: 3px;
  color: #8a99ac;
  font-size: 11px;
}

.summary-list {
  display: grid;
  gap: 0;
}

.summary-row {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  gap: 12px;
  padding: 11px 0;
  border-bottom: 1px solid #edf1f6;
  font-size: 12px;
  line-height: 1.45;
}

.summary-row > span {
  color: #8594a8;
}

.summary-row strong {
  min-width: 0;
  overflow-wrap: anywhere;
  text-align: right;
  color: #344a66;
}

.quantity-summary {
  color: #246ebd !important;
  font-size: 15px;
  font-variant-numeric: tabular-nums;
}

.readiness {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 16px 0 12px;
  padding: 10px 11px;
  border-radius: 9px;
  background: #f5f7fa;
  color: #7c8b9e;
  font-size: 12px;
  line-height: 1.4;
}

.readiness.ready {
  background: #eef9f4;
  color: #287d60;
}

.readiness-dot {
  flex: 0 0 auto;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #a9b4c1;
}

.readiness.ready .readiness-dot {
  background: #35a77d;
  box-shadow: 0 0 0 4px #35a77d18;
}

.submit {
  width: 100%;
}

.notice :deep(.el-card__body) {
  padding: 16px 20px 18px;
}

.notice ul {
  margin: 0;
  padding: 0;
  list-style: none;
  color: #62758d;
}

.notice li {
  position: relative;
  padding: 7px 0 7px 17px;
  overflow-wrap: anywhere;
  font-size: 12px;
  line-height: 1.5;
}

.notice li::before {
  content: '';
  position: absolute;
  top: 13px;
  left: 1px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #6e9bd0;
}

.result-card :deep(.el-card__body) {
  padding: 8px;
}

.result-card :deep(.el-result) {
  padding: 18px 8px;
}

.result-card :deep(.el-result__extra) {
  width: 100%;
}

@media (max-width: 1100px) {
  .op-grid {
    grid-template-columns: 1fr;
  }

  .op-aside {
    position: static;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    align-items: start;
  }

  .result-card {
    grid-column: 1 / -1;
  }
}

@media (max-width: 760px) {
  .operation-picker {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .form-section {
    padding: 22px 18px 24px;
  }

  .stock-preview {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .stock-preview div:nth-child(2) {
    border-right: 0;
  }

  .stock-preview div:nth-child(-n + 2) {
    border-bottom: 1px solid #dfe9f4;
  }

  .op-aside {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 480px) {
  .operation-picker,
  .conditional-grid {
    grid-template-columns: 1fr;
  }

  .summary-row {
    grid-template-columns: 68px minmax(0, 1fr);
  }
}
</style>
