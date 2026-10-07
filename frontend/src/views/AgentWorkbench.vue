<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import GlacierBrainFrame from '../glacier/components/GlacierBrainFrame.vue'
import GIcon from '../glacier/components/GIcon.vue'
import AgentResultCard from '../components/agent/AgentResultCard.vue'
import AgentTimeline from '../components/agent/AgentTimeline.vue'
import ApprovalCard from '../components/agent/ApprovalCard.vue'
import { useWarehouseAgent, type AgentConversationTurn } from '../composables/useWarehouseAgent'
import { useWarehouseAgentSuggestions } from '../composables/useWarehouseAgentSuggestions'
import { useAuthStore } from '../stores/auth'
const auth = useAuthStore(),
  router = useRouter()
const {
  message,
  loading,
  result,
  events,
  proposals,
  proposalBusyId,
  localError,
  proposalError,
  conversation,
  loadProposals,
  submit,
  selectCandidate,
  candidateHistory,
  restoreCandidateResults,
  newConversation,
  approve,
  reject,
} = useWarehouseAgent()
const { suggestions, suggestionsLoading, loadSuggestions } = useWarehouseAgentSuggestions()
const mode = ref('select'),
  historyOpen = ref(false),
  historicalTurn = ref<AgentConversationTurn | null>(null)
