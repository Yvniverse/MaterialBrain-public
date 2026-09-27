<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { formatQuantity } from '../../utils/format'
import type { AgentLocationResultEntity } from '../../types'

const props = defineProps<{
  locationResult: AgentLocationResultEntity
}>()

const router = useRouter()
const primaryLocation = computed(() => props.locationResult.locations[0] || null)
const otherLocations = computed(() => props.locationResult.locations.slice(1))
</script>

<template>
  <section class="location-results" data-testid="agent-location-result">
    <header><span>{{ locationResult.code }}</span><h3>{{ locationResult.mpn || locationResult.name }}</h3><p>{{ locationResult.name }}</p></header>
    <div class="inventory-location-grid">
      <div><span>当前库存</span><b data-testid="current-inventory">{{ formatQuantity(locationResult.material_quantity) }}</b></div>
      <div><span>主库位</span><b>{{ primaryLocation?.full_path || '未登记存放位置' }}</b></div>
    </div>
    <div v-if="locationResult.distribution_status === 'inconsistent'" class="location-warning danger">
      库存记录与库位数量不一致，完成盘点前暂不提供确定库位。
    </div>
    <div v-else-if="locationResult.distribution_status === 'partial'" class="location-warning">
      ⚠ {{ formatQuantity(locationResult.unallocated_quantity) }} 件库存尚未分配到具体货架或抽屉
    </div>
    <details v-if="otherLocations.length" class="other-locations">
      <summary>查看其他存放位置（{{ otherLocations.length }}）</summary>
      <ul>
        <li v-for="location in otherLocations" :key="location.location_id">
          <span>{{ location.full_path }}</span>
          <b>{{ location.quantity_is_exact ? formatQuantity(location.quantity_at_location) : '数量待分配' }}</b>
        </li>
      </ul>
    </details>
    <footer>
      <el-button type="primary" plain @click="router.push(`/materials/${locationResult.material_id}`)">查看物料</el-button>
      <el-button v-if="primaryLocation && locationResult.distribution_status !== 'inconsistent'" type="primary" data-testid="open-location" @click="router.push(`/locations?focus=${primaryLocation.location_id}`)">打开库位</el-button>
    </footer>
    <el-empty
      v-if="locationResult.distribution_status !== 'inconsistent' && !locationResult.locations.length"
      :image-size="52"
      description="尚未登记存放位置"
    />
  </section>
</template>

<style scoped>
.location-results{padding:22px;border:1px solid #dce6ef;border-radius:16px;background:#fff}.location-results>header span{color:#3b78ad;font-size:var(--mb-font-secondary);font-weight:700}.location-results>header h3{margin:4px 0 0;color:#1f4565;font-size:var(--mb-font-section-title);overflow-wrap:anywhere}.location-results>header p{margin:4px 0 0;color:#71869a;font-size:var(--mb-font-body)}.inventory-location-grid{display:grid;grid-template-columns:minmax(150px,.7fr) minmax(240px,1.3fr);gap:12px;margin-top:18px}.inventory-location-grid>div{display:grid;align-content:start;min-height:92px;padding:15px;border:1px solid #e1e9f0;border-radius:12px;background:#f8fafc}.inventory-location-grid span{color:#6f8192;font-size:var(--mb-font-secondary)}.inventory-location-grid b{margin-top:7px;color:#244b68;font-size:var(--mb-font-card-title);line-height:1.45;overflow-wrap:anywhere}.inventory-location-grid>div:first-child b{color:#176d53;font-size:var(--mb-font-critical);font-variant-numeric:tabular-nums}.location-warning{margin-top:14px;padding:12px 14px;border:1px solid #f0d69d;border-radius:10px;background:#fff8e9;color:#8a6225;font-size:var(--mb-font-body);line-height:1.55}.location-warning.danger{border-color:#efc3c3;background:#fff3f3;color:#9a4040}.other-locations{margin-top:14px}.other-locations summary{color:#326f9f;cursor:pointer;font-size:var(--mb-font-secondary);font-weight:650}.other-locations ul{display:grid;gap:8px;margin:10px 0 0;padding:0;list-style:none}.other-locations li{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-top:1px solid #edf1f5;color:#536d83;font-size:var(--mb-font-secondary)}.location-results footer{display:flex;gap:10px;margin-top:16px}.location-results footer :deep(.el-button){margin:0}@media(max-width:650px){.inventory-location-grid{grid-template-columns:1fr}.location-results footer{align-items:stretch;flex-direction:column}.location-results footer :deep(.el-button){width:100%}}
</style>
