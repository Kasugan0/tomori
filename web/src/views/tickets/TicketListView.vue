<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PriorityTag from '@/components/PriorityTag.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useIsDesktop } from '@/shared/breakpoints'
import { FAULT_TYPE_LABELS, PRIORITY_META, TICKET_STATUS_META } from '@/shared/enums'
import {
  formatDateTime,
  formatOverdueDuration,
  formatRepairDeadline,
  formatResponseDeadline,
  isDeadlinePast,
} from '@/shared/format'
import type { FaultTicketOut, PriorityCode, TicketStatus } from '@/shared/types'
import { useTicketsStore } from '@/stores/tickets'

const route = useRoute()
const router = useRouter()
const ticketsStore = useTicketsStore()
const { isDesktop } = useIsDesktop()

const filterStatus = ref<TicketStatus | ''>('')
const filterPriority = ref<PriorityCode | ''>('')
const filterOverdue = ref<'' | 'true' | 'false'>('')
const filterKeyword = ref('')
const currentPage = ref(1)
const pageSize = ref(20)

const statusOptions = computed(() =>
  (Object.keys(TICKET_STATUS_META) as TicketStatus[]).map((code) => ({
    value: code,
    label: TICKET_STATUS_META[code].label,
  })),
)

const priorityOptions = computed(() =>
  (Object.keys(PRIORITY_META) as PriorityCode[]).map((code) => ({
    value: code,
    label: `${PRIORITY_META[code].code} ${PRIORITY_META[code].label}`,
  })),
)

function parseBooleanQueryParam(val: unknown): '' | 'true' | 'false' {
  if (val === 'true' || val === true) return 'true'
  if (val === 'false' || val === false) return 'false'
  return ''
}

function isTicketOverdue(ticket: FaultTicketOut): boolean {
  if (ticket.is_sla_overdue) return true
  if (ticket.status === 'CLOSED' || ticket.status === 'CANCELLED') return false
  return isDeadlinePast(ticket.repair_deadline_at) || isDeadlinePast(ticket.response_deadline_at)
}

async function loadTickets(page = currentPage.value): Promise<void> {
  currentPage.value = page
  try {
    await ticketsStore.fetchList({
      status: filterStatus.value || undefined,
      priority: filterPriority.value || undefined,
      is_sla_overdue:
        filterOverdue.value === 'true'
          ? true
          : filterOverdue.value === 'false'
            ? false
            : undefined,
      keyword: filterKeyword.value.trim() || undefined,
      page: currentPage.value,
      page_size: pageSize.value,
    })
  } catch {
    // 全局错误横幅已在响应拦截器中呈现
  }
}

function handleSearch(): void {
  void loadTickets(1)
}

function handleReset(): void {
  filterStatus.value = ''
  filterPriority.value = ''
  filterOverdue.value = ''
  filterKeyword.value = ''
  void loadTickets(1)
}

function handlePageChange(page: number): void {
  void loadTickets(page)
}

function handleSizeChange(size: number): void {
  pageSize.value = size
  void loadTickets(1)
}

function goDetail(ticketId: number): void {
  void router.push(`/tickets/${ticketId}`)
}

onMounted(() => {
  const qStatus = route.query.status
  if (typeof qStatus === 'string' && qStatus in TICKET_STATUS_META) {
    filterStatus.value = qStatus as TicketStatus
  }
  const qPriority = route.query.priority
  if (typeof qPriority === 'string' && qPriority in PRIORITY_META) {
    filterPriority.value = qPriority as PriorityCode
  }
  filterOverdue.value = parseBooleanQueryParam(route.query.is_sla_overdue)
  if (typeof route.query.keyword === 'string') {
    filterKeyword.value = route.query.keyword
  }
  void loadTickets(1)
})
</script>

