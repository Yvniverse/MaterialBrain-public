<script>
import GIcon from './GIcon.vue'
export default {
  name: 'GlacierHome',
  components: { GIcon },
  props: {
    summary: Object,
    activities: Array,
    projects: Array,
    canAsk: { default: true },
    range: { default: 14 },
    loading: Boolean,
  },
  emits: ['navigate', 'ask', 'range', 'refresh'],
  data() {
    return { question: '' }
  },
  computed: {
    metrics() {
      const s = this.summary || {}
      return [
        {
          label: '在库物料',
          value: s.material_count ?? '—',
          unit: '种',
          icon: 'cube',
          note: '连接型号、库存与库位',
        },
        {
          label: '可用数量',
          value: s.available_quantity ?? '—',
          unit: '件',
          icon: 'layers',
          note: '已扣除预留量',
        },
        {
          label: '预留数量',
          value: s.reserved_quantity ?? '—',
          unit: '件',
          icon: 'folder',
          note: '关联项目备料',
        },
        {
          label: '库存金额',
          value: s.inventory_value ?? '—',
          unit: '元',
          icon: 'wallet',
          note: '按已录入单价统计',
        },
      ]
    },
  },
  methods: {
    ask() {
      const q = this.question.trim()
      if (q) {
        this.$emit('ask', q)
        this.question = ''
      }
    },
  },
}
</script>
<template>
  <div class="g-home">
    <div class="g-page-heading">
      <div>
        <span class="g-eyebrow">YOUR WORK, CONNECTED</span>
        <h1>工作概览<span class="g-title-dot">.</span></h1>
      </div>
      <button class="g-btn" @click="$emit('refresh')" :disabled="loading">
        <GIcon name="refresh" :size="17" />刷新数据
      </button>
    </div>
    <div class="g-home-top">
      <section class="g-welcome">
        <div class="g-welcome-copy">
          <span class="g-tag light">MATERIAL INTELLIGENCE</span>
          <h2>从一个需求，<br />到下一步行动。</h2>
          <p>选型有依据，物料找得到，备料有路径。</p>
        </div>
        <div class="g-optical-object" aria-hidden="true">
          <div class="g-glass-layer one"></div>
          <div class="g-glass-layer two"></div>
          <div class="g-glass-layer three"></div>
          <div class="g-optical-center"><GIcon name="layers" :size="40" /></div>
        </div>
        <form v-if="canAsk" class="g-home-query" @submit.prevent="ask">
          <GIcon name="brain" /><input
            v-model="question"
            aria-label="向物料大脑提问"
            placeholder="描述工程需求，或输入物料型号…"
          /><button class="g-icon-btn primary" type="submit" aria-label="提交需求">
            <GIcon name="arrow" />
          </button>
        </form>
      </section>
      <section class="g-start-panel">
        <slot name="shortcuts"
          ><header>
            <h3>继续你的工作</h3>
            <span class="g-tag">快捷入口</span>
          </header>
          <button class="g-optic-pill" @click="$emit('navigate', 'brain')">
            <span class="g-pictogram blue"><GIcon name="brain" /></span
            ><span><b>物料大脑</b><small>候选 · 证据 · 工程草案</small></span
            ><GIcon name="arrow" :size="19" /></button
          ><button class="g-optic-pill mint" @click="$emit('navigate', 'warehouse')">
            <span class="g-pictogram mint"><GIcon name="cube" /></span
            ><span><b>数字孪生仓库</b><small>空间定位 · 路线预览</small></span
            ><GIcon name="arrow" :size="19" /></button
          ><button class="g-optic-pill neutral" @click="$emit('navigate', 'locations')">
            <span class="g-pictogram"><GIcon name="drawer" /></span
            ><span><b>可视化库位</b><small>抽屉柜 · 元件盒 · 货架</small></span
            ><GIcon name="arrow" :size="19" /></button
        ></slot>
      </section>
    </div>
    <div class="g-metric-grid">
      <article v-for="m in metrics" :key="m.label" class="g-card g-metric">
        <header>
          <span>{{ m.label }}</span
          ><GIcon :name="m.icon" :size="18" />
        </header>
        <div class="g-metric-number">
          {{ m.value }}<small>{{ m.unit }}</small>
        </div>
        <small class="g-muted">{{ m.note }}</small>
      </article>
    </div>
    <div class="g-home-middle">
      <section class="g-card g-trend">
        <header class="g-card-heading">
          <div>
            <h3>库存流转</h3>
            <span class="g-muted">入库与出库变化</span>
          </div>
          <div class="g-segmented">
            <button
              v-for="r in [7, 14, 30]"
              :key="r"
              :class="{ active: range === r }"
              @click="$emit('range', r)"
            >
              {{ r }} 天
            </button>
          </div>
        </header>
        <div class="g-flow-summary">
          <span
            ><i class="g-dot blue"></i>今日入库 <b>{{ summary?.today_inbound ?? '—' }}</b></span
          ><span
            ><i class="g-dot mint"></i>今日出库 <b>{{ summary?.today_outbound ?? '—' }}</b></span
          ><span class="g-muted">单位：件</span>
        </div>
        <slot name="chart"><div class="g-chart-placeholder">库存趋势</div></slot>
      </section>
      <section class="g-card g-projects-panel">
        <slot name="sidepanel"
          ><header class="g-card-heading">
            <h3>近期项目</h3>
            <button class="g-text-btn" @click="$emit('navigate', 'bom')">
              全部项目 <GIcon name="arrow" :size="16" />
            </button>
          </header>
          <button
            class="g-project-row"
            v-for="p in projects"
            :key="p.name"
            @click="$emit('navigate', 'bom')"
          >
            <span class="g-pictogram"><GIcon name="folder" /></span
            ><span
              ><b>{{ p.name }}</b
              ><small>{{ p.note }}</small></span
            ><span class="g-tag" :class="p.tone">{{ p.status }}</span>
          </button>
          <div class="g-project-bottom">
            <GIcon name="info" :size="16" /><span>将物料选择带入项目，继续 BOM 与备料。</span>
          </div></slot
        >
      </section>
    </div>
    <section class="g-card g-activity">
      <header class="g-card-heading">
        <h3>最近动态</h3>
        <button class="g-text-btn" @click="$emit('navigate', 'movements')">
          查看流水 <GIcon name="arrow" :size="16" />
        </button>
      </header>
      <div class="g-table-scroll">
        <table class="g-table">
          <thead>
            <tr>
              <th>物料／事项</th>
              <th>操作</th>
              <th>数量变化</th>
              <th>关联位置／记录</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in activities" :key="a.id">
              <td>
                <b>{{ a.name }}</b>
              </td>
              <td>{{ a.action }}</td>
              <td>{{ a.quantity }}</td>
              <td>{{ a.location }}</td>
              <td class="g-muted">{{ a.time }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
