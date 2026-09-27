<script setup lang="ts">
import { computed } from 'vue'

export interface WarehouseMapNodeView {
  code: string
  node_type: 'packing' | 'entrance' | 'aisle' | 'intersection' | 'pick_face'
  x_m: number
  y_m: number
  label: string
}

export interface WarehouseMapEdgeView {
  code: string
  from_node: string
  to_node: string
  distance_m: number
  bidirectional: boolean
  enabled: boolean
}

export interface WarehouseMapBindingView {
  location_code: string
  pick_node_code: string
  x_m: number
  y_m: number
  width_m: number
  depth_m: number
  rotation_deg: number
  facing: string
  local_geometry_kind: string
}

export interface WarehouseMapView {
  code: string
  version?: string
  name: string
  graph_hash: string
  calibration_status: 'demo_synthetic' | 'measured' | 'verified'
  width_m: string | number
  height_m: string | number
  geometry_note: string
  nodes: WarehouseMapNodeView[]
  edges: WarehouseMapEdgeView[]
  organizer_bindings: WarehouseMapBindingView[]
}

export interface WarehouseRouteSegmentView {
  from_node: string
  to_node: string
  distance_m: number
  path_nodes: string[]
}

export interface WarehouseRouteView {
  strategy: string
  graph_hash: string
  ordered_stop_nodes: string[]
  total_distance_m: number
  segments: WarehouseRouteSegmentView[]
}

const props = withDefaults(
  defineProps<{
    map: WarehouseMapView
    route?: WarehouseRouteView | null
    currentNodeCode?: string | null
  }>(),
  { route: null, currentNodeCode: null },
)

const emit = defineEmits<{
  selectBinding: [binding: WarehouseMapBindingView]
}>()

const width = computed(() => Number(props.map.width_m))
const height = computed(() => Number(props.map.height_m))
const nodeByCode = computed(() => new Map(props.map.nodes.map((node) => [node.code, node])))
const stopSequence = computed(
  () => new Map((props.route?.ordered_stop_nodes || []).map((code, index) => [code, index + 1])),
)

function screenY(y: number) {
  return height.value - y
}

function routePoints(): string {
  const codes: string[] = []
  for (const segment of props.route?.segments || []) {
    for (const code of segment.path_nodes) {
      if (codes[codes.length - 1] !== code) codes.push(code)
    }
  }
  return codes
    .map((code) => nodeByCode.value.get(code))
    .filter((node): node is WarehouseMapNodeView => Boolean(node))
    .map((node) => `${node.x_m},${screenY(node.y_m)}`)
    .join(' ')
}

function calibrationLabel() {
  if (props.map.calibration_status === 'verified') return '已校准验证'
  if (props.map.calibration_status === 'measured') return '已测量 · 待验证'
  return '示例地图 · 非实测'
}
</script>

