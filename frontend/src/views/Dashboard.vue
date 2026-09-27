<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Box,
  Connection,
  DataAnalysis,
  Goods,
  Location,
  Operation,
  Refresh,
  Tickets,
} from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { formatQuantity, formatSignedQuantity } from '../utils/format'

interface Movement {
  id: number
  movement_no: string
  material_id: number
  operation_type: string
  quantity_delta: string
  created_at: string
}

interface Summary {
  material_count: number
  quantity: string
  reserved_quantity: string
  available_quantity: string
  inventory_value: string
  out_of_stock_count: number
  today_inbound: string
  today_outbound: string
  recent_movements: Movement[]
  trend: { date: string; inbound: string; outbound: string }[]
}

const auth = useAuthStore()
const data = ref<Summary | null>(null)
const chart = ref<HTMLElement>()
const busy = ref(false)
const lastUpdated = ref<Date | null>(null)
const chartRange = ref<7 | 14 | 30>(30)
let instance: echarts.ECharts | undefined
let refreshTimer: number | undefined

const movementLabels: Record<string, string> = {
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
  reversal: '冲正流水',
}

const rangeOptions = [
  { label: '7 天', value: 7 },
  { label: '14 天', value: 14 },
  { label: '30 天', value: 30 },
] as const

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了'
  if (hour < 11) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

const todayLabel = computed(() =>
  new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  }).format(new Date()),
)

const lastUpdatedLabel = computed(() => {
  if (!lastUpdated.value) return '等待首次同步'
  return `${new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(lastUpdated.value)} 已同步`
})

const availableRate = computed(() => {
  const total = Number(data.value?.quantity ?? 0)
  if (total <= 0) return 0
  return clampPercentage((Number(data.value?.available_quantity ?? 0) / total) * 100)
})

const reservationRate = computed(() => {
  const total = Number(data.value?.quantity ?? 0)
  if (total <= 0) return 0
  return clampPercentage((Number(data.value?.reserved_quantity ?? 0) / total) * 100)
})

const stockedMaterialCount = computed(() =>
  Math.max((data.value?.material_count ?? 0) - (data.value?.out_of_stock_count ?? 0), 0),
)

const stockCoverageRate = computed(() => {
  const total = data.value?.material_count ?? 0
  if (total <= 0) return 0
  return clampPercentage((stockedMaterialCount.value / total) * 100)
})

const stockHealthLabel = computed(() => {
  if (stockCoverageRate.value >= 95) return '库存覆盖优秀'
  if (stockCoverageRate.value >= 80) return '库存运行平稳'
  return '存在缺货型号'
})

const todayInbound = computed(() => Number(data.value?.today_inbound ?? 0))
const todayOutbound = computed(() => Number(data.value?.today_outbound ?? 0))
const todayNet = computed(() => todayInbound.value - todayOutbound.value)
const flowMaximum = computed(() => Math.max(todayInbound.value, todayOutbound.value, 1))

const metricCards = computed(() => {
  if (!data.value) return []
  return [
    {
      label: '物料型号',
      value: data.value.material_count.toLocaleString('zh-CN'),
      unit: '种',
      detail: `${stockedMaterialCount.value.toLocaleString('zh-CN')} 种现有库存`,
      tone: 'blue',
      icon: Goods,
    },
    {
      label: '现有库存',
      value: formatQuantity(data.value.quantity),
      unit: '件',
      detail: '全部物料账面合计',
      tone: 'violet',
      icon: DataAnalysis,
    },
    {
      label: '项目预留',
      value: formatQuantity(data.value.reserved_quantity),
      unit: '件',
      detail: `占总库存 ${reservationRate.value}%`,
      tone: 'amber',
      icon: Tickets,
    },
    {
      label: '库存估值',
      value: formatCurrency(data.value.inventory_value),
      unit: '',
      detail: '按物料单价实时估算',
      tone: 'green',
      icon: Operation,
    },
  ]
})

