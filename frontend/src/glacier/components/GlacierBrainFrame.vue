<script>
import GIcon from './GIcon.vue'
export default {
  name: 'GlacierBrainFrame',
  components: { GIcon },
  props: {
    question: String,
    loading: Boolean,
    historyCount: { default: 0 },
    selectedCount: { default: 0 },
    mode: { default: 'select' },
  },
  emits: ['new', 'history', 'mode'],
  data() {
    return { contextOpen: false }
  },
  computed: {
    modes() {
      return [
        { id: 'find', icon: 'search', name: '找物料' },
        { id: 'select', icon: 'chip', name: '工程选型' },
        { id: 'bom', icon: 'doc', name: 'BOM 分析' },
        { id: 'pick', icon: 'pin', name: '备料寻仓' },
      ]
    },
  },
}
</script>
<template>
  <div class="g-brain">
    <div class="g-page-heading">
      <div>
        <span class="g-eyebrow">MATERIAL INTELLIGENCE</span>
        <h1>物料大脑<span class="g-title-dot">.</span></h1>
      </div>
      <div class="g-row">
        <button class="g-btn" @click="$emit('history')">
          <GIcon name="history" :size="18" />历史
          <span class="g-count">{{ historyCount }}</span></button
        ><button class="g-btn dark" @click="$emit('new')" :disabled="loading">
          <GIcon name="plus" :size="18" />新任务
        </button>
      </div>
    </div>
    <div class="g-brain-modebar">
      <div class="g-segmented g-mode-tabs" aria-label="任务类型">
        <button
          v-for="m in modes"
          :key="m.id"
          :class="{ active: mode === m.id }"
          @click="$emit('mode', m.id)"
        >
          <GIcon :name="m.icon" :size="18" />{{ m.name }}
        </button>
      </div>
      <button class="g-text-btn g-context-toggle" @click="contextOpen = !contextOpen">
        <GIcon name="folder" :size="17" />工程上下文</button
      ><span class="g-mode-caption">候选、证据与下一步，在一个工作区。</span>
    </div>
    <div class="g-brain-grid">
      <div class="g-brain-main">
        <div class="g-current-query" v-if="question">
          <span class="g-avatar small">M</span>
          <p>{{ question }}</p>
          <span class="g-tag">当前需求</span>
        </div>
        <div class="g-result-region" aria-live="polite" :aria-busy="loading">
          <slot name="results" />
          <div v-if="loading" class="g-running-inline">
            <span class="g-loading-dot"></span>正在整理结果，现有内容仍可查看。
          </div>
        </div>
        <slot name="process" />
        <div class="g-composer-dock"><slot name="composer" /></div>
      </div>
      <aside class="g-context-rail" :class="{ 'is-open': contextOpen }">
        <slot name="context" />
      </aside>
    </div>
  </div>
</template>
