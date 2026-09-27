<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../../api/client'
import type { EngineeringDocumentSummary, EvidenceCitation } from '../../types'

const props = defineProps<{ materialId: number }>()
const documents = ref<EngineeringDocumentSummary[]>([])
type EvidencePage = {
  id: number
  page_number: number
  anchors: EvidenceCitation[]
  layout_blocks?: NonNullable<EvidenceCitation['layout_blocks']>
}
const pagesByDocument = ref<Record<number, EvidencePage[]>>({})
const showHistory = ref(false)
const loading = ref(false)

async function loadDocuments() {
  loading.value = true
  try {
    const response = await api.get<{ items: EngineeringDocumentSummary[] }>(
      `/materials/${props.materialId}/evidence-documents`,
      { params: { include_history: showHistory.value } },
    )
    documents.value = response.data.items
  } finally {
    loading.value = false
  }
}

async function loadPages(documentId: number) {
  if (pagesByDocument.value[documentId]) return
  const response = await api.get<{
    items: EvidencePage[]
  }>(`/evidence-documents/${documentId}/pages`)
  pagesByDocument.value[documentId] = response.data.items
}

onMounted(loadDocuments)
</script>

<template>
  <section class="material-evidence" v-loading="loading" data-testid="material-evidence-panel">
    <header>
      <div>
        <h3>工程证据</h3>
        <p>默认只显示当前版本；历史版本不会用于普通技术结论。</p>
      </div>
      <el-switch v-model="showHistory" active-text="显示历史版本" @change="loadDocuments" />
    </header>
    <div v-if="documents.length" class="document-list">
      <article v-for="document in documents" :key="document.id">
        <header>
          <div>
            <b>{{ document.title }}</b>
            <span>{{ document.document_revision }} · {{ document.document_date }}</span>
          </div>
          <el-tag :type="document.status === 'current' ? 'success' : 'info'">
            {{
              document.status === 'current'
                ? '当前版本'
                : document.status === 'superseded'
                  ? '已被替代'
                  : '已撤回'
            }}
          </el-tag>
        </header>
        <p v-if="document.synthetic_fixture" class="fixture-warning">
          合成 CI 测试证据，并非真实厂商数据手册
        </p>
        <button type="button" @click="loadPages(document.id)">
          查看 {{ document.page_count }} 页引用
        </button>
        <div v-if="pagesByDocument[document.id]" class="page-list">
          <section v-for="page in pagesByDocument[document.id]" :key="page.id">
            <b>第 {{ page.page_number }} 页</b>
            <div
              v-if="page.layout_blocks?.length"
              class="layout-preview"
              data-testid="layout-preview"
            >
              <strong>布局抽取预览</strong>
              <span
                v-for="block in page.layout_blocks"
                :key="block.id"
              >
                {{ block.block_type }} · 顺序 {{ block.reading_order }} ·
                {{ block.location_status === 'available' ? `bbox ${block.bbox?.join(', ')}` : '位置不可用' }}
              </span>
            </div>
            <div v-for="anchor in page.anchors" :key="anchor.anchor_id">
              <strong>{{ anchor.section }}</strong>
              <p>{{ anchor.excerpt }}</p>
            </div>
          </section>
        </div>
      </article>
    </div>
    <el-empty v-else description="暂无已导入的厂商数据手册" />
  </section>
</template>

<style scoped>
.material-evidence {
  display: grid;
  gap: 14px;
}
.material-evidence > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.material-evidence h3 {
  margin: 0;
  color: #294d69;
}
.material-evidence header p {
  margin: 5px 0 0;
  color: #6d8295;
}
.document-list {
  display: grid;
  gap: 12px;
}
.document-list > article {
  display: grid;
  gap: 10px;
  padding: 15px;
  border: 1px solid #d8e5ed;
  border-radius: 12px;
}
.document-list article > header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.document-list article > header div {
  display: grid;
  gap: 4px;
}
.document-list article > header span {
  color: #6d8295;
  font-size: 13px;
}
.fixture-warning {
  margin: 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: #fff6e6;
  color: #805e24;
}
.document-list button {
  width: max-content;
  border: 0;
  background: transparent;
  color: #2878ad;
  cursor: pointer;
  font-weight: 650;
}
.page-list {
  display: grid;
  gap: 9px;
}
.page-list > section {
  display: grid;
  gap: 7px;
  padding: 11px;
  border-radius: 9px;
  background: #f6f9fb;
}
.page-list strong {
  color: #3f6885;
}
.page-list p {
  margin: 4px 0 0;
  color: #536c7f;
  line-height: 1.6;
  white-space: pre-wrap;
}
.layout-preview {
  display: grid;
  gap: 3px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #eef7fb;
  color: #476b82;
  font-size: 13px;
}
</style>
