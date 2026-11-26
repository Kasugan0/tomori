import { createRouter, createWebHistory, type RouteLocationRaw } from 'vue-router'
import { isApiError } from '@/shared/api'
import type { RoleCode } from '@/shared/types'
import { pinia } from '@/stores/pinia'
import { useAuthStore } from '@/stores/auth'
import { useErrorBannerStore } from '@/stores/errorBanner'
import AppShell from '@/layouts/AppShell.vue'

declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean
    roles?: RoleCode[]
    title?: string
  }
}

export const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/auth/LoginView.vue'),
      meta: { requiresAuth: false, title: '登录' },
    },
    {
      path: '/',
      component: AppShell,
      meta: { requiresAuth: true },
      children: [
        {
          path: '',
          name: 'root-redirect',
          redirect: () => {
            const auth = useAuthStore(pinia)
            return auth.defaultHomeRoute
          },
        },
        {
          path: 'dashboard',
          name: 'dashboard',
          component: () => import('@/views/admin/DashboardView.vue'),
          meta: { requiresAuth: true, roles: ['ADMIN'], title: '汇总看板' },
        },
        {
          path: 'inspection/new',
          name: 'inspection-new',
          component: () => import('@/views/inspection/InspectionReportView.vue'),
          meta: { requiresAuth: true, roles: ['INSPECTOR'], title: '点位巡查报障' },
        },
        {
          path: 'tickets',
          name: 'tickets',
          component: () => import('@/views/tickets/TicketListView.vue'),
          meta: { requiresAuth: true, roles: ['ADMIN', 'INSPECTOR'], title: '故障工单列表' },
        },
        {
          path: 'tickets/:ticketId',
          name: 'ticket-detail',
          component: () => import('@/views/tickets/TicketDetailView.vue'),
          meta: { requiresAuth: true, roles: ['ADMIN', 'INSPECTOR'], title: '工单详情' },
        },
        {
          path: 'dispatch',
          name: 'dispatch',
          component: () => import('@/views/dispatch/DispatchBoardView.vue'),
          meta: { requiresAuth: true, roles: ['ADMIN'], title: '区域派单调度台' },
        },
        {
          path: 'repairs',
          name: 'repairs',
          component: () => import('@/views/repair/RepairWorkbenchView.vue'),
          meta: { requiresAuth: true, roles: ['TECHNICIAN'], title: '我的维修待办' },
        },
        {
          path: 'repairs/:dispatchId',
          name: 'repair-detail',
          component: () => import('@/views/repair/RepairDetailView.vue'),
          meta: { requiresAuth: true, roles: ['TECHNICIAN'], title: '维修处置详情' },
        },
        {
          path: 'admin/regions',
          name: 'admin-regions',
          component: () => import('@/views/admin/RegionAdminView.vue'),
          meta: { requiresAuth: true, roles: ['ADMIN'], title: '运维区域管理' },
        },
        {
          path: 'admin/points',
          name: 'admin-points',
          component: () => import('@/views/admin/LightingPointAdminView.vue'),
          meta: { requiresAuth: true, roles: ['ADMIN'], title: '照明点位管理' },
        },
        {
          path: 'admin/assets',
          name: 'admin-assets',
          component: () => import('@/views/admin/FacilityAssetAdminView.vue'),
          meta: { requiresAuth: true, roles: ['ADMIN'], title: '设施资产台账' },
        },
        {
          path: 'admin/rules',
          name: 'admin-rules',
          component: () => import('@/views/admin/BusinessRuleAdminView.vue'),
          meta: { requiresAuth: true, roles: ['ADMIN'], title: '业务规则配置' },
        },
        {
          path: 'admin/scopes',
          name: 'admin-scopes',
          component: () => import('@/views/admin/ScopeAuthorizationView.vue'),
          meta: { requiresAuth: true, roles: ['ADMIN'], title: '业务范围授权' },
        },
        {
          path: 'admin/accounts',
          name: 'admin-accounts',
          component: () => import('@/views/admin/AccountAdminView.vue'),
          meta: { requiresAuth: true, roles: ['ADMIN'], title: '账号与角色管理' },
        },
        {
          path: 'audit',
          name: 'audit',
          component: () => import('@/views/admin/AuditLogView.vue'),
          meta: { requiresAuth: true, roles: ['ADMIN'], title: '操作审计日志' },
        },
        {
          path: 'notifications',
          name: 'notifications',
          component: () => import('@/views/notifications/NotificationCenterView.vue'),
          meta: {
            requiresAuth: true,
            roles: ['ADMIN', 'INSPECTOR', 'TECHNICIAN'],
            title: '站内通知中心',
          },
        },
        {
          path: '403',
          name: 'forbidden',
          component: () => import('@/views/fallback/ForbiddenView.vue'),
          meta: { requiresAuth: true, roles: ['ADMIN', 'INSPECTOR', 'TECHNICIAN'], title: '无权访问' },
        },
      ],
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/fallback/NotFoundView.vue'),
      meta: { requiresAuth: false, title: '页面未找到' },
    },
  ],
})

router.beforeEach(async (to): Promise<boolean | RouteLocationRaw> => {
  const auth = useAuthStore(pinia)
  const bannerStore = useErrorBannerStore(pinia)
  bannerStore.clear()

  if (to.meta.requiresAuth !== false && !auth.isAuthenticated) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  if (auth.isAuthenticated && !auth.meLoaded) {
    try {
      await auth.loadMe()
    } catch (err) {
      if (isApiError(err) && err.httpStatus === 401) {
        auth.clearSession()
        return { path: '/login', query: { redirect: to.fullPath } }
      }
      // 非 401 故障（网络抖动 / 5xx）：保留会话，用已持久化的角色继续导航；
      // meLoaded 仍为 false，下次导航会自动重试 loadMe，错误由全局横幅呈现。
    }
  }

  if (to.path === '/login' && auth.isAuthenticated) {
    return auth.defaultHomeRoute
  }

  if (to.meta.roles && auth.roleCode && !to.meta.roles.includes(auth.roleCode)) {
    return { path: '/403', query: { from: to.fullPath } }
  }

  return true
})

router.afterEach((to) => {
  if (typeof document !== 'undefined' && to.meta.title) {
    document.title = `${to.meta.title} · 公共照明故障工单系统`
  }
})
