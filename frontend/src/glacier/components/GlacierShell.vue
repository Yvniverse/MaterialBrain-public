<script>
import GIcon from './GIcon.vue'
export default {
  name: 'GlacierShell',
  components: { GIcon },
  props: {
    active: String,
    navigation: Array,
    preview: Boolean,
    userName: { default: '个人研发空间' },
    userRole: { default: 'MaterialBrain' },
  },
  emits: ['navigate', 'search', 'bot', 'settings'],
  data() {
    return { mobile: false, collapsed: false }
  },
  computed: {
    current() {
      return this.navigation.find((n) => n.id === this.active)?.label || '工作空间'
    },
  },
  methods: {
    navigateTo(id) {
      this.$emit('navigate', id)
      this.mobile = false
    },
  },
}
</script>
<template>
  <div class="glacier g-shell" :class="{ 'is-collapsed': collapsed }">
    <aside class="g-sidebar" :class="{ 'is-open': mobile }">
      <button class="g-brand" @click="navigateTo('home')" aria-label="MaterialBrain 工作概览">
        <span class="g-brand-logo"><GIcon name="layers" :size="25" /></span
        ><span>MaterialBrain<small>ENGINEERING WORKSPACE</small></span>
      </button>
      <button class="g-search-launch" @click="$emit('search')">
        <GIcon name="search" :size="18" /><span>搜索工作空间</span><kbd>⌘ K</kbd>
      </button>
      <nav aria-label="工作空间导航">
        <template v-for="(item, index) in navigation" :key="item.id"
          ><span
            v-if="item.group && (index === 0 || navigation[index - 1].group !== item.group)"
            class="g-nav-label"
            >{{ item.group }}</span
          ><a
            :href="item.path"
            :data-nav-path="item.path"
            :aria-label="item.label"
            :title="item.label"
            :class="['g-nav-item', { active: active === item.id }]"
            :aria-current="active === item.id ? 'page' : undefined"
            @click.prevent="navigateTo(item.id)"
            ><GIcon :name="item.icon" /><span>{{ item.label }}</span
            ><small v-if="item.tag">{{ item.tag }}</small></a
          ></template
        >
      </nav>
      <div class="g-sidebar-foot">
        <div class="g-space-label">
          <span class="g-avatar">M</span>
          <div>
            <b>{{ userName }}</b
            ><small>{{ preview ? '交互设计 · 演示数据' : userRole }}</small>
          </div>
          <GIcon name="chevron" :size="16" />
        </div>
        <slot name="footer" /><button
          class="g-nav-item"
          @click="collapsed = !collapsed"
          :aria-label="collapsed ? '展开导航' : '收起导航'"
        >
          <GIcon name="menu" /><span>{{ collapsed ? '展开导航' : '收起导航' }}</span></button
        ><button class="g-nav-item" @click="$emit('settings')">
          <GIcon name="settings" /><span>设置与偏好</span>
        </button>
      </div>
    </aside>
    <button
      v-if="mobile"
      class="g-mobile-scrim"
      @click="mobile = false"
      aria-label="关闭导航"
    ></button>
    <div class="g-main-shell">
      <header class="g-topbar">
        <div class="g-breadcrumb">
          <button class="g-icon-btn g-mobile-only" @click="mobile = !mobile" aria-label="打开导航">
            <GIcon name="menu" /></button
          ><span>工作空间</span><span class="g-slash">/</span><b>{{ current }}</b>
        </div>
        <div class="g-top-actions">
          <span class="g-status-pill" v-if="preview"><i></i>演示空间</span
          ><button class="g-icon-btn" @click="$emit('search')" aria-label="搜索">
            <GIcon name="search" /></button
          ><span class="g-divider"></span><slot name="user"><span class="g-avatar">M</span></slot>
        </div>
      </header>
      <main class="g-content" :class="{ 'g-content-full': active === 'warehouse' }"><slot /></main>
    </div>
    <slot name="floating" />
  </div>
</template>
