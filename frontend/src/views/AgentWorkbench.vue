<script setup lang="ts">
import { onMounted } from 'vue'
import AgentConsole from '../components/agent/AgentConsole.vue'
import AgentResultCard from '../components/agent/AgentResultCard.vue'
import AgentTimeline from '../components/agent/AgentTimeline.vue'
import ApprovalCard from '../components/agent/ApprovalCard.vue'
import { useWarehouseAgent } from '../composables/useWarehouseAgent'
import { useWarehouseAgentSuggestions } from '../composables/useWarehouseAgentSuggestions'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const {
  message,
  loading,
  result,
  events,
  proposals,
  proposalBusyId,
  localError,
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

onMounted(async () => {
  await loadSuggestions()
  try {
    await loadProposals()
  } catch {
    // 页面仍可提交查询；全局拦截器已显示具体错误。
  }
})
</script>

<template>
  <div class="page agent-page">
    <div class="page-header">
      <div>
        <div class="agent-kicker">MATERIALBRAIN</div>
        <h1 class="page-title">物料大脑</h1>
        <div class="page-subtitle">
          找物料、查库存、看库位、分析 BOM。需要改动库存时，会先请你确认。
        </div>
      </div>
      <el-tooltip
        content="物料大脑不会直接修改库存，所有库存变更都会先让你确认。"
        placement="bottom-end"
      >
        <div class="safety-badge">
          <i></i><span><b>安全模式</b>库存变更需确认</span>
        </div>
      </el-tooltip>
      <button
        type="button"
        class="new-conversation"
        data-testid="new-conversation"
        :disabled="loading"
        @click="newConversation"
      >
        ＋ 新对话
      </button>
    </div>

    <AgentConsole
      v-model="message"
      :loading="loading"
      :suggestions="suggestions"
      :suggestions-loading="suggestionsLoading"
      @submit="submit"
    />

    <div
      v-if="candidateHistory && result && result !== candidateHistory"
      class="candidate-return-bar"
    >
      <button
        type="button"
        class="candidate-return-button"
        data-testid="return-to-candidates"
        :disabled="loading"
        @click="restoreCandidateResults"
      >
        ← 返回刚才的候选列表
      </button>
      <small>可以查看其他候选；返回不会重新查询，也不会调用模型。</small>
    </div>

    <el-alert
      v-if="localError"
      class="agent-error"
      :title="localError"
      type="error"
      show-icon
      :closable="false"
    />

    <main class="result-column">
      <AgentResultCard v-if="result" :result="result" @select-candidate="selectCandidate" />
      <div v-else-if="loading" class="working-card">
        <span></span>
        <div><b>正在查询</b><small>正在核对真实库存与库位信息</small></div>
      </div>
      <div v-else class="empty-result">
        <div class="brain-mark">MB</div>
        <b>从一个仓库问题开始</b>
        <span>答案、库存、库位和可执行操作会显示在这里。</span>
      </div>
      <AgentTimeline
        v-if="loading || events.length"
        class="result-process"
        :events="events"
        :running="loading"
      />
    </main>

    <section v-if="proposals.length" class="proposal-section">
      <header>
        <div><h2>库存变更确认</h2></div>
        <small>批准前不会修改库存</small>
      </header>
      <div class="proposal-grid">
        <ApprovalCard
          v-for="proposal in proposals"
          :key="proposal.id"
          :proposal="proposal"
          :can-approve="auth.can('inventory:operate')"
          :busy="proposalBusyId === proposal.id"
          @approve="approve"
          @reject="reject"
        />
      </div>
    </section>
  </div>
</template>

<style scoped>
.agent-page {
  max-width: 1180px;
}
.agent-kicker {
  margin-bottom: 5px;
  color: #3479bd;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 1.4px;
}
.safety-badge {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 13px;
  border: 1px solid #d5e6dc;
  border-radius: 11px;
  background: #f2faf6;
  color: #587767;
  cursor: help;
}
.safety-badge > i {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #39a479;
  box-shadow: 0 0 0 5px #39a47918;
}
.safety-badge > span {
  display: flex;
  flex-direction: column;
  font-size: var(--mb-font-secondary);
}
.safety-badge b {
  color: #2c785c;
  font-size: var(--mb-font-body);
}
.new-conversation {
  min-height: 44px;
  padding: 8px 13px;
  border: 1px solid #cdddea;
  border-radius: 10px;
  background: #fff;
  color: #326f9f;
  cursor: pointer;
  font-size: var(--mb-font-secondary);
  font-weight: 650;
}
.new-conversation:disabled {
  cursor: wait;
  opacity: 0.5;
}
.agent-error {
  margin-top: 16px;
}
.candidate-return-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 16px;
  padding: 10px 12px;
  border: 1px solid #c5dceb;
  border-radius: 11px;
  background: #f7fbfe;
}
.candidate-return-button {
  border: 0;
  background: transparent;
  color: #286d9f;
  cursor: pointer;
  font-weight: 700;
}
.candidate-return-button:disabled {
  cursor: wait;
  opacity: 0.5;
}
.candidate-return-bar small {
  color: #70869a;
}
.result-column {
  display: grid;
  min-width: 0;
  gap: 14px;
  margin-top: 20px;
}
.working-card,
.empty-result {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 240px;
  border: 1px dashed #bfd0df;
  border-radius: 16px;
  background: #f9fbfd;
  color: #667f95;
}
.working-card {
  gap: 13px;
}
.working-card > span {
  width: 24px;
  height: 24px;
  border: 3px solid #b8d4ea;
  border-top-color: #3286c8;
  border-radius: 50%;
  animation: working-spin 0.8s linear infinite;
}
.working-card > div {
  display: flex;
  flex-direction: column;
}
.working-card b {
  color: #375b7a;
  font-size: var(--mb-font-card-title);
}
.working-card small {
  margin-top: 4px;
  font-size: var(--mb-font-secondary);
}
.empty-result {
  flex-direction: column;
  text-align: center;
}
.brain-mark {
  display: grid;
  place-items: center;
  width: 58px;
  height: 58px;
  margin-bottom: 12px;
  border-radius: 18px;
  background: linear-gradient(145deg, #66b6f0, #336fbb);
  box-shadow: 0 10px 25px #346eac30;
  color: #fff;
  font-weight: 900;
}
.empty-result b {
  color: #3b5771;
  font-size: var(--mb-font-card-title);
}
.empty-result span {
  margin-top: 6px;
  color: #71869a;
  font-size: var(--mb-font-body);
}
.result-process {
  margin-top: 2px;
}
.proposal-section {
  margin-top: 24px;
  padding-top: 22px;
  border-top: 1px solid #dfe7ef;
}
.proposal-section > header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 12px;
}
.proposal-section h2 {
  margin: 0;
  color: #415771;
  font-size: var(--mb-font-section-title);
}
.proposal-section header small {
  color: #71869a;
  font-size: var(--mb-font-secondary);
}
.proposal-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
  gap: 12px;
}
@keyframes working-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .working-card > span {
    animation-duration: 0.01ms;
  }
}
@media (max-width: 850px) {
  .page-header {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .safety-badge {
    margin-top: 8px;
  }
}
@media (max-width: 600px) {
  .proposal-grid {
    grid-template-columns: 1fr;
  }
  .proposal-section > header {
    align-items: flex-start;
    flex-direction: column;
    gap: 5px;
  }
}
</style>
