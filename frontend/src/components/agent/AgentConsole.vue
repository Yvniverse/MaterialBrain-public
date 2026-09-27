<script setup lang="ts">
import type { AgentSuggestionItem } from '../../types'

const message = defineModel<string>({ default: '' })

withDefaults(
  defineProps<{
    loading: boolean
    suggestions?: AgentSuggestionItem[]
    suggestionsLoading?: boolean
  }>(),
  { suggestions: () => [], suggestionsLoading: false },
)

const emit = defineEmits<{
  submit: []
}>()

function chooseSuggestion(value: string) {
  message.value = value
  emit('submit')
}
</script>

<template>
  <section class="agent-console">
    <div class="console-heading">
      <div>
        <h2>想查什么？</h2>
        <p>输入物料、库存、BOM 或库位问题。</p>
      </div>
    </div>
    <el-input
      v-model="message"
      type="textarea"
      :rows="3"
      maxlength="4000"
      show-word-limit
      resize="none"
      placeholder="例如：这个物料在哪里？现在还有多少？"
      data-testid="agent-query-input"
      @keydown.ctrl.enter.prevent="emit('submit')"
    />
    <div class="console-footer">
      <div class="suggestion-group">
        <span>真实数据库快捷问题</span>
        <div class="suggestions" aria-label="真实数据库快捷问题">
          <button
            v-for="suggestion in suggestions"
            :key="`${suggestion.type}:${suggestion.text}`"
            type="button"
            :disabled="loading || suggestionsLoading"
            @click="chooseSuggestion(suggestion.text)"
          >
            {{ suggestion.text }}
          </button>
        </div>
      </div>
      <el-button
        type="primary"
        size="large"
        :loading="loading"
        :disabled="!message.trim()"
        data-testid="agent-submit"
        @click="emit('submit')"
      >
        查询
      </el-button>
    </div>
    <small class="submit-tip">Ctrl + Enter 快速查询。需要改动库存时，会先请你确认。</small>
  </section>
</template>

<style scoped>
.agent-console{padding:24px;border:1px solid #dce7f2;border-radius:18px;background:linear-gradient(145deg,#fff,#f5f9fd);box-shadow:0 12px 32px #2849680c}.console-heading{margin-bottom:16px}.console-heading h2{margin:0;color:#213d5b;font-size:var(--mb-font-section-title)}.console-heading p{margin:6px 0 0;color:#71869a;font-size:var(--mb-font-body)}.console-footer{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;margin-top:16px}.suggestion-group{display:grid;min-width:0;gap:8px}.suggestion-group>span{color:#6b8094;font-size:var(--mb-font-secondary);font-weight:650}.suggestions{display:flex;flex-wrap:wrap;gap:8px}.suggestions button{min-height:36px;padding:7px 12px;border:1px solid #d4e1ed;border-radius:999px;background:#fff;color:#496984;cursor:pointer;font-size:var(--mb-font-secondary);text-align:left}.suggestions button:hover:not(:disabled){border-color:#75a6d1;background:#eef7ff;color:#246dab}.suggestions button:disabled{cursor:wait;opacity:.5}.submit-tip{display:block;margin-top:11px;color:#7c8d9e;font-size:var(--mb-font-secondary)}@media(max-width:760px){.console-footer{align-items:stretch;flex-direction:column}.console-footer .el-button{width:100%}}
</style>