<template>
  <div class="ticket-list-page">
    <div class="tomori-page-header">
      <h1 class="tomori-page-title">故障工单列表</h1>
      <button
        type="button"
        class="tomori-btn"
        :disabled="ticketsStore.loadingList"
        @click="loadTickets(currentPage)"
      >
        刷新列表
      </button>
    </div>

    <!-- 筛选区 -->
    <section class="tomori-card filter-card">
      <div class="filter-grid">
        <div class="filter-field">
          <label class="filter-label">工单状态</label>
          <el-select
            v-model="filterStatus"
            clearable
            placeholder="全部状态"
            :teleported="false"
            style="width: 100%"
            @change="handleSearch"
          >
            <el-option
              v-for="opt in statusOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </div>

        <div class="filter-field">
          <label class="filter-label">优先级</label>
          <el-select
            v-model="filterPriority"
            clearable
            placeholder="全部优先级"
            :teleported="false"
            style="width: 100%"
            @change="handleSearch"
          >
            <el-option
              v-for="opt in priorityOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </div>

        <div class="filter-field">
          <label class="filter-label">SLA 超时</label>
          <el-select
            v-model="filterOverdue"
            clearable
            placeholder="全部 SLA 状态"
            :teleported="false"
            style="width: 100%"
            @change="handleSearch"
          >
            <el-option label="已超时" value="true" />
            <el-option label="未超时" value="false" />
          </el-select>
        </div>

        <div class="filter-field filter-field--keyword">
          <label class="filter-label">关键词</label>
          <input
            v-model="filterKeyword"
            type="text"
            class="tomori-input"
            placeholder="搜索工单号 / 标题 / 点位"
            @keyup.enter="handleSearch"
          />
        </div>
      </div>

      <div class="filter-actions">
        <button type="button" class="tomori-btn tomori-btn--primary" @click="handleSearch">
          查询
        </button>
        <button type="button" class="tomori-btn" @click="handleReset">
          重置
        </button>
      </div>
    </section>

    <!-- 列表区（v-if / v-else 互斥挂载，确保 workorder-row-* 唯一命中） -->
    <section v-if="isDesktop" class="tomori-card">
      <div v-if="ticketsStore.loadingList" class="list-empty-state">加载中...</div>
      <div v-else-if="ticketsStore.list.length === 0" class="list-empty-state">
        暂无符合条件的故障工单
      </div>
      <div v-else class="table-scroll-wrap">
        <table class="workorder-table">
          <thead>
            <tr>
              <th>工单号</th>
              <th>优先级</th>
              <th>状态</th>
              <th>标题 / 点位 / 类型</th>
              <th>响应时限</th>
              <th>48h 修复截止</th>
              <th>返修</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="ticket in ticketsStore.list"
              :key="ticket.id"
              :data-testid="`workorder-row-${ticket.ticket_no}`"
              class="workorder-table-row"
              :class="{ 'is-overdue': isTicketOverdue(ticket) }"
              @click="goDetail(ticket.id)"
            >
              <td class="cell-mono">
                <div>{{ ticket.ticket_no }}</div>
                <div class="cell-sub">{{ formatDateTime(ticket.created_at) }}</div>
              </td>
              <td>
                <PriorityTag :priority="ticket.priority" />
              </td>
              <td>
                <div style="display: flex; flex-wrap: wrap; gap: 4px; align-items: center">
                  <StatusTag :status="ticket.status" />
                  <el-tag v-if="isTicketOverdue(ticket)" type="danger" effect="dark" size="small">
                    超时
                  </el-tag>
                </div>
              </td>
              <td>
                <div class="cell-title">{{ ticket.title }}</div>
                <div class="cell-sub">
                  {{ FAULT_TYPE_LABELS[ticket.fault_type] }}
                  <span v-if="ticket.point_name || ticket.point_code">
                    · {{ ticket.point_name || ticket.point_code }}
                  </span>
                  <span v-if="ticket.region_name"> · {{ ticket.region_name }}</span>
                </div>
              </td>
              <td class="cell-deadline">
                {{ formatResponseDeadline(ticket.response_deadline_at) }}
              </td>
              <td class="cell-deadline">
                <div>{{ formatRepairDeadline(ticket.repair_deadline_at, ticket.status) }}</div>
                <div v-if="isTicketOverdue(ticket) && ticket.repair_deadline_at" class="overdue-sub">
                  {{ formatOverdueDuration(ticket.repair_deadline_at) }}
                </div>
              </td>
              <td class="cell-center">
                <span v-if="ticket.rework_count > 0" class="rework-pill">
                  {{ ticket.rework_count }} 轮
                </span>
                <span v-else class="cell-sub">0</span>
              </td>
              <td>
                <button
                  type="button"
                  class="tomori-btn"
                  style="padding: 5px 10px; font-size: 12px"
                  @click.stop="goDetail(ticket.id)"
                >
                  详情
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-else class="mobile-card-list">
      <div v-if="ticketsStore.loadingList" class="tomori-card list-empty-state">加载中...</div>
      <div v-else-if="ticketsStore.list.length === 0" class="tomori-card list-empty-state">
        暂无符合条件的故障工单
      </div>
      <div
        v-for="ticket in ticketsStore.list"
        v-else
        :key="ticket.id"
        :data-testid="`workorder-row-${ticket.ticket_no}`"
        class="tomori-card workorder-mobile-card"
        :class="{ 'is-overdue': isTicketOverdue(ticket) }"
        @click="goDetail(ticket.id)"
      >
        <div class="mobile-card-top">
          <strong class="cell-mono">{{ ticket.ticket_no }}</strong>
          <div style="display: flex; gap: 6px; align-items: center">
            <PriorityTag :priority="ticket.priority" />
            <StatusTag :status="ticket.status" />
          </div>
        </div>
        <div class="mobile-card-title">{{ ticket.title }}</div>
        <div class="cell-sub">
          {{ FAULT_TYPE_LABELS[ticket.fault_type] }}
          <span v-if="ticket.point_name || ticket.point_code">
            · {{ ticket.point_name || ticket.point_code }}
          </span>
        </div>
        <div class="mobile-card-meta">
          <div>响应时限：{{ formatResponseDeadline(ticket.response_deadline_at) }}</div>
          <div>48h 截止：{{ formatRepairDeadline(ticket.repair_deadline_at, ticket.status) }}</div>
        </div>
      </div>
    </section>

    <!-- 分页 -->
    <div v-if="ticketsStore.total > 0" class="pagination-wrap">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :total="ticketsStore.total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next"
        @current-change="handlePageChange"
        @size-change="handleSizeChange"
      />
    </div>
  </div>
