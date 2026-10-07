<script>
import { markRaw } from 'vue'
import GIcon from './GIcon.vue'
export default {
  name: 'GlacierWarehouse',
  components: { GIcon },
  props: {
    snapshot: Object,
    loadScene: { type: Function, required: true },
    initialFocusId: Number,
    embedded: Boolean,
    hideHeading: Boolean,
    initialMode: { default: '3d' },
    allowRoutePreview: { default: true },
  },
  emits: ['open-location', 'preview-route', 'refresh', 'selection'],
  data() {
    return {
      scene: null,
      mode: this.initialMode,
      preset: 'overview',
      selectedCode: null,
      slotName: null,
      search: '',
      phase: 'loading',
      error: '',
      stopIndex: 0,
      showOccupied: false,
      loadGeneration: 0,
      railOpen: false,
      diag: null,
      viewportHeight: null,
      layoutObserver: null,
      layoutFrame: 0,
      layoutDisposed: false,
    }
  },
  computed: {
    assets() {
      return (this.snapshot?.assets || []).filter((a) =>
        [a.code, a.name, a.full_path, ...a.slots.flatMap((s) => s.materials.map((m) => m.name))]
          .join(' ')
          .toLowerCase()
          .includes(this.search.toLowerCase()),
      )
    },
    selected() {
      return this.snapshot?.assets.find((a) => a.code === this.selectedCode) || null
    },
    selectedSlot() {
      return this.selected?.slots.find((s) => s.name === this.slotName) || null
    },
    slots() {
      const s = this.selected?.slots || []
      return this.showOccupied ? s.filter((x) => x.material_kind_count > 0) : s
    },
    stops() {
      return this.snapshot?.route?.ordered_stop_nodes || []
    },
    slotColumns() {
      return this.selected?.style === 'drawer_rack_100'
        ? 5
        : this.selected?.style === 'shelf_rack_6'
          ? 3
          : 8
    },
    routeSvg() {
      const r = this.snapshot?.route
      const nodes = this.snapshot?.map?.nodes || []
      return (r?.segments || []).map((s) =>
        s.path_nodes
          .map((c) => nodes.find((n) => n.code === c))
          .filter(Boolean)
          .map((n) => `${n.x_m * 70},${n.y_m * 70}`)
          .join(' '),
      )
    },
    mapViewBox() {
      return `0 0 ${(this.snapshot?.map.width_m || 8) * 70} ${(this.snapshot?.map.height_m || 6) * 70}`
    },
  },
  watch: {
    initialMode(value) {
      this.mode = value
    },
    snapshot() {
      this.mountScene()
    },
    initialFocusId() {
      this.focusLocation(this.initialFocusId)
    },
    hideHeading() {
      this.$nextTick(this.scheduleViewportMeasure)
    },
    embedded() {
      this.$nextTick(this.scheduleViewportMeasure)
    },
    mode(value) {
      if (value === '3d') this.$nextTick(() => this.scene?.resize())
    },
  },
  mounted() {
    this.$el.addEventListener('warehouse-twin:diagnostics', this.onDiagnostics)
    if (typeof ResizeObserver !== 'undefined') {
      this.layoutObserver = markRaw(new ResizeObserver(this.scheduleViewportMeasure))
      this.layoutObserver.observe(this.$refs.body)
      if (this.$el.parentElement) this.layoutObserver.observe(this.$el.parentElement)
    }
    window.addEventListener('resize', this.scheduleViewportMeasure)
    this.$nextTick(this.scheduleViewportMeasure)
    this.mountScene()
  },
  beforeUnmount() {
    this.$el.removeEventListener('warehouse-twin:diagnostics', this.onDiagnostics)
    this.layoutDisposed = true
    window.removeEventListener('resize', this.scheduleViewportMeasure)
    this.layoutObserver?.disconnect()
    cancelAnimationFrame(this.layoutFrame)
    this.loadGeneration++
    this.scene?.dispose()
    this.scene = null
  },
  methods: {
    scheduleViewportMeasure() {
      if (this.layoutDisposed) return
      cancelAnimationFrame(this.layoutFrame)
      this.layoutFrame = requestAnimationFrame(this.measureViewport)
    },
    measureViewport() {
      if (this.layoutDisposed) return
      if (this.embedded) {
        this.viewportHeight = null
        return
      }
      const body = this.$refs.body
      if (!body) return
      const minimum = window.innerWidth <= 600 ? 420 : 520
      const top = Math.max(0, body.getBoundingClientRect().top)
      const height = Math.round(Math.max(minimum, window.innerHeight - top - 16))
      if (height !== this.viewportHeight) this.viewportHeight = height
    },
    onDiagnostics(event) {
      if (event.detail) event.detail.stats = this.diagnostics()
    },
    async mountScene() {
      const token = ++this.loadGeneration
      if (!this.snapshot) return
      this.phase = 'loading'
      this.error = ''
      await this.$nextTick()
      this.scheduleViewportMeasure()
      try {
        const Module = await this.loadScene()
        if (token !== this.loadGeneration) return
        const C = Module.WarehouseScene || Module
        const old = this.scene
        const holder = document.createElement('div')
        holder.className = 'g-three-engine'
        this.$refs.stage.appendChild(holder)
        let next
        try {
          next = new C(holder, this.snapshot, {
            compact: this.embedded,
            onSelect: (code, slot) => this.select(code, slot, false),
            onError: (e) => {
              this.error = e.message
              this.phase = 'error'
            },
          })
        } catch (e) {
          holder.remove()
          throw e
        }
        if (token !== this.loadGeneration) {
          next.dispose()
          holder.remove()
          return
        }
        old?.dispose()
        if (old?.host) old.host.remove()
        this.scene = VueMarkRaw(next)
        if (!this.snapshot.assets.some((a) => a.code === this.selectedCode)) {
          this.selectedCode = null
          this.slotName = null
        }
        this.stopIndex = 0
        this.phase = 'ready'
        this.diag = next.stats()
        if (this.selectedCode) this.scene.select(this.selectedCode, this.slotName, true)
        this.focusLocation(this.initialFocusId)
        if (!this.initialFocusId) this.railOpen = false
        this.$nextTick(() => this.scene?.resize())
      } catch (e) {
        if (token === this.loadGeneration) {
          this.phase = this.scene ? 'stale' : 'error'
          this.error = e?.message || '3D 视图暂不可用'
        }
      }
    },
    select(code, slot = null, focus = true) {
      this.selectedCode = code
      this.slotName = slot
      this.railOpen = true
      this.$nextTick(() =>
        this.$el.querySelector('.g-mini-slots .selected')?.scrollIntoView({ block: 'nearest' }),
      )
      this.scene?.select(code, slot, focus)
      this.$emit('selection', {
        assetCode: code,
        slotName: slot,
        locationId: this.selectedSlot?.location_id || this.selected?.location_id,
      })
    },
    focusLocation(id) {
      if (!id) return
      const a = this.snapshot.assets.find(
        (a) =>
          a.location_id === id ||
          a.slots.some((s) => s.location_id === id || s.descendant_ids.includes(id)),
      )
      if (a) {
        const s = a.slots.find((s) => s.location_id === id || s.descendant_ids.includes(id))
        this.select(a.code, s?.name || null, true)
      }
    },
    setView(name) {
      this.preset = name
      this.mode = '3d'
      this.$nextTick(() => this.scene?.setPreset(name))
    },
    zoom(factor) {
      const s = this.scene
      if (!s) return
      const v = s.camera.position.clone().sub(s.controls.target)
      v.multiplyScalar(factor)
      const len = Math.min(s.controls.maxDistance, Math.max(s.controls.minDistance, v.length()))
      v.setLength(len)
      s.camera.position.copy(s.controls.target).add(v)
      s.controls.update()
      s.dirty = true
    },
    nextStop() {
      if (!this.stops.length) return
      this.stopIndex = (this.stopIndex + 1) % this.stops.length
      const code = this.stops[this.stopIndex]
      this.scene?.setStop(code, true)
      const a = this.snapshot.assets.find((a) => a.pick_node_code === code)
      if (a) this.select(a.code, null, true)
    },
    openLocation() {
      const id = this.selectedSlot?.location_id || this.selected?.location_id
      if (id) this.$emit('open-location', id)
    },
    assetRect(a) {
      return {
        x: (a.x_m - a.footprint.width_m / 2) * 70,
        y: (a.y_m - a.footprint.depth_m / 2) * 70,
        width: a.footprint.width_m * 70,
        height: a.footprint.depth_m * 70,
      }
    },
    typeName(style) {
      return (
        {
          drawer_rack_100: '100 抽零件柜',
          standard_56: '56 格元件盒',
          split_configurable: '可组合元件盒',
          shelf_rack_6: '六层货架',
        }[style] || '仓储设备'
      )
    },
    qty(slot) {
      const v = slot?.quantities_by_unit || {}
      return (
        Object.entries(v)
          .map(([u, n]) => `${n} ${u}`)
          .join(' · ') || '—'
      )
    },
    diagnostics() {
      return this.scene?.stats() || null
    },
  },
}
function VueMarkRaw(value) {
  return markRaw(value)
}
</script>
<template>
  <div
    class="g-warehouse"
    :class="{ 'is-embedded': embedded, 'has-viewport-height': !embedded && viewportHeight !== null }"
    :style="viewportHeight !== null ? { '--g-warehouse-height': viewportHeight + 'px' } : undefined"
  >
    <header v-if="!hideHeading" class="g-warehouse-heading">
      <div>
        <span class="g-eyebrow">SPATIAL INVENTORY</span>
        <h1>数字孪生仓库<span class="g-title-dot">.</span></h1>
      </div>
      <div class="g-row">
        <span class="g-tag">{{ snapshot?.map.name || '仓库地图' }}</span
        ><button class="g-btn" @click="$emit('refresh')">
          <GIcon name="refresh" :size="17" />刷新
        </button>
      </div>
    </header>
    <div ref="body" class="g-warehouse-body">
      <aside class="g-asset-rail">
        <label class="g-input-search"
          ><GIcon name="search" :size="17" /><input
            v-model="search"
            placeholder="查找设备或物料"
            aria-label="查找仓库设备或物料"
        /></label>
        <div class="g-rail-caption">
          <b>仓储设备</b><span>{{ assets.length }}</span>
        </div>
        <button
          v-for="a in assets"
          :key="a.code"
          :class="['g-asset-item', { active: selectedCode === a.code }]"
          @click="select(a.code)"
        >
          <span class="g-device-thumb" :class="a.style"
            ><i v-for="n in a.style === 'shelf_rack_6' ? 6 : 12" :key="n"></i></span
          ><span
            ><b>{{ a.name }}</b
            ><small>{{ typeName(a.style) }}</small
            ><em>{{ a.material_kind_count }} 种物料</em></span
          >
        </button>
        <div class="g-map-note">
          <GIcon name="layers" :size="17" /><span>点击设备查看库位；拖动场景自由观察。</span>
        </div>
      </aside>
      <section class="g-spatial-stage">
        <div
          ref="stage"
          class="g-three-host"
          v-show="mode === '3d'"
          aria-label="可旋转缩放的三维仓库"
        ></div>
        <div v-if="mode === '2d'" class="g-floorplan">
          <svg :viewBox="mapViewBox" role="img" aria-label="仓库平面视图">
            <rect
              x="0"
              y="0"
              :width="snapshot.map.width_m * 70"
              :height="snapshot.map.height_m * 70"
              fill="#e9f0f3"
              rx="6"
            />
            <g
              v-for="a in snapshot.assets"
              :key="a.code"
              tabindex="0"
              role="button"
              :aria-label="a.name"
              @click="select(a.code, null, false)"
              @keydown.enter="select(a.code, null, false)"
            >
              <rect
                v-bind="assetRect(a)"
                :fill="a.code === selectedCode ? '#aad5e5' : '#cad9e1'"
                stroke="#718e9c"
                stroke-width="1.5"
                rx="3"
              />
              <text
                :x="a.x_m * 70"
                :y="(a.y_m + a.footprint.depth_m / 2) * 70 + 15"
                text-anchor="middle"
                fill="#334854"
                font-size="12"
              >
                {{ a.name }}
              </text>
            </g>
            <polyline
              v-for="(points, i) in routeSvg"
              :key="i"
              :points="points"
              fill="none"
              stroke="#538da8"
              stroke-width="3"
              stroke-dasharray="6 4"
            />
          </svg>
        </div>
        <div class="g-canvas-top">
          <div class="g-scene-badge">
            <i :class="{ ready: phase === 'ready' }"></i
            >{{ mode === '3d' ? 'THREE.JS · 3D' : '2D · 平面图' }}
          </div>
          <div class="g-segmented glass">
            <button :class="{ active: mode === '3d' }" @click="mode = '3d'">空间</button
            ><button :class="{ active: mode === '2d' }" @click="mode = '2d'">平面</button>
          </div>
        </div>
        <div v-if="phase === 'loading' && mode === '3d'" class="g-scene-status">
          <span class="g-spinner"></span><b>正在构建三维仓库</b>
        </div>
        <div v-if="(phase === 'error' || phase === 'stale') && mode === '3d'" class="g-scene-error">
          <GIcon name="info" /><b>{{ scene ? '仓库未刷新' : '3D 暂不可用' }}</b>
          <p>{{ error }}</p>
          <button class="g-btn" @click="mountScene">重试 3D</button
          ><button class="g-btn" @click="mode = '2d'">查看平面图</button>
        </div>
        <div class="g-canvas-bottom">
          <div class="g-canvas-controls">
            <button
              class="g-icon-btn"
              @click="setView('overview')"
              aria-label="全景复位"
              title="全景复位"
            >
              <GIcon name="expand" /></button
            ><button class="g-icon-btn" @click="setView('top')" aria-label="俯视" title="俯视">
              <GIcon name="layers" /></button
            ><span class="g-divider"></span
            ><button class="g-icon-btn" @click="zoom(0.85)" aria-label="放大">
              <GIcon name="plus" /></button
            ><button class="g-icon-btn" @click="zoom(1.18)" aria-label="缩小">
              <span class="g-minus"></span>
            </button>
          </div>
          <span class="g-control-hint">拖动旋转 · 滚轮缩放 · 右键平移</span>
        </div>
        <div class="g-route-strip" v-if="stops.length">
          <span class="g-pictogram blue"><GIcon name="route" /></span
          ><span
            ><b>备料路线</b
            ><small
              >{{ stops.length }} 个停靠点 ·
              {{ snapshot.route.total_distance_m.toFixed(1) }} m</small
            ></span
          >
          <div class="g-stop-dots">
            <i v-for="(_, i) in stops" :class="{ active: i === stopIndex }" :key="i">{{ i + 1 }}</i>
          </div>
          <button class="g-btn dark" @click="nextStop">
            下一站 <GIcon name="arrow" :size="16" />
          </button>
        </div>
      </section>
      <aside class="g-location-rail" :class="{ 'is-open': railOpen }">
        <template v-if="selected"
          ><header class="g-rail-caption">
            <b>库位详情</b
            ><button
              class="g-icon-btn g-rail-close"
              @click="railOpen = false"
              aria-label="关闭详情"
            >
              <GIcon name="close" /></button
            ><span class="g-tag">{{ selected.code }}</span>
          </header>
          <h2>{{ selected.name }}</h2>
          <p class="g-muted g-location-path">{{ selected.full_path }}</p>
          <div class="g-location-stats">
            <div>
              <strong>{{ selected.slots.length }}</strong
              ><small>个库位</small>
            </div>
            <div>
              <strong>{{ selected.material_kind_count }}</strong
              ><small>种物料</small>
            </div>
          </div>
          <div class="g-row between">
            <h3>{{ selected.style === 'shelf_rack_6' ? '货架层位' : '选择一个库位' }}</h3>
            <label class="g-switch"><input type="checkbox" v-model="showOccupied" />有物料</label>
          </div>
          <div class="g-mini-slots" :style="{ '--slot-cols': slotColumns }">
            <button
              v-for="s in slots"
              :key="s.name"
              :class="{ occupied: s.material_kind_count > 0, selected: slotName === s.name }"
              @click="select(selected.code, s.name, false)"
              :title="s.materials.map((m) => m.name).join('、') || '空库位'"
            >
              {{ s.name }}
            </button>
          </div>
          <div class="g-legend">
            <span><i class="filled"></i>有物料</span><span><i class="selected"></i>已选</span
            ><span>编号沿用原库位</span>
          </div>
          <section class="g-bin-detail" v-if="selectedSlot">
            <header>
              <GIcon name="pin" :size="18" /><b>{{ selectedSlot.name }}</b
              ><span class="g-tag">{{ qty(selectedSlot) }}</span>
            </header>
            <div class="g-bin-material" v-for="m in selectedSlot.materials" :key="m.name">
              <b>{{ m.name }}</b
              ><span>{{ m.quantity }} {{ m.unit }}</span>
            </div>
            <p v-if="!selectedSlot.materials.length" class="g-muted">此库位尚无物料。</p>
          </section>
          <button class="g-btn dark full" @click="openLocation">
            <GIcon name="drawer" :size="18" />打开原版可视化设备
            <GIcon name="arrow" :size="17" /></button
          ><button
            v-if="allowRoutePreview"
            class="g-btn full"
            @click="$emit('preview-route', [selectedSlot?.location_id || selected.location_id])"
          >
            <GIcon name="route" :size="18" />预览到此库位的路线
          </button></template
        >
        <div v-else class="g-detail-empty">
          <span class="g-pictogram blue"><GIcon name="cube" :size="28" /></span>
          <h3>从空间到具体物料</h3>
          <p>选择场景中的设备，查看抽屉、层位与物料。</p>
          <div class="g-detail-summary">
            <b>{{ snapshot?.assets.length || 0 }}</b
            ><span>台仓储设备</span>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>
