<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Lock, User } from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'
import { apiErrorMessage } from '../utils/apiError'
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const loading = ref(false)
const loginError = ref('')
const form = reactive({ username: '', password: '' })
async function submit() {
  if (loading.value) return
  loginError.value = ''
  loading.value = true
  try {
    await auth.login(form.username.trim(), form.password)
    await router.replace(String(route.query.redirect || '/dashboard'))
  } catch (error) {
    loginError.value = apiErrorMessage(error, 'login')
  } finally {
    loading.value = false
  }
}
</script>
<template>
  <main class="login-page">
    <section class="story">
      <div class="logo">MB</div>
      <div>
        <p class="eyebrow">MATERIALBRAIN WORKSPACE</p>
        <h1>让每一颗元件<br />都有准确去向。</h1>
        <p class="intro">统一库存、清晰库位、完整追溯。为工程团队打造的电子物料协同工作台。</p>
      </div>
      <div class="features"><span>工程选型</span><span>可视化库位</span><span>库存追溯</span></div>
    </section>
    <section class="login-panel">
      <el-card class="login-card"
        ><div class="welcome">
          <p>欢迎回来</p>
          <h2>登录物料管理系统</h2>
          <span>登录你的研发工作空间</span>
        </div>
        <el-alert
          v-if="loginError"
          :title="loginError"
          type="error"
          :closable="false"
          show-icon
          class="login-error"
        /><el-form :model="form" label-position="top" size="large" @keyup.enter="submit"
          ><el-form-item label="登录账号"
            ><el-input
              v-model="form.username"
              :prefix-icon="User"
              autocomplete="username"
              placeholder="请输入账号" /></el-form-item
          ><el-form-item label="密码"
            ><el-input
              v-model="form.password"
              :prefix-icon="Lock"
              type="password"
              show-password
              autocomplete="current-password"
              placeholder="请输入密码" /></el-form-item
          ><el-button
            type="primary"
            :loading="loading"
            :disabled="loading || !form.username || !form.password"
            class="submit"
            @click="submit"
            >登录</el-button
          ></el-form
        >
        <div class="hint">工程资料、库存与库位，在同一个工作空间。</div></el-card
      >
    </section>
  </main>
</template>
<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 1.1fr 1fr;
  background: var(--mb-surface-subtle);
}
.story {
  padding: 7vw;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  color: white;
  background:
    radial-gradient(circle at 80% 15%, var(--g-accent) 0, transparent 28%),
    linear-gradient(145deg, var(--g-accent), var(--g-accent));
  position: relative;
  overflow: hidden;
}
.story:after {
  content: '';
  position: absolute;
  width: 440px;
  height: 440px;
  border: 1px solid #ffffff20;
  border-radius: 50%;
  right: -120px;
  bottom: -180px;
  box-shadow:
    0 0 0 70px #ffffff08,
    0 0 0 140px #ffffff05;
}
.logo {
  width: 52px;
  height: 52px;
  border-radius: 15px;
  display: grid;
  place-items: center;
  background: var(--g-accent);
  font-size: 28px;
  font-weight: 900;
}
.eyebrow {
  color: var(--g-muted);
  letter-spacing: 2.5px;
  font-size: 13px;
  font-weight: 700;
}
.story h1 {
  font-size: clamp(38px, 4vw, 64px);
  line-height: 1.18;
  margin: 18px 0;
}
.intro {
  max-width: 540px;
  line-height: 1.8;
  color: var(--g-muted);
}
.features {
  display: flex;
  gap: 24px;
  color: var(--g-muted);
  font-size: 13px;
}
.features span:before {
  content: '✓';
  color: #62ccad;
  margin-right: 7px;
}
.login-panel {
  display: grid;
  place-items: center;
  padding: 32px;
}
.login-card {
  width: min(430px, 100%);
  border-radius: 18px;
  border: 0;
  padding: 20px;
  box-shadow: 0 25px 70px #24466f20;
}
.welcome p {
  color: var(--g-accent-strong);
  font-weight: 700;
  margin: 0 0 6px;
}
.welcome h2 {
  font-size: 26px;
  margin: 0 0 8px;
}
.welcome span {
  color: var(--g-accent-strong);
  font-size: 13px;
}
.welcome {
  margin-bottom: 30px;
}
.submit {
  width: 100%;
  margin-top: 8px;
  height: 46px;
}
.hint {
  text-align: center;
  color: var(--g-accent-strong);
  font-size: 13px;
  margin-top: 24px;
}
@media (max-width: 800px) {
  .login-page {
    grid-template-columns: 1fr;
  }
  .story {
    display: none;
  }
}
</style>
<style scoped>
.login-error {
  margin-bottom: 18px;
}
</style>
