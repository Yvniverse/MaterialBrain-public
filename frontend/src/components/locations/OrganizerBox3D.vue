<script setup lang="ts">
import { computed } from 'vue'
import type { Material } from '../../types'
import { formatQuantity } from '../../utils/format'
import { materialPrimaryIdentity } from '../../utils/materialIdentity'

export interface OrganizerLocation {
  id: number
  parent_id: number | null
  code: string
  name: string
  type: string
  full_path: string
  manager: string
  notes: string
  is_active: boolean
  organizer_style?:
    | 'standard_56'
    | 'split_configurable'
    | 'drawer_rack_100'
    | 'shelf_rack_6'
    | 'shelf_storage_box'
    | null
  organizer_left_module?: 'small' | 'large' | null
  organizer_right_module?: 'small' | 'large' | null
  bin_material_name?: string
  bin_quantity?: number | null
  bin_content_notes?: string
}

export type OrganizerModule = 'small' | 'large'

const props = withDefaults(defineProps<{
  box: OrganizerLocation | null
  bins: OrganizerLocation[]
  materials: Material[]
  selectedBinId: number | null
  highlightedBinId?: number | null
  editable?: boolean
  open: boolean
  closing: boolean
  layoutSaving?: boolean
}>(), { editable: true })

const emit = defineEmits<{
  select: [bin: OrganizerLocation]
  toggle: []
  changeModule: [side: 'left' | 'right', module: OrganizerModule]
}>()

interface Slot {
  key: string
  label: string
  displayLabel: string
  location: OrganizerLocation | null
}

interface SlotSection {
  key: string
  title: string
  subtitle: string
  module: OrganizerModule
  slots: Slot[]
}

const isMixed = computed(() => props.box?.organizer_style === 'split_configurable')
const moduleSides = ['left', 'right'] as const
const leftModule = computed<OrganizerModule>(() => props.box?.organizer_left_module || 'small')
const rightModule = computed<OrganizerModule>(() => props.box?.organizer_right_module || 'small')

function standardSlotNames(): string[] {
  return Array.from({ length: 7 }, (_, row) =>
    Array.from(
      { length: 8 },
      (_, column) => `${String.fromCharCode(65 + row)}${String(column + 1).padStart(2, '0')}`,
    ),
  ).flat()
}

function moduleSlotNames(side: 'left' | 'right', module: OrganizerModule): string[] {
  const count = module === 'small' ? 28 : 8
  const sideCode = side === 'left' ? 'L' : 'R'
  const moduleCode = module === 'small' ? 'S' : 'L'
  return Array.from(
    { length: count },
    (_, index) => `${sideCode}-${moduleCode}${String(index + 1).padStart(2, '0')}`,
  )
}

function slotsForNames(names: string[]): Slot[] {
  const binsByName = new Map(props.bins.map((item) => [item.name.toUpperCase(), item]))
  return names.map((label) => ({
    key: label,
    label,
    displayLabel: label.includes('-') ? label.split('-')[1] : label,
    location: binsByName.get(label) ?? null,
  }))
}

const sections = computed<SlotSection[]>(() => {
  if (!isMixed.value) {
    return [
      {
        key: 'standard',
        title: '',
        subtitle: '',
        module: 'small',
        slots: slotsForNames(standardSlotNames()),
      },
    ]
  }
  return [
    {
      key: 'left',
      title: '左半区',
      subtitle: leftModule.value === 'small' ? '小格模块 · 28 格' : '大格模块 · 8 格',
      module: leftModule.value,
      slots: slotsForNames(moduleSlotNames('left', leftModule.value)),
    },
    {
      key: 'right',
      title: '右半区',
      subtitle: rightModule.value === 'small' ? '小格模块 · 28 格' : '大格模块 · 8 格',
      module: rightModule.value,
      slots: slotsForNames(moduleSlotNames('right', rightModule.value)),
    },
  ]
})

const capacity = computed(() =>
  sections.value.reduce((total, section) => total + section.slots.length, 0),
)

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

function slotMaterials(slot: Slot): Material[] {
  return slot.location ? (materialsByLocation.value.get(slot.location.id) ?? []) : []
}

function slotHasDirectContent(slot: Slot): boolean {
  return Boolean(slot.location?.bin_material_name?.trim())
}

function slotHasContent(slot: Slot): boolean {
  return slotHasDirectContent(slot) || slotMaterials(slot).length > 0
}

