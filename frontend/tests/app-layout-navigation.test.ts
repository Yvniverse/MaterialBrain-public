import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import AppLayout from '../src/layouts/AppLayout.vue'
import { useAuthStore } from '../src/stores/auth'
import type { User } from '../src/types'

const layoutSource = readFileSync(resolve(process.cwd(), 'src/layouts/AppLayout.vue'), 'utf8')

const paths = [
  '/dashboard',
  '/agent',
  '/locations',
  '/cables',
  '/warehouse-twin',
  '/warehouse-lab',
  '/categories',
  '/materials',
  '/inventory',
  '/movements',
  '/products',
  '/stocktakes',
  '/suppliers',
  '/purchases',
  '/users',
  '/audit',
  '/settings',
]

const admin: User = {
  id: 1,
  username: 'admin',
  full_name: '系统管理员',
  department: '系统',
  is_active: true,
  must_change_password: false,
  created_at: '2026-07-30',
  role: {
    id: 1,
    name: '系统管理员',
    description: '',
    permissions: ['*'],
    is_system: true,
  },
}

const slotStub = { template: '<div><slot /></div>' }

describe('AppLayout sidebar navigation', () => {
  it('shows the requested primary items first and preserves the remaining order', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = admin
    auth.initialized = true

    const router = createRouter({
      history: createMemoryHistory(),
      routes: paths.map((path) => ({
        path,
        component: { template: '<div />' },
        meta: { title: path },
      })),
    })
    await router.push('/dashboard')
    await router.isReady()

    const wrapper = mount(AppLayout, {
      global: {
        plugins: [pinia, router],
        stubs: {
          ElAside: slotStub,
          ElAlert: true,
          ElAvatar: slotStub,
          ElButton: { template: '<button><slot /></button>' },
          ElContainer: slotStub,
          ElDropdown: { template: '<div><slot /><slot name="dropdown" /></div>' },
          ElDropdownItem: { template: '<button><slot /></button>' },
          ElDropdownMenu: slotStub,
          ElHeader: slotStub,
          ElIcon: slotStub,
          ElMain: slotStub,
          RouterView: true,
        },
      },
    })

    const renderedPaths = wrapper
      .findAll('[data-nav-path]')
      .map((link) => link.attributes('data-nav-path'))
    expect(renderedPaths).toEqual(paths)
    expect(renderedPaths.slice(0, 4)).toEqual(['/dashboard', '/agent', '/locations', '/cables'])
    expect(wrapper.find('[data-testid="floating-agent-launcher"]').exists()).toBe(true)
    expect(wrapper.get('[data-nav-path="/products"]').text()).toContain('BOM / 生产')
    expect(wrapper.find('[data-nav-path="/projects"]').exists()).toBe(false)
  })

  it('uses real route links and navigates through every item below Projects and BOM', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = admin
    auth.initialized = true

    const router = createRouter({
      history: createMemoryHistory(),
      routes: paths.map((path) => ({
        path,
        component: { template: '<div />' },
        meta: { title: path },
      })),
    })
    await router.push('/products')
    await router.isReady()

    const wrapper = mount(AppLayout, {
      global: {
        plugins: [pinia, router],
        stubs: {
          ElAside: slotStub,
          ElAlert: true,
          ElAvatar: slotStub,
          ElButton: { template: '<button><slot /></button>' },
          ElContainer: slotStub,
          ElDropdown: { template: '<div><slot /><slot name="dropdown" /></div>' },
          ElDropdownItem: { template: '<button><slot /></button>' },
          ElDropdownMenu: slotStub,
          ElHeader: slotStub,
          ElIcon: slotStub,
          ElMain: slotStub,
          RouterView: true,
        },
      },
    })

    const lowerPaths = paths.slice(paths.indexOf('/products') + 1)
    for (const path of lowerPaths) {
      const link = wrapper.get(`[data-nav-path="${path}"]`)
      expect(link.element.tagName).toBe('A')
      expect(link.attributes('href')).toBe(path)
      await link.trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe(path)
    }
  })

  it('exposes the frontend build fingerprint for runtime provenance checks', () => {
    expect(layoutSource).toContain('import.meta.env.VITE_BUILD_SHA')
    expect(layoutSource).toContain('data-testid="frontend-build-sha"')
    expect(layoutSource).toContain(':data-build-sha="frontendBuildSha"')
  })

  it('keeps the long menu scrollable and separate from the collapse control', () => {
    expect(layoutSource).toContain('<nav class="nav-scroll"')
    expect(layoutSource).not.toContain('<el-menu')
    expect(layoutSource).toMatch(
      /\.nav-scroll\s*\{[^}]*flex:\s*1 1 auto;[^}]*min-height:\s*0;[^}]*overflow-y:\s*auto;/,
    )
    expect(layoutSource).toMatch(/\.nav-item\s*\{[^}]*height:\s*48px;/)
    expect(layoutSource).toMatch(/\.collapse\s*\{[^}]*position:\s*relative;[^}]*z-index:\s*2;/)
  })

  it('requests the location overview when the active location item is clicked again', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = admin
    auth.initialized = true

    const router = createRouter({
      history: createMemoryHistory(),
      routes: paths.map((path) => ({
        path,
        component: { template: '<div />' },
        meta: { title: path },
      })),
    })
    await router.push('/locations')
    await router.isReady()

    const overviewRequest = vi.fn()
    window.addEventListener('locations:show-overview', overviewRequest)
    const wrapper = mount(AppLayout, {
      global: {
        plugins: [pinia, router],
        stubs: {
          ElAside: slotStub,
          ElAlert: true,
          ElAvatar: slotStub,
          ElButton: { template: '<button><slot /></button>' },
          ElContainer: slotStub,
          ElDropdown: { template: '<div><slot /><slot name="dropdown" /></div>' },
          ElDropdownItem: { template: '<button><slot /></button>' },
          ElDropdownMenu: slotStub,
          ElHeader: slotStub,
          ElIcon: slotStub,
          ElMain: slotStub,
          RouterView: true,
        },
      },
    })

    await wrapper.get('[data-nav-path="/locations"]').trigger('click')
    expect(overviewRequest).toHaveBeenCalledTimes(1)

    window.removeEventListener('locations:show-overview', overviewRequest)
    wrapper.unmount()
  })
})
