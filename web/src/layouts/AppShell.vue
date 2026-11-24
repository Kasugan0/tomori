<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { changePasswordApi } from '@/api/auth'
import GlobalErrorBanner from '@/components/GlobalErrorBanner.vue'
import { useIsDesktop } from '@/shared/breakpoints'
import { ROLE_LABELS, SCOPE_TYPE_LABELS } from '@/shared/enums'
import type { RoleCode } from '@/shared/types'
import { useAuthStore } from '@/stores/auth'
import { useErrorBannerStore } from '@/stores/errorBanner'

interface NavItem {
  id: string
  path: string
  label: string
  roles: RoleCode[]
  desktopTestId?: string
  mobileTestId?: string
}

const DESKTOP_NAV_ITEMS: NavItem[] = [
  { id: 'dashboard', path: '/dashboard', label: '汇总看板', roles: ['ADMIN'] },
  {
    id: 'inspection',
    path: '/inspection/new',
    label: '巡查报障',
    roles: ['INSPECTOR'],
    desktopTestId: 'nav-desktop-inspection',
  },
  {
    id: 'workorders',
    path: '/tickets',
    label: '故障工单',
    roles: ['ADMIN', 'INSPECTOR'],
    desktopTestId: 'nav-desktop-workorders',
  },
  {
    id: 'dispatch',
    path: '/dispatch',
    label: '派工调度',
    roles: ['ADMIN'],
    desktopTestId: 'nav-desktop-dispatch',
  },
  {
    id: 'repairs',
    path: '/repairs',
    label: '我的维修',
    roles: ['TECHNICIAN'],
    desktopTestId: 'nav-desktop-repairs',
  },
  {
    id: 'rules',
    path: '/admin/rules',
    label: '业务规则',
    roles: ['ADMIN'],
    desktopTestId: 'nav-desktop-rules',
  },
  {
    id: 'regions',
    path: '/admin/regions',
    label: '运维区域',
    roles: ['ADMIN'],
    desktopTestId: 'nav-desktop-regions',
  },
  {
    id: 'points',
    path: '/admin/points',
    label: '照明点位',
    roles: ['ADMIN'],
    desktopTestId: 'nav-desktop-points',
  },
  {
    id: 'assets',
    path: '/admin/assets',
    label: '设施资产',
    roles: ['ADMIN'],
    desktopTestId: 'nav-desktop-assets',
  },
  {
    id: 'scopes',
    path: '/admin/scopes',
    label: '区域授权',
    roles: ['ADMIN'],
    desktopTestId: 'nav-desktop-scopes',
  },
  {
    id: 'accounts',
    path: '/admin/accounts',
    label: '账号权限',
    roles: ['ADMIN'],
    desktopTestId: 'nav-desktop-accounts',
  },
  {
    id: 'audit',
    path: '/audit',
    label: '操作审计',
    roles: ['ADMIN'],
    desktopTestId: 'nav-desktop-audit',
  },
  {
    id: 'notifications',
    path: '/notifications',
    label: '站内通知',
    roles: ['ADMIN', 'INSPECTOR', 'TECHNICIAN'],
    desktopTestId: 'nav-desktop-notifications',
  },
]

const MOBILE_PRIMARY_ITEMS: NavItem[] = [
  {
    id: 'inspection',
    path: '/inspection/new',
    label: '巡查报障',
    roles: ['INSPECTOR'],
    mobileTestId: 'nav-mobile-inspection',
  },
  {
    id: 'workorders',
    path: '/tickets',
    label: '工单',
    roles: ['ADMIN', 'INSPECTOR'],
    mobileTestId: 'nav-mobile-workorders',
  },
  {
    id: 'repairs',
    path: '/repairs',
    label: '维修',
    roles: ['TECHNICIAN'],
    mobileTestId: 'nav-mobile-repairs',
  },
  {
    id: 'notifications',
    path: '/notifications',
    label: '通知',
    roles: ['ADMIN', 'INSPECTOR', 'TECHNICIAN'],
    mobileTestId: 'nav-mobile-notifications',
  },
]

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const bannerStore = useErrorBannerStore()
const { isDesktop } = useIsDesktop()

const mobileMoreOpen = ref(false)
const passwordDialogVisible = ref(false)
const oldSecret = ref('')
const newSecret = ref('')
const changingPassword = ref(false)

const visibleDesktopNav = computed(() => {
  const role = authStore.roleCode
  if (!role) return []
  return DESKTOP_NAV_ITEMS.filter((item) => item.roles.includes(role))
})

const visibleMobilePrimaryNav = computed(() => {
  const role = authStore.roleCode
  if (!role) return []
  return MOBILE_PRIMARY_ITEMS.filter((item) => item.roles.includes(role))
})