</template>

<style scoped>
.filter-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.filter-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.filter-label {
  font-size: 12px;
  color: var(--tomori-text-secondary);
}

.filter-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.table-scroll-wrap {
  overflow-x: auto;
}

.workorder-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.workorder-table th,
.workorder-table td {
  padding: 10px 12px;
  border-bottom: 1px solid #ebeef5;
  text-align: left;
  vertical-align: middle;
}

.workorder-table th {
  background: #fafafa;
  color: var(--tomori-text-secondary);
  font-weight: 600;
  white-space: nowrap;
}

.workorder-table-row {
  cursor: pointer;
  transition: background-color 0.15s;
}

.workorder-table-row:hover {
  background-color: #f5f7fa;
}

.workorder-table-row.is-overdue td:first-child {
  border-left: 3px solid var(--tomori-danger);
}

.workorder-mobile-card {
  cursor: pointer;
}

.workorder-mobile-card.is-overdue {
  border-color: var(--tomori-danger);
}

.mobile-card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.mobile-card-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 4px;
}

.mobile-card-meta {
  margin-top: 8px;
  font-size: 12px;
  color: var(--tomori-text-secondary);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.cell-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  white-space: nowrap;
}

.cell-title {
  font-weight: 500;
  color: var(--tomori-text);
}

.cell-sub {
  font-size: 12px;
  color: var(--tomori-text-muted);
  margin-top: 2px;
}

.cell-deadline {
  font-size: 12px;
  white-space: nowrap;
}

.cell-center {
  text-align: center;
}

.overdue-sub {
  color: var(--tomori-danger);
  font-size: 12px;
  margin-top: 2px;
}

.rework-pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  background: #fef0f0;
  color: var(--tomori-danger);
  font-size: 12px;
  font-weight: 600;
}

.list-empty-state {
  padding: 28px 12px;
  text-align: center;
  color: var(--tomori-text-muted);
  font-size: 14px;
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
