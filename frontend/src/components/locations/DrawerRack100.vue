<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Material } from '../../types'
import { formatQuantity } from '../../utils/format'
import { materialPrimaryIdentity } from '../../utils/materialIdentity'
import type { OrganizerLocation } from './OrganizerBox3D.vue'

const props = defineProps<{
  rack: OrganizerLocation | null
  bins: OrganizerLocation[]
  materials: Material[]
  selectedBinId: number | null
  highlightedBinId?: number | null
  closing: boolean
}>()

const emit = defineEmits<{
  select: [bin: OrganizerLocation]
  toggle: []
}>()

interface DrawerSlot {
  key: string
  label: string
  location: OrganizerLocation | null
}

const search = ref('')
const viewSize = ref<'large' | 'xlarge'>('xlarge')
const columnLabels = ['A', 'B', 'C', 'D', 'E']
const rowLabels = Array.from({ length: 20 }, (_, index) => String(index + 1).padStart(2, '0'))
const rowGroups = Array.from({ length: 4 }, (_, groupIndex) => ({
  key: `rows-${groupIndex + 1}`,
  rows: rowLabels.slice(groupIndex * 5, groupIndex * 5 + 5),
}))

const materialsByLocation = computed(() => {
  const result = new Map<number, Material[]>()
  props.materials.forEach((material) => {
    if (!material.location_id) return
    const items = result.get(material.location_id) ?? []
    items.push(material)
    result.set(material.location_id, items)
  })
  return result
})

const drawers = computed<DrawerSlot[]>(() => {
  const binsByName = new Map(props.bins.map((item) => [item.name.toUpperCase(), item]))
  return rowLabels.flatMap((row) =>
    columnLabels.map((column) => {
      const label = `${column}${row}`
      return {
        key: label,
        label,
        location: binsByName.get(label) ?? null,
      }
    }),
  )
})

const drawerGroups = computed(() =>
  Array.from({ length: 4 }, (_, groupIndex) => ({
    key: `drawers-${groupIndex + 1}`,
    label: `第 ${groupIndex * 5 + 1}–${groupIndex * 5 + 5} 行抽屉`,
    slots: drawers.value.slice(groupIndex * 25, groupIndex * 25 + 25),
  })),
)

function slotMaterials(slot: DrawerSlot): Material[] {
  return slot.location ? (materialsByLocation.value.get(slot.location.id) ?? []) : []
}

function hasDirectContent(slot: DrawerSlot): boolean {
  return Boolean(slot.location?.bin_material_name?.trim())
}

function hasContent(slot: DrawerSlot): boolean {
  return hasDirectContent(slot) || slotMaterials(slot).length > 0
}

function materialName(slot: DrawerSlot): string {
  if (hasDirectContent(slot)) return slot.location?.bin_material_name?.trim() || ''
  const material = slotMaterials(slot)[0]
  return material ? materialPrimaryIdentity(material) : ''
}

function contentNotes(slot: DrawerSlot): string {
  if (hasDirectContent(slot)) return slot.location?.bin_content_notes?.trim() || ''
  return slotMaterials(slot)[0]?.notes?.trim() || ''
}

function quantity(slot: DrawerSlot): number | null {
  if (hasDirectContent(slot)) return slot.location?.bin_quantity ?? null
  const items = slotMaterials(slot)
  if (!items.length) return null
  return items.reduce((total, item) => total + Number(item.quantity), 0)
}

function slotTitle(slot: DrawerSlot): string {
  if (!slot.location) return `${slot.label} · 尚未创建抽屉库位`
  if (!hasContent(slot)) return `${slot.label} · 空抽屉`
  const count = quantity(slot)
  const countText = count === null ? '' : ` · ${formatQuantity(count)} 件`
  const notes = contentNotes(slot)
  return `${slot.label} · ${materialName(slot)}${countText}${notes ? ` · ${notes}` : ''}`
}

function isLowStock(slot: DrawerSlot): boolean {
  if (hasDirectContent(slot)) return false
  return slotMaterials(slot).some(
    (item) => Number(item.available_quantity) <= Number(item.safety_stock),
  )
}

function matchesSearch(slot: DrawerSlot): boolean {
  const term = search.value.trim().toLocaleLowerCase('zh-CN')
  if (!term) return true
  return [slot.label, materialName(slot), contentNotes(slot)]
    .join(' ')
    .toLocaleLowerCase('zh-CN')
    .includes(term)
}

