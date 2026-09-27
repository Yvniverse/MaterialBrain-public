import type { AxiosError } from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ref } from 'vue'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'
import type { AgentActionProposal, AgentQueryResponse, AgentToolEvent, ApiError } from '../types'

const message = ref('')
const loading = ref(false)
const result = ref<AgentQueryResponse | null>(null)
const events = ref<AgentToolEvent[]>([])
const proposals = ref<AgentActionProposal[]>([])
const proposalBusyId = ref<number | null>(null)
const localError = ref('')
const conversationId = ref<string | null>(null)
const candidateHistory = ref<AgentQueryResponse | null>(null)
export interface AgentConversationTurn {
  id: string
  question: string
  response: AgentQueryResponse | null
  error: string
  pending: boolean
}
const conversation = ref<AgentConversationTurn[]>([])
let proposalsLoaded = false
let pendingOperationId = ''
let pendingOperationMessage = ''

function newOperationId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return `agent-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export function useWarehouseAgent() {
  const auth = useAuthStore()

  async function loadProposals(force = false) {
    if (proposalsLoaded && !force) return
    proposals.value = (await api.get<AgentActionProposal[]>('/agent/proposals')).data
    proposalsLoaded = true
  }

  async function submit(
    queryOverride?: string,
    options: { preserveCandidateHistory?: boolean } = {},
  ) {
    const query = (queryOverride ?? message.value).trim()
    if (!query || loading.value) return
    if (!pendingOperationId || pendingOperationMessage !== query) {
      pendingOperationId = newOperationId()
      pendingOperationMessage = query
    }
    loading.value = true
    localError.value = ''
    if (!options.preserveCandidateHistory) candidateHistory.value = null
    result.value = null
    events.value = []
    const turn: AgentConversationTurn = {
      id: pendingOperationId,
      question: query,
      response: null,
      error: '',
      pending: true,
    }
    conversation.value.push(turn)
    try {
      const response = await api.post<AgentQueryResponse>(
        '/agent/query',
        {
          message: query,
          conversation_id: conversationId.value,
          client_operation_id: pendingOperationId,
        },
        { timeout: 100_000 },
      )
      result.value = response.data
      conversationId.value = response.data.conversation_id
      events.value = response.data.tool_events
      turn.response = response.data
      pendingOperationId = ''
      pendingOperationMessage = ''
      message.value = ''
      if (response.data.proposal_ids.length) await loadProposals(true)
    } catch (caught) {
      const error = caught as AxiosError<ApiError>
      const code = error?.response?.data?.code
      localError.value =
        code === 'AGENT_NOT_CONFIGURED' || code === 'AGENT_DISABLED'
          ? '物料大脑尚未由管理员启用或配置；物料、库存、项目与库位等传统功能仍可正常使用。'
          : code === 'AI_MODEL_POOL_EXHAUSTED'
            ? 'AI 免费额度当前不可用。你仍可使用：搜索物料、查看库存、查看库位、低库存、项目/BOM。'
            : code === 'AGENT_SERVICE_UNAVAILABLE'
              ? 'AI 服务暂不可用，请稍后重试；传统仓库功能不受影响。'
              : code === 'AGENT_CONVERSATION_EXPIRED'
                ? '这段对话的上下文已过期，请重新指定物料或项目。'
                : error.response?.data?.message || '任务执行失败，请稍后重试。'
      if (code === 'AGENT_CONVERSATION_EXPIRED') {
        conversationId.value = null
        message.value = ''
        pendingOperationId = ''
        pendingOperationMessage = ''
      }
      turn.error = localError.value
    } finally {
      turn.pending = false
      loading.value = false
    }
  }

  async function selectCandidate(kind: 'material' | 'project' | 'product', label: string) {
    if (loading.value) return
    if (
      kind === 'material' &&
      result.value &&
      (Boolean(result.value.entities.component_search) ||
        (result.value.entities.material_candidates?.items.length ?? 0) > 1)
    ) {
      candidateHistory.value = result.value
    }
    const query = kind === 'material' ? `${label} 那个` : label
    message.value = query
    await submit(query, { preserveCandidateHistory: kind === 'material' })
  }

  function restoreCandidateResults() {
    if (!candidateHistory.value || loading.value) return
    result.value = candidateHistory.value
    events.value = candidateHistory.value.tool_events
    localError.value = ''
  }

  function newConversation() {
    if (loading.value) return
    conversationId.value = null
    conversation.value = []
    result.value = null
    candidateHistory.value = null
    events.value = []
    localError.value = ''
    message.value = ''
    pendingOperationId = ''
    pendingOperationMessage = ''
  }

  async function approve(proposal: AgentActionProposal) {
    if (!auth.can('inventory:operate')) return
    await ElMessageBox.confirm(
      `确认批准 ${proposal.proposal_no}？审批后将通过库存服务原子预留全部物料。`,
      '批准库存预留',
      { type: 'warning', confirmButtonText: '批准并执行', cancelButtonText: '取消' },
    )
    proposalBusyId.value = proposal.id
    try {
      await api.post(`/agent/proposals/${proposal.id}/approve`)
      await loadProposals(true)
      ElMessage.success('已批准，库存预留已原子执行')
    } catch (error) {
      await loadProposals(true)
      throw error
    } finally {
      proposalBusyId.value = null
    }
  }

  async function reject(proposal: AgentActionProposal) {
    const { value } = await ElMessageBox.prompt('请输入拒绝原因', '拒绝 Proposal', {
      confirmButtonText: '确认拒绝',
      cancelButtonText: '取消',
      inputValue: '当前不执行该预留建议',
      inputPattern: /\S{2,}/,
      inputErrorMessage: '请填写至少 2 个字符',
    })
    proposalBusyId.value = proposal.id
    try {
      await api.post(`/agent/proposals/${proposal.id}/reject`, { reason: value })
      await loadProposals(true)
      ElMessage.success('Proposal 已拒绝')
    } finally {
      proposalBusyId.value = null
    }
  }

  return {
    message,
    loading,
    result,
    events,
    proposals,
    proposalBusyId,
    localError,
    conversationId,
    conversation,
    loadProposals,
    submit,
    selectCandidate,
    candidateHistory,
    restoreCandidateResults,
    newConversation,
    approve,
    reject,
  }
}
