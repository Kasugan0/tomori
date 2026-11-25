<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { RouteLocationRaw } from 'vue-router'
import { ElMessage } from 'element-plus'
import { listDeadLetterNotificationsApi, listReworkOrdersApi, triggerSlaScanApi } from '@/api/operations'
import { listTicketsApi } from '@/api/tickets'
import PriorityTag from '@/components/PriorityTag.vue'
import StatusTag from '@/components/StatusTag.vue'
import { FAULT_TYPE_LABELS } from '@/shared/enums'
import { formatDateTime, formatRepairDeadline } from '@/shared/format'
import type { FaultTicketOut, SlaScanResponseOut } from '@/shared/types'
import { useErrorBannerStore } from '@/stores/errorBanner'

interface SummaryCard {
  key: string
  label: string
  value: number
  target: RouteLocationRaw
  hint: string
  tone: 'primary' | 'warning' | 'danger' | 'info' | 'success'
}

const bannerStore = useErrorBannerStore()

const loading = ref(true)
const slaScanning = ref(false)
const lastSlaScan = ref<SlaScanResponseOut | null>(null)
const recentTickets = ref<FaultTicketOut[]>([])
const counts = ref({
  pendingDispatch: 0,
  reworkPending: 0,
  inRepair: 0,
  pendingAcceptance: 0,
  manualReview: 0,
  slaOverdue: 0,
  closed: 0,
  deadLetters: 0,
})

const cards = computed<SummaryCard[]>(() => [
  {
    key: 'pending_dispatch',
    label: '待派工工单',
    value: counts.value.pendingDispatch + counts.value.reworkPending,
    target: '/dispatch',
    hint: `常规待派 ${counts.value.pendingDispatch} · 返修待派 ${counts.value.reworkPending}`,
    tone: 'warning',
  },
  {
    key: 'in_repair',
    label: '维修中',
    value: counts.value.inRepair,
    target: { path: '/tickets', query: { status: 'IN_REPAIR' } },
    hint: '已接单待完工',
    tone: 'primary',
  },
  {
    key: 'pending_acceptance',
    label: '待验收',
    value: counts.value.pendingAcceptance,
    target: { path: '/tickets', query: { status: 'PENDING_ACCEPTANCE' } },
    hint: '维修已提交待巡查验收',
    tone: 'warning',
  },
  {
    key: 'manual_review',
    label: '人工复核中',
    value: counts.value.manualReview,
    target: { path: '/tickets', query: { status: 'MANUAL_REVIEW' } },
    hint: '返修超阈值或升级转复核',
    tone: 'danger',
  },
  {
    key: 'sla_overdue',
    label: 'SLA 超时',
    value: counts.value.slaOverdue,
    target: { path: '/tickets', query: { is_sla_overdue: 'true' } },
    hint: '郑州法定 48h 基准已逾期',
    tone: 'danger',
  },
  {
    key: 'closed',
    label: '已关闭',
    value: counts.value.closed,
    target: { path: '/tickets', query: { status: 'CLOSED' } },
    hint: '验收闭环完成',
    tone: 'success',
  },
  {
    key: 'dead_letters',
    label: '通知死信',
    value: counts.value.deadLetters,
    target: { path: '/notifications', query: { tab: 'dead-letters' } },
    hint: '需重试或人工确认',
    tone: 'info',
  },
])