const quickActions = computed(() =>
  [
    {
      title: '查看元件盒',
      description: '定位物料所在格口',
      path: '/locations',
      permission: 'location:manage',
      icon: Location,
      tone: 'blue',
    },
    {
      title: '查找线缆',
      description: '按接头与 Pin 数筛选',
      path: '/cables',
      permission: 'material:view',
      icon: Connection,
      tone: 'violet',
    },
    {
      title: '库存操作',
      description: '快速完成入库或出库',
      path: '/inventory',
      permission: 'inventory:operate',
      icon: Operation,
      tone: 'green',
    },
    {
      title: '物料资料',
      description: '查询型号与技术参数',
      path: '/materials',
      permission: 'material:view',
      icon: Box,
      tone: 'amber',
    },
  ].filter((item) => auth.can(item.permission)),
)

function clampPercentage(value: number) {
  return Math.max(0, Math.min(100, Math.round(value)))
}

function formatCurrency(value: unknown) {
  const amount = Number(value ?? 0)
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    maximumFractionDigits: 0,
  }).format(Number.isFinite(amount) ? amount : 0)
}

function flowWidth(value: number) {
  return `${clampPercentage((value / flowMaximum.value) * 100)}%`
}

function formatMovementTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const now = new Date()
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  return new Intl.DateTimeFormat('zh-CN', {
    ...(sameDay ? {} : { month: '2-digit', day: '2-digit' }),
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

async function load(silent = false) {
  if (busy.value) return
  busy.value = true
  try {
    data.value = (await api.get<Summary>('/dashboard/summary')).data
    lastUpdated.value = new Date()
    requestAnimationFrame(draw)
  } catch {
    if (!silent) ElMessage.error('仪表盘数据加载失败，请稍后重试')
  } finally {
    busy.value = false
  }
}

function draw() {
  if (!chart.value || !data.value) return
  const points = data.value.trend.slice(-chartRange.value)
  instance?.dispose()
  instance = echarts.init(chart.value)
  instance.setOption({
    animationDuration: 650,
    grid: { left: 10, right: 12, top: 22, bottom: 6, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(12, 29, 53, .96)',
      borderWidth: 0,
      padding: [10, 12],
      textStyle: { color: '#f7fbff', fontSize: 12 },
      axisPointer: { type: 'line', lineStyle: { color: '#9ebcf0', type: 'dashed' } },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: points.map((item) => item.date.slice(5)),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#dbe5f0' } },
      axisLabel: {
        color: '#8798ae',
        fontSize: 11,
        interval: chartRange.value === 30 ? 4 : chartRange.value === 14 ? 2 : 0,
      },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: '#91a0b3', fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: '#edf2f7', type: 'dashed' } },
    },
    series: [
      {
        name: '入库',
        type: 'line',
        smooth: 0.36,
        showSymbol: false,
        emphasis: { focus: 'series' },
        data: points.map((item) => Number(item.inbound)),
        lineStyle: { color: '#2588f5', width: 3 },
        itemStyle: { color: '#2588f5' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(37, 136, 245, .26)' },
            { offset: 1, color: 'rgba(37, 136, 245, .01)' },
          ]),
        },
      },
      {
        name: '出库',
        type: 'line',
        smooth: 0.36,
        showSymbol: false,
        emphasis: { focus: 'series' },
        data: points.map((item) => Number(item.outbound)),
        lineStyle: { color: '#26ad83', width: 3 },
        itemStyle: { color: '#26ad83' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(38, 173, 131, .18)' },
            { offset: 1, color: 'rgba(38, 173, 131, 0)' },
          ]),
        },
      },
    ],
  })
}

function resizeChart() {
  instance?.resize()
}

function refreshOnFocus() {
  void load(true)
}

watch(chartRange, () => requestAnimationFrame(draw))

onMounted(() => {
  void load()
  refreshTimer = window.setInterval(() => void load(true), 30000)
  window.addEventListener('focus', refreshOnFocus)
  window.addEventListener('resize', resizeChart)
})

onBeforeUnmount(() => {
  instance?.dispose()
  if (refreshTimer) window.clearInterval(refreshTimer)
  window.removeEventListener('focus', refreshOnFocus)
  window.removeEventListener('resize', resizeChart)
})
</script>

