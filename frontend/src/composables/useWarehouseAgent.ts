import type { AxiosError } from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { reactive, ref } from 'vue'
import { api } from '../api/client'
import { resetAgentSuggestions } from './useWarehouseAgentSuggestions'
import {
  captureNavigationContext,
  resetNavigationSession,
  type NavigationExecutionContext,
} from '../embodied/navigationSession'
import { useAuthStore } from '../stores/auth'
import {
  acceptSpatialMission,
  resetSpatialMissionSession,
  subscribeSpatialMission,
  setSpatialConversationId,
} from '../spatial/useSpatialMission'
import type { AgentActionProposal, AgentQueryResponse, AgentToolEvent, ApiError } from '../types'

const message = ref('')
const loading = ref(false)
const result = ref<AgentQueryResponse | null>(null)
const events = ref<AgentToolEvent[]>([])
const proposals = ref<AgentActionProposal[]>([])
const proposalBusyId = ref<number | null>(null)
const localError = ref('')
const proposalError = ref('')
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
subscribeSpatialMission((mission) => {
  if (!mission) return
  if (conversationId.value === null) conversationId.value = mission.conversation_id
  if (mission.conversation_id !== conversationId.value) return
  if (result.value?.entities.spatial_mission?.mission_id === mission.mission_id)
    result.value.entities.spatial_mission = mission
  for (const turn of conversation.value) {
    if (turn.response?.entities.spatial_mission?.mission_id === mission.mission_id)
      turn.response.entities.spatial_mission = mission
  }
})
let proposalsLoaded = false
let pendingOperationId = ''
let pendingOperationMessage = ''
let pendingNavigationContext: NavigationExecutionContext | null = null
let sessionEpoch = 0
/** Both surfaces share this module. Invalidate late responses on account transitions. */
export function resetWarehouseAgentSession() {
  sessionEpoch += 1
  resetAgentSuggestions()
  resetNavigationSession()
  resetSpatialMissionSession()
  message.value = ''
  loading.value = false
  result.value = null
  events.value = []
  proposals.value = []
  proposalBusyId.value = null
  localError.value = ''
  proposalError.value = ''
  conversationId.value = null
  candidateHistory.value = null
  conversation.value = []
  proposalsLoaded = false
  pendingOperationId = ''
  pendingOperationMessage = ''
  pendingNavigationContext = null
}

