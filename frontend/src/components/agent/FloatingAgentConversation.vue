<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Promotion } from '@element-plus/icons-vue'
import { useWarehouseAgent } from '../../composables/useWarehouseAgent'
import { useWarehouseAgentSuggestions } from '../../composables/useWarehouseAgentSuggestions'
import { useAuthStore } from '../../stores/auth'
import { formatQuantity } from '../../utils/format'
import { materialPrimaryIdentity, materialSecondaryIdentity } from '../../utils/materialIdentity'
import { agentProcessLabels, summarizeAgentProcess } from '../../utils/agentProcessSummary'
import type { AgentMaterialEntity, AgentQueryResponse } from '../../types'
import ApprovalCard from './ApprovalCard.vue'
import BuildReadinessCard from './BuildReadinessCard.vue'
import CableResultCard from './CableResultCard.vue'
import ComponentCandidateCard from './ComponentCandidateCard.vue'
import EvidenceResultCard from './EvidenceResultCard.vue'
import EngineeringResearchCard from './EngineeringResearchCard.vue'
import MaterialResultCard from './MaterialResultCard.vue'
import ProductBomPreviewCard from './ProductBomPreviewCard.vue'
import PowerArchitectureOptions from './PowerArchitectureOptions.vue'

const auth = useAuthStore()
const router = useRouter()
const scrollArea = ref<HTMLElement | null>(null)
const {
  message,
  loading,
  conversation,
  proposals,
  proposalBusyId,
  localError,
  loadProposals,
  submit,
  selectCandidate,
  newConversation,
  approve,
  reject,
} = useWarehouseAgent()
const { suggestions, suggestionsLoading, loadSuggestions } = useWarehouseAgentSuggestions()

const degradedActions = [
  ['搜索物料', '/materials'],
  ['查看库存', '/materials'],
  ['查看库位', '/locations'],
  ['低库存', '/materials?stock=low'],
  ['项目/BOM', '/projects'],
  ['产品/单台 BOM', '/products'],
] as const

function materialSummary(response: AgentQueryResponse): AgentMaterialEntity | null {
  const candidates = response.entities.material_candidates
  const selectedId = candidates?.selected_material_id || candidates?.exact_match_ids?.[0]
  const candidate = selectedId
    ? candidates?.items.find((item) => Number(item.id || item.material_id) === Number(selectedId))
    : candidates?.items.length === 1
      ? candidates.items[0]
      : undefined
  const inventory = response.entities.material_detail || response.entities.inventory
  const locations = response.entities.locations
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
}

function hasMaterialAmbiguity(response: AgentQueryResponse) {
  if (
    response.entities.component_search ||
    response.entities.cable_search ||
    response.entities.engineering_evidence ||
    response.entities.component_evidence_comparison
  )
    return false
  const candidates = response.entities.material_candidates
  return Boolean(
    candidates &&
    candidates.items.length > 1 &&
    !candidates.selected_material_id &&
    candidates.exact_match_ids?.length !== 1,
  )
}

function projectVersionAmbiguity(response: AgentQueryResponse) {
  const candidates = response.entities.project_candidates
  const item = candidates?.items?.length === 1 ? candidates.items[0] : null
  const versions = item?.available_versions || []
  if (
    !item ||
    versions.length <= 1 ||
    candidates?.selected_bom_version ||
    !response.answer.includes('BOM_VERSION_REQUIRED')
  )
    return null
  return { item, versions }
}

function hasProjectAmbiguity(response: AgentQueryResponse) {
  const candidates = response.entities.project_candidates
  return Boolean(
    candidates &&
    candidates.items.length > 1 &&
    !candidates.selected_project_id &&
    candidates.exact_match_ids?.length !== 1,
  )
}

function hasProductAmbiguity(response: AgentQueryResponse) {
  const candidates = response.entities.product_candidates
  return Boolean(
    candidates &&
    candidates.items.length > 1 &&
    !candidates.selected_product_id &&
    candidates.exact_match_ids?.length !== 1,
  )
}

