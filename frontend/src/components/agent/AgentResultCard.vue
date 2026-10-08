<script setup lang="ts">
import { computed } from 'vue'
import SpatialMissionCard from '../../spatial/SpatialMissionCard.vue'
import NavigationPlanCard from '../../embodied/components/NavigationPlanCard.vue'
import type { AgentQueryResponse } from '../../types'
import type { AgentMaterialEntity } from '../../types'
import { formatQuantity } from '../../utils/format'
import { formatBusinessText } from '../../utils/businessCopy'
import { renderSafeMarkdown } from '../../utils/safeMarkdown'
import { materialPrimaryIdentity, materialSecondaryIdentity } from '../../utils/materialIdentity'
import BuildReadinessCard from './BuildReadinessCard.vue'
import CableResultCard from './CableResultCard.vue'
import ComponentCandidateCard from './ComponentCandidateCard.vue'
import EvidenceResultCard from './EvidenceResultCard.vue'
import EngineeringResearchCard from './EngineeringResearchCard.vue'
import MaterialResultCard from './MaterialResultCard.vue'
import ProductBomPreviewCard from './ProductBomPreviewCard.vue'
import PowerArchitectureOptions from './PowerArchitectureOptions.vue'

const props = defineProps<{
  result: AgentQueryResponse
}>()

const emit = defineEmits<{
  selectCandidate: [kind: 'material' | 'project' | 'product', label: string]
}>()

function candidateItems(): AgentMaterialEntity[] {
  return props.result.entities.material_candidates?.items || []
}