<template>
  <div class="page dashboard-page">
    <header class="dashboard-header">
      <div>
        <div class="dashboard-eyebrow"><i></i> INVENTORY COMMAND CENTER</div>
        <h1>库存态势，一目了然</h1>
        <p>{{ greeting }}，{{ auth.user?.full_name || '管理员' }}。{{ todayLabel }}，所有关键数据已汇总于此。</p>
      </div>
      <button class="refresh-control" type="button" :disabled="busy" @click="load()">
        <span class="refresh-icon" :class="{ spinning: busy }"><el-icon><Refresh /></el-icon></span>
        <span><b>刷新数据</b><small>{{ lastUpdatedLabel }}</small></span>
      </button>
    </header>

    <div v-if="!data" class="dashboard-loading">
      <el-skeleton :rows="10" animated />
    </div>

    <template v-else>
      <section class="hero-grid" data-testid="dashboard-hero">
        <article class="inventory-hero">
          <div class="hero-orbit orbit-one"></div>
          <div class="hero-orbit orbit-two"></div>
          <div class="hero-topline">
            <span class="live-badge"><i></i> 实时库存</span>
            <span>每 30 秒自动同步</span>
          </div>
          <div class="hero-content">
            <div class="hero-number">
              <span>当前可用库存</span>
              <div><strong>{{ formatQuantity(data.available_quantity) }}</strong><small>件</small></div>
              <p>已扣除项目预留，可立即用于研发生产。</p>
            </div>
            <div class="availability">
              <div
                class="availability-ring"
                :style="{ '--gauge-value': `${availableRate * 3.6}deg` }"
              >
                <div><strong>{{ availableRate }}</strong><span>%</span></div>
              </div>
              <b>库存可用率</b>
              <span>{{ formatQuantity(data.quantity) }} 件总库存</span>
            </div>
          </div>
          <div class="hero-foot">
            <div><span>有库存型号</span><b>{{ stockedMaterialCount }}</b><small>种</small></div>
            <div><span>项目预留</span><b>{{ formatQuantity(data.reserved_quantity) }}</b><small>件</small></div>
            <div><span>库存覆盖</span><b>{{ stockCoverageRate }}</b><small>%</small></div>
          </div>
        </article>

        <article class="flow-card surface-card" data-testid="today-flow">
          <div class="section-kicker">TODAY'S FLOW</div>
          <div class="section-title-row">
            <div>
              <h2>今日库存流动</h2>
              <p>当天全部出入库操作汇总</p>
            </div>
            <span class="health-pill"><i></i>{{ stockHealthLabel }}</span>
          </div>

          <div class="flow-list">
            <div class="flow-item inbound">
              <div class="flow-copy"><span>今日入库</span><b>+{{ formatQuantity(data.today_inbound) }}</b></div>
              <div class="flow-track"><i :style="{ width: flowWidth(todayInbound) }"></i></div>
            </div>
            <div class="flow-item outbound">
              <div class="flow-copy"><span>今日出库</span><b>-{{ formatQuantity(data.today_outbound) }}</b></div>
              <div class="flow-track"><i :style="{ width: flowWidth(todayOutbound) }"></i></div>
            </div>
          </div>

          <div class="flow-summary">
            <div>
              <span>今日净变化</span>
              <b :class="todayNet >= 0 ? 'positive' : 'negative'">{{ formatSignedQuantity(todayNet) }}</b>
            </div>
            <div>
              <span>暂无库存型号</span>
              <b>{{ data.out_of_stock_count }}</b>
            </div>
          </div>
        </article>
      </section>

      <section class="metric-grid" aria-label="核心库存指标">
        <article
          v-for="item in metricCards"
          :key="item.label"
          class="metric-card surface-card"
          :class="item.tone"
        >
          <span class="metric-icon"><el-icon><component :is="item.icon" /></el-icon></span>
          <div class="metric-copy">
            <span>{{ item.label }}</span>
            <div><strong>{{ item.value }}</strong><small>{{ item.unit }}</small></div>
            <p>{{ item.detail }}</p>
          </div>
        </article>
      </section>

      <section v-if="quickActions.length" class="quick-panel surface-card" data-testid="quick-actions">
        <div class="quick-heading">
          <span>QUICK ACCESS</span>
          <div><b>常用功能</b><small>直达最常用的库存工作区</small></div>
        </div>
        <div class="quick-actions">
          <router-link
            v-for="item in quickActions"
            :key="item.path"
            :to="item.path"
            class="quick-action"
            :class="item.tone"
          >
            <span><el-icon><component :is="item.icon" /></el-icon></span>
            <div><b>{{ item.title }}</b><small>{{ item.description }}</small></div>
            <i>→</i>
          </router-link>
        </div>
      </section>

      <section class="insight-grid">
        <article class="trend-panel surface-card" data-testid="inventory-trend">
          <header class="panel-header">
            <div>
              <span class="section-kicker">INVENTORY TREND</span>
              <h2>库存流动趋势</h2>
              <p>对比每日入库与出库数量，快速识别流动节奏。</p>
            </div>
            <div class="range-switch" aria-label="趋势时间范围">
              <button
                v-for="option in rangeOptions"
                :key="option.value"
                type="button"
                :class="{ active: chartRange === option.value }"
                @click="chartRange = option.value"
              >
                {{ option.label }}
              </button>
            </div>
          </header>
          <div class="chart-legend">
            <span><i class="inbound"></i>入库</span>
            <span><i class="outbound"></i>出库</span>
          </div>
          <div ref="chart" class="trend-chart"></div>
        </article>

        <article class="activity-panel surface-card" data-testid="recent-movements">
          <header class="panel-header activity-header">
            <div>
              <span class="section-kicker">RECENT ACTIVITY</span>
              <h2>最近库存流水</h2>
            </div>
            <router-link v-if="auth.can('inventory:view')" to="/movements">查看全部 <span>→</span></router-link>
          </header>

          <div v-if="data.recent_movements.length" class="activity-list">
            <div v-for="movement in data.recent_movements.slice(0, 6)" :key="movement.id" class="activity-row">
              <span
                class="activity-icon"
                :class="Number(movement.quantity_delta) >= 0 ? 'inbound' : 'outbound'"
              >
                {{ Number(movement.quantity_delta) >= 0 ? '↗' : '↘' }}
              </span>
              <div class="activity-copy">
                <b>{{ movementLabels[movement.operation_type] || movement.operation_type }}</b>
                <span>物料 #{{ movement.material_id }} · {{ movement.movement_no }}</span>
              </div>
              <div class="activity-value">
                <b :class="Number(movement.quantity_delta) >= 0 ? 'positive' : 'negative'">
                  {{ formatSignedQuantity(movement.quantity_delta) }}
                </b>
                <span>{{ formatMovementTime(movement.created_at) }}</span>
              </div>
            </div>
          </div>
          <div v-else class="activity-empty">
            <span>↗</span>
            <b>还没有库存流水</b>
            <p>完成第一次入库或出库后，最新动态会出现在这里。</p>
          </div>
        </article>
      </section>
    </template>
  </div>
