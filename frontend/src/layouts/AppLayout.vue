<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import GlacierShell from '../glacier/components/GlacierShell.vue'
import { useAuthStore } from '../stores/auth'
import { api } from '../api/client'
import type { Material, Page } from '../types'
import { materialPrimaryIdentity } from '../utils/materialIdentity'
import { resetWarehouseAgentSession } from '../composables/useWarehouseAgent'
import FloatingAgentLauncher from '../components/agent/FloatingAgentLauncher.vue'
import FloatingAgentPanel from '../components/agent/FloatingAgentPanel.vue'
const auth = useAuthStore(),
  route = useRoute(),
  router = useRouter()
const agentPanelOpen = ref(false),
  searchOpen = ref(false),
  search = ref('')
const foundMaterials = ref<Material[]>([]),
  searchBusy = ref(false),
  searchError = ref('')
let searchTimer: ReturnType<typeof setTimeout> | undefined,
  searchGeneration = 0
const agentAnchor = ref({ x: 0, y: 8, width: 100, height: 100 })
const frontendBuildSha = import.meta.env.VITE_BUILD_SHA || 'unknown'
const entries = [
  {
    id: 'home',
    path: '/dashboard',
    label: '工作概览',
    icon: 'grid',
    group: '工作空间',
    permission: 'dashboard:view',
  },
  {
    id: 'brain',
    path: '/agent',
    label: '物料大脑',
    icon: 'brain',
    group: '工作空间',
    permission: 'material:view',
  },
  {
    id: 'warehouse',
    path: '/warehouse-twin',
    label: '数字孪生仓库',
    icon: 'cube',
    tag: '3D',
    group: '工作空间',
    permission: ['material:view', 'location:manage', 'picking:view'],
  },
  {
    id: 'locations',
    path: '/locations',
    label: '可视化库位',
    icon: 'drawer',
    group: '工作空间',
    permission: ['location:manage', 'material:view'],
  },
  {
    id: 'materials',
    path: '/materials',
    label: '物料库',
    icon: 'layers',
    group: '工程与库存',
    permission: 'material:view',
  },
  {
    id: 'bom',
    path: '/products',
    label: 'BOM / 生产',
    icon: 'doc',
    group: '工程与库存',
    permission: ['material:view', 'project:view', 'project:manage', 'picking:view'],
  },
  {
    id: 'inventory',
    path: '/inventory',
    label: '库存操作',
    icon: 'activity',
    group: '工程与库存',
    permission: 'inventory:operate',
  },
  {
    id: 'movements',
    path: '/movements',
    label: '库存流水',
    icon: 'history',
    group: '工程与库存',
    permission: 'inventory:view',
  },
  {
    id: 'stocktakes',
    path: '/stocktakes',
    label: '盘点管理',
    icon: 'check',
    group: '工程与库存',
    permission: 'inventory:view',
  },
  {
    id: 'cables',
    path: '/cables',
    label: '线缆管理',
    icon: 'link',
    group: '资源与管理',
    permission: 'material:view',
  },
  {
    id: 'categories',
    path: '/categories',
    label: '分类管理',
    icon: 'grid',
    group: '资源与管理',
    permission: 'category:manage',
  },
  {
    id: 'suppliers',
    path: '/suppliers',
    label: '供应商',
    icon: 'cube',
    group: '资源与管理',
    permission: 'supplier:manage',
  },
  {
    id: 'purchases',
    path: '/purchases',
    label: '采购管理',
    icon: 'doc',
    group: '资源与管理',
    permission: 'purchase:manage',
  },
  {
    id: 'users',
    path: '/users',
    label: '用户与角色',
    icon: 'user',
    group: '资源与管理',
    permission: ['user:manage', 'role:manage'],
  },
  {
    id: 'audit',
    path: '/audit',
    label: '审计日志',
    icon: 'history',
    group: '资源与管理',
    permission: 'audit:view',
  },
  {
    id: 'settings',
    path: '/settings',
    label: '系统设置',
    icon: 'settings',
    group: '资源与管理',
    permission: 'role:manage',
  },
]
const navigation = computed(() =>
  entries
    .filter((n) => auth.can(n.permission))
    .map((n) => ({
      ...n,
      path: n.id === 'bom' && !auth.can('material:view') ? '/projects' : n.path,
    })),
)
const active = computed(() =>
  route.path === '/warehouse-lab'
    ? 'warehouse'
    : route.path.startsWith('/projects')
      ? 'bom'
      : navigation.value.find((n) => route.path === n.path || route.path.startsWith(n.path + '/'))
          ?.id || 'home',
)
const searchResults = computed(() =>
  navigation.value.filter((n) =>
    [n.label, n.path].join(' ').toLowerCase().includes(search.value.toLowerCase()),
  ),
)
const showFloatingAgent = computed(
  () => auth.can('material:view') && !route.path.startsWith('/picking/operator/'),
)
const floatingDock = computed(() => {
  if (route.path === '/dashboard') return 'overview'
  if (route.path === '/warehouse-twin') return 'warehouse'
  if (route.path === '/agent') return 'workbench'
  if (route.path === '/locations') return 'storage'
  return 'corner'
})
function navigate(id: string) {
  const n = navigation.value.find((x) => x.id === id)
  if (!n) return
  searchOpen.value = false
  if (n.path === '/locations' && route.path === '/locations')
    window.dispatchEvent(new Event('locations:show-overview'))
  void router.push(n.id === 'bom' && !auth.can('material:view') ? '/projects' : n.path)
}
async function logout() {
  try {
    await auth.logout()
  } finally {
    resetWarehouseAgentSession()
    agentPanelOpen.value = false
    void router.push('/login')
  }
}
function shortcuts(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    searchOpen.value = !searchOpen.value
  }
}
onMounted(() => window.addEventListener('keydown', shortcuts))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', shortcuts)
  clearTimeout(searchTimer)
  searchGeneration++
})
watch(
  () => auth.user?.id,
  (next, previous) => {
    if (next !== previous) {
      resetWarehouseAgentSession()
      agentPanelOpen.value = false
      clearTimeout(searchTimer)
      searchGeneration++
      foundMaterials.value = []
      searchBusy.value = false
      searchError.value = ''
      search.value = ''
      searchOpen.value = false
    }
  },
)
watch(showFloatingAgent, (show) => {
  if (!show) agentPanelOpen.value = false
})
watch([search, searchOpen], () => {
  clearTimeout(searchTimer)
  const token = ++searchGeneration
  foundMaterials.value = []
  searchBusy.value = false
  searchError.value = ''
  const q = search.value.trim()
  if (!searchOpen.value || q.length < 2 || !auth.can('material:view')) return
  const userId = auth.user?.id
  searchBusy.value = true
  searchTimer = setTimeout(async () => {
    try {
      const response = await api.get<Page<Material>>('/materials', {
        params: { q, page: 1, page_size: 8 },
      })
      if (token === searchGeneration && auth.user?.id === userId)
        foundMaterials.value = response.data.items
    } catch {
      if (token === searchGeneration) searchError.value = '物料搜索暂不可用，功能导航仍可使用。'
    } finally {
      if (token === searchGeneration) searchBusy.value = false
    }
  }, 220)
})
function openMaterial(m: Material, twin = false) {
  searchOpen.value = false
  void router.push(
    twin ? { path: '/warehouse-twin', query: { focus: m.location_id } } : `/materials/${m.id}`,
  )
}
</script>
<template>
  <GlacierShell
    :active="active"
    :navigation="navigation"
    :user-name="auth.user?.full_name || '研发空间'"
    :user-role="auth.user?.role.name || 'MaterialBrain'"
    @navigate="navigate"
    @search="searchOpen = true"
    @settings="auth.can('role:manage') ? navigate('settings') : (searchOpen = true)"
  >
    <router-view />
    <template #user
      ><el-dropdown
        ><button class="g-user-menu">
          <span class="g-avatar">{{ auth.user?.full_name?.slice(0, 1) || 'M' }}</span
          ><span>{{ auth.user?.full_name }}</span></button
        ><template #dropdown
          ><el-dropdown-menu
            ><el-dropdown-item disabled>{{ auth.user?.username }}</el-dropdown-item
            ><el-dropdown-item divided @click="logout">退出登录</el-dropdown-item></el-dropdown-menu
          ></template
        ></el-dropdown
      ></template
    >
    <template #footer
      ><details
        class="g-build-info"
        data-testid="frontend-build-sha"
        :data-build-sha="frontendBuildSha"
      >
        <summary>版本信息</summary>
        <code>{{ frontendBuildSha }}</code>
      </details></template
    >
    <template #floating
      ><FloatingAgentLauncher
        v-if="showFloatingAgent"
        :dock="floatingDock"
        @position-change="agentAnchor = $event"
        @open="agentPanelOpen = true" /><FloatingAgentPanel
        v-if="showFloatingAgent"
        :open="agentPanelOpen"
        :anchor="agentAnchor"
        @close="agentPanelOpen = false"
    /></template>
  </GlacierShell>
  <el-dialog v-model="searchOpen" title="搜索工作空间" width="min(580px,94vw)" align-center
    ><el-input v-model="search" placeholder="搜索功能或物料型号…" autofocus clearable />
    <div class="g-command-results">
      <button
        v-for="n in searchResults"
        :key="n.id"
        class="g-command-result"
        @click="navigate(n.id)"
      >
        <b>{{ n.label }}</b
        ><small>{{ n.group }}</small>
      </button>
    </div>
    <p v-if="searchBusy" class="g-muted">正在查找物料…</p>
    <p v-if="searchError" class="g-muted">{{ searchError }}</p>
    <div v-for="m in foundMaterials" :key="m.id" class="g-command-result">
      <button class="g-text-btn" @click="openMaterial(m)">
        <b>{{ materialPrimaryIdentity(m) }}</b></button
      ><button v-if="m.location_id" class="g-text-btn" @click="openMaterial(m, true)">
        定位到仓库 ↗
      </button>
    </div></el-dialog
  >
</template>
