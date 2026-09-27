<script setup lang="ts">
import { computed } from 'vue'
import type {
  ComponentEvidenceComparisonEntity,
  EngineeringEvidenceEntity,
  EvidenceCitation,
} from '../../types'

const props = defineProps<{
  evidence?: EngineeringEvidenceEntity
  comparison?: ComponentEvidenceComparisonEntity
  compact?: boolean
}>()

const citations = computed<EvidenceCitation[]>(() => {
  const items = [...(props.comparison?.citations || []), ...(props.evidence?.citations || [])]
  return [...new Map(items.map((item) => [item.anchor_id, item])).values()]
})
const resultLabels = { same: '相同', different: '不同', unknown: '证据不足' } as const
const factLabels: Record<string, string> = {
  interface: '接口',
  supply_voltage: '供电范围',
  input_voltage: '输入电压',
  input_voltage_absolute_max: '绝对最大输入电压',
  output_current: '输出电流',
  resolution_bits: '分辨率',
  package: '封装',
  gain: '增益',
  purpose: '作用',
  power_dissipation: '功耗',
  thermal_resistance: '热阻',
}

const blockLabels: Record<string, string> = {
  heading: '标题',
  paragraph: '段落',
  table: '表格',
  pin_description: '引脚描述',
  application_circuit: '应用电路',
  unparsed: '未解析',
}

function factLabel(fact: Record<string, unknown>) {
  const variant = fact.variant ? ` · ${String(fact.variant)}` : ''
  if (fact.field === 'pin' && fact.number !== undefined && fact.number !== null) {
    return `Pin ${fact.number}${variant}`
  }
  return `${factLabels[String(fact.field || '')] || String(fact.field || '工程参数')}${variant}`
}

function documentFileUrl(citation: EvidenceCitation) {
  return `/api/v1/evidence-documents/${citation.document_id}/file#page=${citation.page}`
}