function newOperationId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return `agent-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export function useWarehouseAgent() {
  const auth = useAuthStore()

  async function loadProposals(force = false) {
    if (proposalsLoaded && !force) return
    const epoch = sessionEpoch
    try {
      const response = await api.get<AgentActionProposal[]>('/agent/proposals')
      if (epoch !== sessionEpoch) return
      proposals.value = response.data
      proposalsLoaded = true
      proposalError.value = ''
    } catch (error) {
      if (epoch === sessionEpoch) proposalError.value = '待确认操作暂未刷新，已有结果仍可查看。'
      throw error
    }
  }

  async function submit(
    queryOverride?: string,
    options: { preserveCandidateHistory?: boolean } = {},
  ) {
    const epoch = sessionEpoch
    const query = (queryOverride ?? message.value).trim()
    if (!query || loading.value) return
    if (!pendingOperationId || pendingOperationMessage !== query) {
      pendingOperationId = newOperationId()
      pendingOperationMessage = query
      pendingNavigationContext = /重新规划|重规划|replan/i.test(query)
        ? captureNavigationContext()
        : null
    }
    loading.value = true
    localError.value = ''
    if (!options.preserveCandidateHistory) candidateHistory.value = null
    // Keep the last result visible; new tool events describe only the new request.
    events.value = []
    const existingTurn = conversation.value.find((item) => item.id === pendingOperationId)
    const turn: AgentConversationTurn =
      existingTurn ||
      reactive({
        id: pendingOperationId,
        question: query,
        response: null,
        error: '',
        pending: true,
      })
    turn.error = ''
    turn.pending = true
    if (!existingTurn) conversation.value.push(turn)
    try {
      const response = await api.post<AgentQueryResponse>(
        '/agent/query',
        {
          message: query,
          conversation_id: conversationId.value,
          client_operation_id: pendingOperationId,
          ...(pendingNavigationContext ? { navigation_context: pendingNavigationContext } : {}),
        },
        { timeout: 100_000 },
      )
      if (epoch !== sessionEpoch) return
      result.value = response.data
      conversationId.value = response.data.conversation_id
      setSpatialConversationId(response.data.conversation_id)
      if (response.data.entities.spatial_mission)
        acceptSpatialMission(response.data.entities.spatial_mission, response.data.conversation_id)
      events.value = response.data.tool_events
      turn.response = response.data
      pendingOperationId = ''
      pendingOperationMessage = ''
      pendingNavigationContext = null
      message.value = ''
      if (response.data.proposal_ids.length) {
        try {
          await loadProposals(true)
        } catch {
          // The proposal panel has its own retry; never resubmit a successful query.
        }
      }
    } catch (caught) {
      if (epoch !== sessionEpoch) return
      const error = caught as AxiosError<ApiError>
      const code = error?.response?.data?.code
      localError.value =
        code === 'AGENT_NOT_CONFIGURED' || code === 'AGENT_DISABLED'
          ? '物料大脑尚未启用，请在系统设置中配置。'
          : code === 'AI_MODEL_POOL_EXHAUSTED'
            ? '模型额度暂不可用，请稍后重试。'
            : code === 'AGENT_SERVICE_UNAVAILABLE'
              ? '服务暂不可用，已保留上次结果。'
              : code === 'AGENT_CONVERSATION_EXPIRED'
                ? '这段对话的上下文已过期，请重新指定物料或项目。'
                : error.response?.data?.message || '任务未完成，请重试。'
      if (code === 'AGENT_CONVERSATION_EXPIRED') {
        resetSpatialMissionSession()
        conversationId.value = null
        message.value = ''
        pendingOperationId = ''
        pendingOperationMessage = ''
      }
      turn.error = localError.value
    } finally {
      if (epoch === sessionEpoch) {
        turn.pending = false
        loading.value = false
      }
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
    resetSpatialMissionSession()
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
    const epoch = sessionEpoch
    await ElMessageBox.confirm(
      `确认批准 ${proposal.proposal_no}？审批后将通过库存服务原子预留全部物料。`,
      '批准库存预留',
      { type: 'warning', confirmButtonText: '批准并执行', cancelButtonText: '取消' },
    )
    if (epoch !== sessionEpoch) return
    proposalBusyId.value = proposal.id
    try {
      await api.post(`/agent/proposals/${proposal.id}/approve`)
      if (epoch !== sessionEpoch) return
      await loadProposals(true)
      ElMessage.success('已批准并完成库存预留')
    } catch (error) {
      if (epoch === sessionEpoch) {
        try {
          await loadProposals(true)
        } catch {
          /* Keep the original operation error. */
        }
      }
      throw error
    } finally {
      if (epoch === sessionEpoch) proposalBusyId.value = null
    }
  }

  async function reject(proposal: AgentActionProposal) {
    const epoch = sessionEpoch
    const { value } = await ElMessageBox.prompt('请输入拒绝原因', '拒绝操作建议', {
      confirmButtonText: '确认拒绝',
      cancelButtonText: '取消',
      inputValue: '当前不执行该预留建议',
      inputPattern: /\S{2,}/,
      inputErrorMessage: '请填写至少 2 个字符',
    })
    if (epoch !== sessionEpoch) return
    proposalBusyId.value = proposal.id
    try {
      await api.post(`/agent/proposals/${proposal.id}/reject`, { reason: value })
      if (epoch !== sessionEpoch) return
      await loadProposals(true)
      ElMessage.success('已拒绝操作建议')
    } finally {
      if (epoch === sessionEpoch) proposalBusyId.value = null
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
    proposalError,
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
