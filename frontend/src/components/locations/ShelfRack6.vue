<script setup lang="ts">
import { computed } from 'vue'
import type { Material } from '../../types'
import { formatQuantity } from '../../utils/format'
import { materialPrimaryIdentity } from '../../utils/materialIdentity'
import type { OrganizerLocation } from './OrganizerBox3D.vue'

const props = withDefaults(defineProps<{
  rack: OrganizerLocation | null
  shelves: OrganizerLocation[]
  storageBoxes: OrganizerLocation[]
  items: OrganizerLocation[]
  materials: Material[]
  selectedBoxId: number | null
  highlightedLocationId?: number | null
  editable?: boolean
  closing: boolean
}>(), { editable: true })

const emit = defineEmits<{
  selectBox: [box: OrganizerLocation]
  addBox: [shelf: OrganizerLocation]
  deleteBox: [box: OrganizerLocation]
  toggle: []
}>()

const sortedShelves = computed(() =>
  [...props.shelves].sort((a, b) => b.name.localeCompare(a.name, 'zh-CN', { numeric: true })),
)

const occupiedLevelCount = computed(
  () => new Set(props.storageBoxes.map((box) => box.parent_id).filter(Boolean)).size,
)

const totalMaterialKinds = computed(() => {
  const names = new Set<string>()
  props.storageBoxes.forEach((box) => boxMaterialNames(box).forEach((name) => names.add(name)))
  return names.size
})

function boxesForShelf(shelf: OrganizerLocation): OrganizerLocation[] {
  return props.storageBoxes
    .filter((box) => box.parent_id === shelf.id)
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN', { numeric: true }))
}

function itemsForBox(box: OrganizerLocation): OrganizerLocation[] {
  return props.items
    .filter((item) => item.parent_id === box.id)
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN', { numeric: true }))
}

function formalMaterialsForBox(box: OrganizerLocation): Material[] {
  const itemIds = new Set(itemsForBox(box).map((item) => item.id))
  return props.materials.filter(
    (material) =>
      material.location_id === box.id ||
      (material.location_id !== null && itemIds.has(material.location_id)),
  )
}

function boxMaterialNames(box: OrganizerLocation): string[] {
  const directNames = itemsForBox(box)
    .map((item) => item.bin_material_name?.trim())
    .filter((name): name is string => Boolean(name))
  const formalNames = formalMaterialsForBox(box)
    .map(materialPrimaryIdentity)
    .filter(Boolean)
  return [...new Set([...directNames, ...formalNames])]
}

function boxQuantity(box: OrganizerLocation): number {
  const simpleQuantity = itemsForBox(box).reduce(
    (total, item) => total + Number(item.bin_quantity ?? 0),
    0,
  )
  const formalQuantity = formalMaterialsForBox(box).reduce(
    (total, material) => total + Number(material.quantity),
    0,
  )
  return simpleQuantity + formalQuantity
}

function shelfNumber(shelf: OrganizerLocation): string {
  const match = shelf.name.match(/\d+/)
  return match?.[0].padStart(2, '0') ?? shelf.code
}

function selectStorageBox(box: OrganizerLocation) {
  emit('selectBox', box)
}
</script>

