import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AppLayout from '../layouts/AppLayout.vue'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    permission?: string | readonly string[]
  }
}

const children: RouteRecordRaw[] = [
  { path: '', redirect: '/dashboard' },
  {
    path: 'dashboard',
    component: () => import('../views/Dashboard.vue'),
    meta: { title: '仪表盘', permission: 'dashboard:view' },
  },
  {
    path: 'agent',
    component: () => import('../views/AgentWorkbench.vue'),
    meta: { title: '物料大脑', permission: 'material:view' },
  },
  {
    path: 'materials',
    component: () => import('../views/Materials.vue'),
    meta: { title: '物料管理', permission: 'material:view' },
  },
  {
    path: 'materials/new',
    component: () => import('../views/MaterialForm.vue'),
    meta: { title: '新增物料', permission: 'material:manage' },
  },
  {
    path: 'materials/:id',
    component: () => import('../views/MaterialDetail.vue'),
    meta: { title: '物料详情', permission: 'material:view' },
  },
  {
    path: 'materials/:id/edit',
    component: () => import('../views/MaterialForm.vue'),
    meta: { title: '编辑物料', permission: 'material:manage' },
  },
  {
    path: 'cables',
    component: () => import('../views/Cables.vue'),
    meta: { title: '线缆管理', permission: 'material:view' },
  },
  {
    path: 'inventory',
    component: () => import('../views/InventoryOperation.vue'),
    meta: { title: '库存操作', permission: 'inventory:operate' },
  },
  {
    path: 'movements',
    component: () => import('../views/Movements.vue'),
    meta: { title: '库存流水', permission: 'inventory:view' },
  },
  {
    path: 'categories',
    component: () => import('../views/Categories.vue'),
    meta: { title: '分类管理', permission: 'category:manage' },
  },
  {
    path: 'warehouse-lab',
    redirect: (to) => ({ path: '/warehouse-twin', query: { ...to.query, workspace: 'robot-lab' }, hash: to.hash }),
    meta: { title: '具身智能实验室', permission: ['material:view', 'location:manage', 'picking:view'] },
  },
  {
    path: 'warehouse-twin',
    component: () => import('../views/WarehouseTwin.vue'),
    meta: { title: '数字孪生仓库', permission: ['material:view', 'location:manage', 'picking:view'] },
  },
  {
    path: 'picking/operator/:taskId',
    component: () => import('../views/PickingOperator.vue'),
    meta: { title: 'Guided Picking', permission: 'picking:view' },
  },
  {
    path: 'locations',
    component: () => import('../views/Locations.vue'),
    meta: { title: '元件盒库位', permission: ['location:manage', 'material:view'] },
  },
  {
    path: 'suppliers',
    component: () => import('../views/MasterData.vue'),
    props: { mode: 'suppliers' },
    meta: { title: '供应商管理', permission: 'supplier:manage' },
  },
  {
    path: 'projects',
    component: () => import('../views/Projects.vue'),
    meta: {
      title: '项目 / 生产任务',
      permission: ['project:view', 'project:manage', 'picking:view'],
    },
  },
  {
    path: 'products',
    component: () => import('../views/Products.vue'),
    meta: { title: '产品定义 / 版本 BOM', permission: 'material:view' },
  },
  {
    path: 'stocktakes',
    component: () => import('../views/BusinessList.vue'),
    props: { mode: 'stocktakes' },
    meta: { title: '盘点管理', permission: 'inventory:view' },
  },
  {
    path: 'purchases',
    component: () => import('../views/BusinessList.vue'),
    props: { mode: 'purchases' },
    meta: { title: '采购管理', permission: 'purchase:manage' },
  },
  {
    path: 'users',
    component: () => import('../views/Users.vue'),
    meta: { title: '用户与角色', permission: ['user:manage', 'role:manage'] },
  },
  {
    path: 'audit',
    component: () => import('../views/Audit.vue'),
    meta: { title: '审计日志', permission: 'audit:view' },
  },
  {
    path: 'settings',
    component: () => import('../views/Settings.vue'),
    meta: { title: '系统设置', permission: 'role:manage' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('../views/Login.vue'), meta: { title: '登录' } },
    { path: '/', component: AppLayout, children },
    { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.initialized) await auth.fetchMe()
  document.title = `${to.meta.title || '工作台'} · MaterialBrain`
  if (to.path === '/login') return auth.user ? '/dashboard' : true
  if (!auth.user) return { path: '/login', query: { redirect: to.fullPath } }
  if (!auth.can(to.meta.permission)) return '/dashboard'
  return true
})
export default router