<template>
  <section class="warehouse-route-map" data-testid="warehouse-route-map">
    <header>
      <div>
        <span>仓库路线图</span>
        <h3>{{ map.name }}</h3>
      </div>
      <div class="route-meta">
        <b :class="`calibration-${map.calibration_status}`">{{ calibrationLabel() }}</b>
        <span v-if="route">推荐步行 {{ route.total_distance_m.toFixed(1) }} m</span>
      </div>
    </header>

    <p v-if="map.calibration_status !== 'verified'" class="map-caveat">
      {{
        map.calibration_status === 'demo_synthetic'
          ? '基于示例仓库图的推荐路线。'
          : '基于已测量仓库图的推荐路线。'
      }}
      {{ map.geometry_note || '当前路线只对已配置地图成立，不代表真实仓库实测最短距离。' }}
    </p>

    <div class="map-stage">
      <svg
        :viewBox="`0 0 ${width} ${height}`"
        role="img"
        aria-label="MaterialBrain 仓库可通行路线图"
      >
        <rect class="warehouse-boundary" x="0" y="0" :width="width" :height="height" />

        <g class="map-edges">
          <line
            v-for="edge in map.edges.filter((item) => item.enabled)"
            :key="edge.code"
            :x1="nodeByCode.get(edge.from_node)?.x_m"
            :y1="screenY(nodeByCode.get(edge.from_node)?.y_m || 0)"
            :x2="nodeByCode.get(edge.to_node)?.x_m"
            :y2="screenY(nodeByCode.get(edge.to_node)?.y_m || 0)"
          />
        </g>

        <g class="organizers">
          <g v-for="binding in map.organizer_bindings" :key="binding.location_code">
            <rect
              class="organizer-footprint"
              :x="binding.x_m"
              :y="screenY(binding.y_m + binding.depth_m)"
              :width="binding.width_m"
              :height="binding.depth_m"
              :transform="`rotate(${-binding.rotation_deg} ${binding.x_m + binding.width_m / 2} ${screenY(binding.y_m + binding.depth_m / 2)})`"
              rx="0.08"
              @click="emit('selectBinding', binding)"
            />
            <text
              :x="binding.x_m + binding.width_m / 2"
              :y="screenY(binding.y_m + binding.depth_m / 2)"
              text-anchor="middle"
              font-size="0.16"
              pointer-events="none"
            >
              {{ binding.location_code }}
            </text>
          </g>
        </g>

        <polyline v-if="routePoints()" class="route-line" :points="routePoints()" />

        <g v-for="node in map.nodes" :key="node.code" class="map-node">
          <circle
            :class="[
              `node-${node.node_type}`,
              { current: node.code === currentNodeCode, stop: stopSequence.has(node.code) },
            ]"
            :cx="node.x_m"
            :cy="screenY(node.y_m)"
            :r="stopSequence.has(node.code) ? 0.17 : node.node_type === 'pick_face' ? 0.1 : 0.06"
          />
          <text
            v-if="stopSequence.has(node.code)"
            class="stop-number"
            :x="node.x_m"
            :y="screenY(node.y_m) + 0.055"
            text-anchor="middle"
          >
            {{ stopSequence.get(node.code) }}
          </text>
        </g>
      </svg>
    </div>

    <footer v-if="route" class="route-footer">
      <span>策略：{{ route.strategy }}</span>
      <span>Graph {{ route.graph_hash.slice(0, 10) }}</span>
      <span>{{ route.ordered_stop_nodes.length }} 个取料站点</span>
    </footer>
  </section>
</template>

<style scoped>
.warehouse-route-map {
  border: 1px solid #d8e5ee;
  border-radius: 14px;
  background: #fff;
  overflow: hidden;
}
header,
.route-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 14px;
}
header span,
.route-footer,
.map-caveat {
  color: #668094;
  font-size: 12px;
}
header h3 {
  margin: 2px 0 0;
  font-size: 16px;
}
.route-meta {
  display: flex;
  align-items: center;
  gap: 10px;
}
.route-meta b {
  border-radius: 999px;
  padding: 4px 8px;
  font-size: 11px;
}
.calibration-verified {
  color: #18784d;
  background: #e7f7ef;
}
.calibration-measured {
  color: #725f19;
  background: #fff7d8;
}
.calibration-demo_synthetic {
  color: #945523;
  background: #fff0df;
}
.map-caveat {
  margin: 0;
  padding: 9px 14px;
  background: #fff9ef;
  border-top: 1px solid #f1e5d3;
  border-bottom: 1px solid #f1e5d3;
}
.map-stage {
  padding: 12px;
  background: #f7fafc;
}
svg {
  width: 100%;
  max-height: 560px;
  display: block;
}
.warehouse-boundary {
  fill: #fff;
  stroke: #9eb4c5;
  stroke-width: 0.04;
}
.map-edges line {
  stroke: #c8d5df;
  stroke-width: 0.07;
  stroke-linecap: round;
}
.organizer-footprint {
  fill: #e6edf2;
  stroke: #9aacb9;
  stroke-width: 0.035;
  cursor: pointer;
}
.route-line {
  fill: none;
  stroke: #3576a8;
  stroke-width: 0.12;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.map-node circle {
  fill: #8ba4b7;
}
.map-node circle.node-packing {
  fill: #354f67;
}
.map-node circle.stop {
  fill: #fff;
  stroke: #286f9f;
  stroke-width: 0.07;
}
.map-node circle.current {
  stroke: #132f43;
  stroke-width: 0.09;
}
.stop-number {
  font-size: 0.18px;
  font-weight: 800;
  fill: #286f9f;
  pointer-events: none;
}
.route-footer {
  justify-content: flex-start;
  border-top: 1px solid #e5edf3;
}
</style>
