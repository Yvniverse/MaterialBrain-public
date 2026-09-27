import { ref } from 'vue'
import { api } from '../api/client'
import type { AgentSuggestionItem, AgentSuggestionsResponse } from '../types'

const SAFE_FALLBACK: AgentSuggestionItem[] = [
  { type: 'low_stock', text: '哪些物料低于安全库存？', material_id: null, project_id: null },
]
const CACHE_MS = 60_000
const suggestions = ref<AgentSuggestionItem[]>([])
const suggestionsLoading = ref(false)
let loadedAt = 0
let pending: Promise<void> | null = null

export function useWarehouseAgentSuggestions() {
  async function loadSuggestions(force = false) {
    if (!force && suggestions.value.length && Date.now() - loadedAt < CACHE_MS) return
    if (pending) return pending
    suggestionsLoading.value = true
    pending = (async () => {
      try {
        const response = await api.get<AgentSuggestionsResponse>('/agent/suggestions')
        suggestions.value = response.data.items.length ? response.data.items : SAFE_FALLBACK
      } catch {
        suggestions.value = SAFE_FALLBACK
      } finally {
        loadedAt = Date.now()
        suggestionsLoading.value = false
        pending = null
      }
    })()
    return pending
  }

  return { suggestions, suggestionsLoading, loadSuggestions }
}
