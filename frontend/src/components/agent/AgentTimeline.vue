<script setup lang="ts">
import type { AgentToolEvent } from '../../types'
import { agentProcessLabels, summarizeAgentProcess } from '../../utils/agentProcessSummary'

defineProps<{
  events: AgentToolEvent[]
  running: boolean
}>()
</script>

<template>
  <section class="timeline-card" data-testid="agent-timeline">
    <div class="process-status" :class="{ running }">
      <i></i>
      <span>{{ running ? '正在核对物料信息…' : summarizeAgentProcess(events) }}</span>
    </div>
    <details v-if="running || events.length" data-testid="agent-process-details">
      <summary>查看过程</summary>
      <ol>
        <li v-if="running" class="running"><i></i><b>理解问题</b></li>
        <li v-for="(event, index) in events" :key="`${event.tool}-${index}`" :class="event.status">
          <i></i><b>{{ agentProcessLabels[event.tool] || '核对相关信息' }}</b>
        </li>
      </ol>
    </details>
  </section>
</template>

<style scoped>
.timeline-card {
  padding: 14px 16px;
  border: 1px solid #dfe7f0;
  border-radius: 14px;
  background: #fff;
}
.process-status {
  display: flex;
  align-items: center;
  gap: 9px;
  color: #28775d;
  font-size: var(--mb-font-body);
  font-weight: 650;
}
.process-status > i {
  width: 10px;
  height: 10px;
  border: 2px solid #2f9a73;
  border-radius: 50%;
  background: #45bb8d;
}
.process-status.running {
  color: #3d6f98;
}
.process-status.running > i {
  border-color: #3b8fd3;
  border-top-color: transparent;
  background: transparent;
  animation: timeline-spin 0.8s linear infinite;
}
.timeline-card details {
  margin-top: 10px;
}
.timeline-card summary {
  width: max-content;
  color: #326f9f;
  cursor: pointer;
  font-size: var(--mb-font-secondary);
  font-weight: 650;
}
.timeline-card ol {
  display: grid;
  gap: 10px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}
.timeline-card li {
  display: flex;
  align-items: center;
  gap: 9px;
  color: #526a80;
  font-size: var(--mb-font-secondary);
}
.timeline-card li > i {
  width: 9px;
  height: 9px;
  border: 2px solid #aab7c4;
  border-radius: 50%;
  background: #fff;
}
.timeline-card li.success > i {
  border-color: #2c9b75;
  background: #46b98d;
}
.timeline-card li.error > i {
  border-color: #d45751;
  background: #ea746e;
}
.timeline-card li.running > i {
  border-color: #3b8fd3;
  border-top-color: transparent;
  animation: timeline-spin 0.8s linear infinite;
}
@keyframes timeline-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .timeline-card li.running > i,
  .process-status.running > i {
    animation-duration: 0.01ms;
  }
}
</style>