</template>

<style scoped>
.dashboard-page {
  max-width: 1680px;
  color: #1a2c46;
}

.dashboard-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 20px;
}

.dashboard-eyebrow,
.section-kicker {
  color: #3479cc;
  font-size: 10px;
  font-weight: 800;
  line-height: 16px;
  letter-spacing: 1.7px;
}

.dashboard-eyebrow {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dashboard-eyebrow i,
.live-badge i,
.health-pill i {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #26b486;
  box-shadow: 0 0 0 5px rgba(38, 180, 134, .11);
}

.dashboard-header h1 {
  margin: 5px 0 0;
  color: #172a45;
  font-size: clamp(25px, 2vw, 31px);
  line-height: 1.25;
  letter-spacing: -.9px;
}

.dashboard-header p {
  margin: 7px 0 0;
  color: #73849a;
  font-size: 13px;
  line-height: 1.65;
}

.refresh-control {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  gap: 10px;
  min-width: 158px;
  padding: 10px 13px;
  border: 1px solid #dfe7f0;
  border-radius: 12px;
  background: #fff;
  color: #2d4766;
  text-align: left;
  cursor: pointer;
  box-shadow: 0 5px 18px rgba(34, 62, 96, .045);
  transition: transform .18s, border-color .18s, box-shadow .18s;
}

.refresh-control:hover {
  transform: translateY(-1px);
  border-color: #b9d0e9;
  box-shadow: 0 8px 24px rgba(34, 62, 96, .08);
}

.refresh-control:disabled {
  cursor: wait;
  opacity: .75;
}

.refresh-control > span:last-child {
  display: flex;
  flex-direction: column;
}

.refresh-control b {
  font-size: 12px;
  line-height: 17px;
}

.refresh-control small {
  color: #8b9aab;
  font-size: 9px;
  line-height: 15px;
}

.refresh-icon {
  display: grid;
  place-items: center;
  width: 31px;
  height: 31px;
  border-radius: 9px;
  background: #edf5ff;
  color: #2b77cd;
  font-size: 16px;
}

.refresh-icon.spinning .el-icon {
  animation: spin .8s linear infinite;
}

.dashboard-loading {
  min-height: 650px;
  padding: 24px;
  border: 1px solid #e3eaf2;
  border-radius: 18px;
  background: #fff;
}

.hero-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(340px, .8fr);
  gap: 16px;
}