const materialSummary = computed<AgentMaterialEntity | null>(() => {
  const candidates = candidateItems()
  const selectedId =
    props.result.entities.material_candidates?.selected_material_id ||
    props.result.entities.material_candidates?.exact_match_ids?.[0]
  const candidate = selectedId
    ? candidates.find((item) => Number(item.id || item.material_id) === Number(selectedId))
    : candidates.length === 1
      ? candidates[0]
      : undefined
  const inventory = props.result.entities.material_detail || props.result.entities.inventory
  const locations = props.result.entities.locations
  if (!candidate && !inventory && !locations) return null
  const actualLocations = (locations?.locations || []).filter(
    (item) => item.quantity_is_exact !== false,
  )
  return {
    ...(candidate || {}),
    ...(inventory || {}),
    ...(locations
      ? {
          id: locations.material_id,
          material_id: locations.material_id,
          code: locations.code,
          name: locations.name,
          mpn: locations.mpn,
          attributes: locations.attributes || candidate?.attributes || inventory?.attributes,
          unit: locations.unit || candidate?.unit || inventory?.unit,
          quantity: locations.material_quantity,
          reserved_quantity: locations.reserved_quantity || inventory?.reserved_quantity || '0',
          available_quantity:
            locations.available_quantity ||
            inventory?.available_quantity ||
            locations.material_quantity,
          location: actualLocations[0] || null,
          locations: actualLocations,
          distribution_status: locations.distribution_status,
          unallocated_quantity: locations.unallocated_quantity,
        }
      : {}),
  } as AgentMaterialEntity
})
const bomEntity = computed(
  () => props.result.entities.bom_analysis || props.result.entities.project_bom,
)
const componentSearch = computed(() => props.result.entities.component_search)
const powerDesign = computed(() => props.result.entities.power_design)
const engineeringResearch = computed(() => props.result.entities.engineering_research)
const cableSearch = computed(() => props.result.entities.cable_search)
const cableDetail = computed(() => props.result.entities.cable_detail)
const componentRelations = computed(() => props.result.entities.component_relations)
const productAlternates = computed(() => props.result.entities.product_bom_alternates)
const productBomPreview = computed(() => props.result.entities.product_bom_preview)
const engineeringEvidence = computed(() => props.result.entities.engineering_evidence)
const evidenceComparison = computed(() => props.result.entities.component_evidence_comparison)
const narrativeAnswer = computed(() => {
  if (engineeringResearch.value) {
    return (
      props.result.narrative.trim() ||
      engineeringResearch.value.draft.conclusion ||
      props.result.answer.trim()
    )
  }
  return props.result.narrative.trim() || props.result.answer.trim()
})
const narrativeAnswerHtml = computed(() => renderSafeMarkdown(narrativeAnswer.value))
const showNarrativeAnswer = computed(() => {
  const entities = props.result.entities
  if (engineeringResearch.value && narrativeAnswer.value) return true
  if (productBomPreview.value && narrativeAnswer.value) return true
  if (
    powerDesign.value &&
    props.result.execution_mode === 'deterministic' &&
    props.result.narrative.trim()
  ) {
    return true
  }
  if (props.result.execution_mode === 'llm_assisted' && props.result.narrative.trim()) {
    return true
  }
  return !(
    entities.spatial_mission ||
    entities.navigation_plan ||
    entities.low_stock ||
    entities.component_search ||
    entities.power_design ||
    entities.engineering_research ||
    entities.cable_search ||
    entities.cable_detail ||
    entities.inventory ||
    entities.locations ||
    entities.project_bom ||
    entities.bom_analysis ||
    entities.product_bom ||
    entities.product_bom_preview ||
    entities.build_readiness ||
    entities.component_relations ||
    entities.product_bom_alternates ||
    entities.engineering_evidence ||
    entities.component_evidence_comparison ||
    (entities.material_candidates?.items.length ?? 0) > 0 ||
    (entities.project_candidates?.items.length ?? 0) > 0 ||
    (entities.product_candidates?.items.length ?? 0) > 0
  )
})
const relationLabels = {
  similar_to: '相似器件',
  electrical_compatible: '电气兼容',
  pin_compatible: '引脚兼容',
  same_footprint: '相同封装',
} as const
const showMaterialCards = computed(
  () =>
    !componentSearch.value &&
    !cableSearch.value &&
    !cableDetail.value &&
    !engineeringEvidence.value &&
    !evidenceComparison.value &&
    !engineeringResearch.value &&
    !componentRelations.value &&
    !productAlternates.value &&
    !props.result.entities.build_readiness &&
    !props.result.entities.product_bom &&
    !productBomPreview.value &&
    !bomEntity.value &&
    !props.result.entities.low_stock &&
    candidateItems().length <= 1 &&
    Boolean(materialSummary.value),
)
const hasMaterialAmbiguity = computed(
  () =>
    !componentSearch.value &&
    !cableSearch.value &&
    !engineeringEvidence.value &&
    !evidenceComparison.value &&
    candidateItems().length > 1 &&
    !props.result.entities.material_candidates?.selected_material_id &&
    props.result.entities.material_candidates?.exact_match_ids?.length !== 1,
)
const projectCandidates = computed(() => props.result.entities.project_candidates?.items || [])
const projectVersionAmbiguity = computed(() => {
  const candidates = props.result.entities.project_candidates
  const item = candidates?.items?.length === 1 ? candidates.items[0] : null
  const versions = item?.available_versions || []
  if (
    !item ||
    versions.length <= 1 ||
    candidates?.selected_bom_version ||
    !props.result.answer.includes('BOM_VERSION_REQUIRED')
  )
    return null
  return { item, versions }
})
const hasProjectAmbiguity = computed(
  () =>
    projectCandidates.value.length > 1 &&
    !props.result.entities.project_candidates?.selected_project_id &&
    props.result.entities.project_candidates?.exact_match_ids?.length !== 1,
)
const productCandidates = computed(() => props.result.entities.product_candidates?.items || [])
const hasProductAmbiguity = computed(
  () =>
    productCandidates.value.length > 1 &&
    !props.result.entities.product_candidates?.selected_product_id &&
    props.result.entities.product_candidates?.exact_match_ids?.length !== 1,
)

function technicalValue(value: unknown) {
  return typeof value === 'number' || (typeof value === 'string' && /^-?\d+(\.\d+)?$/.test(value))
    ? formatQuantity(value)
    : String(value ?? '—')
}

function relationStatusLabel(status: string) {
  return (
    { validated: '已验证', candidate: '待审核', rejected: '已拒绝', revoked: '已撤销' }[status] ||
    status
  )
}

