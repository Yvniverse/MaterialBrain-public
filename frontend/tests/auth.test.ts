import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { api, uuidKey } from '../src/api/client'
import { useAuthStore } from '../src/stores/auth'
import type { User } from '../src/types'

const user: User = {
  id: 1, username: 'engineer', full_name: '工程师', department: '研发', is_active: true,
  must_change_password: false, created_at: '2026-07-20',
  role: {
    id: 2,
    name: '硬件工程师',
    description: '',
    permissions: ['material:view'],
    is_system: true,
  },
}

describe('authentication and submission safety', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('generates unique idempotency keys', () => {
    const first = uuidKey(); const second = uuidKey()
    expect(first).not.toBe(second)
    expect(first).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('stores the logged-in user and evaluates backend permissions', async () => {
    vi.spyOn(api, 'post').mockResolvedValueOnce({ data: { user } })
    const store = useAuthStore()
    await store.login('engineer', 'password')
    expect(store.user?.full_name).toBe('工程师')
    expect(store.can('material:view')).toBe(true)
    expect(store.can('inventory:operate')).toBe(false)
  })

  it('grants every permission only to wildcard roles', async () => {
    vi.spyOn(api, 'post').mockResolvedValueOnce({ data: { user: { ...user, role: { ...user.role, permissions: ['*'] } } } })
    const store = useAuthStore(); await store.login('admin', 'password')
    expect(store.can('audit:view')).toBe(true)
  })

  it('accepts any matching permission when a page supports multiple managers', () => {
    const store = useAuthStore()
    store.user = user
    expect(store.can(['user:manage', 'material:view'])).toBe(true)
    expect(store.can(['user:manage', 'role:manage'])).toBe(false)
  })
})
