<script setup lang="ts">
import { computed } from 'vue'
import type {
  AgentPowerArchitecture,
  AgentPowerLoadCaseCalculation,
  AgentPowerRailBomDraft,
} from '../../types'
import { formatQuantity } from '../../utils/format'

const props = defineProps<{
  topologies?: AgentPowerArchitecture[]
  railBomDraft?: AgentPowerRailBomDraft
  loadCaseCalculations?: AgentPowerLoadCaseCalculation[]
}>()

const visibleStageNotes = computed(() => {
  const seen = new Set<string>()
  const notesByStage: Record<string, string[]> = {}
  for (const architecture of props.topologies || []) {
    for (const stage of architecture.stages) {
      notesByStage[stage.stage_id] = stage.notes.filter((note) => {
        const normalized = note.trim()
        if (!normalized || seen.has(normalized)) return false
        seen.add(normalized)
        return true
      })
    }
  }
  return notesByStage
})

function currentText(value?: string | null, basis?: string) {
  if (value === null || value === undefined) {
    return basis === 'not_allocated' ? '待分配' : '未提供'
  }
  const milliamps = Number(value) * 1000
  return Number.isFinite(milliamps) ? `${formatQuantity(milliamps)}mA` : `${value}A`
}

function availableText(value: unknown) {
  if (value === null || value === undefined || value === '') return '本轮库存未读取'
  return formatQuantity(value as string | number)
}

function topologyLabel(value: string) {
  return value === 'buck' ? 'Buck' : value === 'ldo' ? 'LDO' : '滤波'
}

function percentText(value?: string | null) {
  if (value === null || value === undefined) return '未知'
  const percent = Number(value) * 100
  return Number.isFinite(percent) ? `${formatQuantity(percent)}%（理想值）` : '未知'
}

function engineeringValue(parameter: Record<string, unknown>) {
  return `${parameter.value ?? '未知'} ${parameter.unit ?? ''}`.trim()
}

function statusLabel(value?: string | null) {
  const labels: Record<string, string> = {
    needs_input: '待补输入',
    needs_selection: '待选型',
    candidate_found: '已有候选',
    selected: '已选择',
    no_grounded_candidate: '无受控候选',
    not_applicable: '不适用',
    out_of_stock: '可核对但可用量为 0',
    needs_confirmation: '待人工确认',
    not_supported: '拓扑不可建立',
    blocked_by_input: '缺少必要输入',
    complete_draft: '草案已齐备（仍只读）',
  }
  return labels[value || ''] || '待确认'
}

function statusType(value?: string | null) {
  if (value === 'candidate_found' || value === 'selected' || value === 'complete_draft')
    return 'success'
  if (value === 'out_of_stock' || value === 'no_grounded_candidate') return 'warning'
  if (value === 'not_applicable') return 'info'
  return 'warning'
}

function matchLabel(value?: string | null) {
  const labels: Record<string, string> = {
    exact: '精确匹配',
    compatible: '兼容匹配',
    partial: '信息不全',
    mismatch: '明确不匹配',
  }
  return labels[value || ''] || '匹配待确认'
}

function constraintText(constraint: {
  key: string
  operator: string
  value: unknown
  unit?: string | null
}) {
  const operator =
    { eq: '=', gte: '≥', lte: '≤', range: '范围', one_of: '可选', preferred: '偏好' }[
      constraint.operator
    ] || constraint.operator
  return `${constraint.key} ${operator} ${String(constraint.value)}${constraint.unit || ''}`
}

function locationText(candidate: {
  locations?: string[]
  location_facts?: Array<Record<string, unknown>>
}) {
  if (candidate.locations?.length) return candidate.locations.join('；')
  return (candidate.location_facts || [])
    .map((item: Record<string, unknown>) => item.full_path || item.code)
    .filter(Boolean)
    .join('；')
}