function showNarrativeAnswer(response: AgentQueryResponse) {
  const entities = response.entities
  return !(
    entities.low_stock ||
    entities.component_search ||
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
}

function projectBomSummary(response: AgentQueryResponse) {
  return response.entities.bom_analysis || response.entities.project_bom
}

async function scrollToLatest() {
  await nextTick()
  if (scrollArea.value) scrollArea.value.scrollTop = scrollArea.value.scrollHeight
}

async function sendQuestion() {
  if (!message.value.trim() || loading.value) return
  await submit()
  await scrollToLatest()
}

async function chooseQuestion(question: string) {
  message.value = question
  await sendQuestion()
}

async function chooseCandidate(kind: 'material' | 'project' | 'product', label: string) {
  await selectCandidate(kind, label)
  await scrollToLatest()
}

watch(() => [conversation.value.length, loading.value], scrollToLatest)

onMounted(async () => {
  await Promise.all([loadSuggestions(), loadProposals().catch(() => undefined)])
  await scrollToLatest()
})
</script>

<template>
  <div class="floating-conversation" data-testid="floating-agent-conversation">
    <div ref="scrollArea" class="conversation-scroll" data-testid="floating-agent-scroll">
      <div class="conversation-toolbar">
        <button
          type="button"
          :disabled="loading"
          data-testid="floating-new-conversation"
          @click="newConversation"
        >
          ＋ 新对话
        </button>
      </div>
      <section v-if="!conversation.length" class="conversation-welcome">
        <b>你好，我可以帮你查物料。</b>
        <span>问我库存、库位、低库存或项目 BOM。</span>
      </section>

      <nav class="quick-questions" aria-label="真实数据库快捷问题">
        <button
          v-for="suggestion in suggestions"
          :key="`${suggestion.type}:${suggestion.text}`"
          type="button"
          :disabled="loading || suggestionsLoading"
          @click="chooseQuestion(suggestion.text)"
        >
          {{ suggestion.text }}
        </button>
      </nav>

      <article v-for="turn in conversation" :key="turn.id" class="conversation-turn">
        <div class="speaker-label">你</div>
        <div class="user-message">{{ turn.question }}</div>

        <div class="speaker-label assistant">物料大脑</div>
        <div v-if="turn.pending" class="assistant-loading"><i></i>正在核对真实库存和库位…</div>
        <div v-else-if="turn.error" class="assistant-error">{{ turn.error }}</div>
        <template v-else-if="turn.response">
          <div v-if="showNarrativeAnswer(turn.response)" class="assistant-answer">
            {{ turn.response.answer }}
          </div>

          <section
            v-if="
              turn.response.entities.power_design ||
              turn.response.entities.engineering_research ||
              turn.response.entities.product_bom_preview
            "
            class="floating-engineering-structured"
            data-testid="floating-engineering-structured"
          >
            <PowerArchitectureOptions
              v-if="turn.response.entities.power_design"
              :topologies="turn.response.entities.power_design.topologies"
              :rail-bom-draft="turn.response.entities.power_design.rail_bom_draft"
              :load-case-calculations="turn.response.entities.power_design.load_case_calculations"
            />
            <EngineeringResearchCard
              v-if="turn.response.entities.engineering_research"
              :research="turn.response.entities.engineering_research"
            />
            <ProductBomPreviewCard
              v-if="turn.response.entities.product_bom_preview"
              :preview="turn.response.entities.product_bom_preview"
            />
          </section>

          <section
            v-if="turn.response.entities.low_stock"
            class="compact-result compact-low-stock"
            data-testid="floating-low-stock-results"
          >
            <h3>低库存物料（{{ turn.response.entities.low_stock.count }}）</h3>
            <ul>
              <li
                v-for="item in turn.response.entities.low_stock.items.slice(0, 5)"
                :key="item.material_id"
              >
                <span>
                  <b>{{ materialPrimaryIdentity(item) }}</b>
                  <small v-if="materialSecondaryIdentity(item)">{{
                    materialSecondaryIdentity(item)
                  }}</small>
                </span>
                <span>
                  可用 {{ formatQuantity(item.available_quantity) }} / 安全库存
                  {{ formatQuantity(item.safety_stock) }}
                </span>
              </li>
            </ul>
            <p v-if="turn.response.entities.low_stock.count > 5" class="compact-more">
              另有 {{ turn.response.entities.low_stock.count - 5 }} 项，请在完整页面查看。
            </p>
          </section>

          <CableResultCard
            v-if="turn.response.entities.cable_search || turn.response.entities.cable_detail"
            :search="turn.response.entities.cable_search"
            :detail="turn.response.entities.cable_detail"
            compact
            @select="chooseCandidate('material', $event)"
          />

          <section
            v-if="turn.response.entities.component_search"
            class="floating-component-results"
            data-testid="floating-component-candidates"
          >
            <header>
              <b>
                {{
                  turn.response.entities.component_search.query.replacement_intent
                    ? '候选相似器件'
                    : '工程候选器件'
                }}
              </b>
              <span>{{ turn.response.entities.component_search.count }} 个</span>
            </header>
            <ComponentCandidateCard
              v-for="candidate in turn.response.entities.component_search.candidates"
              :key="candidate.material_id"
              :candidate="candidate"
              compact
              :selectable="turn.response.entities.component_search.candidates.length > 1"
              @select="chooseCandidate('material', $event)"
            />
          </section>

          <EvidenceResultCard
            v-if="
              turn.response.entities.engineering_evidence ||
              turn.response.entities.component_evidence_comparison
            "
            :evidence="turn.response.entities.engineering_evidence"
            :comparison="turn.response.entities.component_evidence_comparison"
            compact
          />

          <section
            v-if="hasMaterialAmbiguity(turn.response)"
            class="candidate-picker"
            data-testid="floating-material-candidates"
          >
            <b>请选择具体物料</b>
            <button
              v-for="item in turn.response.entities.material_candidates?.items"
              :key="item.id"
              type="button"
              :disabled="loading"
              @click="chooseCandidate('material', item.mpn || item.code)"
            >
              <span>
                <b>{{ materialPrimaryIdentity(item) }}</b>
                <small v-if="materialSecondaryIdentity(item)">{{
                  materialSecondaryIdentity(item)
                }}</small> </span
              ><small>{{ item.package || '未标封装' }}</small>
            </button>
          </section>

          <section
            v-if="projectVersionAmbiguity(turn.response)"
            class="candidate-picker"
            data-testid="floating-project-bom-version-picker"
          >
            <b>{{ projectVersionAmbiguity(turn.response)!.item.name }} 有多个 BOM 版本，请选择</b>
            <button
              v-for="version in projectVersionAmbiguity(turn.response)!.versions"
              :key="version"
              type="button"
              :disabled="loading"
              @click="chooseCandidate('project', version)"
            >
              <span>{{ version }}</span
              ><small>继续刚才的 BOM / 库存查询</small>
            </button>
          </section>

          <section
            v-if="hasProjectAmbiguity(turn.response)"
            class="candidate-picker"
            data-testid="floating-project-candidates"
          >
            <b>请选择具体项目</b>
            <button
              v-for="item in turn.response.entities.project_candidates?.items"
              :key="item.id"
              type="button"
              :disabled="loading"
              @click="chooseCandidate('project', item.code)"
            >
              <span>{{ item.name }}</span
              ><small>{{ item.code }}</small>
            </button>
          </section>

          <section
            v-if="hasProductAmbiguity(turn.response)"
            class="candidate-picker"
            data-testid="floating-product-candidates"
          >
            <b>请选择具体产品</b>
            <button
              v-for="item in turn.response.entities.product_candidates?.items"
              :key="item.id"
              type="button"
              :disabled="loading"
              @click="chooseCandidate('product', item.code)"
            >
              <span>{{ item.name }}</span
              ><small>{{ item.code }}</small>
            </button>
          </section>

          <BuildReadinessCard
            v-if="turn.response.entities.build_readiness"
            :result="turn.response.entities.build_readiness"
            compact
          />

          <section
            v-if="projectBomSummary(turn.response)"
            class="compact-result compact-project-bom"
            data-testid="floating-project-bom"
          >
            <h3>
              {{ projectBomSummary(turn.response)!.project.name }} ·
              {{ projectBomSummary(turn.response)!.version || '当前版本' }} BOM
            </h3>
            <p class="bom-status">
              <template v-if="turn.response.entities.bom_analysis">
                {{
                  turn.response.entities.bom_analysis.sufficient
                    ? '当前齐套'
                    : `缺料 ${turn.response.entities.bom_analysis.shortage_count} 项`
                }}
              </template>
              <template v-else>共 {{ projectBomSummary(turn.response)!.items.length }} 项</template>
            </p>
            <ul>
              <li v-for="item in projectBomSummary(turn.response)!.items" :key="item.material_id">
                <div>
                  <b>{{ item.code }}</b>
                  <span
                    >需求 {{ formatQuantity(item.required_quantity) }} / 可用
                    {{ formatQuantity(item.available_quantity) }}</span
                  >
                  <strong v-if="Number(item.shortage || 0) > 0"
                    >缺口 {{ formatQuantity(item.shortage) }}</strong
                  >
                </div>
                <small v-for="location in item.locations || []" :key="location.location_id">
                  {{ location.full_path }}
                </small>
              </li>
            </ul>
            <small
              v-if="projectBomSummary(turn.response)!.quantity_semantics"
              class="bom-semantics"
              data-testid="floating-bom-semantics"
            >
              物理库存库位：{{ projectBomSummary(turn.response)!.quantity_semantics }}
            </small>
          </section>

          <MaterialResultCard
            v-if="
              materialSummary(turn.response) &&
              !turn.response.entities.cable_search &&
              !turn.response.entities.cable_detail &&
              !turn.response.entities.component_search &&
              !turn.response.entities.component_relations &&
              !turn.response.entities.product_bom_alternates &&
              !turn.response.entities.engineering_evidence &&
              !turn.response.entities.component_evidence_comparison &&
              !turn.response.entities.build_readiness &&
              !turn.response.entities.product_bom &&
              !turn.response.entities.product_bom_preview &&
              !turn.response.entities.project_bom &&
              !turn.response.entities.bom_analysis &&
              !turn.response.entities.low_stock
            "
            :material="materialSummary(turn.response)!"
            compact
          />

          <section
            v-if="turn.response.entities.component_relations"
            class="compact-result relation-result"
            data-testid="floating-component-relations"
          >
            <h3>工程关系</h3>
            <article
              v-for="relation in turn.response.entities.component_relations.items"
              :key="relation.id"
            >
              <b>{{ relation.source_material.mpn || relation.source_material.code }}</b>
              <span>↔ {{ relation.target_material.mpn || relation.target_material.code }}</span>
              <small>{{ relation.status === 'validated' ? '已验证关系' : '待审核关系' }}</small>
            </article>
          </section>

          <section
            v-if="turn.response.entities.product_bom_alternates"
            class="compact-result relation-result"
            data-testid="floating-product-alternates"
          >
            <h3>产品 BOM 位备选</h3>
            <p v-if="turn.response.entities.product_bom_alternates.scope_required">
              请先说明具体产品、版本或 BOM 位。
            </p>
            <article
              v-for="alternate in turn.response.entities.product_bom_alternates.items"
              :key="alternate.id"
            >
              <b>{{ alternate.product.code }} · {{ alternate.revision.revision }}</b>
              <span>
                {{ alternate.primary_material.mpn || alternate.primary_material.code }} →
                {{ alternate.alternate_material.mpn || alternate.alternate_material.code }}
              </span>
              <small>{{ alternate.status === 'approved' ? '此 BOM 位已批准' : '尚未批准' }}</small>
            </article>
          </section>

          <div v-if="turn.response.tool_events.length" class="process-confirmation">
            ✓ {{ summarizeAgentProcess(turn.response.tool_events) }}
          </div>
          <details v-if="turn.response.tool_events.length" class="turn-process">
            <summary>查看过程</summary>
            <ul>
              <li
                v-for="(event, index) in turn.response.tool_events"
                :key="`${event.tool}-${index}`"
              >
                {{ agentProcessLabels[event.tool] || '核对相关信息' }}
              </li>
            </ul>
          </details>
          <details class="turn-technical">
            <summary>技术详情</summary>
            <div>请求编号：{{ turn.response.request_id }}</div>
            <div>execution_mode: {{ turn.response.execution_mode }}</div>
            <div>model_call_count: {{ turn.response.model_call_count }}</div>
            <div
              v-for="(call, index) in turn.response.telemetry"
              v-show="turn.response.model_call_count > 0"
              :key="`floating-model-call-${index}`"
            >
              Provider: {{ call.provider }} · Model: {{ call.model }} · Input tokens:
              {{ call.input_tokens }} · Output tokens: {{ call.output_tokens }}
            </div>
            <div
              v-for="(event, index) in turn.response.tool_events"
              :key="`${event.tool}-technical-${index}`"
            >
              {{ event.tool }} · {{ event.duration_ms }} ms
            </div>
          </details>
        </template>
      </article>

      <nav
        v-if="localError.includes('AI 免费额度')"
        class="degraded-actions"
        aria-label="无需智能助手的仓库功能"
      >
        <el-button
          v-for="action in degradedActions"
          :key="action[0]"
          size="small"
          plain
          @click="router.push(action[1])"
          >{{ action[0] }}</el-button
        >
      </nav>

      <section v-if="proposals.length" class="conversation-proposals">
        <h3>库存变更确认</h3>
        <ApprovalCard
          v-for="proposal in proposals"
          :key="proposal.id"
          :proposal="proposal"
          :can-approve="auth.can('inventory:operate')"
          :busy="proposalBusyId === proposal.id"
          compact
          @approve="approve"
          @reject="reject"
        />
      </section>
    </div>

    <form
      class="conversation-composer"
      data-testid="floating-agent-composer"
      @submit.prevent="sendQuestion"
    >
      <textarea
        v-model="message"
        rows="1"
        maxlength="4000"
        placeholder="输入问题…"
        aria-label="输入问题"
        data-testid="agent-query-input"
        @keydown.enter.exact.prevent="sendQuestion"
      ></textarea>
      <button
        type="submit"
        :disabled="loading || !message.trim()"
        aria-label="发送问题"
        data-testid="agent-submit"
      >
        <el-icon><Promotion /></el-icon>
      </button>
    </form>
  </div>
</template>

<style scoped>
.floating-conversation {
  display: grid;
  min-height: 0;
  grid-template-rows: minmax(0, 1fr) auto;
  background: #f4f7fa;
}
.conversation-scroll {
  min-height: 0;
  padding: 16px;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}
.conversation-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 9px;
}
.conversation-toolbar button {
  min-height: 44px;
  padding: 7px 11px;
  border: 1px solid #cfdeea;
  border-radius: 10px;
  background: #fff;
  color: #39739f;
  cursor: pointer;
  font-size: var(--mb-font-secondary);
  font-weight: 650;
}
.conversation-toolbar button:disabled {
  cursor: wait;
  opacity: 0.5;
}
.conversation-welcome {
  display: grid;
  gap: 5px;
  padding: 14px;
  border: 1px solid #dbe6ef;
  border-radius: 14px;
  background: #fff;
  color: #274965;
}
.conversation-welcome b {
  font-size: var(--mb-font-card-title);
}
.conversation-welcome span {
  color: #6c8296;
  font-size: var(--mb-font-secondary);
}
.quick-questions {
  display: flex;
  gap: 8px;
  margin: 12px -2px 4px;
  padding: 2px;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: thin;
}
.quick-questions button {
  flex: 0 0 auto;
  min-height: 36px;
  max-width: 270px;
  padding: 7px 11px;
  border: 1px solid #cfdeea;
  border-radius: 999px;
  background: #fff;
  color: #416783;
  cursor: pointer;
  font-size: var(--mb-font-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.quick-questions button:disabled {
  cursor: wait;
  opacity: 0.55;
}
.conversation-turn {
  display: grid;
  margin-top: 18px;
}
.speaker-label {
  margin-bottom: 5px;
  color: #70869a;
  font-size: 12px;
  font-weight: 700;
}
.speaker-label.assistant {
  margin-top: 15px;
  color: #286b9f;
}
.user-message {
  justify-self: end;
  max-width: 88%;
  padding: 10px 13px;
  border-radius: 14px 14px 4px;
  background: #2f7ebd;
  color: #fff;
  font-size: var(--mb-font-body);
  line-height: 1.55;
  overflow-wrap: anywhere;
}
.assistant-answer,
.assistant-loading,
.assistant-error {
  padding: 12px 14px;
  border: 1px solid #dbe6ef;
  border-radius: 4px 14px 14px;
  background: #fff;
  color: #294c67;
  font-size: var(--mb-font-body);
  line-height: 1.65;
  white-space: pre-wrap;
}
.assistant-loading {
  display: flex;
  align-items: center;
  gap: 9px;
  color: #58768f;
}
.assistant-loading i {
  width: 16px;
  height: 16px;
  border: 2px solid #b7d2e8;
  border-top-color: #347fc0;
  border-radius: 50%;
  animation: assistant-spin 0.8s linear infinite;
}
.assistant-error {
  border-color: #efc8c8;
  background: #fff5f5;
  color: #a04646;
}
.candidate-picker {
  display: grid;
  gap: 7px;
  margin-top: 9px;
  padding: 11px;
  border: 1px solid #d8e5ee;
  border-radius: 12px;
  background: #fff;
}
.floating-component-results {
  display: grid;
  gap: 9px;
  margin-top: 9px;
}
.floating-component-results > header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  color: #315b79;
}
.floating-component-results > header b {
  font-size: var(--mb-font-body);
}
.floating-component-results > header span,
.floating-component-results > p {
  color: #6d8294;
  font-size: var(--mb-font-secondary);
}
.floating-component-results > p {
  margin: 0;
  line-height: 1.5;
}
.candidate-picker > b {
  color: #315b79;
  font-size: var(--mb-font-body);
}
.candidate-picker button {
  display: flex;
  min-height: 44px;
  align-items: center;
  justify-content: space-between;
  gap: 9px;
  padding: 8px 10px;
  border: 1px solid #cfdeea;
  border-radius: 9px;
  background: #fafdff;
  color: #315b79;
  cursor: pointer;
  text-align: left;
}
.candidate-picker button:disabled {
  cursor: wait;
  opacity: 0.55;
}
.candidate-picker span {
  font-weight: 650;
  overflow-wrap: anywhere;
}
.candidate-picker small {
  color: #70869a;
}
.compact-result {
  margin-top: 9px;
  padding: 13px;
  border: 1px solid #d8e5ee;
  border-radius: 12px;
  background: #fff;
}
.compact-result h3 {
  margin: 0;
  color: #1f4968;
  font-size: var(--mb-font-card-title);
  overflow-wrap: anywhere;
}
.compact-low-stock ul {
  display: grid;
  gap: 7px;
  margin: 11px 0 0;
  padding: 0;
  list-style: none;
}
.compact-low-stock li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  padding: 9px;
  border-radius: 9px;
  background: #f4f7fa;
  color: #31566f;
  font-size: var(--mb-font-secondary);
}
.compact-low-stock li span:first-child {
  font-weight: 700;
  overflow-wrap: anywhere;
}
.compact-low-stock li span:last-child {
  color: #7e5d28;
  text-align: right;
}
.compact-project-bom .bom-status {
  margin: 8px 0 0;
  color: #7e5d28;
  font-weight: 700;
}
.compact-project-bom ul {
  display: grid;
  gap: 7px;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
}
.compact-project-bom li {
  display: grid;
  gap: 5px;
  padding: 9px;
  border-radius: 9px;
  background: #f4f7fa;
}
.compact-project-bom li > div {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 10px;
  color: #31566f;
  font-size: var(--mb-font-secondary);
}
.compact-project-bom strong {
  color: #a24c3f;
}
.compact-project-bom small {
  color: #306d99;
  overflow-wrap: anywhere;
}
.compact-project-bom .bom-semantics {
  display: block;
  margin-top: 10px;
  color: #6d8294;
  line-height: 1.5;
}
.compact-more {
  margin: 9px 0 0;
  color: #6d8294;
  font-size: var(--mb-font-secondary);
}
.compact-result dl {
  display: grid;
  grid-template-columns: 0.75fr 1.25fr;
  gap: 8px;
  margin: 12px 0 0;
}
.compact-result dl > div {
  display: grid;
  gap: 4px;
  padding: 9px;
  border-radius: 9px;
  background: #f4f7fa;
}
.compact-result dt {
  color: #6e8192;
  font-size: 12px;
}
.compact-result dd {
  margin: 0;
  color: #244c68;
  font-size: var(--mb-font-body);
  font-weight: 700;
  overflow-wrap: anywhere;
}
.compact-result dl > div:first-child dd {
  color: #176d53;
  font-size: 22px;
}
.relation-result {
  display: grid;
  gap: 9px;
}
.relation-result article {
  display: grid;
  gap: 4px;
  padding: 10px;
  border-radius: 9px;
  background: #f4f7fa;
  color: #31566f;
}
.relation-result article small {
  color: #28775d;
}
.compact-warning {
  margin: 10px 0 0;
  padding: 9px;
  border-radius: 8px;
  background: #fff6e5;
  color: #8a6225;
  font-size: var(--mb-font-secondary);
  line-height: 1.5;
}
.compact-warning.danger {
  background: #fff0f0;
  color: #984242;
}
.compact-actions {
  display: flex;
  gap: 8px;
  margin-top: 11px;
}
.compact-actions :deep(.el-button) {
  margin: 0;
}
.process-confirmation {
  margin-top: 10px;
  color: #28775d;
  font-size: var(--mb-font-secondary);
  font-weight: 650;
}
.turn-process,
.turn-technical {
  margin-top: 8px;
  color: #547086;
  font-size: 12px;
}
.turn-process summary,
.turn-technical summary {
  width: max-content;
  cursor: pointer;
  color: #39739f;
  font-size: var(--mb-font-secondary);
  font-weight: 650;
}
.turn-process ul {
  display: grid;
  gap: 5px;
  margin: 7px 0 0;
  padding-left: 20px;
}
.turn-technical > div {
  margin-top: 5px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  overflow-wrap: anywhere;
}
.degraded-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
  padding: 10px;
  border: 1px solid #f1d6a4;
  border-radius: 12px;
  background: #fffaf0;
}
.degraded-actions :deep(.el-button) {
  margin: 0;
}
.conversation-proposals {
  display: grid;
  gap: 10px;
  margin-top: 16px;
}
.conversation-proposals h3 {
  margin: 0;
  color: #69532f;
  font-size: var(--mb-font-card-title);
}
.conversation-composer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 44px;
  align-items: end;
  gap: 8px;
  padding: 11px 12px max(11px, env(safe-area-inset-bottom));
  border-top: 1px solid #d7e2eb;
  background: #fff;
}
.conversation-composer textarea {
  width: 100%;
  max-height: 112px;
  min-height: 44px;
  padding: 11px 12px;
  border: 1px solid #cbdbe7;
  border-radius: 12px;
  outline: 0;
  color: #29465e;
  font: inherit;
  font-size: var(--mb-font-body);
  line-height: 1.4;
  resize: none;
}
.conversation-composer textarea:focus {
  border-color: #4a94ce;
  box-shadow: 0 0 0 3px #4a94ce1c;
}
.conversation-composer button {
  display: grid;
  width: 44px;
  height: 44px;
  place-items: center;
  border: 0;
  border-radius: 12px;
  background: #337fbd;
  color: #fff;
  cursor: pointer;
  font-size: 20px;
}
.conversation-composer button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
@keyframes assistant-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .assistant-loading i {
    animation-duration: 0.01ms;
  }
}
@media (max-width: 480px) {
  .conversation-scroll {
    padding: 13px;
  }
  .compact-result dl {
    grid-template-columns: 1fr;
  }
  .user-message {
    max-width: 94%;
  }
}
</style>