async function loadSummary(): Promise<void> {
  loading.value = true
  try {
    const [
      pendingDispatch,
      reworkPending,
      inRepair,
      pendingAcceptance,
      manualReview,
      slaOverdue,
      closed,
      deadLetters,
      recentRes,
    ] = await Promise.all([
      listTicketsApi({ status: 'PENDING_DISPATCH', page: 1, page_size: 1 }),
      listReworkOrdersApi({ status: 'PENDING_DISPATCH', page: 1, page_size: 1 }),
      listTicketsApi({ status: 'IN_REPAIR', page: 1, page_size: 1 }),
      listTicketsApi({ status: 'PENDING_ACCEPTANCE', page: 1, page_size: 1 }),
      listTicketsApi({ status: 'MANUAL_REVIEW', page: 1, page_size: 1 }),
      listTicketsApi({ is_sla_overdue: true, page: 1, page_size: 1 }),
      listTicketsApi({ status: 'CLOSED', page: 1, page_size: 1 }),
      listDeadLetterNotificationsApi({ page: 1, page_size: 1 }),
      listTicketsApi({ page: 1, page_size: 6 }),
    ])
    counts.value = {
      pendingDispatch: pendingDispatch.total,
      reworkPending: reworkPending.total,
      inRepair: inRepair.total,
      pendingAcceptance: pendingAcceptance.total,
      manualReview: manualReview.total,
      slaOverdue: slaOverdue.total,
      closed: closed.total,
      deadLetters: deadLetters.total,
    }
    recentTickets.value = recentRes.items
  } catch {
    // 全局错误横幅已在响应拦截器呈现
  } finally {
    loading.value = false
  }
}

async function handleSlaScan(): Promise<void> {
  slaScanning.value = true
  try {
    const res = await triggerSlaScanApi()
    lastSlaScan.value = res
    ElMessage.success(
      `SLA 扫描完成：标记超时 ${res.marked_overdue} 条，超时回收 ${res.timeout_reassigned} 条，新增通知 ${res.notifications_created} 条`,
    )
    await loadSummary()
  } catch {
    // 全局错误横幅已在响应拦截器呈现
  } finally {
    slaScanning.value = false
  }
}

onMounted(() => {
  bannerStore.clear()
  void loadSummary()
})
</script>

