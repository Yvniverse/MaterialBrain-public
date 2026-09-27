<script setup lang="ts">
import { computed } from 'vue'
import type { BuildPlan } from '../../types'
import { formatQuantity } from '../../utils/format'

const props = withDefaults(
  defineProps<{
    plan: BuildPlan
    busy?: boolean
    compact?: boolean
    readOnly?: boolean
  }>(),
  { busy: false, compact: false, readOnly: false },
)

const emit = defineEmits<{
  propose: [plan: BuildPlan]
  cancel: []
}>()

const reservedItems = computed(() =>
  props.plan.items.filter((item) => Number(item.reserved_for_project_at_plan) > 0),
)
const additionalItems = computed(() =>
  props.plan.items.filter((item) => Number(item.additional_reservation_required) > 0),
)
const visibleAdditional = computed(() =>
  props.compact ? additionalItems.value.slice(0, 3) : additionalItems.value,
)
const safetyRisks = computed(
  () => props.plan.items.filter((item) => item.below_safety_after_build).length,
)
</script>

<template>
  <section class="build-plan-preview" :class="{ compact }" data-testid="build-plan-preview">
    <header>
      <div>
        <span>构建计划</span>
        <b>{{ plan.plan_no }}</b>
      </div>
      <el-tag type="success" effect="plain">{{
        plan.status === 'ready' ? '库存尚未修改' : plan.status
      }}</el-tag>
    </header>

    <div class="plan-summary">
      <strong
        >{{ plan.product.name }} · {{ plan.revision.revision }} ×
        {{ plan.build_quantity }} 台</strong
      >
      <span>关联项目：{{ plan.project.code }} · {{ plan.project.name }}</span>
    </div>

    <div v-if="reservedItems.length" class="quantity-group">
      <b>当前项目已预留</b>
      <div v-for="item in reservedItems" :key="`reserved-${item.material_id}`" class="quantity-row">
        <span>{{ item.code }}</span>
        <strong>{{ formatQuantity(item.reserved_for_project_at_plan) }} {{ item.unit }}</strong>
      </div>
    </div>

    <div class="quantity-group additional">
      <b>本次还需新增预留</b>
      <div v-for="item in visibleAdditional" :key="item.material_id" class="quantity-row">
        <span>
          {{ item.code }}
          <small>总需求 {{ formatQuantity(item.required_total) }}</small>
        </span>
        <strong>{{ formatQuantity(item.additional_reservation_required) }} {{ item.unit }}</strong>
      </div>
      <small v-if="compact && additionalItems.length > 3">
        另有 {{ additionalItems.length - 3 }} 类物料，打开产品页查看全部
      </small>
    </div>

    <p v-if="safetyRisks" class="risk">⚠ 预留后 {{ safetyRisks }} 类物料将低于安全库存</p>
    <p class="safety-copy">创建后只会进入待审批状态，人工批准前不会改变库存。</p>

    <details>
      <summary>技术详情</summary>
      <span>产品 BOM：{{ plan.product_bom_hash.slice(0, 12) }}…</span>
      <span>计划快照：{{ plan.snapshot_hash.slice(0, 12) }}…</span>
    </details>

    <footer v-if="!readOnly && plan.status === 'ready'">
      <el-button :disabled="busy" @click="emit('cancel')">取消</el-button>
      <el-button
        type="primary"
        :loading="busy"
        data-testid="create-build-reservation-proposal"
        @click="emit('propose', plan)"
      >
        创建待审批预留
      </el-button>
    </footer>
  </section>
</template>

<style scoped>
.build-plan-preview {
  display: grid;
  gap: 14px;
  padding: 18px;
  border: 1px solid #b8d8cc;
  border-radius: 14px;
  background: linear-gradient(145deg, #fbfffd, #f1faf6);
  color: #29495d;
}
.build-plan-preview header,
.quantity-row,
.build-plan-preview footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.build-plan-preview header > div,
.plan-summary,
.quantity-group,
.quantity-row > span,
.build-plan-preview details {
  display: grid;
  gap: 4px;
}
.build-plan-preview header span,
.plan-summary span,
.quantity-row small,
.safety-copy,
.build-plan-preview details {
  color: #6b8092;
  font-size: var(--mb-font-secondary);
}
.build-plan-preview header b {
  font-size: var(--mb-font-card-title);
}
.plan-summary {
  padding: 12px;
  border-radius: 10px;
  background: #fff;
}
.quantity-group {
  gap: 7px;
}
.quantity-row {
  padding: 8px 10px;
  border-bottom: 1px solid #dcebe4;
  background: #fff;
}
.quantity-row strong {
  white-space: nowrap;
}
.additional .quantity-row {
  border-left: 3px solid #2d8a69;
}
.risk,
.safety-copy {
  margin: 0;
}
.risk {
  padding: 10px 12px;
  border-radius: 9px;
  background: #fff4d7;
  color: #94610c;
  font-weight: 650;
}
.build-plan-preview details span {
  display: block;
  margin-top: 5px;
  overflow-wrap: anywhere;
}
.build-plan-preview summary {
  cursor: pointer;
  color: #527187;
}
.build-plan-preview footer {
  justify-content: flex-end;
}
.compact {
  padding: 13px;
  gap: 10px;
}
.compact details {
  display: none;
}
@media (max-width: 600px) {
  .build-plan-preview footer {
    align-items: stretch;
    flex-direction: column-reverse;
  }
  .build-plan-preview footer .el-button {
    width: 100%;
    margin: 0;
  }
}
</style>