function alternateStatusLabel(status: string) {
  return (
    {
      approved: '此 BOM 位已批准',
      candidate: '候选，尚未批准',
      rejected: '已拒绝',
      revoked: '批准已撤销',
    }[status] || status
  )
}
</script>

<template>
  <section class="agent-result-card" data-testid="agent-result">
    <SpatialMissionCard v-if="result.entities.spatial_mission" :mission="result.entities.spatial_mission" />
    <NavigationPlanCard v-if="result.entities.navigation_plan" :plan="result.entities.navigation_plan" />
    <header><h2>查询结果</h2></header>
    <div v-if="showNarrativeAnswer" class="answer" v-html="narrativeAnswerHtml"></div>

    <section
      v-if="componentSearch"
      class="component-results"
      data-testid="component-candidate-results"
    >
      <header>
        <h3>
          {{ componentSearch.query.replacement_intent ? '候选相似器件' : '工程候选器件' }}
        </h3>
        <span>{{ componentSearch.count }} 个候选</span>
      </header>
      <div v-if="componentSearch.candidates.length" class="component-grid">
        <ComponentCandidateCard
          v-for="candidate in componentSearch.candidates"
          :key="candidate.material_id"
          :candidate="candidate"
          :selectable="componentSearch.candidates.length > 1"
          @select="emit('selectCandidate', 'material', $event)"
        />
      </div>
      <p v-else class="component-empty">没有找到同时满足这些明确要求的可信候选器件。</p>
      <p class="component-caveat">{{ componentSearch.engineering_caveat }}</p>
    </section>

    <CableResultCard
      v-if="cableSearch || cableDetail"
      :search="cableSearch"
      :detail="cableDetail"
      @select="emit('selectCandidate', 'material', $event)"
    />

    <section v-if="powerDesign" class="data-section power-design" data-testid="power-design-result">
      <header class="power-design-header">
        <div>
          <h3>电源设计方案</h3>
          <p>
            {{ powerDesign.requirements.input_voltage_v }}V →
            {{ powerDesign.requirements.output_voltage_v }}V · 拓扑优先 · 只读
          </p>
        </div>
        <el-tag :type="powerDesign.status === 'supported' ? 'success' : 'warning'">
          {{ powerDesign.status === 'supported' ? '已有兼容候选' : '待补约束' }}
        </el-tag>
      </header>
      <el-alert
        v-if="powerDesign.missing_constraints.length"
        :title="`待补约束：${powerDesign.missing_constraints.join('、')}`"
        type="warning"
        :closable="false"
      />
      <PowerArchitectureOptions
        :topologies="powerDesign.topologies"
        :rail-bom-draft="powerDesign.rail_bom_draft"
        :load-case-calculations="powerDesign.load_case_calculations"
      />
      <details class="power-branch-details">
        <summary>按器件类别查看兼容候选、库存和库位</summary>
        <div class="power-branch-grid">
          <article
            v-for="branch in powerDesign.branches"
            :key="branch.topology"
            class="power-branch"
          >
            <div class="power-branch-title">
              <h4>{{ branch.label }}</h4>
              <span>{{ branch.compatible_candidate_count }} 个兼容候选</span>
            </div>
            <p class="power-tradeoff">{{ branch.tradeoff_summary }}</p>
            <div v-if="branch.candidates.length" class="power-candidate-list">
              <article
                v-for="candidate in branch.candidates"
                :key="candidate.material_id"
                class="power-candidate"
              >
                <div class="power-candidate-title">
                  <b>{{ candidate.mpn || candidate.code }}</b>
                  <el-tag size="small" type="success">{{ candidate.code }}</el-tag>
                </div>
                <div class="power-candidate-meta">
                  <span>{{ candidate.package }}</span>
                  <span
                    >可用 {{ formatQuantity(candidate.inventory.available_quantity) }}
                    {{ candidate.inventory.unit }}</span
                  >
                </div>
                <div class="power-location">
                  <span>库位：</span>
                  <span v-if="candidate.locations.locations.length">
                    {{ candidate.locations.locations.map((item) => item.full_path).join('；') }}
                  </span>
                  <span v-else>暂无可确认实际库位</span>
                </div>
                <div v-if="candidate.calculations?.length" class="power-calculations">
                  <div v-for="calculation in candidate.calculations" :key="calculation.formula">
                    <b v-if="calculation.status === 'calculated'">
                      服务端 LDO 损耗 {{ formatQuantity(calculation.loss_w) }}W · 理想效率
                      {{ formatQuantity(calculation.ideal_efficiency) }}
                    </b>
                    <span v-else>损耗待负载电流：{{ calculation.formula }}</span>
                  </div>
                </div>
                <details class="power-peripherals">
                  <summary>外围角色与证据</summary>
                  <ul>
                    <li v-for="role in candidate.peripheral_roles" :key="role.role">
                      {{ role.role }}：
                      <b v-if="role.exact_value">{{ role.exact_value }}</b>
                      <span v-else>按所选器件数据手册/参考设计定值</span>
                    </li>
                  </ul>
                  <a :href="candidate.evidence.datasheet_url" target="_blank" rel="noreferrer">
                    {{ candidate.evidence.mpn }} 数据手册
                  </a>
                  <span v-if="candidate.evidence_reconciliation?.length" class="power-conflict">
                    已保留本地规格冲突记录，采用一手 TI 证据。
                  </span>
                </details>
              </article>
            </div>
            <p v-else class="power-empty">没有找到已审计且兼容的在库候选。</p>
          </article>
        </div>
      </details>
      <p v-if="powerDesign.next_question" class="power-next-question">
        {{ powerDesign.next_question }}
      </p>
      <p class="power-caveat">
        外围角色已列出；未被数据手册/参考设计锚定的精确阻容、电感值不会臆造。
      </p>
    </section>

    <EngineeringResearchCard v-if="engineeringResearch" :research="engineeringResearch" />

    <ProductBomPreviewCard v-if="productBomPreview" :preview="productBomPreview" />

    <EvidenceResultCard
      v-if="engineeringEvidence || evidenceComparison"
      :evidence="engineeringEvidence"
      :comparison="evidenceComparison"
    />

    <section
      v-if="hasMaterialAmbiguity"
      class="candidate-picker"
      data-testid="material-candidate-picker"
    >
      <h3>找到 {{ candidateItems().length }} 个相近物料，请选择：</h3>
      <button
        v-for="item in candidateItems()"
        :key="item.id"
        type="button"
        @click="emit('selectCandidate', 'material', item.mpn || item.code)"
      >
        <span class="candidate-identity">
          <b>{{ materialPrimaryIdentity(item) }}</b>
          <small v-if="materialSecondaryIdentity(item)">{{
            materialSecondaryIdentity(item)
          }}</small>
          <small>{{ item.package || '未标封装' }}</small>
        </span>
        <span class="candidate-live">
          <b>可用 {{ formatQuantity(item.available_quantity) }}</b>
          <small>{{ item.location?.full_path || '尚未分配实际库位' }}</small>
        </span>
      </button>
    </section>
    <section
      v-if="projectVersionAmbiguity"
      class="candidate-picker"
      data-testid="project-bom-version-picker"
    >
      <h3>{{ projectVersionAmbiguity.item.name }} 有多个 BOM 版本，请选择：</h3>
      <p>选择版本后，我会继续执行刚才的 BOM / 库存查询，不需要重新输入项目。</p>
      <button
        v-for="version in projectVersionAmbiguity.versions"
        :key="version"
        type="button"
        @click="emit('selectCandidate', 'project', version)"
      >
        <b>{{ version }}</b
        ><span>继续核对该版本</span>
      </button>
    </section>
    <section
      v-if="hasProjectAmbiguity"
      class="candidate-picker"
      data-testid="project-candidate-picker"
    >
      <h3>找到多个项目，请选择：</h3>
      <button
        v-for="item in projectCandidates"
        :key="item.id"
        type="button"
        @click="emit('selectCandidate', 'project', item.code)"
      >
        <b>{{ item.name }}</b
        ><span>{{ item.code }}</span>
      </button>
    </section>
    <section
      v-if="hasProductAmbiguity"
      class="candidate-picker"
      data-testid="product-candidate-picker"
    >
      <h3>找到多个产品，请选择：</h3>
      <button
        v-for="item in productCandidates"
        :key="item.id"
        type="button"
        @click="emit('selectCandidate', 'product', item.code)"
      >
        <b>{{ item.name }}</b
        ><span>{{ item.code }}</span>
      </button>
    </section>
    <div v-if="showMaterialCards && materialSummary" class="result-stack">
      <MaterialResultCard :material="materialSummary" />
    </div>

    <section v-if="result.entities.low_stock" class="data-section">
      <h3>低库存物料（{{ result.entities.low_stock.count }}）</h3>
      <el-table :data="result.entities.low_stock.items" size="small">
        <el-table-column prop="code" label="编码" min-width="120" />
        <el-table-column label="型号 / 身份" min-width="160">
          <template #default="{ row }">
            <b>{{ materialPrimaryIdentity(row) }}</b>
            <small v-if="materialSecondaryIdentity(row)">{{
              materialSecondaryIdentity(row)
            }}</small>
          </template>
        </el-table-column>
        <el-table-column label="可用"
          ><template #default="{ row }">{{
            formatQuantity(row.available_quantity)
          }}</template></el-table-column
        >
        <el-table-column label="安全库存"
          ><template #default="{ row }">{{
            formatQuantity(row.safety_stock)
          }}</template></el-table-column
        >
      </el-table>
    </section>

    <section v-if="bomEntity" class="data-section">
      <h3>{{ bomEntity.project.name }} · BOM 库存</h3>
      <el-table :data="bomEntity.items" size="small">
        <el-table-column prop="code" label="物料" min-width="120" />
        <el-table-column prop="mpn" label="MPN" min-width="130" />
        <el-table-column label="需求"
          ><template #default="{ row }">{{
            formatQuantity(row.required_quantity)
          }}</template></el-table-column
        >
        <el-table-column label="可用"
          ><template #default="{ row }">{{
            formatQuantity(row.available_quantity)
          }}</template></el-table-column
        >
        <el-table-column label="项目已预留"
          ><template #default="{ row }">{{
            formatQuantity(row.reserved_for_project)
          }}</template></el-table-column
        >
        <el-table-column v-if="result.entities.bom_analysis" label="库存库位" min-width="260">
          <template #default="{ row }">
            <div v-if="row.locations?.length">
              <div v-for="location in row.locations" :key="location.location_id">
                {{ location.full_path }}
                <small v-if="location.quantity_at_location">
                  · 物理库存 {{ formatQuantity(location.quantity_at_location) }}
                </small>
              </div>
            </div>
            <span v-else>库存尚未映射到具体库位</span>
          </template>
        </el-table-column>
        <el-table-column v-if="result.entities.bom_analysis" label="缺料"
          ><template #default="{ row }"
            ><span :class="Number(row.shortage) > 0 ? 'shortage' : 'enough'">{{
              formatQuantity(row.shortage)
            }}</span></template
          ></el-table-column
        >
      </el-table>
      <small>物理库存库位：{{ bomEntity.quantity_semantics }}</small>
    </section>

    <BuildReadinessCard
      v-if="result.entities.build_readiness"
      :result="result.entities.build_readiness"
    />

    <section
      v-if="componentRelations"
      class="data-section relation-results"
      data-testid="agent-component-relations"
    >
      <h3>工程关系</h3>
      <article v-for="relation in componentRelations.items" :key="relation.id">
        <div>
          <b>{{ relation.source_material.mpn || relation.source_material.code }}</b>
          <span>↔</span>
          <b>{{ relation.target_material.mpn || relation.target_material.code }}</b>
        </div>
        <el-tag
          :type="
            relation.status === 'validated'
              ? 'success'
              : relation.status === 'revoked' || relation.status === 'rejected'
                ? 'info'
                : 'warning'
          "
        >
          {{ relationStatusLabel(relation.status) }} · {{ relationLabels[relation.relation_type] }}
        </el-tag>
        <p>{{ formatBusinessText(relation.evidence_summary || relation.confidence_note) }}</p>
        <p v-if="relation.unavailable_reasons?.length" class="scope-note">
          当前不可用：{{ relation.unavailable_reasons.join('；') }}
        </p>
        <ul v-if="relation.evidence_citations?.length" class="inline-citations">
          <li v-for="citation in relation.evidence_citations" :key="citation.anchor_id">
            {{ citation.document_revision }} · p.{{ citation.page }} · {{ citation.section }}
          </li>
        </ul>
      </article>
    </section>

    <section
      v-if="productAlternates"
      class="data-section relation-results"
      data-testid="agent-product-alternates"
    >
      <h3>产品 BOM 位备选</h3>
      <article v-for="alternate in productAlternates.items" :key="alternate.id">
        <div>
          <b>{{ alternate.product.code }} · {{ alternate.revision.revision }}</b>
          <span
            >{{ alternate.primary_material.mpn || alternate.primary_material.code }} →
            {{ alternate.alternate_material.mpn || alternate.alternate_material.code }}</span
          >
        </div>
        <el-tag
          :type="
            alternate.status === 'approved'
              ? 'success'
              : alternate.status === 'revoked' || alternate.status === 'rejected'
                ? 'info'
                : 'warning'
          "
        >
          {{ alternateStatusLabel(alternate.status) }}
        </el-tag>
        <p>{{ formatBusinessText(alternate.engineering_note || alternate.usage_condition) }}</p>
        <p v-if="alternate.unavailable_reasons?.length" class="scope-note">
          当前不可用：{{ alternate.unavailable_reasons.join('；') }}
        </p>
        <ul v-if="alternate.evidence_citations?.length" class="inline-citations">
          <li v-for="citation in alternate.evidence_citations" :key="citation.anchor_id">
            {{ citation.document_revision }} · p.{{ citation.page }} · {{ citation.section }}
          </li>
        </ul>
      </article>
    </section>

    <section v-if="result.entities.product_bom" class="data-section">
      <h3>
        {{ result.entities.product_bom.product.name }} ·
        {{ result.entities.product_bom.revision.revision }} 单台 BOM
      </h3>
      <el-table :data="result.entities.product_bom.items" size="small">
        <el-table-column prop="code" label="物料" min-width="140" />
        <el-table-column prop="mpn" label="MPN" min-width="130" />
        <el-table-column label="单台用量">
          <template #default="{ row }">{{ formatQuantity(row.quantity_per_unit) }}</template>
        </el-table-column>
        <el-table-column label="当前可用">
          <template #default="{ row }">{{ formatQuantity(row.available_quantity) }}</template>
        </el-table-column>
        <el-table-column label="安全库存">
          <template #default="{ row }">{{ formatQuantity(row.safety_stock) }}</template>
        </el-table-column>
      </el-table>
    </section>

    <details class="technical-details" data-testid="agent-technical-details">
      <summary>技术详情</summary>
      <dl>
        <div>
          <dt>请求编号</dt>
          <dd>{{ result.request_id }}</dd>
        </div>
        <div>
          <dt>execution_mode</dt>
          <dd>{{ result.execution_mode }}</dd>
        </div>
        <div>
          <dt>model_call_count</dt>
          <dd>{{ result.model_call_count }}</dd>
        </div>
        <template v-if="result.model_call_count > 0">
          <div v-for="(call, index) in result.telemetry" :key="`model-call-${index}`">
            <dt>模型调用 {{ index + 1 }}</dt>
            <dd>
              Provider: {{ call.provider }} · Model: {{ call.model }} · Input tokens:
              {{ call.input_tokens }} · Output tokens: {{ call.output_tokens }} · Finish:
              {{ call.finish_reason }}
            </dd>
          </div>
        </template>
        <div v-for="(event, index) in result.tool_events" :key="`${event.tool}-${index}`">
          <dt>{{ event.tool }}</dt>
          <dd>{{ event.duration_ms }} ms · {{ event.status }}</dd>
        </div>
        <div
          v-for="(fact, index) in result.grounded_facts"
          :key="`${fact.source_tool}-${fact.field}-${index}`"
        >
          <dt>{{ fact.source_tool }} · {{ fact.label }}</dt>
          <dd>{{ technicalValue(fact.value) }}{{ fact.unit ? ` ${fact.unit}` : '' }}</dd>
        </div>
      </dl>
    </details>
  </section>