function slotPrimaryName(slot: Slot): string {
  if (slotHasDirectContent(slot)) return slot.location?.bin_material_name || ''
  const material = slotMaterials(slot)[0]
  return material ? materialPrimaryIdentity(material) : ''
}

function slotContentNotes(slot: Slot): string {
  if (slotHasDirectContent(slot)) return slot.location?.bin_content_notes?.trim() || ''
  return slotMaterials(slot)[0]?.notes?.trim() || ''
}

function slotQuantity(slot: Slot): number | null {
  if (slotHasDirectContent(slot)) return slot.location?.bin_quantity ?? null
  const items = slotMaterials(slot)
  if (!items.length) return null
  return items.reduce((total, item) => total + Number(item.quantity), 0)
}

function slotAdditionalCount(slot: Slot): number {
  return slotHasDirectContent(slot) ? 0 : Math.max(0, slotMaterials(slot).length - 1)
}

function slotTitle(slot: Slot): string {
  if (slotHasDirectContent(slot)) {
    const quantity =
      slot.location?.bin_quantity === null || slot.location?.bin_quantity === undefined
        ? ''
        : ` · ${slot.location.bin_quantity} 件`
    const notes = slot.location?.bin_content_notes ? ` · ${slot.location.bin_content_notes}` : ''
    return `${slot.label} · ${slot.location?.bin_material_name}${quantity}${notes}`
  }
  const items = slotMaterials(slot)
  if (!slot.location) return `${slot.label} · 尚未创建库位`
  if (!items.length) return `${slot.label} · 空格`
  return `${slot.label} · ${items.map(materialPrimaryIdentity).join('、')}`
}

function isLowStock(slot: Slot): boolean {
  if (slotHasDirectContent(slot)) return false
  return slotMaterials(slot).some(
    (item) => Number(item.available_quantity) <= Number(item.safety_stock),
  )
}

function selectSlot(slot: Slot) {
  if (slot.location) emit('select', slot.location)
}

function requestModule(side: 'left' | 'right', module: OrganizerModule) {
  const current = side === 'left' ? leftModule.value : rightModule.value
  if (!props.layoutSaving && current !== module) emit('changeModule', side, module)
}
</script>

<template>
  <div
    class="box-visual"
    :class="{
      'is-open': open,
      'is-closed': !open,
      'is-closing': closing,
      preview: !box,
    }"
  >
    <div class="visual-toolbar">
      <div>
        <span>{{ box ? `${capacity} 格平面库位图` : '实物模型预览' }}</span>
        <b>{{ box?.name || '210 × 176 mm 元件盒' }}</b>
      </div>
      <button type="button" class="lid-toggle" :disabled="!box || closing" @click="emit('toggle')">
        {{ closing ? '正在合上…' : open ? '合上并返回当前仓库' : '打开盒盖' }}
      </button>
    </div>

    <div v-if="isMixed && editable !== false" class="module-controls" aria-label="左右半区格型设置">
      <div v-for="side in moduleSides" :key="side" class="module-control">
        <span>{{ side === 'left' ? '左半区' : '右半区' }}</span>
        <div>
          <button
            type="button"
            :class="{ active: (side === 'left' ? leftModule : rightModule) === 'small' }"
            :disabled="layoutSaving || closing"
            @click="requestModule(side, 'small')"
          >
            小格模块 <b>28 格</b>
          </button>
          <button
            type="button"
            :class="{ active: (side === 'left' ? leftModule : rightModule) === 'large' }"
            :disabled="layoutSaving || closing"
            @click="requestModule(side, 'large')"
          >
            大格模块 <b>8 格</b>
          </button>
        </div>
      </div>
      <small>{{ layoutSaving ? '正在保存格型…' : '点击即可切换并保存该半区格型' }}</small>
    </div>

    <div class="scene">
      <div class="organizer-model">
        <div class="lid">
          <div class="lid-tab left"></div>
          <div class="lid-tab right"></div>
          <div class="lid-glass">
            <div class="lid-brand">
              <span>MATERIALBRAIN COMPONENT STORAGE</span>
              <b>{{ box?.name || `${capacity} IN 1` }}</b>
              <small>{{ box?.notes || '贴片元件可视化库位' }}</small>
            </div>
          </div>
          <div class="hinge left"></div>
          <div class="hinge right"></div>
        </div>

        <div class="base">
          <div class="base-shell">
            <div class="slot-grid" :class="isMixed ? 'mixed-slot-grid' : 'standard-slot-grid'">
              <section
                v-for="section in sections"
                :key="section.key"
                class="slot-section"
                :class="[`module-${section.module}`, { 'standard-section': !isMixed }]"
              >
                <div v-if="isMixed" class="half-heading">
                  <b>{{ section.title }}</b>
                  <span>{{ section.subtitle }}</span>
                </div>
                <div class="section-grid">
                  <button
                    v-for="slot in section.slots"
                    :key="slot.key"
                    type="button"
                    class="slot"
                    :data-slot-label="slot.label"
                    :aria-label="slotTitle(slot)"
                    :class="{
                      occupied: slotHasContent(slot),
                      selected: slot.location?.id === selectedBinId,
                      warning: isLowStock(slot),
                      virtual: !slot.location,
                      highlighted: slot.location?.id === highlightedBinId,
                    }"
                    :data-location-id="slot.location?.id"
                    :title="slotTitle(slot)"
                    :disabled="!slot.location"
                    @click="selectSlot(slot)"
                  >
                    <span class="slot-code">{{ slot.displayLabel }}</span>
                    <span v-if="slotHasContent(slot)" class="slot-material">
                      {{ slotPrimaryName(slot) }}
                    </span>
                    <span v-if="slotContentNotes(slot)" class="slot-notes">
                      {{ slotContentNotes(slot) }}
                    </span>
                    <span v-if="!slotHasContent(slot)" class="slot-empty">
                      {{ slot.location ? '空' : '待创建' }}
                    </span>
                    <strong v-if="slotQuantity(slot) !== null">
                      {{ formatQuantity(slotQuantity(slot)) }}
                    </strong>
                    <i v-if="slotAdditionalCount(slot)"> +{{ slotAdditionalCount(slot) }} </i>
                  </button>
                </div>
              </section>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="legend">
      <span><i class="empty"></i>空格</span>
      <span><i class="used"></i>已有物料</span>
      <span><i class="low"></i>库存偏低</span>
      <span><i class="active"></i>当前选中</span>
    </div>
  </div>
