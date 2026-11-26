<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  acknowledgeNotificationApi,
  listDeadLetterNotificationsApi,
  listNotificationsApi,
  markNotificationReadApi,
  retryNotificationApi,
} from '@/api/operations'
import { isApiError } from '@/shared/api'
import { formatDateTime } from '@/shared/format'
import type { NotificationTaskOut } from '@/shared/types'
import { useAuthStore } from '@/stores/auth'
import { useErrorBannerStore } from '@/stores/errorBanner'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const bannerStore = useErrorBannerStore()

const activeTab = ref<'mine' | 'dead_letters'>('mine')
const items = ref<NotificationTaskOut[]>([])
const totalMine = ref(0)
const minePage = ref(1)
const minePageSize = 30
const filterRead = ref<'all' | 'unread' | 'read'>('all')
const filterEventType = ref('')

const deadLetters = ref<NotificationTaskOut[]>([])
const totalDeadLetters = ref(0)
const deadPage = ref(1)
const deadPageSize = 30

const loading = ref(false)
const actingId = ref<number | null>(null)

async function loadMine(targetPage = minePage.value): Promise<void> {
  minePage.value = targetPage
  const mineRes = await listNotificationsApi({
    is_read: filterRead.value === 'unread' ? false : filterRead.value === 'read' ? true : undefined,
    event_type: filterEventType.value.trim() || undefined,
    page: minePage.value,
    page_size: minePageSize,
  })
  items.value = mineRes.items
  totalMine.value = mineRes.total
}

async function loadDeadLetters(targetPage = deadPage.value): Promise<void> {
  if (authStore.roleCode !== 'ADMIN') return
  deadPage.value = targetPage
  const dlRes = await listDeadLetterNotificationsApi({
    page: deadPage.value,
    page_size: deadPageSize,
  })
  deadLetters.value = dlRes.items
  totalDeadLetters.value = dlRes.total
}

async function load(): Promise<void> {
  loading.value = true
  try {
    await Promise.all([loadMine(minePage.value), loadDeadLetters(deadPage.value)])
  } catch {
    // 全局错误横幅已处理
  } finally {
    loading.value = false
  }
}

async function markRead(item: NotificationTaskOut): Promise<void> {
  actingId.value = item.id
  try {
    await markNotificationReadApi(item.id)
    ElMessage.success('已标为已读')
    await loadMine(minePage.value)
  } catch (err: unknown) {
    if (isApiError(err)) bannerStore.showFromApiError(err)
  } finally {
    actingId.value = null
  }
}

async function handleRetry(item: NotificationTaskOut): Promise<void> {
  actingId.value = item.id
  try {
    await retryNotificationApi(item.id)
    ElMessage.success(`通知 #${item.id} 已重回 PENDING 队列`)
    await load()
  } catch (err: unknown) {
    if (isApiError(err)) bannerStore.showFromApiError(err)
  } finally {
    actingId.value = null
  }
}

// TECHNICIAN 无 /tickets/:id 路由权限（router meta.roles），其工单关联通知
// 应跳到自己可访问的派工详情 / 待办；ADMIN/INSPECTOR 仍跳工单详情。
function relatedTarget(item: NotificationTaskOut): string | null {
  if (authStore.roleCode === 'TECHNICIAN') {
    return item.related_dispatch_id ? `/repairs/${item.related_dispatch_id}` : '/repairs'
  }
  return item.related_ticket_id ? `/tickets/${item.related_ticket_id}` : null
}

function relatedLabel(item: NotificationTaskOut): string {
  if (authStore.roleCode === 'TECHNICIAN') {
    return item.related_dispatch_id ? `查看派工 #${item.related_dispatch_id}` : '查看我的待办'
  }
  return `查看工单 #${item.related_ticket_id}`
}

async function handleAcknowledge(item: NotificationTaskOut): Promise<void> {
  actingId.value = item.id
  try {
    await acknowledgeNotificationApi(item.id)
    ElMessage.success(`死信 #${item.id} 已人工确认`)
    await load()
  } catch (err: unknown) {
    if (isApiError(err)) bannerStore.showFromApiError(err)
  } finally {
    actingId.value = null
  }
}

onMounted(() => {
  bannerStore.clear()
  if (authStore.roleCode === 'ADMIN' && route.query.tab === 'dead-letters') {
    activeTab.value = 'dead_letters'
  }
  void load()
})
</script>

