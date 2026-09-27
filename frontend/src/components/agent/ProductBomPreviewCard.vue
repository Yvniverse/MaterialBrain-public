<script setup lang="ts">
import type { ProductBomPreviewEntity, ProductBomPreviewLine } from '../../types'
import { formatQuantity } from '../../utils/format'

const { preview } = defineProps<{ preview: ProductBomPreviewEntity }>()

const actionLabels: Record<ProductBomPreviewLine['action'], string> = {
  add: 'ADD',
  update_quantity: 'UPDATE_QUANTITY',
  no_change: 'NO_CHANGE',
  unresolved: 'UNRESOLVED',
}

function actionType(action: ProductBomPreviewLine['action']) {
  return action === 'unresolved' ? 'warning' : action === 'no_change' ? 'info' : 'success'
}

function quantity(value?: string | null) {
  return value === null || value === undefined || value === '' ? '—' : formatQuantity(value)
}
</script>

<template>
  <section class="product-bom-preview-card" data-testid="product-bom-preview">
    <header class="preview-header">
      <div>
        <span class="preview-eyebrow">Engineering Draft → Product BOM Preview</span>
        <h3>Product BOM Preview</h3>
        <p>
          {{ preview.target_product.code }} · {{ preview.target_revision.revision }} ·
          {{ preview.target_revision.status }}
        </p>
      </div>
      <el-tag type="warning">仅预览 · 未写入正式 BOM</el-tag>
    </header>

    <div class="preview-summary" data-testid="product-bom-preview-summary">
      <span
        >ADD <b>{{ preview.summary.add_count }}</b></span
      >
      <span
        >UPDATE <b>{{ preview.summary.update_quantity_count }}</b></span
      >
      <span
        >NO_CHANGE <b>{{ preview.summary.no_change_count }}</b></span
      >
      <span
        >UNRESOLVED <b>{{ preview.summary.unresolved_count }}</b></span
      >
      <span
        >就绪 <b>{{ preview.summary.ready_for_confirmation_preview ? '是' : '否' }}</b></span
      >
    </div>
    <p v-if="preview.summary.warnings?.length" class="preview-warning">
      {{ preview.summary.warnings.join('；') }}
    </p>
    <div
      v-if="preview.apply_readiness_dry_run"
      class="preview-dry-run"
      data-testid="product-bom-apply-readiness-dry-run"
    >
      Phase 3.3.7 Apply Readiness Dry-Run：允许 Apply = 否 · 需显式确认 · 计划变更
      {{ preview.apply_readiness_dry_run.planned_mutations.length }} 项 ·
      {{ preview.apply_readiness_dry_run.stale_preview ? '预览已过期' : '指纹仍一致' }}
      <small>
        只读：{{ preview.apply_readiness_dry_run.read_only ? '是' : '否' }} · 正式 BOM 未修改：{{
          preview.apply_readiness_dry_run.formal_product_bom_modified ? '否' : '是'
        }}
      </small>
    </div>

    <table>
      <thead>
        <tr>
          <th>动作</th>
          <th>角色 / 需求</th>
          <th>物料</th>
          <th>草案单台用量</th>
          <th>目标版本单台用量</th>
          <th>结论</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="line in preview.lines" :key="`${line.requirement_id}-${line.material_code}`">
          <td>
            <el-tag size="small" :type="actionType(line.action)">{{
              actionLabels[line.action]
            }}</el-tag>
          </td>
          <td>
            {{ line.role || '—' }}<small>{{ line.requirement_id || '—' }}</small>
          </td>
          <td>
            <b>{{ line.mpn || line.material_code || '—' }}</b
            ><small>{{ line.material_code || '未选物料' }}</small>
          </td>
          <td>{{ quantity(line.draft_quantity) }}</td>
          <td>{{ quantity(line.existing_quantity_per_unit) }}</td>
          <td>
            {{ line.reason }}
            <small>{{ line.reason_code }}</small>
            <small v-if="line.selected_candidate_match_status"
              >当前候选：{{ line.selected_candidate_match_status }} ·
              {{ line.selected_candidate_still_valid ? '仍有效' : '已失效' }}</small
            >
          </td>
        </tr>
      </tbody>
    </table>

    <p v-if="preview.summary.complete_for_apply_preview" class="preview-complete">
      所有草案行均已形成可应用预览；本阶段仍不会提供 Apply 或正式 BOM 写入。
    </p>
    <ul v-if="preview.unresolved.length" class="preview-unresolved">
      <li v-for="item in preview.unresolved" :key="`${item.requirement_id}-${item.reason}`">
        {{ item.role || item.requirement_id || '未解决项' }}：{{ item.reason }}
      </li>
    </ul>
    <small class="preview-semantics">{{ preview.quantity_semantics }}</small>
  </section>
</template>

<style scoped>
.product-bom-preview-card {
  display: grid;
  gap: 12px;
  padding: 16px;
  border: 1px solid #d8e5ef;
  border-radius: 14px;
  background: #fff;
  overflow-x: auto;
}
.preview-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.preview-eyebrow,
.preview-header p,
.preview-semantics,
.preview-unresolved {
  color: #71869a;
  font-size: var(--mb-font-secondary);
}
.preview-eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.preview-header h3 {
  margin: 3px 0;
  color: #31566f;
  font-size: var(--mb-font-card-title);
}
.preview-header p {
  margin: 0;
}
.preview-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.preview-summary span {
  padding: 6px 9px;
  border-radius: 8px;
  background: #f2f7fb;
  color: #536c80;
  font-size: var(--mb-font-secondary);
}
.preview-summary b {
  color: #294d69;
}
table {
  width: 100%;
  min-width: 700px;
  border-collapse: collapse;
  font-size: var(--mb-font-secondary);
}
th,
td {
  padding: 8px 9px;
  border-bottom: 1px solid #e3ebf1;
  text-align: left;
  vertical-align: top;
}
th {
  color: #60788d;
  font-weight: 700;
}
td small {
  display: block;
  margin-top: 3px;
  color: #7b8fa0;
}
.preview-complete {
  margin: 0;
  color: #267659;
  font-size: var(--mb-font-secondary);
}
.preview-warning {
  margin: 0;
  color: #8b632f;
  font-size: var(--mb-font-secondary);
  line-height: 1.5;
}
.preview-dry-run {
  margin: 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: #f5f0ff;
  color: #5c4b82;
  font-size: var(--mb-font-secondary);
  line-height: 1.5;
}
.preview-dry-run small {
  display: block;
  color: #776a97;
}
.preview-unresolved {
  margin: 0;
  padding-left: 18px;
  line-height: 1.55;
}
.preview-semantics {
  display: block;
}
@media (max-width: 700px) {
  .preview-header {
    display: grid;
  }
}
</style>