<template>
  <div>
    <div class="tomori-page-header">
      <h2 class="tomori-page-title">运维管理汇总看板</h2>
      <div style="display: flex; flex-wrap: wrap; gap: 8px">
        <router-link to="/dispatch" class="tomori-btn tomori-btn--primary" style="text-decoration: none">
          进入派工调度台
        </router-link>
        <button
          type="button"
          class="tomori-btn tomori-btn--warning"
          data-testid="sla-scan-trigger-btn"
          :disabled="slaScanning"
          @click="handleSlaScan"
        >
          {{ slaScanning ? '扫描中...' : '手动触发 SLA 扫描（演示/运维用）' }}
        </button>
        <button type="button" class="tomori-btn" :disabled="loading" @click="loadSummary">刷新</button>
      </div>
    </div>

    <el-alert
      v-if="lastSlaScan"
      type="info"
      show-icon
      style="margin-bottom: 12px"
      :title="`SLA 扫描结果：标记超时 ${lastSlaScan.marked_overdue} 条，接单超时回收 ${lastSlaScan.timeout_reassigned} 条，创建通知 ${lastSlaScan.notifications_created} 条`"
      @close="lastSlaScan = null"
    />

    <div class="card-grid">
      <router-link
        v-for="card in cards"
        :key="card.key"
        :to="card.target"
        class="summary-card"
        :class="`tone-${card.tone}`"
        :data-testid="`dashboard-summary-card-${card.key}`"
      >
        <div class="summary-head">
          <span class="summary-label">{{ card.label }}</span>
          <span class="summary-go">进入 →</span>
        </div>
        <div class="summary-value">{{ loading ? '—' : card.value }}</div>
        <div class="summary-hint">{{ card.hint }}</div>
      </router-link>
    </div>

    <div class="tomori-card" style="margin-top: 16px">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px">
        <h3 style="margin: 0; font-size: 15px">近期故障工单动态</h3>
        <router-link to="/tickets" style="font-size: 13px; color: var(--tomori-primary); text-decoration: none">
          查看全部工单 →
        </router-link>
      </div>
      <div v-if="!loading && recentTickets.length === 0" style="color: var(--tomori-text-muted); font-size: 13px">
        暂无工单记录
      </div>
      <div v-else style="overflow-x: auto">
        <table class="recent-table">
          <thead>
            <tr>
              <th>工单号</th>
              <th>标题</th>
              <th>故障类型</th>
              <th>优先级</th>
              <th>状态</th>
              <th>48h 截止</th>
              <th>创建时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ticket in recentTickets" :key="ticket.id">
              <td>
                <router-link :to="`/tickets/${ticket.id}`" class="ticket-link">
                  {{ ticket.ticket_no }}
                </router-link>
              </td>
              <td>{{ ticket.title }}</td>
              <td>{{ FAULT_TYPE_LABELS[ticket.fault_type] || ticket.fault_type }}</td>
              <td><PriorityTag :priority="ticket.priority" /></td>
              <td>
                <StatusTag :status="ticket.status" kind="ticket" />
                <el-tag v-if="ticket.is_sla_overdue" type="danger" size="small" style="margin-left: 4px">
                  超时
                </el-tag>
              </td>
              <td>{{ formatRepairDeadline(ticket.repair_deadline_at, ticket.status) }}</td>
              <td>{{ formatDateTime(ticket.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="tomori-card" style="margin-top: 16px">
      <h3 style="margin: 0 0 8px; font-size: 15px">SLA 与合规基线说明</h3>
      <ul style="margin: 0; padding-left: 20px; font-size: 13px; line-height: 1.9; color: var(--tomori-text-secondary)">
        <li>
          法定唯一时限：郑州市令第 152 号第 19 条第 3 款 —— 发现后 <strong>48 小时（2880 分钟）</strong>内维修养护或督促，
          <strong>P1–P4 全部适用</strong>（P4 观察不免除）。
        </li>
        <li>
          <code>repair_deadline_at</code> 自巡查确认/人工建单（<code>T_discover</code>）起算，
          <code>regrade</code> 与改派均不顺延。
        </li>
        <li>
          <code>response_deadline_at</code> 因郑州规章无分钟级响应时限，默认恒为 <code>null</code>，界面显示「—」。
        </li>
        <li>SLA 扫描只做标记与超时回收，<strong>不自动关单</strong>；关单必须经巡查员验收闭环。</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 12px;
}

.summary-card {
  display: block;
  text-decoration: none;
  color: inherit;
  text-align: left;
  background: #ffffff;
  border: 1px solid #ebeef5;
  border-left-width: 4px;
  border-radius: 6px;
  padding: 14px 16px;
  cursor: pointer;
  transition: box-shadow 0.2s, transform 0.15s;
}

.summary-card:hover {
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
  transform: translateY(-1px);
}

.summary-card.tone-primary {
  border-left-color: #409eff;
}
.summary-card.tone-warning {
  border-left-color: #e6a23c;
}
.summary-card.tone-danger {
  border-left-color: #f56c6c;
}
.summary-card.tone-info {
  border-left-color: #909399;
}
.summary-card.tone-success {
  border-left-color: #67c23a;
}

.summary-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
}

.summary-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--tomori-text-secondary);
}

.summary-go {
  font-size: 12px;
  color: var(--tomori-primary);
}

.summary-value {
  font-size: 26px;
  font-weight: 700;
  margin: 6px 0 4px;
}

.summary-hint {
  font-size: 12px;
  color: var(--tomori-text-muted);
}

.recent-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.recent-table th,
.recent-table td {
  padding: 9px 10px;
  border-bottom: 1px solid #ebeef5;
  text-align: left;
  white-space: nowrap;
}

.recent-table th {
  background: #fafafa;
  font-weight: 600;
  color: var(--tomori-text-secondary);
}

.ticket-link {
  color: var(--tomori-primary);
  font-weight: 600;
  text-decoration: none;
}

.ticket-link:hover {
  text-decoration: underline;
}
</style>