<template>
  <div>
    <div class="tomori-page-header">
      <h2 class="tomori-page-title">站内通知中心</h2>
      <div style="display: flex; gap: 8px; flex-wrap: wrap">
        <button
          type="button"
          class="tomori-btn"
          :class="{ 'tomori-btn--primary': activeTab === 'mine' }"
          @click="activeTab = 'mine'"
        >
          我的通知 ({{ totalMine }})
        </button>
        <button
          v-if="authStore.roleCode === 'ADMIN'"
          type="button"
          class="tomori-btn"
          :class="{ 'tomori-btn--primary': activeTab === 'dead_letters' }"
          @click="activeTab = 'dead_letters'"
        >
          死信队列 ({{ totalDeadLetters }})
        </button>
        <button type="button" class="tomori-btn" :disabled="loading" @click="load">刷新</button>
      </div>
    </div>

    <div v-if="activeTab === 'mine'" class="tomori-card filter-row">
      <el-select
        v-model="filterRead"
        :teleported="false"
        style="width: 140px"
        @change="() => loadMine(1)"
      >
        <el-option label="全部状态" value="all" />
        <el-option label="仅未读" value="unread" />
        <el-option label="仅已读" value="read" />
      </el-select>
      <input
        v-model="filterEventType"
        class="tomori-input"
        style="width: 220px"
        placeholder="事件类型 (如 TICKET_DISPATCHED)"
        @keyup.enter="() => loadMine(1)"
      />
      <button type="button" class="tomori-btn" :disabled="loading" @click="() => loadMine(1)">筛选</button>
    </div>

    <div v-if="loading" class="tomori-card">加载中...</div>

    <div v-else-if="activeTab === 'mine'" class="notif-list">
      <div v-if="items.length === 0" class="tomori-card">暂无站内通知</div>
      <div
        v-for="item in items"
        :key="item.id"
        class="tomori-card"
        :data-testid="`notification-row-${item.id}`"
      >
        <div class="notif-head">
          <div>
            <el-tag size="small" :type="item.is_read ? 'info' : 'warning'" style="margin-right: 6px">
              {{ item.is_read ? '已读' : '未读' }}
            </el-tag>
            <strong>{{ item.title }}</strong>
            <code style="margin-left: 8px; font-size: 12px">{{ item.event_type }}</code>
          </div>
          <span class="hint-text">{{ formatDateTime(item.created_at) }}</span>
        </div>
        <p style="margin: 8px 0; font-size: 13px">{{ item.body }}</p>
        <div style="display: flex; gap: 8px; flex-wrap: wrap">
          <button
            v-if="!item.is_read"
            type="button"
            class="tomori-btn"
            :disabled="actingId === item.id"
            @click="markRead(item)"
          >
            标记已读
          </button>
          <button
            v-if="relatedTarget(item)"
            type="button"
            class="tomori-btn"
            @click="router.push(relatedTarget(item) as string)"
          >
            {{ relatedLabel(item) }}
          </button>
        </div>
      </div>

      <div v-if="totalMine > minePageSize" style="display: flex; justify-content: flex-end; margin-top: 8px">
        <el-pagination
          v-model:current-page="minePage"
          :page-size="minePageSize"
          :total="totalMine"
          layout="total, prev, pager, next"
          @current-change="(p: number) => loadMine(p)"
        />
      </div>
    </div>

    <div v-else class="notif-list">
      <div v-if="deadLetters.length === 0" class="tomori-card">暂无死信任务</div>
      <div
        v-for="dl in deadLetters"
        :key="dl.id"
        class="tomori-card"
        :data-testid="`notification-row-${dl.id}`"
      >
        <div class="notif-head">
          <div>
            <el-tag size="small" type="danger" style="margin-right: 6px">DEAD_LETTER</el-tag>
            <strong>{{ dl.title }}</strong>
            <code style="margin-left: 8px; font-size: 12px">{{ dl.event_type }}</code>
            <small style="margin-left: 8px">尝试 {{ dl.attempt_count }}/{{ dl.max_attempts }}</small>
          </div>
          <span class="hint-text">{{ formatDateTime(dl.created_at) }}</span>
        </div>
        <p style="margin: 6px 0; font-size: 13px">{{ dl.body }}</p>
        <div v-if="dl.last_error" style="color: var(--tomori-danger); font-size: 12px; margin-bottom: 8px">
          最近错误：{{ dl.last_error }}
        </div>
        <div style="display: flex; gap: 8px; flex-wrap: wrap">
          <button
            type="button"
            class="tomori-btn tomori-btn--primary"
            data-testid="notification-retry-btn"
            :disabled="actingId === dl.id"
            @click="handleRetry(dl)"
          >
            重试派发
          </button>
          <button
            type="button"
            class="tomori-btn"
            data-testid="notification-acknowledge-btn"
            :disabled="actingId === dl.id"
            @click="handleAcknowledge(dl)"
          >
            人工确认放弃
          </button>
          <button
            v-if="dl.related_ticket_id"
            type="button"
            class="tomori-btn"
            @click="router.push(`/tickets/${dl.related_ticket_id}`)"
          >
            查看工单 #{{ dl.related_ticket_id }}
          </button>
        </div>
      </div>

      <div v-if="totalDeadLetters > deadPageSize" style="display: flex; justify-content: flex-end; margin-top: 8px">
        <el-pagination
          v-model:current-page="deadPage"
          :page-size="deadPageSize"
          :total="totalDeadLetters"
          layout="total, prev, pager, next"
          @current-change="(p: number) => loadDeadLetters(p)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.filter-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.notif-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.notif-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.hint-text {
  font-size: 12px;
  color: var(--tomori-text-muted);
}
</style>