<template>
  <div class="shelf-rack-visual" :class="{ 'is-closing': closing }">
    <header class="rack-toolbar">
      <div class="rack-identity">
        <span>MODULAR SHELF SYSTEM · 6 LEVELS</span>
        <b>{{ rack?.name || '六层箱式货架' }}</b>
        <small>每层初始为空，可按照现场摆放情况自由添加物料箱</small>
      </div>

      <div class="rack-toolbar-side">
        <div class="rack-summary" aria-label="货架使用概况">
          <span
            ><b>{{ occupiedLevelCount }}</b
            >/6 层使用</span
          >
          <span
            ><b>{{ storageBoxes.length }}</b> 个箱子</span
          >
          <span
            ><b>{{ totalMaterialKinds }}</b> 种物料</span
          >
        </div>
        <button
          type="button"
          class="overview-button"
          :disabled="!rack || closing"
          @click="emit('toggle')"
        >
          <i aria-hidden="true">‹</i>
          {{ closing ? '正在返回…' : '返回当前仓库库位列表' }}
        </button>
      </div>
    </header>

    <div class="rack-guidance">
      <span><i aria-hidden="true"></i>点击蓝色箱子，在右侧管理箱内物料</span>
      <span>每层可放多个箱子，超出宽度时可横向滚动</span>
    </div>

    <div class="rack-stage">
      <div class="warehouse-wall" aria-hidden="true"></div>
      <div class="rack-floor" aria-hidden="true"></div>

      <div class="rack-frame">
        <div class="rack-cast-shadow" aria-hidden="true"></div>
        <div class="rack-cross-brace brace-a" aria-hidden="true"></div>
        <div class="rack-cross-brace brace-b" aria-hidden="true"></div>

        <div class="rack-nameplate">
          <span>PENGKA <i>STORAGE</i></span>
          <b>{{ rack?.code || 'SHELF-RACK-01' }}</b>
          <small>MAX · 6 LEVELS</small>
        </div>

        <div class="rack-upright left" aria-hidden="true"><i></i></div>
        <div class="rack-upright right" aria-hidden="true"><i></i></div>

        <div class="shelf-stack">
          <section
            v-for="shelf in sortedShelves"
            :key="shelf.id"
            class="rack-level"
            :data-shelf-id="shelf.id"
            :data-location-id="shelf.id"
            :class="{
              occupied: boxesForShelf(shelf).length,
              highlighted: shelf.id === highlightedLocationId,
            }"
          >
            <div class="level-badge">
              <span>LEVEL</span>
              <b>{{ shelfNumber(shelf) }}</b>
            </div>

            <div class="level-space">
              <div v-if="!boxesForShelf(shelf).length" class="empty-level">
                <i aria-hidden="true"></i>
                <div>
                  <b>本层空置</b>
                  <span>可在右侧开始摆放箱子</span>
                </div>
              </div>

              <div class="boxes-row">
                <div
                  v-for="box in boxesForShelf(shelf)"
                  :key="box.id"
                  class="storage-box"
                  :class="{
                    selected: box.id === selectedBoxId,
                    highlighted:
                      box.id === highlightedLocationId ||
                      itemsForBox(box).some((item) => item.id === highlightedLocationId),
                  }"
                  :data-location-id="
                    itemsForBox(box).some((item) => item.id === highlightedLocationId)
                      ? highlightedLocationId
                      : box.id
                  "
                  role="button"
                  tabindex="0"
                  :aria-label="`选择箱子 ${box.name}`"
                  @click="selectStorageBox(box)"
                  @keydown.enter.self.prevent="selectStorageBox(box)"
                  @keydown.space.self.prevent="selectStorageBox(box)"
                >
                  <div class="bin-shell" aria-hidden="true">
                    <i class="bin-cavity"></i>
                    <i class="bin-rim"></i>
                    <i class="bin-side left"></i>
                    <i class="bin-side right"></i>
                  </div>

                  <button
                    v-if="editable !== false"
                    type="button"
                    class="box-delete"
                    :aria-label="`删除箱子 ${box.name}`"
                    title="从货架移除箱子"
                    @click.stop="emit('deleteBox', box)"
                  >
                    ×
                  </button>

                  <div class="bin-label">
                    <span>{{ box.code }}</span>
                    <strong>{{ box.name }}</strong>
                    <small v-if="boxMaterialNames(box).length">
                      {{ boxMaterialNames(box).slice(0, 2).join(' · ') }}
                    </small>
                    <small v-else>等待放入物料</small>
                  </div>

                  <div class="bin-stats">
                    <span>{{ boxMaterialNames(box).length }} 种</span>
                    <b v-if="boxQuantity(box)">{{ formatQuantity(boxQuantity(box)) }} 件</b>
                    <b v-else>空箱</b>
                  </div>
                  <span v-if="box.id === selectedBoxId" class="selected-tag">当前选中</span>
                </div>

                <button v-if="editable !== false" type="button" class="add-storage-box" @click="emit('addBox', shelf)">
                  <span class="add-bin-icon" aria-hidden="true"><i></i></span>
                  <b>{{ boxesForShelf(shelf).length ? '继续添加' : '放置箱子' }}</b>
                  <small>{{ shelf.name }}</small>
                </button>
              </div>
            </div>

            <div class="shelf-plank" aria-hidden="true"><i></i><span></span></div>
          </section>
        </div>

        <div class="rack-foot left" aria-hidden="true"></div>
        <div class="rack-foot right" aria-hidden="true"></div>
      </div>
    </div>

    <footer class="rack-legend">
      <span><i class="legend-bin"></i>已放置箱子</span>
      <span><i class="legend-selected"></i>当前选中</span>
      <span><i class="legend-empty"></i>空置层位</span>
      <small>箱子与箱内物料均实时保存到库位数据库</small>
    </footer>
  </div>
