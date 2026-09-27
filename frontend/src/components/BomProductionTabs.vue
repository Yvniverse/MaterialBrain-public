<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const availableTabs = computed(() => {
  const tabs: Array<{ name: 'products' | 'projects'; label: string; path: string }> = []
  if (auth.can('material:view')) {
    tabs.push({ name: 'products', label: '产品定义', path: '/products' })
  }
  if (auth.can(['project:view', 'project:manage', 'picking:view'])) {
    tabs.push({ name: 'projects', label: '项目 / 生产任务', path: '/projects' })
  }
  return tabs
})

const activeTab = computed(() => (route.path.startsWith('/projects') ? 'projects' : 'products'))

async function changeTab(name: string | number) {
  const target = availableTabs.value.find((item) => item.name === name)
  if (target && target.path !== route.path) await router.push(target.path)
}
</script>

<template>
  <section class="bom-production-tabs card" aria-label="BOM / 生产工作区">
    <div class="workspace-heading">
      <div>
        <span class="eyebrow">BOM / 生产</span>
        <b>从产品设计真相进入生产执行</b>
      </div>
      <small>产品定义 = 单台工程 BOM；项目 / 生产任务 = BuildPlan、预留与备料执行。</small>
    </div>
    <el-tabs :model-value="activeTab" class="workspace-tabs" @tab-change="changeTab">
      <el-tab-pane
        v-for="tab in availableTabs"
        :key="tab.name"
        :name="tab.name"
        :label="tab.label"
      />
    </el-tabs>
  </section>
</template>

<style scoped>
.bom-production-tabs {
  margin-bottom: 16px;
  padding: 15px 18px 0;
}
.workspace-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 6px;
}
.workspace-heading > div {
  display: grid;
  gap: 2px;
}
.workspace-heading small {
  max-width: 640px;
  color: #6d8295;
  text-align: right;
  line-height: 1.55;
}
.eyebrow {
  color: #2376a6;
  font-size: var(--mb-font-secondary);
  font-weight: 750;
  letter-spacing: 0.05em;
}
.workspace-tabs :deep(.el-tabs__header) {
  margin-bottom: 0;
}
@media (max-width: 780px) {
  .workspace-heading {
    display: grid;
  }
  .workspace-heading small {
    text-align: left;
  }
}
</style>
