<script setup lang="ts">
import type { AgentEngineeringResearchEntity } from '../../types'
import { formatQuantity } from '../../utils/format'
import PowerArchitectureOptions from './PowerArchitectureOptions.vue'

const { research } = defineProps<{
  research: AgentEngineeringResearchEntity
}>()

function candidateLocationText(
  candidate: AgentEngineeringResearchEntity['draft']['buck']['candidates'][number],
) {
  return (
    candidate.locations.locations
      ?.map((location) => location.full_path)
      .filter(Boolean)
      .join('；') || '暂无可确认实际库位'
  )
}

function statusLabel(status: string) {
  return status === 'success' ? '已完成' : status === 'error' ? '失败' : '规划中'
}

function axisLabel(status: string) {
  return (
    {
      found: '已找到',
      not_found: '未找到',
      needs_constraints: '仍缺约束',
      sufficient: '充分',
      partial: '部分',
      insufficient: '不足',
      not_checked: '未检查',
      reviewable: '可审阅',
      not_formed: '未形成',
      stocked_matched: '规格匹配且库存/库位已查',
      shortage: '规格匹配但可用量不足',
      no_matching_material: '规格已知但没有匹配物料',
      needs_design_selection: '规格或工况待确认（不判为缺料）',
      ambiguous_candidates: '候选不唯一或属性未知',
      stocked_location_unassigned: '有库存但库位未确认',
      out_of_stock: '匹配物料存在但可用量为 0',
      candidate_found: '有满足约束的候选，尚未选择',
      selected: '已选草案器件（未写正式 BOM）',
      needs_selection: '规格已知，待选料',
      no_grounded_candidate: '没有受控工程证据',
      needs_input: '缺少必要输入',
      blocked_by_input: '缺少必要输入',
      complete_draft: '草案已齐备（仍只读）',
    }[status] || status
  )
}

function matchLabel(status?: string | null) {
  return (
    {
      exact: '精确匹配',
      compatible: '兼容匹配',
      partial: '信息不全',
      mismatch: '明确不匹配',
    }[status || ''] || '匹配待确认'
  )
}

function componentClassLabel(componentClass?: string | null) {
  return (
    {
      capacitor: '电容',
      resistor: '电阻',
      inductor: '电感',
      inductor_ferrite: '磁珠/电感',
      'buck converter': 'Buck',
      'switching regulator': '开关稳压',
      'dc-dc converter': 'DC/DC',
      ldo: 'LDO',
      'linear regulator': '线性稳压',
      cable: '线缆',
      connector: '连接器',
      unknown: '未知类别',
    }[componentClass || 'unknown'] ||
    componentClass ||
    '未知类别'
  )
}

function provenanceLabel(axis: unknown) {
  const kind = String((axis as { kind?: unknown } | null)?.kind || 'unknown')
  return (
    {
      official_vendor: '官方资料',
      managed_evidence: '受控工程证据',
      catalog: '目录资料',
      operational_db: '系统记录',
      sample_synthetic: '演示/合成',
      unknown: '未知',
    }[kind] || kind
  )
}

