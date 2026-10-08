<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { useWarehouseAgent } from '../composables/useWarehouseAgent'
import { formatQuantity, formatSignedQuantity } from '../utils/format'
import GlacierHome from '../glacier/components/GlacierHome.vue'
import GIcon from '../glacier/components/GIcon.vue'
interface Summary {
  material_count: number
  quantity: string
  reserved_quantity: string
  available_quantity: string
  inventory_value: string
  out_of_stock_count: number
  today_inbound: string
  today_outbound: string
  recent_movements: {
    id: number
    movement_no: string
    material_id: number
    material_name?: string
    material_mpn?: string | null
    operation_type: string
    quantity_delta: string
    created_at: string
  }[]
  trend: { date: string; inbound: string; outbound: string }[]
}
const auth = useAuthStore(),
  router = useRouter(),
  agent = useWarehouseAgent()
const data = ref<Summary | null>(null),
  busy = ref(false),
  error = ref(''),
  chartRange = ref<7 | 14 | 30>(14),
  chart = ref<HTMLElement>(),
  updated = ref('')
let instance: echarts.ECharts | undefined,
  timer: number | undefined,
  observer: ResizeObserver | undefined,
  alive = true
const operationLabels: Record<string, string> = {
  inbound: '采购入库',
  outbound: '领用出库',
  scrap: '报废出库',
  refund: '退料入库',
  reserve: '项目预留',
  unreserve: '取消预留',
  reserve_outbound: '预留出库',
  adjust: '盘点调整',
  transfer: '库位调拨',
  initial: '初始库存',
  initial_location_allocation: '库位分配',
  reversal: '冲正流水',
}
const summary = computed(() =>
  data.value
    ? {
        ...data.value,
        material_count: data.value.material_count.toLocaleString('zh-CN'),
        available_quantity: formatQuantity(data.value.available_quantity),
        reserved_quantity: formatQuantity(data.value.reserved_quantity),
        inventory_value: Number(data.value.inventory_value).toLocaleString('zh-CN', {
          maximumFractionDigits: 2,
        }),
      }
    : null,
)
const activities = computed(() =>
  (data.value?.recent_movements || []).map((m) => ({
    id: m.id,
    name: m.material_mpn || m.material_name || '物料 #' + m.material_id,
    action: operationLabels[m.operation_type] || m.operation_type,
    quantity: formatSignedQuantity(m.quantity_delta),
    location: m.movement_no,
    time: new Date(m.created_at).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }),
  })),
)
const quickActions = computed(() =>
  [
    {
      id: 'brain',
      title: '物料大脑',
      description: '候选 · 证据 · 工程草案',
      icon: 'brain',
      tone: '',
      permission: 'material:view',
    },
    {
      id: 'warehouse',
      title: '数字孪生仓库',
      description: '空间定位 · 路线预览',
      icon: 'cube',
      tone: 'mint',
      permission: ['material:view', 'location:manage', 'picking:view'],
    },
    {
      id: 'locations',
      title: '可视化库位',
      description: '抽屉柜 · 元件盒 · 货架',
      icon: 'drawer',
      tone: 'neutral',
      permission: ['location:manage', 'material:view'],
    },
  ].filter((x) => auth.can(x.permission)),
)
const utilityActions = computed(() =>
  [
    { id: 'cables', label: '查找线缆', permission: 'material:view' },
    { id: 'inventory', label: '库存操作', permission: 'inventory:operate' },
    { id: 'materials', label: '物料资料', permission: 'material:view' },
    { id: 'locations', label: '查看元件盒', permission: ['material:view', 'location:manage'] },
  ].filter((x) => auth.can(x.permission)),
)
const percentages = computed(() => {
  const d = data.value
  if (!d) return { available: '—', reserved: '—', coverage: '—' }
  const total = Number(d.quantity)
  return {
    available: total > 0 ? Math.round((Number(d.available_quantity) / total) * 100) + '%' : '—',
    reserved: total > 0 ? Math.round((Number(d.reserved_quantity) / total) * 100) + '%' : '—',
    coverage:
      d.material_count > 0
        ? Math.round(((d.material_count - d.out_of_stock_count) / d.material_count) * 100) + '%'
        : '—',
  }
})
function navigate(id: string) {
  const paths: Record<string, string> = {
    brain: '/agent',
    warehouse: '/warehouse-twin',
    locations: '/locations',
    bom: auth.can('material:view') ? '/products' : '/projects',
    cables: '/cables',
    inventory: '/inventory',
    materials: '/materials',
    movements: '/movements',
  }
  if (paths[id]) void router.push(paths[id])
}
function ask(question: string) {
  if (!auth.can('material:view')) return
  agent.message.value = question
  void router.push('/agent').then(() => agent.submit())
}
async function load() {
  if (busy.value) return
  busy.value = true
  try {
    const response = await api.get<Summary>('/dashboard/summary')
    if (!alive) return
    data.value = response.data
    updated.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    error.value = ''
    await nextTick()
    draw()
  } catch {
    if (alive) error.value = '暂未刷新，已保留上次数据。'
  } finally {
    if (alive) busy.value = false
  }
}
function draw() {
  if (!chart.value || !data.value) return
  instance ??= echarts.init(chart.value)
  const points = data.value.trend.slice(-chartRange.value)
  instance.setOption({
    animationDuration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 300,
    grid: { left: 10, right: 12, top: 22, bottom: 6, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#fff',
      borderColor: '#d7e3e9',
      textStyle: { color: '#25343e', fontSize: 14 },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: points.map((p) => p.date.slice(5)),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#dae5eb' } },
      axisLabel: { color: '#637b89', fontSize: 12 },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: '#637b89', fontSize: 12 },
      splitLine: { lineStyle: { color: '#edf2f5', type: 'dashed' } },
    },
    series: [
      {
        name: '入库',
        type: 'line',
        smooth: 0.2,
        showSymbol: false,
        data: points.map((p) => Number(p.inbound)),
        itemStyle: { color: '#729ab3' },
        lineStyle: { width: 2.5 },
        areaStyle: { color: 'rgba(151,188,207,.17)' },
      },
      {
        name: '出库',
        type: 'line',
        smooth: 0.2,
        showSymbol: false,
        data: points.map((p) => Number(p.outbound)),
        itemStyle: { color: '#77afab' },
        lineStyle: { width: 2.5 },
        areaStyle: { color: 'rgba(166,210,204,.13)' },
      },
    ],
  })
  instance.resize()
}
function onFocus() {
  void load()
}
watch(chartRange, () => draw())
onMounted(() => {
  void load()
  observer = new ResizeObserver(() => instance?.resize())
  if (chart.value) observer.observe(chart.value)
  timer = window.setInterval(() => {
    if (document.visibilityState === 'visible') void load()
  }, 30000)
  window.addEventListener('focus', onFocus)
})
onBeforeUnmount(() => {
  alive = false
  if (timer) window.clearInterval(timer)
  window.removeEventListener('focus', onFocus)
  observer?.disconnect()
  instance?.dispose()
})
</script>
<template>
  <div v-if="error" class="g-inline-notice" role="status">
    {{ error }}<button class="g-text-btn" @click="load">重试</button>
  </div>
  <GlacierHome
    :summary="summary ?? undefined"
    :activities="activities"
    :projects="[]"
    :range="chartRange"
    :loading="busy"
    :can-ask="auth.can('material:view')"
    @navigate="navigate"
    @ask="ask"
    @refresh="load"
    @range="chartRange = $event"
  >
    <template #shortcuts
      ><header>
        <h3>继续你的工作</h3>
        <span class="g-tag">快捷入口</span>
      </header>
      <button
        v-for="q in quickActions"
        :key="q.id"
        :class="['g-optic-pill', q.tone]"
        @click="navigate(q.id)"
      >
        <span :class="['g-pictogram', q.tone || 'blue']"><GIcon :name="q.icon" /></span
        ><span
          ><b>{{ q.title }}</b
          ><small>{{ q.description }}</small></span
        ><GIcon name="arrow" :size="19" /></button
    ></template>
    <template #chart
      ><div ref="chart" class="g-live-chart" aria-label="真实入库出库趋势" />
      <p v-if="data && !data.trend.length" class="g-muted">当前时段暂无流水。</p></template
    >
    <template #sidepanel
      ><header class="g-card-heading">
        <h3>库存概况</h3>
        <span class="g-tag">{{ updated ? updated + ' 更新' : '等待数据' }}</span>
      </header>
      <div class="g-summary-kv">
        <span>账面总量</span><b>{{ data ? formatQuantity(data.quantity) : '—' }}</b>
      </div>
      <div class="g-summary-kv">
        <span>缺货型号</span><b>{{ data?.out_of_stock_count ?? '—' }}</b>
      </div>
      <div class="g-summary-kv">
        <span>可用比例 / 预留比例</span
        ><b>{{ percentages.available }} / {{ percentages.reserved }}</b>
      </div>
      <div class="g-summary-kv">
        <span>有库存型号占比</span><b>{{ percentages.coverage }}</b>
      </div>
      <div class="g-summary-kv">
        <span>今日净流转</span
        ><b>{{
          data
            ? formatSignedQuantity(String(Number(data.today_inbound) - Number(data.today_outbound)))
            : '—'
        }}</b>
      </div>
      <div class="g-utility-actions">
        <button v-for="q in utilityActions" :key="q.id" class="g-btn" @click="navigate(q.id)">
          {{ q.label }}
        </button>
      </div></template
    >
  </GlacierHome>
</template>