function inventoryStatusLabel(value?: string | null) {
  if (value === 'in_stock') return '在库事实'
  if (value === 'out_of_stock') return '可用量为 0'
  if (value === 'not_checked') return '尚未读取库存'
  return '库存未知'
}
</script>

<template>
  <section
    v-if="topologies?.length"
    class="power-architectures"
    data-testid="power-architecture-options"
  >
    <section
      v-if="loadCaseCalculations?.length"
      class="load-case-comparison"
      data-testid="power-load-case-comparison"
    >
      <h4>负载点损耗比较 · 服务端确定性结果</h4>
      <div v-for="item in loadCaseCalculations" :key="item.load_current_a" class="load-case-row">
        <b>负载 {{ currentText(item.load_current_a, 'user_total') }}</b>
        <span>
          直接 LDO {{ item.direct_ldo_input_v }}V → {{ item.output_voltage_v }}V：
          {{ formatQuantity(item.direct_ldo_loss_w) }}W
        </span>
        <span v-if="item.post_buck_ldo_loss_w">
          Buck 后级 LDO {{ item.post_buck_intermediate_voltage_v }}V →
          {{ item.output_voltage_v }}V： {{ formatQuantity(item.post_buck_ldo_loss_w) }}W
        </span>
      </div>
    </section>
    <div class="architecture-heading">
      <h4>电源架构比较</h4>
      <span>服务端计算 · 只读</span>
    </div>
    <div class="architecture-grid">
      <article
        v-for="architecture in topologies"
        :key="architecture.topology"
        class="architecture-card"
        :data-topology="architecture.topology"
      >
        <header>
          <b>{{ architecture.label }}</b>
          <el-tag v-if="architecture.selected_by_user" size="small" type="success">
            本轮选择
          </el-tag>
        </header>
        <p class="architecture-summary">{{ architecture.summary }}</p>

        <div v-if="architecture.rails.length" class="architecture-rails">
          <div v-for="rail in architecture.rails" :key="rail.rail_id" class="architecture-rail">
            <b>{{ rail.label }}</b>
            <span
              >{{ rail.voltage_v ?? '未知' }}V ·
              {{ currentText(rail.load_current_a, rail.current_basis) }}</span
            >
          </div>
        </div>

        <div class="architecture-stages">
          <div
            v-for="stage in architecture.stages"
            :key="stage.stage_id"
            class="architecture-stage"
          >
            <div class="stage-title">
              <b>{{ topologyLabel(stage.topology) }}</b>
              <span
                >{{ stage.input_voltage_v ?? '未知' }}V →
                {{ stage.output_voltage_v ?? '未知' }}V</span
              >
            </div>
            <div class="stage-facts">
              <span>负载 {{ currentText(stage.load_current_a, stage.current_basis) }}</span>
              <span v-if="stage.loss_w !== null && stage.loss_w !== undefined">
                LDO 损耗 {{ formatQuantity(stage.loss_w) }}W
              </span>
              <span v-if="stage.ideal_efficiency !== null && stage.ideal_efficiency !== undefined">
                理想效率 {{ percentText(stage.ideal_efficiency) }}
              </span>
              <span
                v-if="stage.quiescent_current_a !== null && stage.quiescent_current_a !== undefined"
              >
                静态电流典型 {{ formatQuantity(Number(stage.quiescent_current_a) * 1000000) }}µA
              </span>
              <span v-if="stage.headroom_v !== null && stage.headroom_v !== undefined">
                压差 {{ formatQuantity(stage.headroom_v) }}V · dropout 需按负载核对
              </span>
              <span v-else-if="stage.dropout_status === 'verify_at_load'"
                >dropout 需按负载核对</span
              >
              <span
                v-if="stage.thermal_screen?.estimated_delta_t_c !== undefined"
                class="thermal-screen"
              >
                SOT-223 一阶温升筛查
                {{ formatQuantity(stage.thermal_screen.estimated_delta_t_c as string | number) }}°C
              </span>
            </div>
            <div v-if="stage.candidate_devices.length" class="architecture-candidates">
              <div v-for="candidate in stage.candidate_devices" :key="candidate.material_id">
                <b>{{ candidate.mpn || candidate.code }}</b>
                <span>{{ candidate.code }} · {{ candidate.package || '未标封装' }}</span>
                <span
                  >可用 {{ availableText(candidate.inventory?.available_quantity) }}
                  {{ candidate.inventory?.unit || '' }}</span
                >
                <span v-if="candidate.locations?.length"
                  >库位 {{ candidate.locations.join('；') }}</span
                >
                <details
                  v-if="
                    candidate.peripheral_roles?.length ||
                    candidate.engineering_parameters?.length ||
                    candidate.citations?.length
                  "
                >
                  <summary>器件参数、外围角色与证据</summary>
                  <ul v-if="candidate.engineering_parameters?.length">
                    <li
                      v-for="parameter in candidate.engineering_parameters"
                      :key="String(parameter.key)"
                    >
                      <b>{{ parameter.label }}：{{ engineeringValue(parameter) }}</b>
                      <span v-if="parameter.conditions"> · {{ parameter.conditions }}</span>
                      <a
                        v-if="parameter.source_url"
                        :href="String(parameter.source_url)"
                        target="_blank"
                        rel="noopener noreferrer"
                        >资料 p.{{ parameter.source_page }}</a
                      >
                    </li>
                  </ul>
                  <ul v-if="candidate.peripheral_roles?.length">
                    <li v-for="role in candidate.peripheral_roles" :key="role.role">
                      {{ role.role }}：{{ role.exact_value || '按所选器件资料确认' }}
                    </li>
                  </ul>
                  <ul v-if="candidate.citations?.length">
                    <li
                      v-for="citation in candidate.citations"
                      :key="String(citation.anchor_id || citation.source_url || candidate.code)"
                    >
                      {{ citation.document_title || citation.source_title || candidate.mpn }}
                      {{ citation.document_revision || '' }}
                      <span v-if="citation.page || citation.physical_page">
                        · p.{{ citation.page || citation.physical_page }}
                      </span>
                    </li>
                  </ul>
                </details>
              </div>
            </div>
            <ul v-if="visibleStageNotes[stage.stage_id]?.length" class="stage-notes">
              <li v-for="note in visibleStageNotes[stage.stage_id]" :key="note">{{ note }}</li>
            </ul>
          </div>
        </div>
        <ul v-if="architecture.constraints.length" class="architecture-constraints">
          <li v-for="constraint in architecture.constraints" :key="constraint">{{ constraint }}</li>
        </ul>
      </article>
    </div>

    <section v-if="railBomDraft" class="rail-bom-draft" data-testid="power-rail-bom-draft">
      <header>
        <div>
          <h4>多轨工程 BOM 草案</h4>
          <small>
            {{ railBomDraft.selected_topology || '尚未选择架构' }} · {{ railBomDraft.status }} ·
            只读，不写入正式 BOM
          </small>
        </div>
        <el-tag size="small" :type="statusType(railBomDraft.status)">
          {{ statusLabel(railBomDraft.status) }}
        </el-tag>
      </header>
      <div
        v-if="railBomDraft.completeness"
        class="bom-completeness"
        data-testid="power-bom-completeness"
      >
        <b>Completeness</b>
        <span
          >要求 {{ railBomDraft.completeness.required_roles }} · 已有候选
          {{ railBomDraft.completeness.candidate_covered_roles }} · 已选择
          {{ railBomDraft.completeness.selected_roles }} · 未解决
          {{ railBomDraft.completeness.unresolved_roles }} · 待输入
          {{ railBomDraft.completeness.needs_input_roles }} · 可用量为 0
          {{ railBomDraft.completeness.out_of_stock_roles }}</span
        >
        <strong>{{
          railBomDraft.completeness.complete_for_review ? '可进入评审' : '尚不可评审'
        }}</strong>
        <ul v-if="railBomDraft.completeness.blocking_reasons.length">
          <li v-for="reason in railBomDraft.completeness.blocking_reasons" :key="reason">
            {{ reason }}
          </li>
        </ul>
      </div>
      <p v-if="!railBomDraft.rails.length" class="no-rail-draft">
        选择直接 Buck、Buck+LDO 或分轨方案后生成对应草案。
      </p>
      <div v-for="rail in railBomDraft.rails" :key="rail.rail_id" class="rail-bom-row">
        <div class="rail-bom-rail-header">
          <b
            >{{ rail.label }} · {{ rail.voltage_v ?? '未知' }}V ·
            {{ currentText(rail.load_current_a, rail.current_basis) }}</b
          >
          <el-tag size="small" :type="statusType(rail.selection_status)">
            {{ statusLabel(rail.selection_status) }}
          </el-tag>
        </div>
        <div v-for="stage in rail.stages" :key="stage.stage_id" class="rail-bom-stage">
          <div class="rail-bom-stage-heading">
            <b>
              {{ topologyLabel(stage.topology) }} {{ stage.input_voltage_v ?? '未知' }}V →
              {{ stage.output_voltage_v ?? '未知' }}V
              <span v-if="stage.loss_w !== null && stage.loss_w !== undefined">
                · 损耗 {{ formatQuantity(stage.loss_w) }}W
              </span>
            </b>
            <el-tag size="small" :type="statusType(stage.selection_status)">
              {{ statusLabel(stage.selection_status) }}
            </el-tag>
          </div>
          <div v-if="stage.completeness" class="stage-completeness">
            要求 {{ stage.completeness.required_roles }} · 已知约束
            {{ stage.completeness.grounded_roles }} · 候选
            {{ stage.completeness.candidate_covered_roles }} · 已选
            {{ stage.completeness.selected_roles }} · 待输入
            {{ stage.completeness.needs_input_roles }}
          </div>
          <div v-if="stage.engineering_facts?.length" class="rail-bom-facts">
            <b>工程事实</b>
            <span v-for="fact in stage.engineering_facts" :key="String(fact.fact_id)">
              {{ fact.mpn }} · {{ fact.role }}：{{ fact.value }}
              <small v-if="fact.source_page"> · 资料 p.{{ fact.source_page }}</small>
            </span>
          </div>
          <div v-if="stage.bom_requirements?.length" class="rail-bom-requirements">
            <b class="rail-bom-requirements-title">BOM Requirements</b>
            <article
              v-for="requirement in stage.bom_requirements"
              :key="requirement.requirement_id"
              class="rail-bom-requirement"
            >
              <div class="rail-bom-requirement-heading">
                <span>{{ requirement.role }}</span>
                <el-tag size="small" :type="statusType(requirement.status)">
                  {{ statusLabel(requirement.status) }}
                </el-tag>
              </div>
              <small v-if="requirement.exact_value" class="rail-bom-evidence-fact">
                证据要求：{{ requirement.exact_value }} · {{ requirement.evidence_status }}
              </small>
              <div v-if="requirement.constraints?.length" class="rail-bom-constraints">
                <span
                  v-for="constraint in requirement.constraints"
                  :key="`${requirement.requirement_id}-${constraint.key}`"
                >
                  {{ constraintText(constraint) }}
                </span>
              </div>
              <div v-if="requirement.selected_material_id" class="selected-draft-fact">
                已选草案物料 #{{ requirement.selected_material_id }} ·
                {{ requirement.selection_basis || '显式选择' }} · 未写入正式 BOM
              </div>
              <div v-if="requirement.selection_conflict" class="selection-conflict">
                {{ requirement.selection_conflict }}
              </div>
              <div v-if="requirement.candidates?.length" class="rail-bom-candidates">
                <div
                  v-for="candidate in requirement.candidates"
                  :key="`${requirement.requirement_id}-${candidate.material_id}`"
                  class="rail-bom-candidate"
                >
                  <b>{{ candidate.mpn || candidate.code }}</b>
                  <span>{{ candidate.code }} · {{ candidate.package || '未标封装' }}</span>
                  <span
                    >{{ inventoryStatusLabel(candidate.inventory_status) }} · 可用
                    {{ availableText(candidate.inventory?.available_quantity) }}
                    {{ candidate.inventory?.unit || '' }}</span
                  >
                  <span v-if="locationText(candidate)">库位 {{ locationText(candidate) }}</span>
                  <span v-if="candidate.match_status" class="candidate-match">
                    {{ matchLabel(candidate.match_status) }}
                  </span>
                  <span
                    v-for="reason in candidate.match_reasons || []"
                    :key="reason"
                    class="candidate-match-reason"
                  >
                    {{ reason }}
                  </span>
                  <span
                    v-for="unknown in candidate.match_unknowns || []"
                    :key="unknown"
                    class="candidate-match-unknown"
                  >
                    {{ unknown }}
                  </span>
                </div>
              </div>
              <span v-else class="rail-bom-requirement-note">
                {{ requirement.notes?.[0] || statusLabel(requirement.status) }}
              </span>
              <details v-if="requirement.peripheral_roles?.length || requirement.citations?.length">
                <summary>外围证据与引用</summary>
                <ul>
                  <li v-for="role in requirement.peripheral_roles" :key="String(role.role)">
                    {{ role.mpn || '' }} {{ role.role }}：
                    {{ role.exact_value || role.constraint_value || '按器件资料确认' }}
                  </li>
                  <li
                    v-for="citation in requirement.citations"
                    :key="String(citation.anchor_id || citation.source_page || citation.role)"
                  >
                    {{ citation.source_document_revision || citation.source_title || '工程证据' }}
                    <span v-if="citation.source_page"> · p.{{ citation.source_page }}</span>
                  </li>
                </ul>
              </details>
            </article>
          </div>
        </div>
      </div>
      <ul v-if="railBomDraft.manual_review.length" class="rail-bom-review">
        <li v-for="item in railBomDraft.manual_review" :key="item">{{ item }}</li>
      </ul>
    </section>
  </section>
