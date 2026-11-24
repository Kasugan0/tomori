<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import PriorityTag from '@/components/PriorityTag.vue'
import StatusTag from '@/components/StatusTag.vue'
import { FAULT_TYPE_LABELS } from '@/shared/enums'
import {
  formatDateTime,
  formatOverdueDuration,
  formatRepairDeadline,
  isDeadlinePast,
} from '@/shared/format'
import type { DispatchRecordOut, DispatchStatus, PriorityCode } from '@/shared/types'
import { useDispatchesStore } from '@/stores/dispatches'

type WorkbenchTab = 'ALL' | 'PENDING_ACCEPT' | 'ACCEPTED' | 'COMPLETED'

const router = useRouter()
const dispatchesStore = useDispatchesStore()

const activeTab = ref<WorkbenchTab>('ALL')

const PRIORITY_RANK: Record<PriorityCode, number> = {
  P1: 1,
  P2: 2,
  P3: 3,
  P4: 4,
}

const tabs: ReadonlyArray<{ key: WorkbenchTab; label: string }> = [
  { key: 'ALL', label: '全部派工' },
  { key: 'PENDING_ACCEPT', label: '待接单' },
  { key: 'ACCEPTED', label: '维修中' },
  { key: 'COMPLETED', label: '已完工' },
]

function countForTab(tab: WorkbenchTab): number {
  if (tab === 'ALL') return dispatchesStore.myDispatches.length
  return dispatchesStore.myDispatches.filter((d) => d.status === (tab as DispatchStatus)).length
}

function getEffectiveDeadlineMs(d: DispatchRecordOut): number {
  const iso = d.repair_deadline_at ?? d.accept_deadline_at
  const ms = new Date(iso).getTime()
  return Number.isNaN(ms) ? Number.MAX_SAFE_INTEGER : ms
}

function isDispatchOverdue(d: DispatchRecordOut): boolean {
  if (d.status !== 'PENDING_ACCEPT' && d.status !== 'ACCEPTED') return false
  if (isDeadlinePast(d.repair_deadline_at)) return true
  if (d.status === 'PENDING_ACCEPT' && isDeadlinePast(d.accept_deadline_at)) return true
  return false
}

const filteredDispatches = computed<DispatchRecordOut[]>(() => {
  const base =
    activeTab.value === 'ALL'
      ? [...dispatchesStore.myDispatches]
      : dispatchesStore.myDispatches.filter((d) => d.status === activeTab.value)

  return base.sort((a, b) => {
    const rankA = a.ticket_priority ? PRIORITY_RANK[a.ticket_priority] : 99
    const rankB = b.ticket_priority ? PRIORITY_RANK[b.ticket_priority] : 99
    if (rankA !== rankB) return rankA - rankB
    return getEffectiveDeadlineMs(a) - getEffectiveDeadlineMs(b)
  })
})

async function loadMyDispatches(): Promise<void> {
  try {
    await dispatchesStore.fetchMine({ page_size: 100 })
  } catch {
    // 全局错误横幅已在拦截器处理
  }
}

function goDetail(dispatchId: number): void {
  void router.push(`/repairs/${dispatchId}`)
}

onMounted(() => {
  void loadMyDispatches()
})
</script>