.inventory-hero {
  position: relative;
  min-width: 0;
  min-height: 306px;
  padding: 24px 26px 21px;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, .07);
  border-radius: 20px;
  background:
    radial-gradient(circle at 88% 12%, rgba(69, 137, 222, .24), transparent 35%),
    linear-gradient(135deg, #0d223f 0%, #132f54 62%, #174265 100%);
  color: #fff;
  box-shadow: 0 18px 44px rgba(16, 42, 75, .17);
}

.hero-orbit {
  position: absolute;
  border: 1px solid rgba(166, 203, 241, .12);
  border-radius: 50%;
  pointer-events: none;
}

.orbit-one {
  top: -174px;
  right: -96px;
  width: 430px;
  height: 430px;
}

.orbit-two {
  top: -102px;
  right: -26px;
  width: 270px;
  height: 270px;
}

.hero-topline {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #8da8c8;
  font-size: 10px;
  line-height: 16px;
}

.live-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #c8ddf2;
  font-weight: 700;
  letter-spacing: .5px;
}

.live-badge i {
  width: 5px;
  height: 5px;
  background: #59ddb0;
  box-shadow: 0 0 0 5px rgba(89, 221, 176, .1);
}

.hero-content {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 150px;
  align-items: center;
  gap: 28px;
  min-height: 174px;
  padding: 15px 7px 12px;
}

.hero-number > span {
  color: #9fb6cf;
  font-size: 12px;
  line-height: 18px;
}

.hero-number > div {
  display: flex;
  align-items: baseline;
  gap: 9px;
  margin-top: 3px;
}