function valueText(value: unknown) {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) return value.join('、')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function conditionText(value: unknown) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key}=${valueText(item)}`)
      .join('；')
  }
  return valueText(value)
}

function factValueText(fact: Record<string, unknown>) {
  if (fact.min !== undefined && fact.max !== undefined) {
    const range = `${fact.min}–${fact.max} ${fact.unit || ''}`.trim()
    const annotations = factAnnotations(fact)
    return annotations.length ? `${range}（${annotations.join('；')}）` : range
  }
  const value = fact.name ?? fact.value ?? fact.values
  const text =
    fact.field === 'thermal_resistance' && value && typeof value === 'object'
      ? Object.entries(value as Record<string, unknown>)
          .map(([key, item]) => `${key}=${valueText(item)}`)
          .join('；')
      : valueText(value)
  const base = fact.unit && value !== undefined && value !== null && !Array.isArray(value)
    ? `${text} ${fact.unit}`
    : text
  const annotations = factAnnotations(fact)
  return annotations.length ? `${base}（${annotations.join('；')}）` : base
}

function factAnnotations(fact: Record<string, unknown>) {
  const annotations: string[] = []
  if (fact.fact_type === 'derived_calculation' && fact.calculation) {
    annotations.push(`派生计算：${String(fact.calculation)}`)
  }
  if (fact.conditions) {
    annotations.push(`条件：${conditionText(fact.conditions)}`)
  }
  return annotations
}
</script>

<template>
  <section
    class="evidence-card"
    :class="{ compact }"
    data-testid="engineering-evidence-result"
  >
    <header>
      <div>
        <span>工程证据</span>
        <h3>{{ comparison ? '器件证据比较' : '页级引用' }}</h3>
      </div>
      <el-tag :type="(comparison?.unknowns.length || evidence?.evidence_coverage === 'insufficient') ? 'warning' : 'success'">
        {{ comparison?.conclusion || evidence?.conclusion }}
      </el-tag>
    </header>
    <p
      v-if="evidence?.retrieval?.vector_status === 'VECTOR_BLOCKED_BY_ENVIRONMENT'"
      class="retrieval-note"
    >
      当前使用 FTS + 结构化事实 + 布局索引；向量检索未启用。相似度不能作为工程事实。
    </p>

    <section v-if="evidence?.material_scope.length" class="material-scope">
      <b>精确物料/封装范围</b>
      <ul>
        <li v-for="material in evidence.material_scope" :key="material.id">
          {{ material.mpn }} · {{ material.package || '未标封装' }} · {{ material.manufacturer || '未标厂家' }}
        </li>
      </ul>
    </section>

    <dl v-if="comparison" class="comparison-list">
      <div v-for="row in comparison.comparisons" :key="row.field">
        <dt>{{ row.field }}</dt>
        <dd>
          <span>{{ valueText(row.first) }}</span>
          <b>{{ resultLabels[row.result] }}</b>
          <span>{{ valueText(row.second) }}</span>
        </dd>
      </div>
    </dl>
    <dl v-else-if="evidence?.facts.length" class="fact-list">
      <div v-for="fact in evidence.facts" :key="`${fact.anchor_id}:${fact.field}:${fact.variant || ''}`">
        <dt>{{ factLabel(fact) }}</dt>
        <dd>{{ factValueText(fact) }}</dd>
      </div>
    </dl>

    <div v-if="citations.length" class="citations">
      <b>引用</b>
      <details v-for="citation in citations" :key="`${evidence?.query || 'comparison'}:${citation.anchor_id}`">
        <summary>
          <span>{{ citation.document_title }} · {{ citation.document_revision }}</span>
          <strong>p.{{ citation.page }} · {{ citation.section }}</strong>
        </summary>
        <p>{{ citation.excerpt }}</p>
        <div v-if="citation.layout_blocks?.length" class="layout-preview">
          <b>布局抽取预览</b>
          <ul>
            <li v-for="block in citation.layout_blocks" :key="block.id">
              {{ blockLabels[block.block_type] || block.block_type }} · 顺序 {{ block.reading_order }} ·
              {{ block.location_status === 'available' ? `bbox ${block.bbox?.join(', ')}` : '位置不可用' }}
            </li>
          </ul>
          <small>布局块只用于定位；工程事实仍必须来自当前 EvidenceAnchor。</small>
        </div>
        <a
          v-if="!citation.synthetic_fixture"
          class="document-link"
          :href="documentFileUrl(citation)"
          target="_blank"
          rel="noopener"
        >
          打开原始数据手册 · p.{{ citation.page }}
        </a>
        <small v-else>合成 CI 测试证据，并非真实厂商数据手册</small>
      </details>
    </div>
  </section>
</template>

<style scoped>
.evidence-card{display:grid;gap:13px;padding:17px;border:1px solid #cfe0ec;border-radius:14px;background:linear-gradient(145deg,#fbfdff,#f4f9fc);font-size:var(--mb-font-body)}
.evidence-card>header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.evidence-card header div{display:grid;gap:3px}.evidence-card header span{color:#4b7898;font-size:var(--mb-font-secondary);font-weight:700}.evidence-card h3{margin:0;color:#294d69;font-size:var(--mb-font-card-title)}
.material-scope{display:grid;gap:5px;padding:9px 11px;border-radius:9px;background:#fff;color:#476b82;font-size:var(--mb-font-secondary)}.material-scope ul{display:flex;flex-wrap:wrap;gap:6px 14px;margin:0;padding-left:18px}.material-scope li{color:#294d69;font-weight:650}
.comparison-list,.fact-list{display:grid;gap:8px;margin:0}.comparison-list>div,.fact-list>div{display:grid;grid-template-columns:minmax(100px,.55fr) minmax(0,1.45fr);gap:12px;padding:9px 11px;border-radius:9px;background:#fff}.comparison-list dt,.fact-list dt{color:#5f7890}.comparison-list dd,.fact-list dd{margin:0;color:#294d69;font-weight:650}.comparison-list dd{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:9px}.comparison-list dd span:last-child{text-align:right}.comparison-list dd b{color:#39779f;font-size:var(--mb-font-secondary)}
.citations{display:grid;gap:7px}.citations>b{color:#3d617c}.citations details{padding:9px 11px;border:1px solid #d9e6ee;border-radius:9px;background:#fff}.citations summary{display:flex;justify-content:space-between;gap:12px;color:#48677f;cursor:pointer}.citations summary span{font-size:var(--mb-font-secondary);font-weight:650}.citations summary strong{color:#28719e;font-size:var(--mb-font-secondary);white-space:nowrap}.citations p{margin:9px 0 5px;color:#506b80;line-height:1.55;white-space:pre-wrap}.document-link{display:inline-block;color:#2878ad;font-size:var(--mb-font-secondary);font-weight:650;text-decoration:none}.citations small{color:#9a6328}.retrieval-note,.layout-preview{margin:0;padding:9px 11px;border-radius:8px;background:#eef7fb;color:#476b82;font-size:var(--mb-font-secondary);line-height:1.55}.layout-preview ul{margin:6px 0;padding-left:18px}.layout-preview small{color:#6d8295}.safety-note{margin:0;padding:9px 11px;border-radius:8px;background:#fff6e7;color:#775927;font-size:var(--mb-font-secondary);line-height:1.55}.compact{padding:12px;gap:10px}.compact .comparison-list>div,.compact .fact-list>div{grid-template-columns:1fr}.compact .citations summary{display:grid;gap:3px}
</style>
