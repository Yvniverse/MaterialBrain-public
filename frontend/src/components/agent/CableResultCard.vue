<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { AgentCableItem, AgentCableSearchEntity } from '../../types'
import { formatQuantity } from '../../utils/format'

const props = defineProps<{
  search?: AgentCableSearchEntity
  detail?: AgentCableItem
  compact?: boolean
}>()

const emit = defineEmits<{
  select: [label: string]
}>()

const router = useRouter()
const items = props.search?.items || (props.detail ? [props.detail] : [])
const state =
  props.search?.result_state ||
  (props.search?.needs_direction_disambiguation
    ? 'awaiting_clarification'
    : items.length
      ? 'exact_match'
      : props.detail
        ? 'exact_match'
        : 'no_match')
const requestedIdentity = [
  props.search?.constraints?.connector_a,
  props.search?.constraints?.connector_b,
].find((value): value is string => typeof value === 'string' && Boolean(value.trim()))
const exactIdentityWarning =
  requestedIdentity && state !== 'exact_match'
    ? `⚠ 未找到与 ${requestedIdentity} 完全相同的精确型号；以下结果仅是近似候选，不能视为该型号的确认匹配。`
    : ''

function directionLabel(direction: AgentCableItem['direction']) {
  return { same: '同向 A 型', reverse: '反向 B 型', unspecified: '方向未注明' }[direction]
}

function pinLabel(item: AgentCableItem) {
  if (!item.pin_count) return '—'
  return item.pin_count_b ? `${item.pin_count}→${item.pin_count_b} Pin` : `${item.pin_count} Pin`
}
</script>

<template>
  <section class="cable-results" :class="{ compact }" data-testid="agent-cable-results">
    <header>
      <div>
        <h3>{{ state === 'near_match' ? '相近线缆' : state === 'no_match' ? '未找到匹配线缆' : '线缆结果' }}</h3>
        <p v-if="search?.needs_direction_disambiguation" class="direction-question">
          {{ search.clarification }}
        </p>
      </div>
      <span v-if="search?.needs_direction_disambiguation">等待确认方向</span>
      <span v-else-if="state !== 'no_match'">{{ items.length }} 条</span>
    </header>
    <p v-if="state === 'near_match'" class="near-summary">
      没有找到完全符合当前规格的线缆，下面是最接近的结果。
    </p>
    <p
      v-if="exactIdentityWarning"
      class="exact-identity-warning"
      data-testid="cable-exact-identity-warning"
    >
      {{ exactIdentityWarning }}
    </p>
    <article v-for="item in items" :key="item.material_id" class="cable-candidate">
      <div class="cable-heading">
        <div>
          <b>{{ item.name }}</b
          ><small>{{ item.code }}</small>
        </div>
        <span>{{ directionLabel(item.direction) }}</span>
      </div>
      <dl>
        <div>
          <dt>规格</dt>
          <dd>
            {{ item.connector_pitch_mm ? `${formatQuantity(item.connector_pitch_mm)} mm · ` : ''
            }}{{ pinLabel(item) }} · {{ formatQuantity(item.length_cm) }} cm
          </dd>
        </div>
        <div>
          <dt>可用库存</dt>
          <dd>{{ formatQuantity(item.available_quantity) }} {{ item.unit }}</dd>
        </div>
        <div>
          <dt>存放位置</dt>
          <dd>{{ item.locations[0]?.full_path || '尚无可确认的实际库位' }}</dd>
        </div>
      </dl>
      <p v-if="item.match_reasons.length" class="match-reasons">
        {{ item.match_reasons.join(' · ') }}
      </p>
      <p v-if="item.differences?.length" class="near-differences">
        与要求不同：{{ item.differences.join('；') }}
      </p>
      <div class="cable-actions">
        <el-button
          size="small"
          plain
          @click="router.push(`/materials/${item.material_id}`)"
        >
          查看物料
        </el-button>
        <el-button
          v-if="search && search.items.length > 1"
          size="small"
          plain
          @click="emit('select', item.mpn || item.code)"
          >选择这条</el-button
        >
        <el-button
          size="small"
          plain
          type="primary"
          @click="router.push(`/cables?focus=${item.material_id}`)"
        >
          在线缆库查看
        </el-button>
        <el-button
          v-if="item.locations[0]"
          size="small"
          type="primary"
          @click="router.push(`/locations?focus=${item.locations[0].location_id}`)"
          >带我去找</el-button
        >
      </div>
    </article>
    <p v-if="state === 'no_match'" class="cable-empty">
      没有满足当前规格的线缆。可调整长度或补充连接器型号后再查。
    </p>
  </section>
</template>

<style scoped>
.cable-results {
  display: grid;
  gap: 12px;
}
.cable-results > header,
.cable-heading,
.cable-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.cable-results h3,
.cable-results p {
  margin: 0;
}
.cable-results h3 {
  color: #31566f;
  font-size: var(--mb-font-card-title);
}
.cable-results > header > span,
.cable-candidate small,
.match-reasons,
.near-differences,
.near-summary,
.exact-identity-warning,
.cable-empty {
  color: #6b8092;
  font-size: var(--mb-font-secondary);
}
.near-differences,
.near-summary,
.exact-identity-warning,
.cable-empty {
  margin: 0;
  padding: 10px;
  border-radius: 8px;
  background: #fff8e9;
  color: #8a6225;
}
.direction-question {
  margin-top: 6px !important;
  color: #9a5b00;
  font-weight: 650;
}
.cable-candidate {
  display: grid;
  gap: 10px;
  padding: 15px;
  border: 1px solid #d8e5ef;
  border-radius: 14px;
  background: #fbfdff;
}
.cable-heading > div {
  display: grid;
  gap: 3px;
}
.cable-heading > span {
  padding: 4px 9px;
  border-radius: 999px;
  background: #eaf4ff;
  color: #24618f;
  font-size: 13px;
}
.cable-candidate dl {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
}
.cable-candidate dl div {
  display: grid;
  gap: 3px;
}
.cable-candidate dt {
  color: #73879a;
  font-size: 13px;
}
.cable-candidate dd {
  margin: 0;
  color: #294d69;
  font-size: 15px;
  font-weight: 650;
  overflow-wrap: anywhere;
}
.cable-actions {
  justify-content: flex-start;
  flex-wrap: wrap;
}
.compact .cable-candidate dl {
  grid-template-columns: 1fr;
}
.compact .cable-candidate {
  padding: 12px;
}
@media (max-width: 720px) {
  .cable-candidate dl {
    grid-template-columns: 1fr;
  }
}
</style>
