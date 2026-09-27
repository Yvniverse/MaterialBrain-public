<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { AgentMaterialEntity } from '../../types'
import { formatQuantity } from '../../utils/format'
import { sanitizeBusinessCopy } from '../../utils/businessCopy'
import { materialPrimaryIdentity, materialSecondaryIdentity } from '../../utils/materialIdentity'

const props = defineProps<{
  material: AgentMaterialEntity
  compact?: boolean
}>()

const router = useRouter()
const materialId = computed(() => Number(props.material.id || props.material.material_id))
const primaryIdentity = computed(() => materialPrimaryIdentity(props.material))
const secondaryIdentity = computed(() => materialSecondaryIdentity(props.material))
const location = computed(
  () =>
    props.material.location ||
    props.material.locations?.find((item) => item.quantity_is_exact !== false) ||
    null,
)
</script>

<template>
  <article class="material-result" :class="{ compact }" data-testid="agent-material-result">
    <header>
      <div>
        <span>{{ material.code }}</span
        ><b>{{ primaryIdentity }}</b
        ><small v-if="secondaryIdentity">{{ secondaryIdentity }}</small>
      </div>
      <el-tag v-if="material.low_stock" type="warning">低库存</el-tag>
    </header>
    <div class="material-meta">
      <span>{{ sanitizeBusinessCopy(material.manufacturer) || '未标厂家' }}</span>
      <span>{{ material.package || '未标封装' }}</span>
      <span>{{ sanitizeBusinessCopy(material.specification) || '未标规格' }}</span>
    </div>
    <div v-if="material.available_quantity !== undefined" class="stock-grid">
      <div>
        <span>当前库存</span><b>{{ formatQuantity(material.quantity) }}</b>
      </div>
      <div>
        <span>已预留</span><b>{{ formatQuantity(material.reserved_quantity) }}</b>
      </div>
      <div>
        <span>可用库存</span
        ><b class="available">{{ formatQuantity(material.available_quantity) }}</b>
      </div>
      <div v-if="material.safety_stock !== undefined">
        <span>安全库存</span><b>{{ formatQuantity(material.safety_stock) }}</b>
      </div>
    </div>
    <div v-if="location" class="primary-location">
      <span>存放位置</span><b>{{ location.full_path }}</b>
    </div>
    <div v-else class="primary-location unallocated">尚未分配实际库位</div>
    <div v-if="material.distribution_status === 'inconsistent'" class="stock-warning danger">
      库存记录与库位数量不一致，请先盘点。
    </div>
    <div
      v-else-if="material.distribution_status === 'partial'"
      class="stock-warning"
    >
      ⚠ {{ formatQuantity(material.unallocated_quantity) }} 件库存尚未分配到具体货架或抽屉
    </div>
    <footer>
      <el-button link type="primary" @click="router.push(`/materials/${materialId}`)"
        >查看物料</el-button
      >
      <el-button
        v-if="location?.location_id"
        link
        type="primary"
        @click="router.push(`/locations?focus=${location.location_id}`)"
        >打开库位</el-button
      >
    </footer>
  </article>
</template>

<style scoped>
.material-result {
  padding: 20px;
  border: 1px solid #dce6ef;
  border-radius: 14px;
  background: #fff;
}
.material-result header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.material-result header > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
}
.material-result header span {
  color: #3a78b3;
  font-size: var(--mb-font-secondary);
  font-weight: 750;
}
.material-result header b {
  margin-top: 4px;
  color: #204768;
  font-size: var(--mb-font-card-title);
  overflow-wrap: anywhere;
}
.material-result header small {
  margin-top: 3px;
  color: #71869a;
  font-size: var(--mb-font-secondary);
}
.material-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 12px;
}
.material-meta span {
  padding: 5px 8px;
  border-radius: 7px;
  background: #f1f5f8;
  color: #61768b;
  font-size: var(--mb-font-secondary);
}
.stock-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  margin-top: 14px;
  overflow: hidden;
  border: 1px solid #e2e9f0;
  border-radius: 10px;
}
.stock-grid div {
  padding: 12px;
  border-right: 1px solid #e2e9f0;
}
.stock-grid div:last-child {
  border: 0;
}
.stock-grid span {
  display: block;
  color: #718294;
  font-size: var(--mb-font-secondary);
}
.stock-grid b {
  display: block;
  margin-top: 5px;
  color: #3b536a;
  font-size: var(--mb-font-card-title);
}
.stock-grid .available {
  color: #19765b;
  font-size: var(--mb-font-critical);
}
.primary-location {
  margin-top: 11px;
  padding: 10px;
  border-radius: 8px;
  background: #edf6fd;
  color: #306d99;
  font-size: var(--mb-font-secondary);
}
.primary-location span,
.primary-location b {
  display: block;
}
.primary-location span {
  color: #66839a;
  font-size: var(--mb-font-secondary);
}
.primary-location b {
  margin-top: 3px;
}
.primary-location.unallocated {
  background: #fff8e9;
  color: #8a6225;
}
.stock-warning {
  margin-top: 10px;
  padding: 10px;
  border-radius: 8px;
  background: #fff8e9;
  color: #8a6225;
  font-size: var(--mb-font-secondary);
}
.stock-warning.danger {
  background: #fff0f0;
  color: #984242;
}
.material-result footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 11px;
}
.material-result.compact {
  padding: 14px;
}
.material-result.compact .stock-grid {
  grid-template-columns: 1fr;
}
.material-result.compact .stock-grid div {
  border-right: 0;
  border-bottom: 1px solid #e2e9f0;
}
@media (max-width: 520px) {
  .stock-grid {
    grid-template-columns: 1fr;
  }
  .stock-grid div {
    border-right: 0;
    border-bottom: 1px solid #e2e9f0;
  }
}
</style>