const matchCount = computed(() =>
  search.value.trim() ? drawers.value.filter(matchesSearch).length : 0,
)

function selectDrawer(slot: DrawerSlot) {
  if (slot.location) emit('select', slot.location)
}
</script>

<template>
  <div class="rack-visual" :class="[{ 'is-closing': closing }, `view-${viewSize}`]">
    <div class="rack-toolbar">
      <div>
        <span>100-DRAWER STORAGE RACK</span>
        <b>{{ rack?.name || '100 抽零件货架' }}</b>
        <small>20 行 × 5 列 · A01–E20</small>
      </div>
      <div class="rack-actions">
        <div class="size-control" aria-label="货架显示大小">
          <span>显示大小</span>
          <button
            type="button"
            :class="{ active: viewSize === 'large' }"
            :aria-pressed="viewSize === 'large'"
            data-size="large"
            @click="viewSize = 'large'"
          >
            大
          </button>
          <button
            type="button"
            :class="{ active: viewSize === 'xlarge' }"
            :aria-pressed="viewSize === 'xlarge'"
            data-size="xlarge"
            @click="viewSize = 'xlarge'"
          >
            超大
          </button>
        </div>
        <button
          type="button"
          class="overview-button"
          :disabled="!rack || closing"
          @click="emit('toggle')"
        >
          {{ closing ? '正在返回…' : '返回当前仓库库位列表' }}
        </button>
      </div>
    </div>

    <div class="rack-tools">
      <label>
        <span>快速定位抽屉</span>
        <input
          v-model="search"
          type="search"
          placeholder="输入编号、物料名称或备注"
          aria-label="搜索货架抽屉"
        />
      </label>
      <p v-if="search.trim()">
        找到 <b>{{ matchCount }}</b> 个匹配抽屉
      </p>
      <p v-else>点击任意抽屉，在右侧直接填写物料信息</p>
    </div>

    <div class="rack-stage">
      <div class="rack-cabinet">
        <div class="rack-top">
          <div>
            <span>PENGKA</span>
            <b>{{ rack?.code || 'RACK-100' }}</b>
          </div>
          <small>100 DRAWERS</small>
        </div>

        <div class="column-headings" aria-hidden="true">
          <span v-for="column in columnLabels" :key="column">{{ column }} 列</span>
        </div>

        <div class="rack-grid-shell">
          <div class="row-headings" aria-hidden="true">
            <div v-for="group in rowGroups" :key="group.key" class="row-heading-group">
              <span v-for="row in group.rows" :key="row">{{ row }}</span>
            </div>
          </div>
          <div class="drawer-grid">
            <div
              v-for="group in drawerGroups"
              :key="group.key"
              class="drawer-group"
              :aria-label="group.label"
            >
              <button
                v-for="slot in group.slots"
                :key="slot.key"
                type="button"
                class="rack-drawer"
                :class="{
                  occupied: hasContent(slot),
                  selected: slot.location?.id === selectedBinId,
                  warning: isLowStock(slot),
                  virtual: !slot.location,
                  muted: search.trim() && !matchesSearch(slot),
                  matched: search.trim() && matchesSearch(slot),
                  highlighted: slot.location?.id === highlightedBinId,
                }"
                :data-location-id="slot.location?.id"
                :data-drawer-label="slot.label"
                :aria-label="slotTitle(slot)"
                :title="slotTitle(slot)"
                :disabled="!slot.location"
                @click="selectDrawer(slot)"
              >
                <span class="drawer-code">{{ slot.label }}</span>
                <b>{{ hasContent(slot) ? materialName(slot) : '空' }}</b>
                <small v-if="quantity(slot) !== null">
                  {{ formatQuantity(quantity(slot)) }}
                </small>
                <i aria-hidden="true"></i>
              </button>
            </div>
          </div>
        </div>

        <div class="rack-base"><span></span></div>
      </div>
    </div>

    <div class="rack-specs">
      <span><b>65 cm</b> 柜宽</span>
      <span><b>117 cm</b> 柜高</span>
      <span><b>22 cm</b> 柜深</span>
      <span><b>9.7 × 4.7 × 19.3 cm</b> 单抽内尺寸</span>
    </div>

    <div class="rack-legend">
      <span><i class="empty"></i>空抽屉</span>
      <span><i class="used"></i>已有物料</span>
      <span><i class="low"></i>库存偏低</span>
      <span><i class="active"></i>当前选中</span>
    </div>
  </div>