.hero-number strong {
  max-width: 100%;
  color: #fff;
  font-size: clamp(40px, 5vw, 62px);
  font-weight: 720;
  line-height: 1.08;
  letter-spacing: -2.5px;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.hero-number small {
  color: #9db5ce;
  font-size: 12px;
}

.hero-number p {
  margin: 8px 0 0;
  color: #92aac4;
  font-size: 11px;
  line-height: 18px;
}

.availability {
  display: flex;
  align-items: center;
  position: relative;
  flex-direction: column;
}

.availability-ring {
  position: relative;
  display: grid;
  place-items: center;
  width: 112px;
  height: 112px;
  border-radius: 50%;
}

.availability-ring:before,
.availability-ring:after {
  position: absolute;
  border-radius: 50%;
  content: "";
}

.availability-ring:before {
  inset: 0;
  background: conic-gradient(#55ddb1 var(--gauge-value), rgba(180, 207, 233, .13) 0);
  box-shadow: 0 0 30px rgba(55, 196, 154, .08);
}

.availability-ring:after {
  inset: 8px;
  border: 1px solid rgba(197, 221, 244, .09);
  background: #143253;
}

.availability-ring > div {
  position: relative;
  z-index: 1;
}

.availability-ring strong {
  font-size: 27px;
  line-height: 1;
}

.availability-ring span {
  margin-left: 2px;
  color: #91abc5;
  font-size: 10px;
}

.availability > b {
  margin-top: 8px;
  color: #d8e8f7;
  font-size: 11px;
  line-height: 16px;
}

.availability > span {
  color: #819bb7;
  font-size: 9px;
  line-height: 14px;
}

.hero-foot {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  padding-top: 15px;
  border-top: 1px solid rgba(186, 211, 237, .11);
}

.hero-foot > div {
  min-width: 0;
  padding: 0 18px;
  border-right: 1px solid rgba(186, 211, 237, .1);
}

.hero-foot > div:first-child {
  padding-left: 7px;
}

.hero-foot > div:last-child {
  border-right: 0;
}

.hero-foot span {
  display: block;
  color: #87a2bd;
  font-size: 9px;
  line-height: 14px;
}

.hero-foot b {
  display: inline-block;
  max-width: calc(100% - 25px);
  margin-top: 3px;
  color: #edf7ff;
  font-size: 19px;
  line-height: 24px;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.hero-foot small {
  margin-left: 4px;
  color: #7895b1;
  font-size: 9px;
}

.surface-card {
  min-width: 0;
  border: 1px solid #e1e8f1;
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 9px 30px rgba(31, 58, 91, .055);
}

.flow-card {
  display: flex;
  flex-direction: column;
  padding: 22px 22px 18px;
}

.section-title-row,
.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

.section-title-row h2,
.panel-header h2 {
  margin: 3px 0 0;
  color: #1c324d;
  font-size: 18px;
  line-height: 25px;
  letter-spacing: -.35px;
}

.section-title-row p,
.panel-header p {
  margin: 3px 0 0;
  color: #8795a8;
  font-size: 10px;
  line-height: 16px;
}

.health-pill {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  gap: 7px;
  padding: 6px 8px;
  border-radius: 20px;
  background: #edf9f5;
  color: #268365;
  font-size: 9px;
  font-weight: 700;
  line-height: 13px;
}

.health-pill i {
  width: 5px;
  height: 5px;
  box-shadow: none;
}

.flow-list {
  display: grid;
  gap: 18px;
  margin-top: 25px;
}

.flow-copy {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.flow-copy span {
  color: #63758b;
  font-size: 11px;
  line-height: 17px;
}

.flow-copy b {
  color: #21405f;
  font-size: 20px;
  line-height: 25px;
  font-variant-numeric: tabular-nums;
}

.flow-item.inbound .flow-copy b {
  color: #267edb;
}

.flow-item.outbound .flow-copy b {
  color: #239470;
}

.flow-track {
  height: 6px;
  margin-top: 8px;
  overflow: hidden;
  border-radius: 10px;
  background: #edf2f7;
}

.flow-track i {
  display: block;
  min-width: 0;
  height: 100%;
  border-radius: inherit;
  transition: width .5s ease;
}

.flow-item.inbound .flow-track i {
  background: linear-gradient(90deg, #56a3f7, #267fdf);
}

.flow-item.outbound .flow-track i {
  background: linear-gradient(90deg, #53c9a4, #249672);
}

.flow-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: auto;
  padding-top: 20px;
}

.flow-summary > div {
  min-width: 0;
  padding: 11px 12px;
  border: 1px solid #e8edf3;
  border-radius: 11px;
  background: #f8fafc;
}

.flow-summary span {
  display: block;
  color: #8795a6;
  font-size: 9px;
  line-height: 14px;
}

.flow-summary b {
  display: block;
  max-width: 100%;
  margin-top: 3px;
  color: #263f5c;
  font-size: 17px;
  line-height: 22px;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.positive {
  color: #238c6d !important;
}

.negative {
  color: #d35a58 !important;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-top: 14px;
}

.metric-card {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  align-items: start;
  gap: 13px;
  min-height: 123px;
  padding: 18px;
  transition: transform .18s, box-shadow .18s;
}

.metric-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 13px 34px rgba(31, 58, 91, .085);
}

.metric-icon {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border-radius: 13px;
  font-size: 19px;
}

.metric-card.blue .metric-icon {
  background: #eaf4ff;
  color: #2b7cd3;
}

.metric-card.violet .metric-icon {
  background: #f1edff;
  color: #7255d4;
}

.metric-card.amber .metric-icon {
  background: #fff5e4;
  color: #c47a1f;
}

.metric-card.green .metric-icon {
  background: #eaf8f3;
  color: #218d6c;
}

.metric-copy {
  min-width: 0;
}

.metric-copy > span {
  color: #718197;
  font-size: 10px;
  line-height: 16px;
}

.metric-copy > div {
  display: flex;
  align-items: baseline;
  gap: 5px;
  margin-top: 3px;
}

.metric-copy strong {
  max-width: 100%;
  color: #1a304c;
  font-size: clamp(21px, 2vw, 27px);
  line-height: 32px;
  letter-spacing: -.65px;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.metric-copy small {
  flex: 0 0 auto;
  color: #909daf;
  font-size: 9px;
}

.metric-copy p {
  margin: 4px 0 0;
  color: #98a4b3;
  font-size: 9px;
  line-height: 15px;
}

.quick-panel {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  align-items: center;
  gap: 20px;
  margin-top: 14px;
  padding: 18px 20px;
}

.quick-heading > span {
  color: #367bc6;
  font-size: 10px;
  font-weight: 800;
  line-height: 16px;
  letter-spacing: 1.5px;
}

.quick-heading > div {
  display: flex;
  flex-direction: column;
  margin-top: 3px;
}

.quick-heading b {
  color: #253d59;
  font-size: 17px;
  line-height: 24px;
}

.quick-heading small {
  color: #919eae;
  font-size: 11px;
  line-height: 17px;
}

.quick-actions {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 7px;
}

.quick-action {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) auto;
  align-items: center;
  min-width: 0;
  min-height: 66px;
  gap: 11px;
  padding: 11px 12px;
  border: 1px solid transparent;
  border-radius: 13px;
  background: #f7f9fc;
  transition: border-color .16s, background .16s, transform .16s;
}

.quick-action:hover {
  transform: translateY(-1px);
  border-color: #d7e3f0;
  background: #fff;
}

.quick-action > span {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 11px;
  background: #e8f3ff;
  color: #2b7dcc;
  font-size: 18px;
}

.quick-action.violet > span {
  background: #f0edff;
  color: #6c55ca;
}

.quick-action.green > span {
  background: #e7f7f1;
  color: #248d6c;
}

.quick-action.amber > span {
  background: #fff3df;
  color: #c47b21;
}

.quick-action > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.quick-action b {
  color: #304963;
  font-size: 13px;
  line-height: 19px;
}

.quick-action small {
  color: #929faf;
  font-size: 10px;
  line-height: 16px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}

.quick-action > i {
  color: #96a5b5;
  font-size: 16px;
  font-style: normal;
}

.insight-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(350px, .75fr);
  align-items: stretch;
  gap: 14px;
  margin-top: 14px;
  padding-bottom: 16px;
}

.trend-panel,
.activity-panel {
  min-height: 386px;
  padding: 20px 21px 16px;
}

.panel-header h2 {
  font-size: 17px;
}

.range-switch {
  display: inline-flex;
  padding: 3px;
  border-radius: 10px;
  background: #f0f4f8;
}

.range-switch button {
  min-width: 48px;
  padding: 6px 9px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #7f8fa2;
  font-size: 9px;
  cursor: pointer;
}

.range-switch button.active {
  background: #fff;
  color: #2b70be;
  font-weight: 700;
  box-shadow: 0 3px 9px rgba(41, 69, 102, .09);
}

.chart-legend {
  display: flex;
  gap: 16px;
  margin-top: 12px;
  color: #77889c;
  font-size: 9px;
  line-height: 14px;
}

.chart-legend span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.chart-legend i {
  width: 17px;
  height: 3px;
  border-radius: 4px;
}

.chart-legend i.inbound {
  background: #2588f5;
}

.chart-legend i.outbound {
  background: #26ad83;
}

.trend-chart {
  width: 100%;
  height: 292px;
  margin-top: 2px;
}

.activity-header {
  align-items: center;
  padding-bottom: 13px;
  border-bottom: 1px solid #edf1f5;
}

.activity-header a {
  flex: 0 0 auto;
  color: #3979bf;
  font-size: 9px;
  line-height: 14px;
}

.activity-header a span {
  margin-left: 3px;
}

.activity-list {
  display: grid;
}

.activity-row {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  min-height: 49px;
  padding: 9px 0;
  border-bottom: 1px solid #f0f3f6;
}

.activity-row:last-child {
  border-bottom: 0;
}

.activity-icon {
  display: grid;
  place-items: center;
  width: 31px;
  height: 31px;
  border-radius: 9px;
  font-size: 13px;
  font-weight: 800;
}

.activity-icon.inbound {
  background: #e9f4ff;
  color: #2b7fd8;
}

.activity-icon.outbound {
  background: #e9f8f3;
  color: #258f6e;
}

.activity-copy,
.activity-value {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.activity-copy b {
  color: #304963;
  font-size: 10px;
  line-height: 16px;
}

.activity-copy span {
  color: #99a5b3;
  font-size: 8px;
  line-height: 13px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}

.activity-value {
  align-items: flex-end;
}

.activity-value b {
  font-size: 11px;
  line-height: 16px;
  font-variant-numeric: tabular-nums;
}

.activity-value span {
  color: #99a5b4;
  font-size: 8px;
  line-height: 13px;
}

.activity-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 285px;
  flex-direction: column;
  color: #8b9bad;
  text-align: center;
}

.activity-empty > span {
  display: grid;
  place-items: center;
  width: 43px;
  height: 43px;
  border-radius: 13px;
  background: #edf5fd;
  color: #3c7aba;
  font-size: 18px;
}

.activity-empty b {
  margin-top: 12px;
  color: #415a73;
  font-size: 12px;
}

.activity-empty p {
  max-width: 230px;
  margin: 5px 0 0;
  font-size: 9px;
  line-height: 16px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 1260px) {
  .hero-grid {
    grid-template-columns: minmax(0, 1.4fr) minmax(315px, .8fr);
  }

  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .quick-panel {
    grid-template-columns: 180px minmax(0, 1fr);
  }

  .quick-actions {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1020px) {
  .hero-grid,
  .insight-grid {
    grid-template-columns: 1fr;
  }

  .inventory-hero {
    min-height: 294px;
  }

  .flow-card {
    min-height: 280px;
  }

  .quick-panel {
    grid-template-columns: 1fr;
  }

  .quick-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .quick-heading > div {
    align-items: flex-end;
  }
}

@media (max-width: 760px) {
  .dashboard-header {
    align-items: stretch;
    flex-direction: column;
    gap: 13px;
  }

  .refresh-control {
    width: 100%;
  }

  .inventory-hero {
    min-height: 0;
    padding: 20px 18px 18px;
    border-radius: 17px;
  }

  .hero-content {
    grid-template-columns: 1fr;
    gap: 14px;
    padding-top: 24px;
  }

  .hero-number {
    text-align: center;
  }

  .hero-number > div {
    justify-content: center;
  }

  .hero-number strong {
    font-size: clamp(38px, 13vw, 54px);
  }

  .hero-foot {
    margin-top: 12px;
  }

  .hero-foot > div {
    padding: 0 9px;
  }

  .hero-foot > div:first-child {
    padding-left: 0;
  }

  .hero-foot span {
    font-size: 8px;
  }

  .hero-foot b {
    font-size: 16px;
  }

  .metric-grid {
    grid-template-columns: 1fr;
  }

  .metric-card {
    min-height: 104px;
    padding: 15px;
  }

  .quick-actions {
    grid-template-columns: 1fr;
  }

  .quick-heading {
    align-items: flex-start;
  }

  .quick-heading > div {
    align-items: flex-start;
  }

  .panel-header {
    align-items: stretch;
    flex-direction: column;
  }

  .range-switch {
    align-self: flex-start;
  }

  .trend-panel,
  .activity-panel {
    min-height: 0;
    padding: 18px 15px 14px;
  }

  .trend-chart {
    height: 270px;
  }

  .activity-header {
    align-items: center;
    flex-direction: row;
  }

  .activity-row {
    grid-template-columns: 32px minmax(0, 1fr) auto;
    gap: 7px;
  }
}

@media (max-width: 410px) {
  .hero-topline > span:last-child {
    display: none;
  }

  .flow-summary {
    grid-template-columns: 1fr;
  }

  .activity-value span {
    display: none;
  }
}
</style>
