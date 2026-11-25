<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { isApiError } from '@/shared/api'
import { ERROR_CODE_TEXT } from '@/shared/enums'
import { useAuthStore } from '@/stores/auth'

interface LoginErrorState {
  code: string
  message: string
}

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const username = ref('')
const secret = ref('')
const submitting = ref(false)
const loginError = ref<LoginErrorState | null>(null)

const canSubmit = computed<boolean>(
  () => !submitting.value && username.value.trim().length > 0 && secret.value.length > 0,
)

function resolveRedirectTarget(): string {
  const raw = route.query.redirect
  if (typeof raw === 'string' && raw.startsWith('/') && !raw.startsWith('//')) {
    return raw
  }
  return authStore.defaultHomeRoute
}

async function handleSubmit(): Promise<void> {
  if (!canSubmit.value) return
  submitting.value = true
  loginError.value = null

  try {
    await authStore.login(username.value.trim(), secret.value)
    await router.replace(resolveRedirectTarget())
  } catch (err: unknown) {
    if (isApiError(err)) {
      const code = err.error.code
      loginError.value = {
        code,
        message: err.error.message || ERROR_CODE_TEXT[code] || '登录失败，请检查账号或密码',
      }
    } else {
      loginError.value = {
        code: 'INTERNAL_ERROR',
        message: err instanceof Error ? err.message : '网络异常，请稍后重试',
      }
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card tomori-card">
      <div class="login-header">
        <div class="login-badge">Tomori #141 · 公共照明运维</div>
        <h1 class="login-title">公共照明故障工单系统</h1>
        <p class="login-subtitle">运维管理 / 巡查 / 维修统一登录入口</p>
      </div>

      <el-alert
        v-if="loginError"
        data-testid="auth-login-error"
        :data-code="loginError.code"
        :title="loginError.message"
        type="error"
        :closable="false"
        show-icon
        class="login-alert"
      >
        <div class="login-error-code">错误码：{{ loginError.code }}</div>
      </el-alert>

      <form class="login-form" @submit.prevent="handleSubmit">
        <label class="login-field">
          <span class="login-label">用户名</span>
          <input
            v-model="username"
            type="text"
            class="tomori-input"
            autocomplete="username"
            placeholder="请输入账号名"
            data-testid="auth-username-input"
            :disabled="submitting"
          />
        </label>

        <label class="login-field">
          <span class="login-label">密码</span>
          <input
            v-model="secret"
            type="password"
            class="tomori-input"
            autocomplete="current-password"
            placeholder="请输入密码"
            data-testid="auth-password-input"
            :disabled="submitting"
          />
        </label>

        <button
          type="submit"
          class="tomori-btn tomori-btn--primary login-submit-btn"
          data-testid="auth-login-submit"
          :disabled="!canSubmit"
        >
          {{ submitting ? '登录中...' : '登 录' }}
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: linear-gradient(135deg, #eff6ff 0%, #f8fafc 100%);
}

.login-card {
  width: 100%;
  max-width: 400px;
  padding: 28px 24px;
  margin-bottom: 0;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
}

.login-header {
  text-align: center;
  margin-bottom: 20px;
}

.login-badge {
  display: inline-block;
  font-size: 12px;
  font-weight: 600;
  color: #2563eb;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 999px;
  padding: 2px 10px;
  margin-bottom: 10px;
}

.login-title {
  margin: 0 0 6px;
  font-size: 20px;
  font-weight: 700;
  color: var(--tomori-text);
}

.login-subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--tomori-text-secondary);
}

.login-alert {
  margin-bottom: 16px;
}

.login-error-code {
  font-size: 12px;
  color: var(--tomori-text-secondary);
  margin-top: 2px;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.login-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.login-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--tomori-text-secondary);
}

.login-submit-btn {
  width: 100%;
  margin-top: 8px;
  padding: 11px 16px;
  font-weight: 600;
}

@media (max-width: 480px) {
  .login-card {
    padding: 22px 18px;
  }

  .login-title {
    font-size: 18px;
  }
}
</style>