</template>

<style scoped>
.rack-visual {
  --rack-width: 960px;
  --rack-grid-height: 1160px;
  --rack-heading-size: 12px;
  --rack-row-size: 10px;
  --rack-code-size: 11px;
  --rack-name-size: 14px;
  --rack-quantity-size: 11px;
  position: relative;
  min-width: 0;
  padding: 20px;
  border: 1px solid #dfe8f3;
  border-radius: 20px;
  background: radial-gradient(circle at 50% 10%, #ffffff 0, #f5f8fc 46%, #eaf0f7 100%);
  box-shadow: 0 8px 28px #29496b0a;
  overflow: hidden;
}

.rack-visual.view-xlarge {
  --rack-width: 1160px;
  --rack-grid-height: 1380px;
  --rack-heading-size: 13px;
  --rack-row-size: 11px;
  --rack-code-size: 12px;
  --rack-name-size: 16px;
  --rack-quantity-size: 12px;
}

.rack-visual::before {
  position: absolute;
  inset: 0;
  content: '';
  background-image:
    linear-gradient(#6682a30b 1px, transparent 1px),
    linear-gradient(90deg, #6682a30b 1px, transparent 1px);
  background-size: 28px 28px;
  pointer-events: none;
}

.rack-toolbar,
.rack-tools,
.rack-stage,
.rack-specs,
.rack-legend {
  position: relative;
  z-index: 1;
}

.rack-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.rack-toolbar > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.rack-toolbar span {
  color: #65809d;
  font-size: 10px;
  letter-spacing: 1.5px;
}

.rack-toolbar b {
  margin-top: 5px;
  color: #1f3854;
  font-size: 19px;
  overflow-wrap: anywhere;
}

.rack-toolbar small {
  margin-top: 3px;
  color: #8192a4;
  font-size: 11px;
}

.rack-toolbar > .rack-actions {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  flex-direction: row;
  gap: 10px;
}

.size-control {
  display: grid;
  grid-template-columns: auto auto;
  padding: 3px;
  border: 1px solid #d3deea;
  border-radius: 9px;
  background: #edf3f9;
  gap: 3px;
}

.size-control > span {
  grid-column: 1 / -1;
  padding: 1px 5px 2px;
  color: #75899d;
  font-size: 8px;
  letter-spacing: 0;
  text-align: center;
}

.size-control button {
  min-width: 42px;
  padding: 5px 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: #6b8095;
  cursor: pointer;
  font-size: 10px;
}

.size-control button.active {
  border-color: #8bb7dc;
  background: #fff;
  box-shadow: 0 2px 6px #365a7d1c;
  color: #246ba9;
  font-weight: 700;
}

.overview-button {
  flex: 0 0 auto;
  padding: 8px 13px;
  border: 1px solid #cbd9e8;
  border-radius: 9px;
  background: #ffffffd9;
  color: #376187;
  cursor: pointer;
}

.overview-button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.rack-tools {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 14px;
  margin-top: 14px;
  padding: 11px 13px;
  border: 1px solid #dce6f0;
  border-radius: 12px;
  background: #ffffffd9;
}

.rack-tools label {
  display: flex;
  min-width: 220px;
  max-width: 430px;
  flex: 1;
  flex-direction: column;
  gap: 5px;
}

.rack-tools label span {
  color: #506b86;
  font-size: 10px;
  font-weight: 700;
}

.rack-tools input {
  width: 100%;
  height: 34px;
  padding: 0 11px;
  border: 1px solid #cad7e5;
  border-radius: 8px;
  outline: none;
  background: #fff;
  color: #294765;
  font: inherit;
  font-size: 12px;
}

.rack-tools input:focus {
  border-color: #4c91ce;
  box-shadow: 0 0 0 3px #4c91ce24;
}

.rack-tools p {
  margin: 0 0 7px;
  color: #7e8fa1;
  font-size: 10px;
}

.rack-tools p b {
  color: #2874b7;
}

.rack-stage {
  display: flex;
  justify-content: center;
  min-width: 0;
  padding: 30px 10px 24px;
  overflow-x: auto;
}

.rack-cabinet {
  position: relative;
  flex: 0 0 var(--rack-width);
  width: var(--rack-width);
  padding: 22px 38px 34px;
  border: 7px solid #252d36;
  border-radius: 11px 11px 7px 7px;
  background: linear-gradient(90deg, #202832, #3a444f 48%, #202832);
  box-shadow:
    inset 0 0 0 2px #525d68,
    0 24px 36px #24364b38;
  transition:
    transform 0.24s ease,
    opacity 0.2s ease;
}

.is-closing .rack-cabinet {
  transform: translateY(22px) scale(0.88);
  opacity: 0;
}

.rack-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 43px;
  padding: 0 9px 10px;
  color: #cbd5df;
}

.rack-top > div {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.rack-top span {
  color: #63aaf0;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 1.5px;
}

.rack-top b {
  max-width: 390px;
  font-size: 15px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}

.rack-top small {
  color: #8f9ba7;
  font-size: 10px;
  letter-spacing: 1px;
}

.column-headings {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  margin-left: 35px;
  padding: 0 5px 8px;
  gap: 7px;
}

.column-headings span {
  color: #9caab7;
  font-size: var(--rack-heading-size);
  font-weight: 700;
  text-align: center;
}

.rack-grid-shell {
  display: grid;
  grid-template-columns: 29px minmax(0, 1fr);
  gap: 6px;
}

.row-headings {
  display: grid;
  grid-template-rows: repeat(4, minmax(0, 1fr));
  box-sizing: border-box;
  height: var(--rack-grid-height);
  padding: 10px 0;
  gap: 24px;
}

.row-heading-group {
  display: grid;
  grid-template-rows: repeat(5, minmax(0, 1fr));
  min-height: 0;
  gap: 6px;
}

.row-headings span {
  display: grid;
  place-items: center;
  color: #8d9aa7;
  font-size: var(--rack-row-size);
  font-weight: 700;
}

.drawer-grid {
  display: grid;
  grid-template-rows: repeat(4, minmax(0, 1fr));
  height: var(--rack-grid-height);
  padding: 10px;
  border: 3px solid #171d24;
  border-radius: 4px;
  background: #11171d;
  box-shadow: inset 0 0 16px #000;
  gap: 24px;
}

.drawer-group {
  position: relative;
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  grid-template-rows: repeat(5, minmax(0, 1fr));
  min-height: 0;
  gap: 6px 9px;
}

.drawer-group + .drawer-group::before {
  position: absolute;
  top: -14px;
  right: 4px;
  left: 4px;
  height: 3px;
  border-radius: 3px;
  content: '';
  background: linear-gradient(90deg, #54616e, #aab6c1 50%, #54616e);
  box-shadow: 0 1px 0 #000;
}

.rack-drawer {
  position: relative;
  display: grid;
  grid-template-columns: 45px minmax(0, 1fr) auto;
  align-items: center;
  appearance: none;
  min-width: 0;
  min-height: 0;
  padding: 4px 9px;
  border: 1px solid #6b7783;
  border-radius: 2px;
  background: linear-gradient(180deg, #f9fbfddd, #bac5cfe0);
  box-shadow:
    inset 0 1px #fff,
    0 2px 0 #080b0e;
  color: #34485b;
  cursor: pointer;
  transform: none;
  transition:
    transform 0.14s ease,
    border-color 0.14s ease,
    background 0.14s ease,
    opacity 0.14s ease;
  overflow: visible;
}

.rack-drawer::before {
  position: absolute;
  top: -2px;
  left: 50%;
  width: 24px;
  height: 5px;
  border-radius: 0 0 3px 3px;
  content: '';
  background: #27313a;
  transform: translateX(-50%);
}

.rack-drawer:hover:not(:disabled),
.rack-drawer:focus-visible {
  z-index: 4;
  border-color: #63aaf0;
  background: linear-gradient(180deg, #fff, #dcecf8);
  transform: translateX(10px);
  outline: none;
  box-shadow:
    inset 0 1px #fff,
    -5px 3px 8px #0005,
    0 0 0 2px #63aaf05c;
}

.rack-drawer.selected {
  z-index: 3;
  border-color: #42a5f5;
  background: linear-gradient(180deg, #ecf8ff, #9ed2f3);
  transform: translateX(10px);
  box-shadow:
    inset 0 1px #fff,
    -5px 3px 8px #0005,
    0 0 0 2px #4ca9ec;
}

.rack-drawer.highlighted {
  z-index: 5;
  outline: 4px solid #ffb33f;
  outline-offset: 2px;
  animation: focused-drawer-pulse 1.25s ease-in-out 3;
}

@keyframes focused-drawer-pulse {
  50% { filter: brightness(1.18); box-shadow: 0 0 0 8px #ffb33f35; }
}

.rack-drawer.occupied {
  border-color: #5798c6;
  background: linear-gradient(180deg, #edf8ff, #b7d9ef);
}

.rack-drawer.warning {
  border-color: #d78b32;
  background: linear-gradient(180deg, #fff8e9, #edc88f);
}

.rack-drawer.matched {
  z-index: 2;
  border-color: #39a7ef;
  box-shadow: 0 0 0 2px #39a7ef7a;
}

.rack-drawer.muted {
  opacity: 0.25;
}

.rack-drawer.virtual {
  border-style: dashed;
  cursor: default;
  opacity: 0.38;
}

.drawer-code {
  color: #5b6d7c;
  font-size: var(--rack-code-size);
  font-weight: 800;
  pointer-events: none;
}

.rack-drawer > b {
  min-width: 0;
  color: #284d6c;
  font-size: var(--rack-name-size);
  line-height: 1;
  text-align: center;
  white-space: nowrap;
  text-overflow: ellipsis;
  pointer-events: none;
  overflow: hidden;
}

.rack-drawer > small {
  color: #266fa4;
  font-size: var(--rack-quantity-size);
  font-weight: 800;
  pointer-events: none;
}

.rack-drawer > i {
  position: absolute;
  right: 4px;
  bottom: 2px;
  left: 4px;
  height: 1px;
  background: #ffffffa6;
  pointer-events: none;
}

.rack-base {
  position: absolute;
  right: 12px;
  bottom: -13px;
  left: 12px;
  height: 14px;
  border-radius: 2px 2px 6px 6px;
  background: linear-gradient(#313b45, #171d23);
  box-shadow: 0 5px 9px #1d2c3b35;
}

.rack-base span {
  position: absolute;
  right: 42%;
  bottom: 3px;
  left: 42%;
  height: 3px;
  border-radius: 3px;
  background: #06090c;
}

.rack-specs {
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 7px;
}

.rack-specs span {
  padding: 6px 9px;
  border: 1px solid #d8e2ec;
  border-radius: 8px;
  background: #ffffffc7;
  color: #7b8da0;
  font-size: 9px;
}

.rack-specs b {
  color: #365d80;
}

.rack-legend {
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  margin-top: 12px;
  color: #71849a;
  font-size: 10px;
  gap: 14px;
}

.rack-legend span {
  display: flex;
  align-items: center;
  gap: 5px;
}

.rack-legend i {
  width: 9px;
  height: 9px;
  border: 1px solid #9daab7;
  border-radius: 2px;
  background: #d6dee6;
}

.rack-legend i.used {
  border-color: #5798c6;
  background: #b7d9ef;
}

.rack-legend i.low {
  border-color: #d78b32;
  background: #edc88f;
}

.rack-legend i.active {
  border-color: #2876c5;
  background: #62b4ec;
}

@media (max-width: 640px) {
  .rack-visual {
    padding: 14px 10px;
  }

  .rack-toolbar {
    align-items: flex-start;
  }

  .rack-toolbar > .rack-actions {
    align-items: stretch;
    flex-direction: column-reverse;
  }

  .rack-toolbar b {
    font-size: 16px;
  }

  .overview-button {
    max-width: 128px;
    white-space: normal;
  }

  .rack-tools {
    align-items: stretch;
    flex-direction: column;
  }

  .rack-tools label {
    min-width: 0;
    max-width: none;
  }

  .rack-tools p {
    margin: 0;
  }

  .rack-stage {
    justify-content: flex-start;
    padding-right: 0;
    padding-left: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .rack-cabinet,
  .rack-drawer {
    transition-duration: 0.01ms !important;
  }
  .rack-drawer.highlighted { animation: none; }
}
</style>