</template>

<style scoped>
.box-visual {
  position: relative;
  min-width: 0;
  padding: 18px 20px 12px;
  border: 1px solid #dfe8f3;
  border-radius: 20px;
  background: radial-gradient(circle at 50% 18%, #fff 0, #f7faff 48%, #edf3fa 100%);
  overflow: hidden;
}

.box-visual::before {
  position: absolute;
  inset: 0;
  content: '';
  background-image:
    linear-gradient(#6682a30b 1px, transparent 1px),
    linear-gradient(90deg, #6682a30b 1px, transparent 1px);
  background-size: 28px 28px;
  pointer-events: none;
}

.visual-toolbar {
  position: relative;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.visual-toolbar > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.visual-toolbar span {
  color: #6c819a;
  font-size: 11px;
  letter-spacing: 1.4px;
}

.visual-toolbar b {
  margin-top: 4px;
  color: #1e3857;
  font-size: 18px;
  overflow-wrap: anywhere;
}

.lid-toggle {
  padding: 8px 13px;
  border: 1px solid #cbd9e8;
  border-radius: 9px;
  background: #ffffffd9;
  color: #376187;
  cursor: pointer;
}

.lid-toggle:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.module-controls {
  position: relative;
  z-index: 5;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 14px;
  padding: 12px;
  border: 1px solid #dce7f2;
  border-radius: 13px;
  background: #ffffffd9;
  gap: 10px 14px;
}

.module-control {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  min-width: 0;
  gap: 10px;
}

.module-control > span {
  color: #365a7d;
  font-size: 12px;
  font-weight: 700;
}

.module-control > div {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  padding: 3px;
  border-radius: 9px;
  background: #edf3f9;
  gap: 3px;
}

.module-control button {
  min-width: 0;
  padding: 7px 6px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: #74879a;
  cursor: pointer;
  font-size: 11px;
  white-space: nowrap;
}

.module-control button b {
  margin-left: 3px;
  color: inherit;
}

.module-control button.active {
  border-color: #83b2dc;
  background: #fff;
  box-shadow: 0 2px 7px #365a7d1c;
  color: #246ba9;
}

.module-control button:disabled {
  cursor: wait;
  opacity: 0.65;
}

.module-controls > small {
  grid-column: 1 / -1;
  color: #8a99a9;
  font-size: 10px;
  text-align: right;
}

.scene {
  position: relative;
  z-index: 1;
  height: 930px;
}

.organizer-model {
  position: absolute;
  top: 4px;
  left: 3%;
  width: 94%;
  height: 910px;
}

.lid {
  position: absolute;
  z-index: 1;
  top: 28px;
  left: 2%;
  width: 96%;
  height: 255px;
  transform-origin: 50% 0;
  transition:
    transform 0.3s cubic-bezier(0.22, 0.8, 0.28, 1),
    opacity 0.22s;
  pointer-events: none;
}

.is-open .lid {
  transform: none;
}

.is-closed .lid {
  z-index: 8;
  transform: translateY(272px) scaleY(2.4);
  opacity: 0.96;
}

.is-closing .lid {
  z-index: 8;
  animation: lid-close-flat 0.32s cubic-bezier(0.22, 0.8, 0.28, 1) forwards;
}

@keyframes lid-close-flat {
  0% {
    transform: translateY(0) scaleY(1);
    opacity: 1;
  }

  48% {
    transform: translateY(112px) scaleY(1.35);
    opacity: 0.98;
  }

  100% {
    transform: translateY(272px) scaleY(2.4);
    opacity: 0.96;
  }
}

.lid-glass {
  position: absolute;
  inset: 0;
  border: 3px solid #aebed2;
  border-radius: 12px 12px 7px 7px;
  background: linear-gradient(145deg, #ffffffb8, #dfeaf692 55%, #f9fcffb8);
  box-shadow:
    inset 0 0 0 6px #ffffff7d,
    inset 0 -18px 32px #6e8cac19,
    0 16px 34px #243d5e21;
  backdrop-filter: blur(3px);
}

.lid-glass::after {
  position: absolute;
  inset: 12px;
  content: '';
  border: 1px solid #ffffffbd;
  border-radius: 7px;
}

.lid-brand {
  position: absolute;
  z-index: 2;
  top: 50%;
  left: 50%;
  display: flex;
  align-items: center;
  flex-direction: column;
  width: 80%;
  transform: translate(-50%, -50%);
  color: #59738f;
  text-align: center;
  text-shadow: 0 1px #fff;
}

.lid-brand span {
  font-size: 10px;
  letter-spacing: 2px;
}

.lid-brand b {
  margin: 8px 0 5px;
  color: #385774;
  font-size: 25px;
  letter-spacing: 3px;
}

.lid-brand small {
  max-width: 80%;
  font-size: 11px;
  overflow-wrap: anywhere;
}

.lid-tab {
  position: absolute;
  z-index: 3;
  top: -14px;
  width: 34px;
  height: 22px;
  border: 3px solid #aebed2;
  border-bottom: 0;
  border-radius: 6px 6px 0 0;
  background: #eef4fb;
}

.lid-tab.left {
  left: 18%;
}

.lid-tab.right {
  right: 18%;
}

.hinge {
  position: absolute;
  z-index: 4;
  bottom: -7px;
  width: 70px;
  height: 13px;
  border: 2px solid #91a5bd;
  border-radius: 8px;
  background: linear-gradient(#dce7f3, #aebfd1);
}

.hinge.left {
  left: 13%;
}

.hinge.right {
  right: 13%;
}

.base {
  position: absolute;
  z-index: 2;
  top: 300px;
  left: 0;
  width: 100%;
  height: 620px;
  transform: none;
  transition:
    transform 0.24s ease,
    opacity 0.2s ease;
  pointer-events: auto;
}

.is-closed .base,
.is-closing .base {
  transform: scale(0.985);
  opacity: 0.82;
  pointer-events: none;
}

.base-shell {
  padding: 14px;
  border: 2px solid #a7b8cc;
  border-radius: 15px;
  background: #e7eef6;
  box-shadow:
    inset 0 0 0 4px #ffffffa6,
    0 14px 28px #263f5f20;
  isolation: isolate;
  overflow: hidden;
}

.slot-grid {
  position: relative;
  z-index: 2;
  width: 100%;
  isolation: isolate;
  pointer-events: auto;
}

.slot-section {
  min-width: 0;
}

.section-grid {
  display: grid;
  gap: 8px;
}

.standard-slot-grid .section-grid {
  grid-template-columns: repeat(8, minmax(0, 1fr));
}

.mixed-slot-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.mixed-slot-grid .slot-section {
  padding: 10px;
  border: 1px solid #cbd8e6;
  border-radius: 11px;
  background: #dbe6f0;
  box-shadow: inset 0 0 0 2px #ffffff7d;
}

.half-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  height: 26px;
  color: #4c6782;
}

.half-heading b {
  font-size: 11px;
}

.half-heading span {
  color: #71879d;
  font-size: 9px;
}

.mixed-slot-grid .section-grid {
  height: 532px;
}

.mixed-slot-grid .module-small .section-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  grid-template-rows: repeat(7, minmax(0, 1fr));
}

.mixed-slot-grid .module-large .section-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-template-rows: repeat(4, minmax(0, 1fr));
}

.mixed-slot-grid .slot {
  height: auto;
}

.slot {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  appearance: none;
  min-width: 0;
  height: 76px;
  padding: 6px 5px 5px;
  border: 1px solid #b9c9d9;
  border-radius: 8px;
  background: #f9fbfd;
  box-shadow:
    inset 0 0 0 1px #fff,
    0 1px 3px #34506b1a;
  color: #637990;
  cursor: pointer;
  touch-action: manipulation;
  transform: none;
  transition:
    border-color 0.18s,
    background 0.18s,
    box-shadow 0.18s;
  overflow: hidden;
}

.slot::after {
  position: absolute;
  inset: 4px;
  content: '';
  border: 1px solid #ffffffbb;
  border-radius: 4px;
  pointer-events: none;
}

.slot:not(:disabled):hover {
  border-color: #5d91c8;
  background: #edf7ff;
  box-shadow:
    inset 0 0 0 1px #fff,
    0 0 0 2px #84b8e459;
}

.slot:not(:disabled):focus-visible {
  z-index: 3;
  outline: 3px solid #78b9ee;
  outline-offset: 2px;
}

.slot:disabled {
  cursor: default;
}

.slot.occupied {
  border-color: #78a8d2;
  background: #dceefa;
}

.slot.warning {
  border-color: #e7a458;
  background: #fff0d9;
}

.slot.selected {
  border-color: #2876c5;
  background: #b9ddf8;
  box-shadow:
    inset 0 0 0 2px #fff,
    0 0 0 3px #3e89cb73;
}

.slot.highlighted {
  z-index: 4;
  outline: 4px solid #ffb33f;
  outline-offset: 2px;
  animation: focused-slot-pulse 1.25s ease-in-out 3;
}

@keyframes focused-slot-pulse {
  50% { filter: brightness(1.12); box-shadow: 0 0 0 8px #ffb33f35; }
}

.slot.virtual {
  border-style: dashed;
  cursor: default;
  opacity: 0.52;
}

.slot-code {
  position: relative;
  z-index: 1;
  color: #5d7188;
  font-size: 11px;
  font-weight: 700;
  pointer-events: none;
}

.slot-material {
  position: absolute;
  z-index: 1;
  right: 4px;
  top: 50%;
  bottom: auto;
  left: 4px;
  transform: translateY(-50%);
  color: #204f78;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.1;
  text-align: center;
  white-space: nowrap;
  text-overflow: ellipsis;
  pointer-events: none;
  overflow: hidden;
}

.slot-notes {
  position: absolute;
  z-index: 1;
  top: calc(50% + 15px);
  right: 4px;
  left: 4px;
  color: #678097;
  font-size: 10px;
  font-weight: 500;
  line-height: 1.1;
  text-align: center;
  white-space: nowrap;
  text-overflow: ellipsis;
  pointer-events: none;
  overflow: hidden;
}

.slot-empty {
  position: absolute;
  z-index: 1;
  right: 4px;
  bottom: 5px;
  color: #a1afbd;
  font-size: 10px;
  pointer-events: none;
}

.slot strong {
  position: relative;
  z-index: 1;
  color: #206da8;
  font-size: 12px;
  pointer-events: none;
}

.slot i {
  position: absolute;
  z-index: 2;
  top: 4px;
  right: 4px;
  padding: 1px 3px;
  border-radius: 5px;
  background: #327fc0;
  color: #fff;
  font-size: 8px;
  font-style: normal;
  pointer-events: none;
}

.mixed-slot-grid .module-large .slot-code {
  font-size: 13px;
}

.mixed-slot-grid .module-large .slot-material {
  font-size: 24px;
}

.mixed-slot-grid .module-large .slot-notes {
  top: calc(50% + 17px);
  font-size: 12px;
}

.mixed-slot-grid .module-large .slot-empty {
  font-size: 11px;
}

.mixed-slot-grid .module-large .slot strong {
  font-size: 13px;
}

.legend {
  position: relative;
  z-index: 2;
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 18px;
  color: #71849a;
  font-size: 11px;
}

.legend span {
  display: flex;
  align-items: center;
  gap: 6px;
}

.legend i {
  width: 9px;
  height: 9px;
  border: 1px solid #b8c7d7;
  border-radius: 3px;
  background: #f7fafc;
}

.legend i.used {
  border-color: #78a8d2;
  background: #cfe7f8;
}

.legend i.low {
  border-color: #e7a458;
  background: #f9dfba;
}

.legend i.active {
  border-color: #2876c5;
  background: #78b7e6;
}

.preview .lid-toggle {
  visibility: hidden;
}

@media (max-width: 900px) {
  .scene {
    height: 810px;
  }

  .organizer-model {
    height: 790px;
  }

  .lid {
    height: 220px;
  }

  .base {
    top: 250px;
    height: 550px;
  }

  .slot {
    height: 68px;
  }

  .slot-grid {
    gap: 10px;
  }

  .section-grid {
    gap: 5px;
  }

  .mixed-slot-grid .section-grid {
    height: 476px;
  }

  .is-closed .lid {
    transform: translateY(222px) scaleY(2.44);
  }

  .is-closing .lid {
    animation-name: lid-close-flat-medium;
  }

  @keyframes lid-close-flat-medium {
    0% {
      transform: translateY(0) scaleY(1);
      opacity: 1;
    }

    48% {
      transform: translateY(90px) scaleY(1.4);
      opacity: 0.98;
    }

    100% {
      transform: translateY(222px) scaleY(2.44);
      opacity: 0.96;
    }
  }
}

@media (max-width: 640px) {
  .box-visual {
    padding: 14px 10px 10px;
  }

  .visual-toolbar {
    align-items: flex-start;
  }

  .visual-toolbar b {
    font-size: 15px;
  }

  .module-controls {
    grid-template-columns: 1fr;
    padding: 9px;
  }

  .module-controls > small {
    grid-column: 1;
  }

  .module-control {
    grid-template-columns: 54px minmax(0, 1fr);
  }

  .module-control button {
    font-size: 10px;
  }

  .lid-toggle {
    max-width: 145px;
    white-space: normal;
  }

  .scene {
    height: 650px;
  }

  .organizer-model {
    top: 14px;
    left: 0;
    width: 100%;
    height: 630px;
  }

  .lid {
    top: 12px;
    height: 145px;
  }

  .is-closed .lid {
    transform: translateY(158px) scaleY(3.18);
  }

  .is-closing .lid {
    animation-name: lid-close-flat-mobile;
  }

  @keyframes lid-close-flat-mobile {
    0% {
      transform: translateY(0) scaleY(1);
      opacity: 1;
    }

    48% {
      transform: translateY(64px) scaleY(1.6);
      opacity: 0.98;
    }

    100% {
      transform: translateY(158px) scaleY(3.18);
      opacity: 0.96;
    }
  }

  .lid-brand b {
    font-size: 18px;
  }

  .lid-brand small {
    display: none;
  }

  .base {
    top: 170px;
    left: 0;
    width: 100%;
    height: 470px;
    transform: none;
  }

  .base-shell {
    padding: 10px;
  }

  .slot-grid {
    gap: 3px;
  }

  .section-grid {
    gap: 3px;
  }

  .mixed-slot-grid {
    gap: 5px;
  }

  .mixed-slot-grid .slot-section {
    padding: 5px;
    border-radius: 7px;
  }

  .half-heading {
    height: 25px;
  }

  .half-heading span {
    max-width: 82px;
    white-space: nowrap;
    text-overflow: ellipsis;
    overflow: hidden;
  }

  .mixed-slot-grid .section-grid {
    height: 420px;
  }

  .slot {
    height: 60px;
    padding: 4px 3px;
    border-radius: 5px;
    box-shadow:
      inset 0 0 0 1px #fff,
      0 1px 2px #34506b17;
  }

  .slot-material {
    font-size: 18px;
  }

  .slot-notes {
    top: calc(50% + 12px);
    font-size: 9px;
  }

  .slot-code {
    font-size: 10px;
  }

  .slot i {
    font-size: 8px;
  }

  .slot strong,
  .slot-empty {
    display: none;
  }

  .legend {
    gap: 9px;
  }

  .legend span {
    font-size: 9px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .lid,
  .base {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
  .slot.highlighted { animation: none; }
}
</style>
