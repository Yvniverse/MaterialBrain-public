<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { AgentComponentCandidate } from '../../types'
import { formatQuantity } from '../../utils/format'
import { formatBusinessText } from '../../utils/businessCopy'

const props = withDefaults(
  defineProps<{
    candidate: AgentComponentCandidate
    compact?: boolean
    selectable?: boolean
  }>(),
  { compact: false, selectable: true },
)
const emit = defineEmits<{ select: [label: string] }>()
const router = useRouter()
const reasons = computed(() =>
  props.candidate.technical_claims_allowed ? props.candidate.match_reasons.slice(0, 4) : [],
)
const firstLocation = computed(() => props.candidate.locations.locations[0])
const relationLabels = {
  similar_to: '已验证相似关系',
  electrical_compatible: '已验证电气兼容',
  pin_compatible: '已验证引脚兼容',
  same_footprint: '已验证相同封装',
} as const
</script>

<template>
  <article class="component-candidate" :class="{ compact }" data-testid="component-candidate-card">
    <header>
      <div>
        <h4>{{ candidate.mpn || candidate.code }}</h4>
        <p>{{ candidate.code }} · {{ candidate.name }}</p>
      </div>
      <span v-if="candidate.technical_claims_allowed" class="candidate-state">候选</span>
    </header>

    <div class="identity-line">
      <span>{{ formatBusinessText(candidate.manufacturer) || '制造商未登记' }}</span>
      <span>{{ candidate.package || '封装未登记' }}</span>
    </div>

    <ul v-if="reasons.length" class="match-reasons" aria-label="匹配原因">
      <li v-for="reason in reasons" :key="reason">{{ reason }}</li>
    </ul>
    <p v-else class="confidence-warning">仅匹配 catalog identity，低置信度元数据不用于技术结论。</p>

    <dl>
      <div>
        <dt>可用库存</dt>
        <dd>
          {{ formatQuantity(candidate.inventory.available_quantity) }}
          {{ candidate.inventory.unit }}
        </dd>
      </div>
      <div>
        <dt>存放位置</dt>
        <dd>{{ firstLocation?.full_path || '未登记可确认位置' }}</dd>
      </div>
    </dl>

    <section
      v-if="candidate.validated_relations?.length"
      class="validated-relations"
      aria-label="已验证工程关系"
    >
      <b>已验证工程关系</b>
      <div v-for="relation in candidate.validated_relations" :key="relation.relation_id">
        <span>{{ relationLabels[relation.relation_type] }}</span>
        <strong>{{ relation.related_material.mpn || relation.related_material.code }}</strong>
        <small>{{ formatBusinessText(relation.evidence_summary) }}</small>
      </div>
      <p>这些记录不构成全局替代批准；实际使用仍需核对具体产品 BOM 位。</p>
    </section>

    <p class="verification-warning">需工程验证，不代表已确认兼容或可直接替代。</p>
    <footer>
      <button
        v-if="selectable"
        type="button"
        class="select-candidate"
        @click="emit('select', candidate.mpn || candidate.code)"
      >
        选择此器件
      </button>
      <button type="button" @click="router.push(`/materials/${candidate.material_id}`)">
        查看物料
      </button>
      <button
        v-if="firstLocation"
        type="button"
        @click="router.push(`/locations?focus=${firstLocation.location_id}`)"
      >
        打开库位
      </button>
    </footer>
  </article>
</template>

<style scoped>
.component-candidate {
  display: grid;
  gap: 12px;
  padding: 16px;
  border: 1px solid #d7e4ee;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 7px 22px #365d7a0c;
}
.component-candidate header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.component-candidate h4 {
  margin: 0;
  color: #214d6c;
  font-size: var(--mb-font-card-title);
  overflow-wrap: anywhere;
}
.component-candidate header p {
  margin: 4px 0 0;
  color: #6c8194;
  font-size: var(--mb-font-secondary);
}
.candidate-state {
  flex: 0 0 auto;
  padding: 4px 8px;
  border-radius: 999px;
  background: #eaf6f1;
  color: #25765b;
  font-size: 12px;
  font-weight: 700;
}
.identity-line {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  color: #536f85;
  font-size: var(--mb-font-secondary);
}
.identity-line span {
  padding: 4px 8px;
  border-radius: 7px;
  background: #f1f5f8;
}
.match-reasons {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.match-reasons li {
  padding: 5px 8px;
  border: 1px solid #cce2d7;
  border-radius: 8px;
  background: #f1faf6;
  color: #326f59;
  font-size: var(--mb-font-secondary);
}
.component-candidate dl {
  display: grid;
  grid-template-columns: minmax(110px, 0.75fr) minmax(0, 1.25fr);
  gap: 8px;
  margin: 0;
}
.component-candidate dl > div {
  padding: 9px;
  border-radius: 9px;
  background: #f5f8fa;
}
.component-candidate dt {
  color: #718596;
  font-size: 12px;
}
.component-candidate dd {
  margin: 4px 0 0;
  color: #294f69;
  font-size: var(--mb-font-body);
  font-weight: 700;
  overflow-wrap: anywhere;
}
.confidence-warning,
.verification-warning {
  margin: 0;
  padding: 9px 10px;
  border-radius: 8px;
  background: #fff6e5;
  color: #866125;
  font-size: var(--mb-font-secondary);
  line-height: 1.5;
}
.confidence-warning {
  background: #fff0f0;
  color: #944848;
}
.validated-relations {
  display: grid;
  gap: 8px;
  padding: 11px;
  border: 1px solid #c9e0d5;
  border-radius: 10px;
  background: #f4fbf7;
  color: #315f4d;
}
.validated-relations > div {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 3px 8px;
}
.validated-relations span {
  font-size: 12px;
}
.validated-relations strong {
  overflow-wrap: anywhere;
}
.validated-relations small {
  grid-column: 1/-1;
  color: #637d71;
}
.validated-relations p {
  margin: 0;
  color: #78612d;
  font-size: 12px;
  line-height: 1.5;
}
.component-candidate footer {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.component-candidate footer button {
  min-height: 44px;
  padding: 8px 11px;
  border: 1px solid #c9dae7;
  border-radius: 9px;
  background: #fff;
  color: #316e9b;
  cursor: pointer;
  font-weight: 650;
}
.component-candidate footer .select-candidate {
  border-color: #337fbd;
  background: #337fbd;
  color: #fff;
}
.component-candidate.compact {
  gap: 9px;
  padding: 12px;
  box-shadow: none;
}
.component-candidate.compact dl {
  grid-template-columns: 1fr;
}
@media (max-width: 520px) {
  .component-candidate dl {
    grid-template-columns: 1fr;
  }
}
</style>
