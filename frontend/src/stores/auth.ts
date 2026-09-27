import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '../api/client'
import type { User } from '../types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const initialized = ref(false)
  const permissions = computed(() => new Set(user.value?.role.permissions || []))
  function can(permission?: string | readonly string[]) {
    if (!permission || permissions.value.has('*')) return true
    if (typeof permission === 'string') return permissions.value.has(permission)
    return permission.some((item) => permissions.value.has(item))
  }
  async function login(username: string, password: string) { const { data } = await api.post<{ user: User }>('/auth/login', { username, password }); user.value = data.user }
  async function fetchMe() { try { user.value = (await api.get<User>('/auth/me')).data } catch { user.value = null } finally { initialized.value = true } }
  async function logout() { try { await api.post('/auth/logout') } finally { user.value = null } }
  return { user, initialized, permissions, can, login, fetchMe, logout }
})