const regionSummary = computed(() => {
  if (!authStore.scopeAuthorizations.length) {
    return authStore.roleCode === 'ADMIN' ? '全区域管理员' : '暂无区域授权'
  }
  const active = authStore.scopeAuthorizations.filter((s) => s.is_active)
  if (!active.length) return '暂无有效授权'
  return active
    .map((s) => `${s.region_name || s.region_code || `区域#${s.region_id}`}(${s.scope_type})`)
    .join('、')
})

function isActive(path: string): boolean {
  return route.path === path || route.path.startsWith(`${path}/`)
}

function go(path: string): void {
  mobileMoreOpen.value = false
  bannerStore.clear()
  void router.push(path)
}

async function handleLogout(): Promise<void> {
  mobileMoreOpen.value = false
  bannerStore.clear()
  await authStore.logout()
  await router.replace('/login')
}

async function handleChangePassword(): Promise<void> {
  if (!oldSecret.value || !newSecret.value) return
  changingPassword.value = true
  try {
    await changePasswordApi({
      old_password: oldSecret.value,
      new_password: newSecret.value,
    })
    passwordDialogVisible.value = false
    oldSecret.value = ''
    newSecret.value = ''
    ElMessage.success('密码已修改，请重新登录')
    authStore.clearSession()
    await router.replace('/login')
  } finally {
    changingPassword.value = false
  }
}
</script>

<template>
  <div class="shell-root" :class="{ 'is-mobile': !isDesktop }">
    <!-- Desktop Sidebar (v-if 互斥挂载 R1) -->
    <aside v-if="isDesktop" class="shell-sidebar">
      <div class="shell-brand">
        <div class="shell-brand-title">公共照明故障工单</div>
        <div class="shell-brand-sub">Tomori #141</div>
      </div>

      <div class="shell-user-card">
        <div class="shell-user-name">{{ authStore.account?.full_name || authStore.account?.username || '已登录用户' }}</div>
        <div style="margin-top: 6px">
          <el-tag
            data-testid="shell-user-role"
            :data-role="authStore.roleCode || ''"
            size="small"
            type="primary"
            disable-transitions
          >
            {{ authStore.roleCode ? ROLE_LABELS[authStore.roleCode] : '' }}
          </el-tag>
        </div>
        <div data-testid="shell-user-regions" class="shell-user-regions">
          {{ regionSummary }}
        </div>
      </div>

      <nav class="shell-nav">
        <button
          v-for="item in visibleDesktopNav"
          :key="item.id"
          type="button"
          class="shell-nav-item"
          :class="{ 'is-active': isActive(item.path) }"
          :data-testid="item.desktopTestId"
          @click="go(item.path)"
        >
          {{ item.label }}
        </button>
      </nav>

      <div class="shell-sidebar-footer">
        <button type="button" class="tomori-btn" style="width: 100%; margin-bottom: 8px" @click="passwordDialogVisible = true">
          修改密码
        </button>
        <button
          type="button"
          class="tomori-btn tomori-btn--danger"
          style="width: 100%"
          data-testid="auth-logout-btn"
          @click="handleLogout"
        >
          退出登录
        </button>
      </div>
    </aside>

    <!-- Mobile Header (v-else 互斥挂载 R1) -->
    <header v-else class="shell-mobile-header">
      <div>
        <div class="shell-mobile-title">公共照明工单</div>
        <div data-testid="shell-user-regions" class="shell-mobile-sub">
          {{ regionSummary }}
        </div>
      </div>
      <div style="display: flex; align-items: center; gap: 8px">
        <el-tag
          data-testid="shell-user-role"
          :data-role="authStore.roleCode || ''"
          size="small"
          type="primary"
          disable-transitions
        >
          {{ authStore.roleCode ? ROLE_LABELS[authStore.roleCode] : '' }}
        </el-tag>
        <button
          type="button"
          class="tomori-btn"
          style="padding: 5px 10px; font-size: 12px"
          data-testid="auth-logout-btn"
          @click="handleLogout"
        >
          退出
        </button>
      </div>
    </header>

    <!-- Main workspace -->
    <main class="shell-main">
      <GlobalErrorBanner />
      <router-view />
    </main>

    <!-- Mobile Bottom Tab Bar (v-if="!isDesktop" 互斥挂载 R1) -->
    <nav v-if="!isDesktop" class="shell-mobile-tabbar">
      <button
        v-for="item in visibleMobilePrimaryNav"
        :key="item.id"
        type="button"
        class="shell-tab-item"
        :class="{ 'is-active': isActive(item.path) }"
        :data-testid="item.mobileTestId"
        @click="go(item.path)"
      >
        {{ item.label }}
      </button>
      <button
        type="button"
        class="shell-tab-item"
        data-testid="nav-mobile-more"
        @click="mobileMoreOpen = true"
      >
        更多
      </button>
    </nav>

    <!-- Mobile Drawer (inside AppShell, scoped without Teleport) -->
    <div v-if="!isDesktop && mobileMoreOpen" class="shell-mobile-drawer-backdrop" @click.self="mobileMoreOpen = false">
      <div class="shell-mobile-drawer">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
          <strong>工作台菜单（{{ authStore.account?.full_name || authStore.account?.username }}）</strong>
          <button type="button" class="tomori-btn" style="padding: 4px 10px" @click="mobileMoreOpen = false">关闭</button>
        </div>
        <div style="font-size: 12px; color: var(--tomori-text-secondary); margin-bottom: 12px">
          <div v-for="s in authStore.scopeAuthorizations.filter((x) => x.is_active)" :key="s.id">
            {{ s.region_name || `区域#${s.region_id}` }}：{{ SCOPE_TYPE_LABELS[s.scope_type] }}
          </div>
        </div>
        <div style="display: flex; flex-direction: column; gap: 8px">
          <button
            v-for="item in visibleDesktopNav"
            :key="item.id"
            type="button"
            class="tomori-btn"
            style="justify-content: flex-start"
            @click="go(item.path)"
          >
            {{ item.label }}
          </button>
          <button type="button" class="tomori-btn" style="justify-content: flex-start" @click="passwordDialogVisible = true">
            修改密码
          </button>
        </div>
      </div>
    </div>

    <!-- Change Password Dialog (:teleported="false") -->
    <el-dialog
      v-model="passwordDialogVisible"
      title="修改登录密码"
      width="380px"
      :teleported="false"
    >
      <div style="display: flex; flex-direction: column; gap: 12px">
        <label>
          <div style="font-size: 13px; margin-bottom: 4px">原密码</div>
          <input v-model="oldSecret" type="password" class="tomori-input" placeholder="请输入原密码" />
        </label>
        <label>
          <div style="font-size: 13px; margin-bottom: 4px">新密码（至少 8 位）</div>
          <input v-model="newSecret" type="password" class="tomori-input" placeholder="请输入新密码" />
        </label>
      </div>
      <template #footer>
        <button type="button" class="tomori-btn" @click="passwordDialogVisible = false">取消</button>
        <button
          type="button"
          class="tomori-btn tomori-btn--primary"
          :disabled="changingPassword || !oldSecret || newSecret.length < 8"
          @click="handleChangePassword"
        >
          确认修改
        </button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.shell-root {
  display: flex;
  min-height: 100vh;
}

.shell-root.is-mobile {
  flex-direction: column;
  padding-bottom: 64px;
}

.shell-sidebar {
  width: 240px;
  flex-shrink: 0;
  background: #1f2937;
  color: #f9fafb;
  display: flex;
  flex-direction: column;
  padding: 16px 12px;
}

.shell-brand {
  padding: 4px 8px 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.shell-brand-title {
  font-size: 16px;
  font-weight: 700;
}

.shell-brand-sub {
  font-size: 12px;
  color: #9ca3af;
}

.shell-user-card {
  padding: 12px 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  margin-bottom: 10px;
}

.shell-user-name {
  font-size: 14px;
  font-weight: 600;
}

.shell-user-regions {
  margin-top: 6px;
  font-size: 12px;
  color: #d1d5db;
  line-height: 1.4;
}

.shell-nav {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
}

.shell-nav-item {
  width: 100%;
  text-align: left;
  background: transparent;
  border: none;
  border-radius: 6px;
  color: #d1d5db;
  padding: 10px 12px;
  font-size: 14px;
  cursor: pointer;
}

.shell-nav-item:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #ffffff;
}

.shell-nav-item.is-active {
  background: #2563eb;
  color: #ffffff;
  font-weight: 600;
}

.shell-sidebar-footer {
  padding-top: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}

.shell-main {
  flex: 1;
  min-width: 0;
  padding: 20px;
}

.shell-root.is-mobile .shell-main {
  padding: 12px;
}

.shell-mobile-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  background: #1f2937;
  color: #ffffff;
}

.shell-mobile-title {
  font-size: 15px;
  font-weight: 700;
}

.shell-mobile-sub {
  font-size: 11px;
  color: #d1d5db;
  max-width: 210px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shell-mobile-tabbar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  height: 56px;
  background: #ffffff;
  border-top: 1px solid var(--tomori-border);
  display: flex;
  z-index: 40;
}

.shell-tab-item {
  flex: 1;
  border: none;
  background: transparent;
  font-size: 13px;
  color: var(--tomori-text-secondary);
  cursor: pointer;
}

.shell-tab-item.is-active {
  color: var(--tomori-primary);
  font-weight: 700;
}

.shell-mobile-drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  z-index: 50;
  display: flex;
  align-items: flex-end;
}

.shell-mobile-drawer {
  width: 100%;
  max-height: 75vh;
  overflow-y: auto;
  background: #ffffff;
  border-top-left-radius: 12px;
  border-top-right-radius: 12px;
  padding: 16px;
}
</style>