<template>
  <div class="repair-workbench-page">
    <div class="tomori-page-header">
      <h1 class="tomori-page-title">我的维修待办</h1>
      <button
        type="button"
        class="tomori-btn"
        :disabled="dispatchesStore.loading"
        @click="loadMyDispatches"
      >
        {{ dispatchesStore.loading ? '刷新中...' : '刷新待办' }}
      </button>
    </div>

    <!-- 状态筛选标签 -->
    <div class="workbench-tabs" role="tablist">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        role="tab"
        :aria-selected="activeTab === tab.key"
        class="workbench-tab-btn"
        :class="{ 'is-active': activeTab === tab.key }"
        @click="activeTab = tab.key"
      >
        <span>{{ tab.label }}</span>
        <span class="tab-count">{{ countForTab(tab.key) }}</span>
      </button>
    </div>

    <div v-if="dispatchesStore.loading && dispatchesStore.myDispatches.length === 0" class="tomori-card empty-box">
      正在加载我的派工单...
    </div>

    <div v-else-if="filteredDispatches.length === 0" class="tomori-card empty-box">
      当前分类暂无派工单
    </div>

    <div v-else class="dispatch-card-grid">
      <div
        v-for="d in filteredDispatches"
        :key="d.id"
        :data-testid="`repair-row-${d.id}`"
        class="tomori-card dispatch-card"
        :class="{ 'is-overdue-card': isDispatchOverdue(d) }"
        @click="goDetail(d.id)"
      >
        <div class="dispatch-card-top">
          <div class="dispatch-no-wrap">
            <strong class="cell-mono">{{ d.ticket_no || `工单 #${d.ticket_id}` }}</strong>
            <el-tag v-if="isDispatchOverdue(d)" type="danger" effect="dark" size="small">
              已超时
            </el-tag>
          </div>
          <div class="dispatch-tags">
            <PriorityTag :priority="d.ticket_priority ?? null" />
            <StatusTag :status="d.status" kind="dispatch" />
          </div>
        </div>

        <div class="dispatch-title">
          {{ d.ticket_title || `故障维修任务 #${d.id}` }}
        </div>

        <div class="dispatch-sub">
          <span v-if="d.ticket_fault_type">{{ FAULT_TYPE_LABELS[d.ticket_fault_type] }}</span>
          <span v-if="d.point_name || d.point_code">
            · {{ d.point_name || d.point_code }}
          </span>
          <span v-if="d.region_name"> · {{ d.region_name }}</span>
        </div>

        <div class="dispatch-deadlines">
          <div>接单截止：{{ formatDateTime(d.accept_deadline_at) }}</div>
          <div :class="{ 'overdue-text': isDeadlinePast(d.repair_deadline_at) && (d.status === 'PENDING_ACCEPT' || d.status === 'ACCEPTED') }">
            48h 修复截止：{{ formatRepairDeadline(d.repair_deadline_at, d.ticket_status) }}
            <span
              v-if="isDispatchOverdue(d) && d.repair_deadline_at"
              style="margin-left: 6px"
            >
              {{ formatOverdueDuration(d.repair_deadline_at) }}
            </span>
          </div>
        </div>

        <div class="dispatch-card-footer">
          <span class="cell-sub">派工单 #{{ d.id }} · {{ formatDateTime(d.dispatched_at) }}</span>
          <button
            type="button"
            class="tomori-btn tomori-btn--primary"
            style="padding: 6px 12px; font-size: 13px"
            @click.stop="goDetail(d.id)"
          >
            进入处置
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.workbench-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}

.workbench-tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: 999px;
  border: 1px solid var(--tomori-border);
  background: var(--tomori-surface);
  color: var(--tomori-text-secondary);
  font-size: 13px;
  cursor: pointer;
  font-family: inherit;
}

.workbench-tab-btn.is-active {
  background: var(--tomori-primary);
  border-color: var(--tomori-primary);
  color: #ffffff;
  font-weight: 600;
}

.tab-count {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 10px;
  background: rgba(0, 0, 0, 0.08);
  font-size: 12px;
}

.workbench-tab-btn.is-active .tab-count {
  background: rgba(255, 255, 255, 0.25);
}

.dispatch-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
}

@media (max-width: 767px) {
  .dispatch-card-grid {
    grid-template-columns: 1fr;
  }
}

.dispatch-card {
  margin-bottom: 0;
  cursor: pointer;
  border-width: 1px;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.dispatch-card:hover {
  border-color: var(--tomori-primary);
}

.dispatch-card.is-overdue-card {
  border: 2px solid var(--tomori-danger);
}

.dispatch-card-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 8px;
}

.dispatch-no-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.dispatch-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.dispatch-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--tomori-text);
  margin-bottom: 4px;
}

.dispatch-sub {
  font-size: 12px;
  color: var(--tomori-text-secondary);
  margin-bottom: 8px;
}

.dispatch-deadlines {
  font-size: 12px;
  color: var(--tomori-text-secondary);
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 8px;
  background: #f9fafb;
  border-radius: 4px;
}

.overdue-text {
  color: var(--tomori-danger);
  font-weight: 600;
}

.dispatch-card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}

.cell-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 14px;
}

.cell-sub {
  font-size: 12px;
  color: var(--tomori-text-muted);
}

.empty-box {
  padding: 28px 16px;
  text-align: center;
  color: var(--tomori-text-muted);
  font-size: 14px;
}
</style>