function provenanceText(provenance: unknown) {
  const axes = provenance as
    { spec?: unknown; stock?: unknown; location?: unknown } | null | undefined
  if (!axes) return '规格来源未知 · 库存来源未知 · 库位来源未知'
  return `规格 ${provenanceLabel(axes.spec)} · 库存 ${provenanceLabel(axes.stock)} · 库位 ${provenanceLabel(axes.location)}`
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

function candidateFact(
  candidate: Record<string, unknown>,
  key: 'match_reasons' | 'match_unknowns',
) {
  const values = candidate[key]
  return Array.isArray(values) ? values.map((value) => String(value)).join('；') : ''
}

function peripheralLocationText(
  row: NonNullable<AgentEngineeringResearchEntity['peripheral_requirements']>[number],
) {
  return (
    row.location
      ?.map((location) => String(location.full_path || location.code || ''))
      .filter(Boolean)
      .join('；') || '暂无可确认实际库位'
  )
}

function currentText() {
  const current = research.requirements.load_current_a ?? research.requirements.load_current_max_a
  if (current === null || current === undefined || current === '') return '本轮未提供负载电流'
  const milliamps = Number(current) * 1000
  return Number.isFinite(milliamps) ? `${formatQuantity(milliamps)}mA` : `${String(current)}A`
}

function candidateInventoryText(
  candidate: AgentEngineeringResearchEntity['draft']['buck']['candidates'][number],
) {
  const quantity = candidate.inventory?.available_quantity
  if (quantity === null || quantity === undefined || quantity === '') return '本轮库存未读取'
  return `${formatQuantity(quantity)} ${candidate.inventory.unit || ''}`.trim()
}
</script>

<template>
  <section class="research-card" data-testid="engineering-research-result">
    <header class="research-header">
      <div>
        <span class="research-eyebrow"
          >工程研究 · 第 {{ research.plan.round }} 轮{{
            research.plan.continuation ? '追加研究' : '初始研究'
          }}</span
        >
        <h3>工程研究草案</h3>
        <p>
          {{ research.requirements.input_voltage_v }}V →
          {{ research.requirements.output_voltage_v }}V / {{ currentText() }} · 只读 · 需要人工审核
        </p>
      </div>
      <el-tag :type="research.draft_status === 'reviewable' ? 'success' : 'warning'">
        草案 {{ axisLabel(research.draft_status) }}
      </el-tag>
    </header>

    <div class="research-status-axis">
      <span
        >候选：<b>{{ axisLabel(research.candidate_status) }}</b></span
      >
      <span
        >页级证据：<b>{{ axisLabel(research.evidence_status) }}</b></span
      >
      <span
        >工程草案：<b>{{ axisLabel(research.draft_status) }}</b></span
      >
    </div>
    <div
      v-if="research.selection_action_result"
      class="research-selection-truth"
      data-testid="engineering-selection-truth"
    >
      {{ research.selection_action_result.narrative }}
      <small
        >server truth · {{ research.selection_action_result.resolution }} · model state calls
        0</small
      >
    </div>
    <div v-else-if="research.active_selection_context" class="research-selection-context">
      当前选择上下文：{{ research.active_selection_context.role || '工程候选' }} · 有效候选
      {{ research.active_selection_context.valid_candidate_ids.length }} 个 ·
      {{
        research.active_selection_context.selected_material_id
          ? `已选 #${research.active_selection_context.selected_material_id}`
          : '尚未选择'
      }}
    </div>
    <div v-if="research.completeness || research.draft.completeness" class="research-completeness">
      <b>工程 BOM Completeness</b>
      <span>
        要求 {{ (research.completeness || research.draft.completeness)!.required_roles }} · 已知约束
        {{ (research.completeness || research.draft.completeness)!.grounded_roles }} · 候选
        {{ (research.completeness || research.draft.completeness)!.candidate_covered_roles }} · 已选
        {{ (research.completeness || research.draft.completeness)!.selected_roles }} · 未解决
        {{ (research.completeness || research.draft.completeness)!.unresolved_roles }} · 待输入
        {{ (research.completeness || research.draft.completeness)!.needs_input_roles }}
      </span>
      <span
        v-if="
          (research.completeness || research.draft.completeness)!.requirements_defined !== undefined
        "
      >
        V2 定义 {{ (research.completeness || research.draft.completeness)!.requirements_defined }} ·
        已落地 {{ (research.completeness || research.draft.completeness)!.requirements_grounded }} ·
        候选覆盖 {{ (research.completeness || research.draft.completeness)!.candidate_covered }} ·
        显式选择 {{ (research.completeness || research.draft.completeness)!.explicitly_selected }} ·
        证据缺口 {{ (research.completeness || research.draft.completeness)!.evidence_gap }}
      </span>
      <strong>{{
        (research.completeness || research.draft.completeness)!.complete_for_review
          ? '可进入评审'
          : '尚不可评审'
      }}</strong>
      <ul v-if="(research.completeness || research.draft.completeness)!.blocking_reasons.length">
        <li
          v-for="reason in (research.completeness || research.draft.completeness)!.blocking_reasons"
          :key="reason"
        >
          {{ reason }}
        </li>
      </ul>
    </div>
    <PowerArchitectureOptions
      :topologies="research.draft.topologies || research.topologies"
      :rail-bom-draft="research.draft.rail_bom_draft || research.rail_bom_draft"
    />

    <details class="research-plan">
      <summary>Research Plan · 工具执行轨迹</summary>
      <ol>
        <li v-for="step in research.plan.steps" :key="`${step.sequence}-${step.tool}`">
          <b>{{ step.tool }}</b>
          <span>{{ step.purpose }}</span>
          <el-tag
            size="small"
            :type="
              step.status === 'success' ? 'success' : step.status === 'error' ? 'danger' : 'info'
            "
          >
            {{ statusLabel(step.status) }}
          </el-tag>
        </li>
      </ol>
    </details>

    <details class="research-branch-details">
      <summary>候选器件、电气筛选与证据细节</summary>
      <div class="research-branches">
        <article
          v-for="branch in [research.draft.buck, research.draft.ldo]"
          :key="branch === research.draft.buck ? 'buck' : 'ldo'"
          class="research-branch"
        >
          <h4>{{ branch === research.draft.buck ? 'Buck（开关降压）' : 'LDO（线性稳压）' }}</h4>
          <p>{{ branch.summary }}</p>
          <div v-if="branch.candidates.length" class="research-candidates">
            <article
              v-for="candidate in branch.candidates"
              :key="candidate.material_id"
              class="research-candidate"
            >
              <div class="research-candidate-title">
                <b>{{ candidate.mpn || candidate.code }}</b>
                <el-tag
                  size="small"
                  :type="candidate.evidence_status === 'sufficient' ? 'success' : 'warning'"
                >
                  证据 {{ axisLabel(candidate.evidence_status) }}
                </el-tag>
              </div>
              <small>{{ candidate.code }} · {{ candidate.package || '未标封装' }}</small>
              <div class="research-live-facts">
                可用 {{ candidateInventoryText(candidate) }} · 库位
                {{ candidateLocationText(candidate) }}
              </div>
              <div class="research-provenance">
                类别 {{ componentClassLabel(candidate.component_class) }} ·
                {{ provenanceText(candidate.provenance) }}
                <span v-if="candidate.rejection_reason"> · {{ candidate.rejection_reason }}</span>
              </div>
              <div v-if="candidate.electrical_thermal_judgment" class="research-judgment">
                {{ candidate.electrical_thermal_judgment }}
              </div>
              <ul v-if="candidate.evidence_gaps.length" class="research-gaps">
                <li v-for="gap in candidate.evidence_gaps" :key="gap">证据缺口：{{ gap }}</li>
              </ul>
              <div
                v-for="calculation in candidate.calculations || []"
                :key="calculation.formula"
                class="research-calculation"
              >
                <b v-if="calculation.status === 'calculated'">
                  服务端派生 LDO 损耗 {{ formatQuantity(calculation.loss_w) }}W · 理想效率
                  {{ formatQuantity(calculation.ideal_efficiency) }}
                </b>
                <span v-else>损耗待补最大负载电流：{{ calculation.formula }}</span>
              </div>
              <div
                v-if="candidate.thermal_analysis?.status === 'illustrative_first_order'"
                class="research-thermal"
              >
                <b>热筛查（说明性一阶计算）</b>
                <div v-for="point in candidate.thermal_analysis.points || []" :key="point.package">
                  {{ point.package }}：RθJA {{ formatQuantity(point.rtheta_ja_c_per_w) }} °C/W，
                  一阶温升约 {{ formatQuantity(point.first_order_rise_c) }} °C
                </div>
                <small>{{ candidate.thermal_analysis.warning }}</small>
              </div>
              <div
                v-else-if="candidate.thermal_analysis?.warning"
                class="research-thermal research-thermal-unknown"
              >
                {{ candidate.thermal_analysis.warning }}
              </div>
              <ul v-if="candidate.peripheral_roles.length" class="research-peripherals">
                <li v-for="role in candidate.peripheral_roles" :key="role.role">
                  {{ role.role }}：{{ role.exact_value || '按具体器件数据手册/参考设计核对' }}
                </li>
              </ul>
              <ul v-if="candidate.citations.length" class="research-citations">
                <li v-for="citation in candidate.citations" :key="citation.anchor_id">
                  {{ citation.document_title }} {{ citation.document_revision }} · p.{{
                    citation.page
                  }}
                  · {{ citation.section }}
                </li>
              </ul>
            </article>
          </div>
          <p v-else class="research-empty">没有找到满足约束的候选。</p>
        </article>
      </div>
    </details>

    <section v-if="research.draft.peripheral_requirements?.length" class="research-bom-draft">
      <div class="research-bom-title">
        <div>
          <span class="research-eyebrow">Phase 3.3 · Read-only Engineering BOM Draft</span>
          <h4>外围逐项物料、库存、库位与证据</h4>
        </div>
        <el-tag
          :type="
            research.draft.engineering_bom_draft?.status === 'reviewable' ? 'success' : 'warning'
          "
        >
          {{ axisLabel(research.draft.engineering_bom_draft?.status || 'unknown') }}
        </el-tag>
      </div>
      <p class="research-bom-note">
        主芯片 material_id={{
          research.draft.selected_primary_material_id ||
          research.selected_primary_material_id ||
          '未知'
        }}； 只读展示，不修改正式 Product BOM，不批准替代料，不结算 Picking。
      </p>
      <div class="research-bom-rows">
        <article
          v-for="row in research.draft.peripheral_requirements"
          :key="row.requirement_id"
          class="research-bom-row"
        >
          <div class="research-bom-row-title">
            <b>{{ row.role }}</b>
            <el-tag
              size="small"
              :type="row.selection_status === 'stocked_matched' ? 'success' : 'warning'"
            >
              {{ axisLabel(row.selection_status) }}
            </el-tag>
          </div>
          <div class="research-bom-spec">
            规格：{{ row.value || row.constraint_value || '待确认' }}
            <span v-if="row.rated_voltage_v"> · {{ row.rated_voltage_v }}V</span>
            <span v-if="row.dielectric"> · {{ row.dielectric }}</span>
            <span v-if="row.package"> · {{ row.package }}</span>
            · 需求 {{ row.required_quantity || '未知' }}
          </div>
          <div class="research-provenance">
            期待类别 {{ (row.expected_component_classes || []).join(' / ') || '未定义' }} · 当前类别
            {{ componentClassLabel(row.component_class) }} ·
            {{ provenanceText(row.provenance) }}
            <span v-if="row.evidence_gap"> · 证据缺口</span>
          </div>
          <div v-if="row.constraints?.length" class="research-bom-constraints">
            <span
              v-for="constraint in row.constraints"
              :key="`${row.requirement_id}-${constraint.key}`"
            >
              {{ constraintText(constraint) }}
            </span>
          </div>
          <div v-if="row.selected_material_id" class="research-bom-selected">
            已选草案物料 #{{ row.selected_material_id }} ·
            {{ row.selection_basis || 'explicit_user' }} · 未写入正式 BOM
          </div>
          <div v-if="row.selection_conflict" class="research-bom-conflict">
            {{ row.selection_conflict }}
          </div>
          <div class="research-bom-facts">
            匹配：{{ row.matched_mpn || row.matched_code || '无' }} · 可用：{{
              row.available_quantity ?? '未知'
            }}
            · 库位：{{ peripheralLocationText(row) }}
          </div>
          <div
            v-if="row.shortage_quantity && row.shortage_quantity !== '0'"
            class="research-bom-shortage"
          >
            短缺数量：{{ row.shortage_quantity }}
          </div>
          <div v-if="row.material_candidate_ids?.length" class="research-bom-candidates">
            搜索候选 material_id：{{
              row.material_candidate_ids.join(', ')
            }}；不满足规格者未自动作为替代料。
          </div>
          <div v-if="row.candidates?.length" class="research-bom-candidate-details">
            <div v-for="candidate in row.candidates" :key="String(candidate.material_id)">
              <b>{{ String(candidate.mpn || candidate.code || candidate.material_id) }}</b>
              · {{ matchLabel(String(candidate.match_status || '')) }}
              <span v-if="candidate.component_class">
                · 类别 {{ componentClassLabel(String(candidate.component_class)) }}</span
              >
              <span v-if="candidate.rejection_reason">
                · {{ String(candidate.rejection_reason) }}</span
              >
              <span v-if="candidateFact(candidate, 'match_reasons')">
                · {{ candidateFact(candidate, 'match_reasons') }}</span
              >
              <span v-if="candidateFact(candidate, 'match_unknowns')">
                · {{ candidateFact(candidate, 'match_unknowns') }}</span
              >
            </div>
          </div>
          <div
            v-if="row.source_anchor && Object.keys(row.source_anchor).length"
            class="research-bom-evidence"
          >
            PDF 依据：{{
              row.source_anchor.document_title || row.source_anchor.document_key || '工程证据'
            }}
            {{ row.source_anchor.document_revision || '' }} · p.{{
              row.source_anchor.page || row.source_anchor.physical_page || '?'
            }}
          </div>
          <ul v-if="row.unknowns?.length" class="research-bom-unknowns">
            <li v-for="unknown in row.unknowns" :key="unknown">保持未知：{{ unknown }}</li>
          </ul>
        </article>
      </div>
    </section>

    <details
      v-if="research.draft.manual_review.length || research.draft.unknowns.length"
      class="research-review-details"
    >
      <summary>审核条件与仍未知项</summary>
      <section v-if="research.draft.manual_review.length" class="research-review">
        <h4>人工审核清单</h4>
        <ul>
          <li v-for="item in research.draft.manual_review" :key="item">{{ item }}</li>
        </ul>
      </section>
      <section v-if="research.draft.unknowns.length" class="research-unknowns">
        <h4>保持未知</h4>
        <ul>
          <li v-for="item in research.draft.unknowns" :key="item">{{ item }}</li>
        </ul>
      </section>
    </details>
    <p class="research-safety">不会自动修改 BOM、库存、预留或 Picking 结算。</p>
  </section>
</template>

<style scoped>
.research-card {
  display: grid;
  gap: 13px;
  padding: 18px;
  border: 1px solid #cfe0ec;
  border-radius: 14px;
  background: linear-gradient(145deg, #fbfdff, #f4f9fc);
  font-size: var(--mb-font-body);
}
.research-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
.research-eyebrow {
  color: #4b7898;
  font-size: var(--mb-font-secondary);
  font-weight: 700;
}
.research-header h3 {
  margin: 3px 0;
  color: #294d69;
  font-size: var(--mb-font-card-title);
}
.research-header p,
.research-branch > p,
.research-empty {
  margin: 0;
  color: #6b8092;
  font-size: var(--mb-font-secondary);
  line-height: 1.55;
}
.research-status-axis {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  color: #536f82;
  font-size: var(--mb-font-secondary);
}
.research-status-axis span {
  padding: 6px 9px;
  border: 1px solid #d7e5ed;
  border-radius: 999px;
  background: #fff;
}
.research-status-axis b {
  color: #285d7b;
}
.research-selection-truth,
.research-selection-context {
  display: grid;
  gap: 3px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #eef7fb;
  color: #315f78;
  font-size: var(--mb-font-secondary);
  line-height: 1.5;
}
.research-selection-truth small {
  color: #5f7f91;
}
.research-completeness {
  display: grid;
  gap: 4px;
  padding: 9px 11px;
  border: 1px solid #d7e5ed;
  border-radius: 9px;
  background: #f8fcfe;
  color: #536f82;
  font-size: var(--mb-font-secondary);
  line-height: 1.45;
}
.research-completeness strong,
.research-bom-selected {
  color: #2f745c;
}
.research-completeness ul {
  margin: 0;
  padding-left: 18px;
  color: #7b5a2a;
}
.research-plan {
  padding: 10px 12px;
  border: 1px solid #d9e6ee;
  border-radius: 9px;
  background: #fff;
}
.research-plan summary {
  cursor: pointer;
  color: #35698a;
  font-weight: 700;
}
.research-branch-details,
.research-review-details {
  display: grid;
  gap: 10px;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid #d9e6ee;
  border-radius: 9px;
  background: #fff;
}
.research-branch-details > summary,
.research-review-details > summary {
  color: #35698a;
  cursor: pointer;
  font-weight: 700;
}
.research-plan ol {
  display: grid;
  gap: 7px;
  margin: 10px 0 0;
  padding-left: 22px;
}
.research-plan li {
  display: grid;
  grid-template-columns: minmax(170px, 0.8fr) minmax(0, 1.7fr) auto;
  gap: 8px;
  align-items: center;
  color: #526e82;
  font-size: var(--mb-font-secondary);
}
.research-plan li b {
  color: #315a77;
  font-family: ui-monospace, Consolas, monospace;
}
.research-branches {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.research-branch {
  display: grid;
  gap: 9px;
  padding: 13px;
  border: 1px solid #dbe7ef;
  border-radius: 12px;
  background: #fff;
}
.research-branch h4,
.research-review h4,
.research-unknowns h4 {
  margin: 0;
  color: #31566f;
}
.research-candidates {
  display: grid;
  gap: 8px;
}
.research-candidate {
  display: grid;
  gap: 6px;
  padding: 10px;
  border: 1px solid #e1eaf1;
  border-radius: 10px;
  background: #fbfdff;
}
.research-candidate-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.research-candidate small,
.research-live-facts,
.research-peripherals,
.research-citations,
.research-gaps {
  color: #6b8092;
  font-size: var(--mb-font-secondary);
  line-height: 1.5;
}
.research-live-facts {
  overflow-wrap: anywhere;
}
.research-provenance {
  color: #6f8796;
  font-size: var(--mb-font-secondary);
  line-height: 1.45;
  overflow-wrap: anywhere;
}
.research-bom-constraints,
.research-bom-candidate-details {
  color: #4e7188;
  font-size: var(--mb-font-secondary);
  line-height: 1.5;
}
.research-bom-constraints {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 9px;
}
.research-bom-candidate-details {
  display: grid;
  gap: 3px;
}
.research-bom-conflict {
  color: #9a5a2d;
  font-size: var(--mb-font-secondary);
}
.research-judgment {
  padding: 7px 9px;
  border-radius: 8px;
  background: #eef7fb;
  color: #315f78;
  font-size: var(--mb-font-secondary);
  line-height: 1.5;
}
.research-gaps {
  margin: 0;
  padding-left: 19px;
  color: #8a6843;
}
.research-calculation {
  padding: 7px 9px;
  border-radius: 8px;
  background: #f2faf6;
  color: #2f775e;
  font-size: var(--mb-font-secondary);
}
.research-thermal {
  display: grid;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #fff5e8;
  color: #845f32;
  font-size: var(--mb-font-secondary);
  line-height: 1.5;
}
.research-thermal small {
  color: #8b6c49;
}
.research-thermal-unknown {
  background: #fff8ea;
}
.research-peripherals,
.research-citations,
.research-review ul,
.research-unknowns ul {
  margin: 0;
  padding-left: 19px;
}
.research-citations {
  color: #39779f;
}
.research-review,
.research-unknowns {
  padding: 11px 13px;
  border-radius: 9px;
  background: #fff;
}
.research-review ul,
.research-unknowns ul {
  margin-top: 7px;
  color: #5f7588;
  line-height: 1.6;
}
.research-unknowns {
  background: #fff8ea;
}
.research-safety {
  margin: 0;
  padding: 9px 11px;
  border-radius: 8px;
  background: #edf7fb;
  color: #476b82;
  font-size: var(--mb-font-secondary);
  font-weight: 650;
}
.research-bom-draft {
  display: grid;
  gap: 9px;
  padding: 13px;
  border: 1px solid #c8dfea;
  border-radius: 12px;
  background: #f8fcfe;
}
.research-bom-title {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
.research-bom-title h4 {
  margin: 3px 0 0;
  color: #31566f;
}
.research-bom-note,
.research-bom-spec,
.research-bom-facts,
.research-bom-candidates,
.research-bom-evidence,
.research-bom-unknowns {
  color: #5f7588;
  font-size: var(--mb-font-secondary);
  line-height: 1.55;
}
.research-bom-note {
  margin: 0;
}
.research-bom-rows {
  display: grid;
  gap: 8px;
}
.research-bom-row {
  display: grid;
  gap: 5px;
  padding: 10px;
  border: 1px solid #dbe7ef;
  border-radius: 9px;
  background: #fff;
}
.research-bom-row-title {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
  color: #31566f;
}
.research-bom-facts {
  overflow-wrap: anywhere;
}
.research-bom-shortage {
  padding: 5px 8px;
  border-radius: 6px;
  background: #fff1e2;
  color: #95622f;
  font-weight: 650;
  font-size: var(--mb-font-secondary);
}
.research-bom-candidates {
  color: #8a6843;
}
.research-bom-evidence {
  color: #39779f;
}
.research-bom-unknowns {
  margin: 0;
  padding-left: 19px;
  color: #8a6843;
}
@media (max-width: 800px) {
  .research-branches {
    grid-template-columns: 1fr;
  }
  .research-plan li {
    grid-template-columns: 1fr;
    gap: 3px;
  }
}
</style>
