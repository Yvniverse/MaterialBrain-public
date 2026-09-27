<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api/client'
import type { Page } from '../types'
interface Audit {id:number;actor_id:number|null;action:string;resource_type:string;resource_id:string;request_id:string;success:boolean;ip_address:string;created_at:string}
const data=ref<Page<Audit>>({items:[],total:0,page:1,page_size:30});const page=reactive({page:1,page_size:30});async function load(){data.value=(await api.get<Page<Audit>>('/audit-logs',{params:page})).data}onMounted(load)
</script>
<template><div class="page"><div class="page-header"><div><h1 class="page-title">审计日志</h1><div class="page-subtitle">登录、资料修改和关键业务操作的责任追踪</div></div></div><el-card class="card"><el-table :data="data.items" stripe><el-table-column prop="created_at" label="时间" width="190"/><el-table-column prop="actor_id" label="用户 ID" width="90"/><el-table-column prop="action" label="操作" width="190"/><el-table-column label="资源"><template #default="{row}">{{row.resource_type}} #{{row.resource_id}}</template></el-table-column><el-table-column prop="ip_address" label="来源 IP"/><el-table-column prop="request_id" label="请求 ID" min-width="280"/><el-table-column label="结果" width="80"><template #default="{row}"><el-tag :type="row.success?'success':'danger'">{{row.success?'成功':'失败'}}</el-tag></template></el-table-column></el-table><el-pagination v-model:current-page="page.page" v-model:page-size="page.page_size" :total="data.total" layout="total,prev,pager,next" class="pager" @change="load"/></el-card></div></template>
<style scoped>.pager{justify-content:flex-end;margin-top:16px}</style>
