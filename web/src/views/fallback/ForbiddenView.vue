<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ROLE_LABELS } from '@/shared/enums'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const roleDisplay = computed<string>(() =>
  authStore.roleCode ? `${ROLE_LABELS[authStore.roleCode]} (${authStore.roleCode})` : '未登录',
)

const fromPath = computed<string>(() => {
  const raw = route.query.from
  if (typeof raw === 'string' && raw.length > 0) return raw
  if (Array.isArray(raw) && typeof raw[0] === 'string' && raw[0].length > 0) return raw[0]
  return ''
})

function goBackHome(): void {
  void router.replace(authStore.defaultHomeRoute)
}
</script>

<template>
  <div class="tomori-card fallback-box" data-testid="forbidden-page">
    <h2 class="fallback-title">403 · 无权访问该页面</h2>
    <p class="fallback-desc">
      当前登录角色：<strong>{{ roleDisplay }}</strong>
    </p>
    <p v-if="fromPath" class="fallback-path">
      受限目标路径：<code>{{ fromPath }}</code>
    </p>
    <div class="fallback-actions">
      <button
        type="button"
        class="tomori-btn tomori-btn--primary"
        data-testid="forbidden-back-btn"
        @click="goBackHome"
      >
        返回我的工作台
      </button>
    </div>
  </div>
</template>

<style scoped>
.fallback-box {
  width: 100%;
  max-width: 560px;
  margin: 32px auto;
  text-align: center;
  padding: 32px 24px;
}

.fallback-title {
  margin: 0 0 10px;
  color: var(--tomori-danger);
  font-size: 20px;
}

.fallback-desc {
  margin: 0 0 12px;
  color: var(--tomori-text-secondary);
  font-size: 14px;
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
