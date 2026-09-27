<script setup lang="ts">
import type { AgentGroundedFact } from '../../types'
import { formatQuantity } from '../../utils/format'

defineProps<{
  facts: AgentGroundedFact[]
}>()

const sourceLabels: Record<string, string> = {
  get_inventory_availability: '库存工具',
  get_low_stock_materials: '低库存工具',
  find_material_locations: '库位工具',
  get_project_bom: 'BOM 工具',
  analyze_project_bom_stock: 'BOM 分析工具',
  propose_inventory_reservation: 'Proposal 工具',
}

function displayValue(fact: AgentGroundedFact) {
  const value = fact.value
  const formatted =
    typeof value === 'number' || (typeof value === 'string' && /^-?\d+(\.\d+)?$/.test(value))
      ? formatQuantity(value)
      : String(value ?? '—')
  return fact.unit ? `${formatted} ${fact.unit}` : formatted
}
</script>

<template>
  <section v-if="facts.length" class="grounded-facts" data-testid="agent-grounded-facts">
    <header><span>VERIFIED FACTS</span><b>确定性事实</b><small>以下值直接来自本轮工具结果</small></header>
    <div class="fact-grid">
      <article v-for="(fact, index) in facts" :key="`${fact.source_tool}-${fact.entity_id}-${fact.field}-${index}`">
        <span>{{ fact.label }}</span>
        <b>{{ displayValue(fact) }}</b>
        <small>{{ sourceLabels[fact.source_tool] || fact.source_tool }}</small>
      </article>
    </div>
  </section>
</template>

<style scoped>
.grounded-facts{padding:15px;border:1px solid #cfe4d9;border-radius:14px;background:#f8fcfa}.grounded-facts>header{display:grid;grid-template-columns:1fr auto;align-items:end;margin-bottom:10px}.grounded-facts>header span{grid-column:1;color:#23805e;font-size:8px;font-weight:800;letter-spacing:1.3px}.grounded-facts>header b{grid-column:1;margin-top:2px;color:#2f5e4d;font-size:13px}.grounded-facts>header small{grid-row:1/3;grid-column:2;color:#718b80;font-size:8px}.fact-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:7px}.fact-grid article{display:flex;min-width:0;flex-direction:column;padding:9px;border:1px solid #dcece4;border-radius:9px;background:#fff}.fact-grid span{color:#72877e;font-size:8px}.fact-grid b{margin-top:3px;color:#225d47;font-size:13px;overflow-wrap:anywhere}.fact-grid small{margin-top:4px;color:#9aaba4;font-size:7px}@media(max-width:520px){.grounded-facts>header{display:flex;align-items:flex-start;flex-direction:column}.grounded-facts>header small{margin-top:3px}}
</style>
