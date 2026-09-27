<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { BuildReadinessEntity } from '../../types'
import { formatQuantity } from '../../utils/format'

const props = withDefaults(
  defineProps<{
    result: BuildReadinessEntity
    compact?: boolean
    generatingPlan?: boolean
    generateLabel?: string
  }>(),
  { compact: false, generatingPlan: false, generateLabel: '生成项目预留方案' },
)

const emit = defineEmits<{ generatePlan: [result: BuildReadinessEntity] }>()

const router = useRouter()
const shortages = computed(() => props.result.items.filter((item) => Number(item.shortage) > 0))
const risks = computed(() =>
  props.result.items.filter((item) => item.below_safety_after_build && Number(item.shortage) === 0),
)
const visibleShortages = computed(() =>
  props.compact ? shortages.value.slice(0, 3) : shortages.value,
)
const blockers = computed(() => props.result.items.filter((item) => item.material_blocker))
const itemsWithAlternates = computed(() =>
  props.result.items.filter((item) => item.approved_alternates?.length),
)
const canGeneratePlan = computed(
  () => Boolean(props.result.project) && props.result.sufficient && blockers.value.length === 0,
)
</script>

<template>
  <section
    class="readiness-card"
    :class="{ compact, insufficient: !result.sufficient }"
    data-testid="build-readiness-card"
  >
    <header>
      <div>
        <b>{{ result.product.name }} · {{ result.revision.revision }}</b>
        <span>计划：{{ result.build_quantity }} 台</span>
      </div>
      <span class="read-only">只读分析</span>
    </header>

    <div class="verdict" :class="result.sufficient ? 'ready' : 'blocked'">
      <strong>{{
        result.sufficient ? '✓ 当前库存可满足' : `${result.build_quantity} 台暂时备不齐`
      }}</strong>
      <span>最大可立即构建：{{ result.max_buildable_units }} 台</span>
    </div>

    <div v-if="shortages.length" class="shortage-list">
      <b>缺 {{ result.shortage_count }} 类物料</b>
      <div v-for="item in visibleShortages" :key="item.material_id" class="readiness-row">
        <span>{{ item.mpn || item.name }}</span>
        <small>
          需要 {{ formatQuantity(item.required_total) }} · 可覆盖
          {{ formatQuantity(item.coverage) }} · 缺 {{ formatQuantity(item.shortage) }}
        </small>
      </div>
      <small v-if="compact && shortages.length > 3">另有 {{ shortages.length - 3 }} 类缺料</small>
    </div>

    <p v-if="risks.length" class="safety-risk">⚠ {{ risks.length }} 类物料构建后将低于安全库存</p>

    <section
      v-if="itemsWithAlternates.length"
      class="alternate-notice"
      data-testid="readiness-alternates"
    >
      <b>已批准的 BOM 位备选（仅供参考）</b>
      <div v-for="item in itemsWithAlternates" :key="item.material_id">
        <span>{{ item.mpn || item.name }}</span>
        <small v-for="alternate in item.approved_alternates" :key="alternate.alternate_id">
          {{ alternate.mpn || alternate.code }} · 可用
          {{ formatQuantity(alternate.available_quantity) }} {{ alternate.unit }}
        </small>
      </div>
      <p>本次结果和构建计划仍只按主 BOM 计算，未自动使用备选库存。</p>
    </section>

    <div v-if="blockers.length" class="material-blockers">
      <b>暂时无法生成构建计划</b>
      <span>单台 BOM 中包含已停用物料：</span>
      <span v-for="item in blockers" :key="item.material_id"
        >{{ item.code }} · {{ item.name }}</span
      >
      <small>请先克隆并修订产品 BOM。</small>
    </div>

    <footer>
      <el-button
        v-if="canGeneratePlan"
        size="small"
        type="primary"
        :loading="generatingPlan"
        data-testid="generate-build-plan"
        @click="emit('generatePlan', result)"
      >
        {{ generateLabel }}
      </el-button>
      <el-button
        size="small"
        :type="canGeneratePlan ? 'default' : 'primary'"
        @click="
          router.push({
            path: '/products',
            query: { product: result.product.id, revision: result.revision.id },
          })
        "
      >
        打开产品 BOM
      </el-button>
    </footer>
  </section>
</template>

<style scoped>
.readiness-card {
  display: grid;
  gap: 13px;
  padding: 17px;
  border: 1px solid #b9ddcf;
  border-radius: 14px;
  background: #f8fffb;
  color: #29495d;
}
.readiness-card.insufficient {
  border-color: #efc5c2;
  background: #fffafa;
}
header,
header > div,
.verdict,
.shortage-list,
.readiness-row {
  display: grid;
  gap: 4px;
}
header {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
}
header b {
  font-size: var(--mb-font-card-title);
  overflow-wrap: anywhere;
}
header span,
.readiness-row small,
.shortage-list > small {
  color: #6b8092;
  font-size: var(--mb-font-secondary);
}
.read-only {
  padding: 3px 8px;
  border-radius: 999px;
  background: #e8f3f7;
  white-space: nowrap;
}
.verdict {
  padding: 13px;
  border-radius: 10px;
}
.verdict.ready {
  background: #e9f8f0;
  color: #19714e;
}
.verdict.blocked {
  background: #fdebea;
  color: #b43e38;
}
.verdict strong {
  font-size: 18px;
}
.shortage-list {
  color: #b43e38;
}
.readiness-row {
  padding: 9px 10px;
  border-left: 3px solid #d4544e;
  background: #fff;
}
.readiness-row small {
  color: #7d5958;
}
.safety-risk {
  margin: 0;
  padding: 10px 12px;
  border-radius: 9px;
  background: #fff4d7;
  color: #94610c;
  font-weight: 650;
}
.material-blockers {
  display: grid;
  gap: 5px;
  padding: 11px 12px;
  border-radius: 9px;
  background: #fdebea;
  color: #9e3732;
}
.material-blockers small {
  color: #7d5958;
}
.alternate-notice {
  display: grid;
  gap: 7px;
  padding: 11px 12px;
  border-radius: 9px;
  background: #eef6fb;
  color: #315c77;
}
.alternate-notice > div {
  display: grid;
  gap: 2px;
  padding-left: 9px;
  border-left: 3px solid #74a8c8;
}
.alternate-notice small {
  color: #668096;
}
.alternate-notice p {
  margin: 0;
  color: #785f2a;
  font-size: var(--mb-font-secondary);
}
footer {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
.compact {
  padding: 13px;
  gap: 10px;
}
.compact .read-only {
  display: none;
}
.compact .verdict strong {
  font-size: 16px;
}
</style>
