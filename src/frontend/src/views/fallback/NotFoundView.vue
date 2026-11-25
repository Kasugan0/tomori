<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

function goHome(): void {
  void router.replace(authStore.isAuthenticated ? authStore.defaultHomeRoute : '/login')
}
</script>

<template>
  <div class="not-found-page" data-testid="not-found-page">
    <div class="tomori-card fallback-box">
      <h2 class="fallback-title">404 · 页面不存在</h2>
      <p class="fallback-desc">
        请检查访问路径或返回工作台首页。
      </p>
      <p class="fallback-path">
        请求路径：<code>{{ route.fullPath }}</code>
      </p>
      <div class="fallback-actions">
        <button type="button" class="tomori-btn tomori-btn--primary" @click="goHome">
          {{ authStore.isAuthenticated ? '返回工作台首页' : '返回登录页' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.not-found-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: var(--tomori-bg);
}

.fallback-box {
  width: 100%;
  max-width: 520px;
  margin-bottom: 0;
  text-align: center;
  padding: 32px 24px;
}

.fallback-title {
  margin: 0 0 10px;
  font-size: 20px;
  color: var(--tomori-text);
}

.fallback-desc {
  margin: 0 0 12px;
  font-size: 14px;
  color: var(--tomori-text-secondary);
}

.fallback-path {
  margin: 0 0 20px;
  padding: 8px 12px;
  background: #f5f7fa;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  font-size: 13px;
  color: var(--tomori-text-muted);
  word-break: break-all;
}

.fallback-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
}
</style>