const currentQuestion = computed(() => conversation.value.at(-1)?.question || '')
const hasEngineering = computed(() =>
  Boolean(result.value?.entities.engineering_research || result.value?.entities.power_design),
)
const modeSeeds: Record<string, string> = {
  find: '查找物料，并列出可用库存与库位：',
  select: '请根据以下工程需求比较候选物料：',
  bom: '分析项目 BOM 的齐套情况和缺料项：',
  pick: '请帮我定位这些物料所在的库位：',
}
function changeMode(id: string) {
  mode.value = id
  if (!message.value) message.value = modeSeeds[id] || ''
}
function send() {
  void submit()
}
function sendSuggestion(question: string) {
  message.value = question
  send()
}
function startNew() {
  newConversation()
  historicalTurn.value = null
}
function openHistory() {
  historicalTurn.value = null
  historyOpen.value = true
}
onMounted(() => {
  void loadSuggestions()
  void loadProposals().catch(() => {
    /* Existing results remain usable. */
  })
})
</script>
<template>
  <GlacierBrainFrame
    :question="currentQuestion"
    :mode="mode"
    :loading="loading"
    :history-count="conversation.length"
    @mode="changeMode"
    @history="openHistory"
    @new="startNew"
  >
    <template #results>
      <div v-if="candidateHistory && result && result !== candidateHistory" class="g-inline-notice">
        <button
          class="g-text-btn"
          :disabled="loading"
          data-testid="return-to-candidates"
          @click="restoreCandidateResults"
        >
          ← 返回候选列表
        </button>
      </div>
      <div v-if="localError" class="g-inline-notice" role="alert">
        <span>{{ localError }}</span
        ><button
          class="g-text-btn"
          :disabled="loading"
          @click="submit(conversation.at(-1)?.question)"
        >
          重试
        </button>
      </div>
      <AgentResultCard v-if="result" :result="result" @select-candidate="selectCandidate" />
      <section v-else class="g-card g-brain-empty">
        <span class="g-pictogram blue"><GIcon name="brain" :size="28" /></span>
        <h2>从一个工程需求开始</h2>
        <p>候选、库存、库位和下一步操作，会整理在这里。</p>
        <div class="g-suggestion-list">
          <button
            v-for="s in suggestions"
            :key="s.text"
            class="g-btn"
            :disabled="loading"
            @click="sendSuggestion(s.text)"
          >
            {{ s.text }}<GIcon name="arrow" :size="16" /></button
          ><span v-if="suggestionsLoading" class="g-muted">正在读取建议</span>
        </div>
      </section>
    </template>
    <template #process
      ><details v-if="loading || events.length" class="g-process">
        <summary>
          <span>整理过程</span
          ><span>{{ events.length }} 个记录{{ loading ? ' · 处理中' : '' }}</span>
        </summary>
        <AgentTimeline :events="events" :running="loading" /></details
    ></template>
    <template #composer
      ><form class="g-composer" @submit.prevent="send">
        <textarea
          v-model="message"
          data-testid="agent-query-input"
          rows="2"
          aria-label="向物料大脑提问"
          placeholder="继续补充负载、封装或成本要求…"
          @keydown.ctrl.enter.prevent="send"
          @keydown.meta.enter.prevent="send"
        />
        <div class="g-composer-foot">
          <span class="g-composer-context"
            ><GIcon name="brain" :size="15" />与悬浮助手共用当前会话</span
          >
          <div class="g-composer-actions">
            <span>Ctrl / ⌘ + Enter</span
            ><button
              class="g-icon-btn primary"
              type="submit"
              data-testid="agent-submit"
              :disabled="loading || !message.trim()"
              aria-label="提交问题"
            >
              <GIcon name="arrow" />
            </button>
          </div>
        </div></form
    ></template>
    <template #context
      ><section class="g-card g-context-card">
        <header><span>工程上下文</span><GIcon name="folder" /></header>
        <h3>{{ hasEngineering ? '当前工程任务' : '当前任务' }}</h3>
        <p>{{ currentQuestion || '先描述目标或选择一条建议。' }}</p>
        <div class="g-row between">
          <span class="g-tag">{{ conversation.length }} 轮对话</span
          ><span class="g-muted">共享会话</span>
        </div>
        <button
          class="g-btn dark full"
          @click="router.push(auth.can('material:view') ? '/products' : '/projects')"
        >
          查看 BOM / 项目<GIcon name="arrow" :size="17" />
        </button>
      </section>
      <section class="g-card g-context-card">
        <header><span>继续工作</span><GIcon name="layers" /></header>
        <button class="g-btn full" @click="router.push('/materials')">在物料库查看资料</button
        ><button class="g-btn full" @click="router.push('/warehouse-twin')">
          打开数字孪生仓库
        </button>
        <p>当前选择和参数保留在结果卡中；展开卡片可查看完整工程明细。</p>
      </section>
      <section v-if="proposals.length || proposalError" class="g-approval-panel">
        <header class="g-card-heading">
          <h3>待确认操作</h3>
          <button class="g-text-btn" @click="loadProposals(true).catch(() => {})">刷新</button>
        </header>
        <p v-if="proposalError" role="status" class="g-muted">{{ proposalError }}</p>
        <ApprovalCard
          v-for="proposal in proposals"
          :key="proposal.id"
          :proposal="proposal"
          :can-approve="auth.can('inventory:operate')"
          :busy="proposalBusyId === proposal.id"
          @approve="approve"
          @reject="reject"
        />
      </section>
    </template>
  </GlacierBrainFrame>
  <el-drawer v-model="historyOpen" title="当前会话记录" size="min(880px,96vw)" append-to-body>
    <div v-if="!historicalTurn" class="g-history-list">
      <button
        v-for="t in [...conversation].reverse()"
        :key="t.id"
        class="g-history-row"
        @click="historicalTurn = t"
      >
        <b>{{ t.question }}</b
        ><span>{{ t.pending ? '处理中' : t.response ? '查看结果' : '未完成' }}</span>
      </button>
      <p v-if="!conversation.length">还没有会话记录。</p>
    </div>
    <template v-else
      ><button class="g-text-btn" @click="historicalTurn = null">← 返回记录</button>
      <h3>{{ historicalTurn.question }}</h3>
      <p class="g-muted">历史快照：仅供查阅，不切换当前工程选择。</p>
      <div inert class="g-history-snapshot">
        <AgentResultCard v-if="historicalTurn.response" :result="historicalTurn.response" />
      </div>
      <p v-if="historicalTurn.error">{{ historicalTurn.error }}</p></template
    >
  </el-drawer>
</template>
