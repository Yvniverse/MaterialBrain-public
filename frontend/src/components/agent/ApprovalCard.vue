<script setup lang="ts">
import { computed } from 'vue'
import type { AgentActionProposal } from '../../types'
import { formatQuantity } from '../../utils/format'

const props = withDefaults(
  defineProps<{
    proposal: AgentActionProposal
    canApprove: boolean
    busy: boolean
    compact?: boolean
  }>(),
  { compact: false },
)

const emit = defineEmits<{
  approve: [proposal: AgentActionProposal]
  reject: [proposal: AgentActionProposal]
}>()

function displayItem(materialId: number) {
  return props.proposal.display.items.find((item) => item.material_id === materialId)
}

const visibleItems = computed(() =>
  props.compact ? props.proposal.payload.items.slice(0, 3) : props.proposal.payload.items,
)

const statusLabels: Record<string, string> = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已拒绝',
  executed: '已执行',
  failed: '执行失败',
  expired: '已过期',
}
</script>

<template>
  <article class="approval-card" data-testid="agent-approval-card">
    <header>
      <div>
        <span>{{ proposal.display.source === 'build_plan' ? '构建物料预留确认' : '库存变更确认' }}</span>
        <b>{{ proposal.proposal_no }}</b>
      </div>
      <el-tag :type="proposal.status === 'pending' ? 'warning' : proposal.status === 'executed' ? 'success' : 'info'">{{ statusLabels[proposal.status] }}</el-tag>
    </header>
    <p>{{ proposal.reason }}</p>
    <div v-if="proposal.display.source === 'build_plan'" class="build-provenance">
      <b>{{ proposal.display.product_name }} · {{ proposal.display.product_revision }} × {{ proposal.display.build_quantity }} 台</b>
      <span>构建计划 {{ proposal.display.build_plan_no }}</span>
      <span>产品 BOM {{ proposal.display.product_bom_hash?.slice(0, 12) }}…</span>
    </div>
    <div class="proposal-project">
      项目
      <b>{{ proposal.display.project_code || `#${proposal.payload.project_id}` }}</b>
      {{ proposal.display.project_name }}
    </div>
    <ul>
      <li v-for="item in visibleItems" :key="item.material_id">
        <span>
          <b>{{ displayItem(item.material_id)?.code || `物料 #${item.material_id}` }}</b>
          {{ displayItem(item.material_id)?.mpn || displayItem(item.material_id)?.name || '' }}
          <small v-if="proposal.display.source === 'build_plan'">
            总需求 {{ formatQuantity(displayItem(item.material_id)?.required_total || '0') }} ·
            当前项目已预留 {{ formatQuantity(displayItem(item.material_id)?.reserved_for_project_at_plan || '0') }}
          </small>
        </span>
        <b>{{ proposal.display.source === 'build_plan' ? '新增 ' : '× ' }}{{ formatQuantity(item.quantity) }}</b>
      </li>
    </ul>
    <p v-if="compact && proposal.payload.items.length > 3" class="more-items">
      另有 {{ proposal.payload.items.length - 3 }} 类物料，请打开产品页查看全部。
    </p>
    <el-alert
      v-if="proposal.status === 'failed' && proposal.display.source === 'build_plan'"
      title="该构建计划已过期"
      description="库存、项目预留或产品 BOM 已发生变化。请重新分析后生成新的预留方案；已批准数量没有被静默修改。"
      type="error"
      :closable="false"
      show-icon
    />
    <el-alert v-else-if="proposal.error_message" :title="proposal.error_message" type="error" :closable="false" />
    <el-alert
      v-if="proposal.status === 'executed' && proposal.display.source === 'build_plan'"
      :title="`已为 ${proposal.display.project_code} 完成构建物料预留`"
      :description="`${proposal.display.product_name} · ${proposal.display.product_revision} × ${proposal.display.build_quantity} 台`"
      type="success"
      :closable="false"
      show-icon
    />
    <p v-if="proposal.status === 'pending'" class="pending-note">库存尚未修改，需要人工批准。</p>
    <footer v-if="proposal.status === 'pending'">
      <span v-if="!canApprove">你没有批准库存变更的权限</span>
      <div>
        <el-button :loading="busy" @click="emit('reject', proposal)">拒绝</el-button>
        <el-button type="primary" :loading="busy" :disabled="!canApprove" data-testid="approve-proposal" @click="emit('approve', proposal)">批准预留</el-button>
      </div>
    </footer>
  </article>
</template>

<style scoped>
.approval-card{padding:18px;border:1px solid #efd8a7;border-radius:14px;background:linear-gradient(145deg,#fffdf8,#fff8e9);font-size:var(--mb-font-body)}.approval-card header{display:flex;align-items:flex-start;justify-content:space-between}.approval-card header>div{display:flex;flex-direction:column}.approval-card header span{color:#94641c;font-size:var(--mb-font-secondary);font-weight:750}.approval-card header b{margin-top:4px;color:#604820}.approval-card p{margin:12px 0;color:#6f6048;font-size:var(--mb-font-body);line-height:1.55}.build-provenance{display:grid;gap:4px;margin:10px 0;padding:11px;border-radius:9px;background:#fff;color:#735c38}.build-provenance span{font-size:var(--mb-font-secondary);color:#8b7553}.proposal-project{padding:9px;border-radius:8px;background:#fff8e9;color:#7e633e;font-size:var(--mb-font-secondary)}.approval-card ul{margin:9px 0;padding:0;list-style:none}.approval-card li{display:flex;justify-content:space-between;gap:12px;padding:8px 2px;border-bottom:1px solid #eee1c9;color:#755f3d;font-size:var(--mb-font-secondary)}.approval-card li>span{display:grid;gap:3px}.approval-card li small{color:#8a7657}.pending-note{padding:8px 10px;border-radius:8px;background:#fff4d7;font-weight:650}.approval-card footer{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:13px}.approval-card footer>span{color:#9a672b;font-size:var(--mb-font-secondary)}.approval-card footer>div{display:flex;gap:8px}.approval-card footer .el-button{margin:0}@media(max-width:600px){.approval-card footer{align-items:stretch;flex-direction:column}.approval-card footer>div{justify-content:flex-end}.approval-card li{align-items:flex-start;flex-direction:column}}
</style>