</template>

<style scoped>
.shelf-rack-visual {
  --ink: #18324b;
  --muted: #70869a;
  --line: #dbe5ee;
  position: relative;
  min-width: 0;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 20px;
  background: #f8fafc;
  box-shadow: 0 14px 38px #29496b12;
  overflow: hidden;
}

.rack-toolbar {
  position: relative;
  z-index: 5;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.rack-identity {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.rack-identity > span {
  color: #4a7ca8;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 1.7px;
}

.rack-identity > b {
  margin-top: 5px;
  color: var(--ink);
  font-size: 21px;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.rack-identity > small {
  margin-top: 5px;
  color: #8293a3;
  font-size: 11px;
}

.rack-toolbar-side {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  gap: 12px;
}

.rack-summary {
  display: flex;
  padding: 6px;
  border: 1px solid #dbe5ee;
  border-radius: 11px;
  background: #fff;
  box-shadow: 0 5px 14px #29496b0b;
}

.rack-summary span {
  padding: 3px 10px;
  color: #7c8d9e;
  font-size: 9px;
  white-space: nowrap;
}

.rack-summary span + span {
  border-left: 1px solid #e7edf3;
}

.rack-summary b {
  color: #286a9e;
  font-size: 12px;
}

.overview-button {
  display: inline-flex;
  align-items: center;
  height: 38px;
  padding: 0 13px;
  border: 1px solid #cbd9e7;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 5px 14px #29496b0b;
  color: #376187;
  cursor: pointer;
  font-size: 11px;
  gap: 6px;
}

.overview-button i {
  color: #2d75ad;
  font-size: 21px;
  font-style: normal;
  line-height: 1;
}

.overview-button:hover:not(:disabled) {
  border-color: #75a7ce;
  background: #f4f9fd;
}

.overview-button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.rack-guidance {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  padding: 9px 12px;
  border: 1px solid #dfe8f0;
  border-radius: 10px;
  background: #fff;
  color: #7d8fa0;
  font-size: 10px;
  gap: 14px;
}

.rack-guidance span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.rack-guidance i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #3e91cf;
  box-shadow: 0 0 0 4px #3e91cf18;
}

.rack-stage {
  position: relative;
  min-width: 0;
  margin-top: 14px;
  padding: 34px 18px 45px;
  border: 1px solid #dce5ed;
  border-radius: 16px;
  background: #edf2f6;
  overflow-x: auto;
  overflow-y: hidden;
}

.warehouse-wall {
  position: absolute;
  inset: 0 0 29%;
  background:
    linear-gradient(90deg, #ffffff70 1px, transparent 1px),
    linear-gradient(#ffffff80 1px, transparent 1px), linear-gradient(120deg, #f9fbfc, #e5ebf0);
  background-size:
    42px 42px,
    42px 42px,
    auto;
}

.rack-floor {
  position: absolute;
  right: -8%;
  bottom: -90px;
  left: -8%;
  height: 42%;
  background:
    repeating-linear-gradient(87deg, transparent 0 74px, #8092a20d 75px 76px),
    linear-gradient(#d8e0e7, #f1f4f7);
  transform: perspective(520px) rotateX(58deg);
  transform-origin: 50% 100%;
}

.rack-frame {
  position: relative;
  z-index: 2;
  box-sizing: border-box;
  width: 100%;
  min-width: 900px;
  min-height: 808px;
  padding: 70px 52px 35px;
  transition:
    transform 0.28s ease,
    opacity 0.22s ease;
}

.is-closing .rack-frame {
  transform: translateY(25px) scale(0.91);
  opacity: 0;
}

.rack-cast-shadow {
  position: absolute;
  right: 8%;
  bottom: 1px;
  left: 8%;
  height: 28px;
  border-radius: 50%;
  background: #263c4f2e;
  filter: blur(12px);
  transform: skewX(-11deg);
}

.rack-nameplate {
  position: absolute;
  z-index: 6;
  top: 0;
  right: 64px;
  left: 64px;
  display: flex;
  align-items: center;
  height: 48px;
  padding: 0 17px;
  border: 1px solid #aeb9c3;
  border-radius: 5px;
  background: linear-gradient(180deg, #fbfcfd, #d2d9df 54%, #b4bec7);
  box-shadow:
    inset 0 1px #fff,
    0 6px 10px #2e405021;
  color: #344b60;
  gap: 12px;
}

.rack-nameplate::before,
.rack-nameplate::after {
  width: 7px;
  height: 7px;
  border: 1px solid #7b8894;
  border-radius: 50%;
  content: '';
  background: #aab4bd;
  box-shadow: inset 0 1px 2px #566572;
}

.rack-nameplate > span {
  color: #285e88;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 1.4px;
}

.rack-nameplate > span i {
  color: #788b9b;
  font-size: 8px;
  font-style: normal;
  font-weight: 600;
}

.rack-nameplate > b {
  min-width: 0;
  color: #233d54;
  font-size: 15px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}

.rack-nameplate > small {
  margin-left: auto;
  color: #6e7f8f;
  font-size: 9px;
  letter-spacing: 1px;
}

.rack-upright {
  position: absolute;
  z-index: 7;
  top: -8px;
  bottom: 10px;
  width: 31px;
  border: 1px solid #84919c;
  border-radius: 3px 3px 2px 2px;
  background:
    repeating-linear-gradient(180deg, transparent 0 16px, #596774 16px 20px),
    linear-gradient(90deg, #7b8995, #e9edf0 33%, #bac3ca 66%, #75828d);
  box-shadow:
    inset 2px 0 #ffffff9c,
    inset -2px 0 #59677454,
    4px 7px 11px #3043542b;
}

.rack-upright::before {
  position: absolute;
  inset: 5px 9px;
  border-radius: 5px;
  content: '';
  background: repeating-linear-gradient(180deg, #53616d 0 5px, transparent 5px 17px);
}

.rack-upright.left {
  left: 12px;
}

.rack-upright.right {
  right: 12px;
}

.rack-cross-brace {
  position: absolute;
  z-index: 0;
  top: 79px;
  bottom: 42px;
  left: 50%;
  width: 10px;
  border: 1px solid #9faab3;
  background: linear-gradient(90deg, #8b98a3, #e1e6ea 52%, #7e8b96);
  box-shadow: 2px 3px 5px #394b5b1c;
  opacity: 0.5;
  transform-origin: 50% 50%;
}

.rack-cross-brace.brace-a {
  transform: rotate(51deg) scaleY(1.33);
}

.rack-cross-brace.brace-b {
  transform: rotate(-51deg) scaleY(1.33);
}

.shelf-stack {
  position: relative;
  z-index: 3;
  display: grid;
  min-width: 0;
  gap: 14px;
}

.rack-level {
  position: relative;
  min-width: 0;
  min-height: 98px;
  padding: 0 0 15px;
}

.level-badge {
  position: absolute;
  z-index: 9;
  bottom: -2px;
  left: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 67px;
  height: 18px;
  border: 1px solid #697783;
  border-radius: 3px;
  background: linear-gradient(180deg, #eef2f5, #9faab3);
  box-shadow:
    inset 0 1px #fff,
    2px 4px 6px #2a3d4e26;
  color: #526b80;
  gap: 4px;
}

.level-badge span {
  font-size: 6px;
  font-weight: 700;
  letter-spacing: 0.9px;
}

.level-badge b {
  color: #203e57;
  font-size: 11px;
  line-height: 1;
}

.level-space {
  position: relative;
  display: flex;
  align-items: flex-end;
  min-width: 0;
  min-height: 86px;
  padding: 7px 13px 5px;
  border-right: 1px solid #aebbc6;
  border-left: 1px solid #aebbc6;
  background: #edf2f5c7;
  box-shadow: inset 0 -16px 18px #8595a320;
}

.boxes-row {
  display: flex;
  align-items: flex-end;
  width: 100%;
  min-width: 0;
  padding: 4px 4px 8px;
  gap: 13px;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: thin;
  scrollbar-color: #9fb1c0 transparent;
}

.boxes-row::-webkit-scrollbar {
  height: 4px;
}

.boxes-row::-webkit-scrollbar-thumb {
  border-radius: 8px;
  background: #9fb1c0;
}

.empty-level {
  position: absolute;
  top: 11px;
  right: 152px;
  bottom: 14px;
  left: 17px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed #afbecb;
  border-radius: 7px;
  background: #f4f7f98f;
  color: #7c8e9e;
  pointer-events: none;
  gap: 9px;
}

.empty-level > i {
  position: relative;
  width: 30px;
  height: 20px;
  border: 1px solid #a8b7c4;
  border-radius: 3px;
  background: #edf2f6;
}

.empty-level > i::before {
  position: absolute;
  top: -5px;
  left: 50%;
  width: 14px;
  height: 5px;
  border: 1px solid #a8b7c4;
  border-bottom: 0;
  border-radius: 3px 3px 0 0;
  content: '';
  transform: translateX(-50%);
}

.empty-level > div {
  display: flex;
  flex-direction: column;
}

.empty-level b {
  color: #61788b;
  font-size: 10px;
}

.empty-level span {
  margin-top: 2px;
  color: #95a3af;
  font-size: 8px;
}

.storage-box {
  position: relative;
  display: flex;
  box-sizing: border-box;
  flex: 0 0 188px;
  width: 188px;
  height: 78px;
  padding: 24px 12px 8px;
  border: 0;
  outline: none;
  color: #183e5e;
  cursor: pointer;
  flex-direction: column;
  transition:
    transform 0.17s ease,
    filter 0.17s ease;
}

.bin-shell,
.bin-cavity,
.bin-rim,
.bin-side {
  position: absolute;
  pointer-events: none;
}

.bin-shell {
  z-index: -1;
  inset: 7px 0 0;
  border: 2px solid #155f99;
  border-radius: 4px 4px 7px 7px;
  background: linear-gradient(150deg, #5db1e6, #2588cb 52%, #1266a2);
  box-shadow:
    inset 0 0 0 3px #ffffff24,
    3px 5px 0 #2e668e,
    6px 8px 10px #253f522e;
}

.bin-cavity {
  z-index: 1;
  top: -10px;
  right: 7px;
  left: 7px;
  height: 23px;
  border: 2px solid #246eaa;
  border-radius: 5px 5px 2px 2px;
  background: linear-gradient(180deg, #0b2d48, #1e6ea8 65%, #65b6e6);
  box-shadow:
    inset 0 5px 8px #0a2b45b8,
    0 2px #91cef0;
  transform: perspective(140px) rotateX(12deg);
  transform-origin: 50% 100%;
}

.bin-rim {
  z-index: 2;
  top: 8px;
  right: 5px;
  left: 5px;
  height: 5px;
  border-radius: 3px;
  background: linear-gradient(#83c7ed, #277db8);
  box-shadow: 0 2px 2px #164f78;
}

.bin-side {
  top: 13px;
  bottom: 2px;
  width: 8px;
  background: #226c9f;
}

.bin-side.left {
  left: -3px;
  transform: skewY(12deg);
}

.bin-side.right {
  right: -3px;
  transform: skewY(-12deg);
}

.storage-box:hover,
.storage-box:focus-visible {
  z-index: 4;
  filter: saturate(1.08) brightness(1.04);
  transform: translateY(-5px);
}

.storage-box:focus-visible {
  border-radius: 7px;
  box-shadow: 0 0 0 3px #3c91ce4f;
}

.storage-box.selected {
  z-index: 3;
  transform: translateY(-5px);
}

.storage-box.highlighted .bin-shell,
.rack-level.highlighted .level-space {
  outline: 4px solid #ffb33f;
  outline-offset: 3px;
  animation: focused-shelf-pulse 1.25s ease-in-out 3;
}

@keyframes focused-shelf-pulse {
  50% { filter: brightness(1.12); box-shadow: 0 0 0 8px #ffb33f35; }
}

.storage-box.selected .bin-shell {
  border-color: #0c75be;
  background: linear-gradient(150deg, #82cbf3, #359ad8 52%, #1578b8);
  box-shadow:
    inset 0 0 0 3px #ffffff38,
    0 0 0 3px #ffb84d,
    3px 6px 0 #326d97,
    8px 12px 17px #1d435d3d;
}

.storage-box.selected .bin-rim {
  background: linear-gradient(#a9ddf7, #2b91ce);
}

.bin-label {
  display: flex;
  min-width: 0;
  padding: 4px 8px 3px;
  border: 1px solid #bdd5e4;
  border-radius: 3px;
  background: #f9fcfe;
  box-shadow: inset 0 0 0 1px #fff;
  flex-direction: column;
  text-align: left;
}

.bin-label > span {
  color: #86a0b3;
  font-size: 5px;
  letter-spacing: 0.5px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}

.bin-label strong {
  color: #1d4d70;
  font-size: 11px;
  line-height: 1.2;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}

.bin-label small {
  margin-top: 1px;
  color: #758b9d;
  font-size: 7px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}

.bin-stats {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  margin-top: 3px;
  padding: 0 4px;
  color: #d9effc;
  font-size: 8px;
}

.bin-stats b {
  color: #fff;
  font-size: 8px;
}

.selected-tag {
  position: absolute;
  z-index: 6;
  top: -8px;
  left: 11px;
  padding: 3px 6px;
  border-radius: 7px;
  background: #ffb549;
  box-shadow: 0 2px 5px #724a1538;
  color: #70450b;
  font-size: 6px;
  font-weight: 800;
}

.box-delete {
  position: absolute;
  z-index: 8;
  top: 12px;
  right: 6px;
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 1px solid #ffffff8c;
  border-radius: 50%;
  background: #164f78c9;
  color: #e8f6ff;
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
  opacity: 0;
  transition:
    opacity 0.15s ease,
    background 0.15s ease;
}

.storage-box:hover .box-delete,
.storage-box:focus-within .box-delete,
.storage-box.selected .box-delete {
  opacity: 1;
}

.box-delete:hover,
.box-delete:focus-visible {
  border-color: #fff;
  outline: none;
  background: #d4524b;
  color: #fff;
}

.add-storage-box {
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  flex: 0 0 125px;
  width: 125px;
  height: 69px;
  margin-left: auto;
  padding: 6px;
  border: 1px dashed #7e9bb2;
  border-radius: 7px;
  background: #f8fbfdd9;
  color: #55748d;
  cursor: pointer;
  flex-direction: column;
  transition:
    border-color 0.15s ease,
    background 0.15s ease,
    transform 0.15s ease;
}

.add-storage-box:hover,
.add-storage-box:focus-visible {
  border-color: #2f82bd;
  outline: none;
  background: #e9f5fd;
  color: #216c9f;
  transform: translateY(-2px);
}

.add-bin-icon {
  position: relative;
  width: 30px;
  height: 18px;
  border: 1px solid #7ea1ba;
  border-radius: 3px 3px 5px 5px;
  background: #dcebf5;
  box-shadow: 2px 2px 0 #aac0d0;
}

.add-bin-icon::before,
.add-bin-icon::after {
  position: absolute;
  top: 8px;
  left: 50%;
  width: 10px;
  height: 1px;
  content: '';
  background: #397da9;
  transform: translateX(-50%);
}

.add-bin-icon::after {
  transform: translateX(-50%) rotate(90deg);
}

.add-bin-icon i {
  position: absolute;
  top: -3px;
  right: 3px;
  left: 3px;
  height: 5px;
  border: 1px solid #7ea1ba;
  border-radius: 3px;
  background: #eef6fb;
}

.add-storage-box > b {
  margin-top: 5px;
  font-size: 9px;
}

.add-storage-box > small {
  margin-top: 1px;
  color: #91a0ad;
  font-size: 7px;
}

.shelf-plank {
  position: absolute;
  z-index: 8;
  right: -9px;
  bottom: 0;
  left: -9px;
  height: 18px;
  border: 1px solid #7d8994;
  background: linear-gradient(180deg, #f4f6f8 0 20%, #bbc4cb 22% 58%, #7e8a94 60% 100%);
  box-shadow:
    inset 0 1px #fff,
    0 5px 8px #2f405027;
}

.shelf-plank::after {
  position: absolute;
  right: -1px;
  bottom: -5px;
  left: 8px;
  height: 5px;
  content: '';
  background: #717e89;
  clip-path: polygon(0 0, 100% 0, 98% 100%, 2% 100%);
  opacity: 0.75;
}

.shelf-plank > i,
.shelf-plank > span {
  position: absolute;
  top: 6px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #697682;
  box-shadow: inset 0 1px 1px #37444e;
}

.shelf-plank > i {
  left: 15px;
}

.shelf-plank > span {
  right: 15px;
}

.rack-foot {
  position: absolute;
  z-index: 9;
  bottom: 0;
  width: 61px;
  height: 17px;
  border: 1px solid #6f7b86;
  border-radius: 2px 2px 5px 5px;
  background: linear-gradient(#b8c1c8, #6c7883);
  box-shadow: 0 7px 8px #25384932;
}

.rack-foot.left {
  left: -2px;
}

.rack-foot.right {
  right: -2px;
}

.rack-legend {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  margin-top: 13px;
  color: #71859a;
  font-size: 9px;
  gap: 15px;
}

.rack-legend > span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.rack-legend > i,
.rack-legend span > i {
  width: 9px;
  height: 9px;
  border: 1px solid #9cacba;
  border-radius: 2px;
}

.legend-bin {
  border-color: #2f7eb6 !important;
  background: #55a7da;
}

.legend-selected {
  border-color: #d78b27 !important;
  background: #ffb549;
}

.legend-empty {
  border-style: dashed !important;
  background: #eef3f6;
}

.rack-legend small {
  margin-left: auto;
  color: #91a0ad;
  font-size: 8px;
}

@media (max-width: 720px) {
  .shelf-rack-visual {
    padding: 14px 10px;
  }

  .rack-toolbar {
    flex-direction: column;
  }

  .rack-toolbar-side {
    align-items: stretch;
    width: 100%;
    flex-direction: column;
  }

  .rack-summary {
    justify-content: space-around;
  }

  .overview-button {
    align-self: flex-start;
  }

  .rack-guidance {
    align-items: flex-start;
    flex-direction: column;
  }

  .rack-stage {
    padding-right: 5px;
    padding-left: 5px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .rack-frame,
  .storage-box,
  .add-storage-box {
    transition-duration: 0.01ms !important;
  }
  .storage-box.highlighted .bin-shell,
  .rack-level.highlighted .level-space { animation: none; }
}
</style>
