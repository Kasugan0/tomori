import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import type { AccountOut, RoleCode, ScopeAuthorizationOut } from '@/shared/types'
import { loginApi, logoutApi, fetchMeApi } from '@/api/auth'

const TOKEN_STORAGE_KEY = 'tomori_access_token'
const ROLE_STORAGE_KEY = 'tomori_role_code'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(
    typeof sessionStorage !== 'undefined' ? sessionStorage.getItem(TOKEN_STORAGE_KEY) : null,
  )
  const account = ref<AccountOut | null>(null)
  const roleCode = ref<RoleCode | null>(
    typeof sessionStorage !== 'undefined'
      ? (sessionStorage.getItem(ROLE_STORAGE_KEY) as RoleCode | null)
      : null,
  )
  const scopeAuthorizations = ref<ScopeAuthorizationOut[]>([])
  const meLoaded = ref(false)
  const sessionGeneration = ref(0)

  const isAuthenticated = computed(() => token.value !== null && token.value.length > 0)

  const defaultHomeRoute = computed<string>(() => {
    switch (roleCode.value) {
      case 'ADMIN':
        return '/dashboard'
      case 'INSPECTOR':
        return '/inspection/new'
      case 'TECHNICIAN':
        return '/repairs'
      default:
        return '/login'
    }
  })

  function persistToken(nextToken: string | null, nextRole: RoleCode | null): void {
    token.value = nextToken
    roleCode.value = nextRole
    if (typeof sessionStorage !== 'undefined') {
      if (nextToken) {
        sessionStorage.setItem(TOKEN_STORAGE_KEY, nextToken)
      } else {
        sessionStorage.removeItem(TOKEN_STORAGE_KEY)
      }
      if (nextRole) {
        sessionStorage.setItem(ROLE_STORAGE_KEY, nextRole)
      } else {
        sessionStorage.removeItem(ROLE_STORAGE_KEY)
      }
    }
  }

  async function login(username: string, secret: string): Promise<void> {
    const generation = ++sessionGeneration.value
    const res = await loginApi({ username, password: secret })
    if (generation !== sessionGeneration.value) throw new Error('STALE_SESSION')
    persistToken(res.access_token, res.account.role_code)
    account.value = res.account
    await loadMe()
  }

  async function loadMe(): Promise<void> {
    if (!token.value) return
    const generation = sessionGeneration.value
    const me = await fetchMeApi()
    if (generation !== sessionGeneration.value) throw new Error('STALE_SESSION')
    account.value = me.account
    roleCode.value = me.role_code
    scopeAuthorizations.value = me.scope_authorizations ?? []
    meLoaded.value = true
    persistToken(token.value, me.role_code)
  }

  function clearSession(): void {
    sessionGeneration.value++
    persistToken(null, null)
    account.value = null
    scopeAuthorizations.value = []
    meLoaded.value = false
    ElMessage.closeAll()
  }

  async function logout(): Promise<void> {
    const generation = sessionGeneration.value
    try {
      if (token.value) {
        await logoutApi()
      }
    } finally {
      if (generation === sessionGeneration.value) clearSession()
    }
  }

  return {
    token,
    account,
    roleCode,
    scopeAuthorizations,
    meLoaded,
    sessionGeneration,
    isAuthenticated,
    defaultHomeRoute,
    login,
    loadMe,
    logout,
    clearSession,
  }
})