</template>

<style scoped>
.power-architectures {
  display: grid;
  gap: 10px;
  min-width: 0;
}
.load-case-comparison {
  display: grid;
  gap: 7px;
  min-width: 0;
  padding: 10px;
  border: 1px solid #d8e5ed;
  border-radius: 10px;
  background: #f8fcfe;
}
.load-case-comparison h4 {
  margin: 0;
  color: #31566f;
  font-size: var(--mb-font-secondary);
}
.load-case-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  min-width: 0;
  color: #667e91;
  font-size: var(--mb-font-secondary);
  line-height: 1.45;
}
.load-case-row b {
  color: #486276;
}
.architecture-heading,
.architecture-card > header,
.rail-bom-draft > header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
}
.architecture-heading h4,
.rail-bom-draft h4 {
  margin: 0;
  color: #31566f;
}
.architecture-heading > span,
.rail-bom-draft small,
.no-rail-draft {
  color: #6b8092;
  font-size: var(--mb-font-secondary);
}
.architecture-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr));
  gap: 9px;
}
.architecture-card,
.rail-bom-draft {
  display: grid;
  gap: 8px;
  min-width: 0;
  padding: 11px;
  border: 1px solid #d8e5ed;
  border-radius: 10px;
  background: #fff;
}
.architecture-card > header b,
.architecture-summary,
.architecture-rail,
.architecture-stage,
.architecture-constraints,
.rail-bom-row,
.rail-bom-stage,
.rail-bom-review {
  overflow-wrap: anywhere;
}
.architecture-summary {
  margin: 0;
  color: #5f7588;
  font-size: var(--mb-font-secondary);
  line-height: 1.5;
}
.architecture-rails,
.architecture-stages,
.architecture-candidates {
  display: grid;
  gap: 6px;
}
.architecture-rail,
.architecture-stage,
.rail-bom-row {
  display: grid;
  gap: 5px;
  min-width: 0;
  padding: 8px;
  border-radius: 8px;
  background: #f7fafc;
  color: #486276;
  font-size: var(--mb-font-secondary);
}
.architecture-rail span,
.stage-title span,
.stage-facts,
.architecture-candidates span,
.rail-bom-stage,
.no-rail-draft {
  color: #667e91;
  font-size: var(--mb-font-secondary);
  line-height: 1.45;
}
.stage-title {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.stage-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
}
.architecture-candidates > div {
  display: grid;
  gap: 3px;
  padding: 7px;
  border-left: 2px solid #b6d5e6;
}
.architecture-candidates details summary {
  cursor: pointer;
}
.thermal-screen {
  color: #825a25 !important;
}
.architecture-candidates ul,
.stage-notes,
.architecture-constraints,
.rail-bom-review {
  margin: 3px 0 0;
  padding-left: 18px;
  color: #667e91;
  font-size: var(--mb-font-secondary);
  line-height: 1.45;
}
.rail-bom-row {
  background: #f8fcfe;
}
.bom-completeness {
  display: grid;
  gap: 4px;
  padding: 8px 9px;
  border: 1px solid #d7e7ee;
  border-radius: 8px;
  background: #f7fbfd;
  color: #486276;
  font-size: var(--mb-font-secondary);
  line-height: 1.45;
}
.bom-completeness strong {
  color: #31566f;
}
.bom-completeness ul {
  margin: 0;
  padding-left: 18px;
  color: #7b5a2a;
}
.stage-completeness {
  color: #62798b;
  font-size: var(--mb-font-secondary);
}
.rail-bom-stage {
  display: grid;
  gap: 2px;
  padding-left: 9px;
  border-left: 2px solid #b6d5e6;
}
.rail-bom-rail-header,
.rail-bom-stage-heading,
.rail-bom-requirement-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 7px;
}
.rail-bom-stage-heading {
  color: #486276;
}
.rail-bom-facts,
.rail-bom-requirements {
  display: grid;
  gap: 6px;
  margin-top: 4px;
}
.rail-bom-facts {
  padding: 7px 9px;
  border-radius: 7px;
  background: #eef7fb;
  color: #4e7188;
}
.rail-bom-facts span {
  overflow-wrap: anywhere;
}
.rail-bom-requirements-title {
  color: #31566f;
}
.rail-bom-requirement {
  display: grid;
  gap: 5px;
  padding: 8px 9px;
  border: 1px solid #dce8ee;
  border-radius: 8px;
  background: #fff;
}
.rail-bom-evidence-fact,
.rail-bom-requirement-note,
.rail-bom-candidate span,
.rail-bom-requirement details {
  color: #667e91;
  font-size: var(--mb-font-secondary);
  line-height: 1.45;
}
.rail-bom-candidates {
  display: grid;
  gap: 5px;
}
.rail-bom-constraints {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  color: #4e7188;
  font-size: var(--mb-font-secondary);
}
.selected-draft-fact {
  color: #2f745c;
  font-size: var(--mb-font-secondary);
}
.selection-conflict,
.candidate-match-unknown {
  color: #9a5a2d;
}
.candidate-match,
.candidate-match-reason {
  color: #4e7188;
}
.rail-bom-candidate {
  display: grid;
  gap: 2px;
  padding: 6px 8px;
  border-left: 2px solid #9bc7dc;
  background: #f7fafc;
}
.rail-bom-requirement details summary {
  cursor: pointer;
  color: #35698a;
  font-weight: 650;
}
.rail-bom-requirement details ul {
  margin: 4px 0 0;
  padding-left: 17px;
}
.rail-bom-review {
  margin-top: 0;
}
@media (max-width: 520px) {
  .architecture-heading,
  .architecture-card > header,
  .rail-bom-draft > header {
    flex-wrap: wrap;
  }
}
</style>