</template>

<style scoped>
.agent-result-card {
  display: grid;
  gap: 14px;
}
.agent-result-card > header h2 {
  margin: 0;
  padding-bottom: 10px;
  border-bottom: 1px solid #dde6ee;
  color: #29445f;
  font-size: var(--mb-font-section-title);
}
.answer {
  padding: 18px;
  border: 1px solid #d8e5ef;
  border-radius: 14px;
  background: #fafdff;
  color: #294d69;
  font-size: var(--mb-font-body);
  font-weight: 550;
  line-height: 1.75;
  white-space: normal;
  overflow-wrap: anywhere;
}
.answer :deep(p) {
  margin: 0 0 0.75em;
}
.answer :deep(p:last-child) {
  margin-bottom: 0;
}
.answer :deep(h1),
.answer :deep(h2),
.answer :deep(h3),
.answer :deep(h4),
.answer :deep(h5),
.answer :deep(h6) {
  margin: 0.15em 0 0.6em;
  color: #29445f;
  font-size: 1.05em;
}
.answer :deep(ul),
.answer :deep(ol) {
  margin: 0.35em 0 0.75em;
  padding-left: 1.5em;
}
.answer :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.5em 0 0.75em;
  font-weight: 450;
}
.answer :deep(th),
.answer :deep(td) {
  border: 1px solid #d8e5ef;
  padding: 7px 9px;
  text-align: left;
  vertical-align: top;
}
.answer :deep(th) {
  background: #f0f6fb;
  font-weight: 700;
}
.answer :deep(pre) {
  max-width: 100%;
  overflow-x: auto;
  padding: 10px;
  border-radius: 8px;
  background: #eef4f8;
  font-weight: 450;
}
.answer :deep(code) {
  padding: 0.05em 0.25em;
  border-radius: 4px;
  background: #eef4f8;
  font-weight: 450;
}
.answer :deep(pre code) {
  padding: 0;
  background: transparent;
}
.answer :deep(a) {
  color: #17689c;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.answer :deep(blockquote) {
  margin: 0.5em 0;
  padding: 0.15em 0 0.15em 0.85em;
  border-left: 3px solid #b8ccdc;
  color: #51697d;
}
.result-stack {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 10px;
}
.component-results {
  display: grid;
  gap: 11px;
}
.component-results > header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}
.component-results h3 {
  margin: 0;
  color: #31566f;
  font-size: var(--mb-font-card-title);
}
.component-results > header span,
.component-caveat,
.component-empty {
  color: #6b8092;
  font-size: var(--mb-font-secondary);
}
.component-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(310px, 1fr));
  gap: 11px;
}
.component-caveat,
.component-empty {
  margin: 0;
  line-height: 1.55;
}
.power-design {
  display: grid;
  gap: 13px;
}
.power-design-header,
.power-branch-title,
.power-candidate-title,
.power-candidate-meta,
.power-location {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.power-design-header h3,
.power-branch-title h4 {
  margin: 0;
  color: #31566f;
}
.power-design-header p,
.power-tradeoff,
.power-next-question,
.power-caveat,
.power-empty {
  margin: 4px 0 0;
  color: #6b8092;
  font-size: var(--mb-font-secondary);
  line-height: 1.55;
}
.power-branch-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.power-branch {
  display: grid;
  gap: 9px;
  padding: 13px;
  border: 1px solid #dbe7ef;
  border-radius: 12px;
  background: #fbfdff;
}
.power-branch-title span,
.power-candidate-meta,
.power-location,
.power-conflict {
  color: #71869a;
  font-size: var(--mb-font-secondary);
}
.power-candidate-list {
  display: grid;
  gap: 8px;
}
.power-candidate {
  display: grid;
  gap: 7px;
  padding: 10px;
  border: 1px solid #e1eaf1;
  border-radius: 10px;
  background: #fff;
}
.power-location {
  justify-content: flex-start;
  align-items: flex-start;
}
.power-calculations {
  padding: 7px 9px;
  border-radius: 8px;
  background: #f2faf6;
  color: #2f775e;
  font-size: var(--mb-font-secondary);
}
.power-peripherals summary {
  cursor: pointer;
  color: #35698a;
  font-size: var(--mb-font-secondary);
  font-weight: 700;
}
.power-peripherals ul {
  margin: 8px 0;
  padding-left: 18px;
  color: #607789;
  font-size: var(--mb-font-secondary);
  line-height: 1.5;
}
.power-peripherals a {
  color: #2b76ad;
  font-size: var(--mb-font-secondary);
}
.power-conflict {
  display: block;
  margin-top: 5px;
  color: #9a6a2a;
}
@media (max-width: 800px) {
  .power-branch-grid {
    grid-template-columns: 1fr;
  }
}
.candidate-picker {
  display: grid;
  gap: 9px;
  padding: 16px;
  border: 1px solid #d8e5ef;
  border-radius: 14px;
  background: #fafdff;
}
.candidate-picker h3 {
  margin: 0 0 2px;
  color: #35536f;
  font-size: var(--mb-font-card-title);
}
.candidate-picker button {
  display: flex;
  min-height: 48px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 13px;
  border: 1px solid #cfdeea;
  border-radius: 10px;
  background: #fff;
  color: #315b79;
  cursor: pointer;
  text-align: left;
}
.candidate-identity,
.candidate-live {
  display: grid;
  gap: 3px;
}
.candidate-live {
  justify-items: end;
  text-align: right;
}
.candidate-picker small {
  color: #70869a;
  font-size: var(--mb-font-secondary);
}
.candidate-picker button:hover {
  border-color: #6ca4d1;
  background: #f1f8fe;
}
.candidate-picker b {
  font-size: var(--mb-font-body);
  overflow-wrap: anywhere;
}
.candidate-picker span {
  color: #70869a;
  font-size: var(--mb-font-secondary);
}
.data-section {
  padding: 18px;
  border: 1px solid #dce6ef;
  border-radius: 14px;
  background: #fff;
  overflow: hidden;
  font-size: var(--mb-font-body);
}
.data-section h3 {
  margin: 0 0 12px;
  color: #35536f;
  font-size: var(--mb-font-card-title);
}
.data-section > small {
  display: block;
  margin-top: 10px;
  color: #71869a;
  font-size: var(--mb-font-secondary);
}
.relation-results {
  display: grid;
  gap: 10px;
}
.relation-results h3 {
  margin-bottom: 2px;
}
.relation-results article {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 7px 12px;
  padding: 12px;
  border: 1px solid #d8e5ed;
  border-radius: 10px;
}
.relation-results article > div {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  align-items: center;
}
.relation-results article p {
  grid-column: 1/-1;
  margin: 0;
  color: #667e91;
  font-size: var(--mb-font-secondary);
}
.scope-note {
  margin: 0;
  padding: 9px 11px;
  border-radius: 8px;
  background: #fff6e5;
  color: #7d5c25;
  font-size: var(--mb-font-secondary);
  line-height: 1.55;
}
.policy-note {
  border: 1px solid #ead7ae;
}
.inline-citations {
  grid-column: 1/-1;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin: 0;
  padding: 0;
  list-style: none;
  color: #39779f;
  font-size: var(--mb-font-secondary);
}
.shortage {
  color: #c6534d;
  font-weight: 700;
}
.enough {
  color: #25805f;
}
.technical-details {
  padding: 2px 2px 0;
}
.technical-details summary {
  width: max-content;
  color: #607b91;
  cursor: pointer;
  font-size: var(--mb-font-secondary);
  font-weight: 650;
}
.technical-details dl {
  display: grid;
  gap: 8px;
  margin: 12px 0 0;
  padding: 13px;
  border: 1px solid #dfe7ee;
  border-radius: 10px;
  background: #f8fafc;
}
.technical-details dl > div {
  display: grid;
  grid-template-columns: minmax(130px, 0.8fr) minmax(0, 1.2fr);
  gap: 10px;
}
.technical-details dt {
  color: #62788d;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.technical-details dd {
  margin: 0;
  color: #425b71;
  font-size: 12px;
  overflow-wrap: anywhere;
}
@media (max-width: 600px) {
  .technical-details dl > div {
    grid-template-columns: 1fr;
  }
}
</style>
