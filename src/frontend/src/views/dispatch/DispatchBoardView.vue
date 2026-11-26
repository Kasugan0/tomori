<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getDispatchCandidatesApi } from '@/api/dispatches'
import { triggerSlaScanApi } from '@/api/operations'
import { getTicketDetailApi, listTicketsApi } from '@/api/tickets'
import PriorityTag from '@/components/PriorityTag.vue'
import StatusTag from '@/components/StatusTag.vue'
import { isApiError } from '@/shared/api'
import { fetchAllPages } from '@/shared/pagination'
import {
  FAULT_TYPE_LABELS,
  getNotEligibleReasonText,
  PRIORITY_META,
  TICKET_STATUS_META,
} from '@/shared/enums'
import {
  formatConfidence,
  formatDateTime,
  formatOverdueDuration,
  formatRepairDeadline,
  formatResponseDeadline,
} from '@/shared/format'
import { useIdempotentSubmit } from '@/shared/idempotency'
import type {
  DispatchCandidateItem,
  DispatchCandidatesResponseOut,
  FaultTicketDetailOut,
  FaultTicketOut,
  PriorityCode,
  SlaScanResponseOut,
  TicketStatus,
} from '@/shared/types'
import { useErrorBannerStore } from '@/stores/errorBanner'
import { useMetadataStore } from '@/stores/metadata'
import { useTicketsStore } from '@/stores/tickets'

const QUEUE_STATUSES: ReadonlyArray<TicketStatus> = [
  'PENDING_DISPATCH',
  'REWORK_PENDING_DISPATCH',
  'DISPATCHED',
  'IN_REPAIR',
  'MANUAL_REVIEW',
]

const STATUS_TIER: Record<TicketStatus, number> = {
  REWORK_PENDING_DISPATCH: 0,
  PENDING_DISPATCH: 1,
  MANUAL_REVIEW: 2,
  DISPATCHED: 3,
  IN_REPAIR: 4,
  AI_CANDIDATE_PENDING: 5,
  PENDING_ACCEPTANCE: 6,
  CLOSED: 7,
  CANCELLED: 8,
}

const PRIORITY_RANK: Record<PriorityCode, number> = {
  P1: 1,
  P2: 2,
  P3: 3,
  P4: 4,
}

const route = useRoute()
const bannerStore = useErrorBannerStore()
const metadataStore = useMetadataStore()
const ticketsStore = useTicketsStore()
const dispatchSubmit = useIdempotentSubmit()

const queue = ref<FaultTicketOut[]>([])
const loadingQueue = ref(false)
const selectedTicketId = ref<number | null>(null)
const selectedDetail = ref<FaultTicketDetailOut | null>(null)
const loadingDetail = ref(false)

const candidatesOpen = ref(false)
const candidatesData = ref<DispatchCandidatesResponseOut | null>(null)
const loadingCandidates = ref(false)
const selectedTechAccountId = ref<number | undefined>(undefined)
const dispatchRemark = ref('')
const reassignReason = ref('')
const reassignEscalate = ref(false)

const slaScanning = ref(false)
const lastSlaScan = ref<SlaScanResponseOut | null>(null)

const filters = ref<{
  region_id: number | undefined
  priority: PriorityCode | undefined
  status: TicketStatus | undefined
  is_sla_overdue: boolean
  keyword: string
}>({
  region_id: undefined,
  priority: undefined,
  status: undefined,
  is_sla_overdue: false,
  keyword: '',
})

const selectedTicket = computed<FaultTicketOut | null>(() => {
  if (selectedDetail.value && selectedDetail.value.id === selectedTicketId.value) {
    return selectedDetail.value
  }
  return queue.value.find((t) => t.id === selectedTicketId.value) ?? queue.value[0] ?? null
})

const isReassignMode = computed(() => {
  const st = selectedTicket.value?.status
  return st === 'DISPATCHED' || st === 'IN_REPAIR'
})

const canOpenDispatch = computed(() => {
  const st = selectedTicket.value?.status
  return (
    st === 'PENDING_DISPATCH' ||
    st === 'REWORK_PENDING_DISPATCH' ||
    st === 'DISPATCHED' ||
    st === 'IN_REPAIR'
  )
})

const selectedRegionDisplay = computed(() => {
  const t = selectedTicket.value
  if (!t) return '—'
  if (t.region_name) return `${t.region_name} (#${t.region_id})`
  const matched = metadataStore.regions.find((r) => r.id === t.region_id)
  return matched ? `${matched.name} (#${matched.id})` : `区域 #${t.region_id}`
})

const selectedEligibleCandidate = computed<DispatchCandidateItem | null>(
  () => candidatesData.value?.candidates.find((c) => c.account_id === selectedTechAccountId.value) ?? null,
)

const canDispatch = computed(() => {
  if (dispatchSubmit.isSubmitting.value) return false
  if (!selectedTicket.value || !canOpenDispatch.value || !selectedEligibleCandidate.value) return false
  if (isReassignMode.value && reassignReason.value.trim().length === 0) return false
  return true
})

function sortQueue(items: FaultTicketOut[]): FaultTicketOut[] {
  return [...items].sort((a, b) => {
    const tierDiff = (STATUS_TIER[a.status] ?? 9) - (STATUS_TIER[b.status] ?? 9)
    if (tierDiff !== 0) return tierDiff
    const pa = a.priority ? PRIORITY_RANK[a.priority] : 9
    const pb = b.priority ? PRIORITY_RANK[b.priority] : 9
    if (pa !== pb) return pa - pb
    const da = a.repair_deadline_at ? new Date(a.repair_deadline_at).getTime() : Number.MAX_SAFE_INTEGER
    const db = b.repair_deadline_at ? new Date(b.repair_deadline_at).getTime() : Number.MAX_SAFE_INTEGER
    if (da !== db) return da - db
    return b.id - a.id
  })
}

async function loadSelectedDetail(ticketId: number): Promise<void> {
  loadingDetail.value = true
  try {
    selectedDetail.value = await getTicketDetailApi(ticketId)
  } catch {
    // 全局错误横幅已处理
  } finally {
    loadingDetail.value = false
  }
}

async function loadQueue(): Promise<void> {
  loadingQueue.value = true
  try {
    const commonParams = {
      region_id: filters.value.region_id,
      priority: filters.value.priority,
      is_sla_overdue: filters.value.is_sla_overdue ? true : undefined,
      keyword: filters.value.keyword.trim() || undefined,
    }

    let items: FaultTicketOut[] = []
    if (filters.value.status) {
      const fetched = await fetchAllPages((page, pageSize) =>
        listTicketsApi({ ...commonParams, status: filters.value.status, page, page_size: pageSize }),
      )
      items = fetched.filter((t) => QUEUE_STATUSES.includes(t.status))
    } else {
      const responses = await Promise.all(
        QUEUE_STATUSES.map((st) =>
          fetchAllPages((page, pageSize) =>
            listTicketsApi({ ...commonParams, status: st, page, page_size: pageSize }),
          ),
        ),
      )
      const dedup = new Map<number, FaultTicketOut>()
      for (const fetched of responses) {
        for (const item of fetched) {
          dedup.set(item.id, item)
        }
      }
      items = Array.from(dedup.values())
    }

    queue.value = sortQueue(items)

    const queryTicketId = Number(route.query.ticketId)
    if (selectedTicketId.value === null && Number.isFinite(queryTicketId)) {
      const target = queue.value.find((t) => t.id === queryTicketId)
      if (target) selectedTicketId.value = target.id
    }
    if (queue.value.length > 0 && !queue.value.some((t) => t.id === selectedTicketId.value)) {
      selectedTicketId.value = queue.value[0].id
      candidatesOpen.value = false
      candidatesData.value = null
      selectedTechAccountId.value = undefined
    }
    if (queue.value.length === 0) {
      selectedTicketId.value = null
      selectedDetail.value = null
      candidatesOpen.value = false
      candidatesData.value = null
      selectedTechAccountId.value = undefined
    } else if (selectedTicketId.value !== null) {
      await loadSelectedDetail(selectedTicketId.value)
    }
  } catch {
    // 全局错误横幅已处理
  } finally {
    loadingQueue.value = false
  }
}

async function openCandidateDrawer(): Promise<void> {
  if (!selectedTicket.value || !canOpenDispatch.value) return
  loadingCandidates.value = true
  candidatesOpen.value = true
  selectedTechAccountId.value = undefined
  dispatchSubmit.resetKey()
  try {
    const res = await getDispatchCandidatesApi(selectedTicket.value.id)
    candidatesData.value = res
    if (res.candidates.length > 0) {
      selectedTechAccountId.value = res.candidates[0].account_id
    }
  } catch (err: unknown) {
    if (isApiError(err)) bannerStore.showFromApiError(err)
  } finally {
    loadingCandidates.value = false
  }
}

async function handleSelectTicket(ticket: FaultTicketOut): Promise<void> {
  selectedTicketId.value = ticket.id
  candidatesOpen.value = false
  candidatesData.value = null
  selectedTechAccountId.value = undefined
  dispatchRemark.value = ''
  reassignReason.value = ''
  reassignEscalate.value = false
  dispatchSubmit.resetKey()
  await loadSelectedDetail(ticket.id)
}

async function handleConfirmDispatch(): Promise<void> {
  if (!selectedTicket.value || !selectedEligibleCandidate.value) return
  const ticket = selectedTicket.value
  const tech = selectedEligibleCandidate.value
  const summary = [
    `工单号：${ticket.ticket_no}`,
    `维修员：${tech.full_name} (${tech.username})`,
    `所属区域：${selectedRegionDisplay.value}`,
    `48h 法定截止：${formatRepairDeadline(ticket.repair_deadline_at, ticket.status)}`,
    isReassignMode.value ? `改派原因：${reassignReason.value.trim()}` : '',
  ]
    .filter(Boolean)
    .join('\n')

  try {
    await ElMessageBox.confirm(summary, isReassignMode.value ? '确认改派工单' : '确认派工', {
      confirmButtonText: '确认',
      cancelButtonText: '取消',
      type: 'info',
    })
  } catch {
    return
  }

  try {
    await dispatchSubmit.run(async (key, markReplayed) => {
      if (isReassignMode.value) {
        await ticketsStore.reassign(
          ticket.id,
          {
            technician_account_id: tech.account_id,
            scope_authorization_id: tech.scope_authorization_id,
            reason: reassignReason.value.trim(),
            escalate: reassignEscalate.value,
          },
          key,
          markReplayed,
        )
      } else {
        await ticketsStore.dispatch(
          ticket.id,
          {
            technician_account_id: tech.account_id,
            scope_authorization_id: tech.scope_authorization_id,
            dispatch_mode:
              candidatesData.value?.candidates[0]?.account_id === tech.account_id
                ? 'AUTO_RECOMMENDED'
                : 'MANUAL',
            remark: dispatchRemark.value.trim() || null,
          },
          key,
          markReplayed,
        )
      }
    })
    ElMessage.success(isReassignMode.value ? '改派成功' : '派工成功')
    reassignReason.value = ''
    reassignEscalate.value = false
    dispatchRemark.value = ''
    candidatesOpen.value = false
    candidatesData.value = null
    selectedTechAccountId.value = undefined
    await loadQueue()
  } catch (err: unknown) {
    if (isApiError(err)) {
      bannerStore.showFromApiError(err)
      if (err.error.code === 'DISPATCH_TECHNICIAN_NOT_ELIGIBLE') {
        await openCandidateDrawer()
      }
    }
  }
}

async function handleSlaScan(): Promise<void> {
  slaScanning.value = true
  try {
    const res = await triggerSlaScanApi()
    lastSlaScan.value = res
    ElMessage.success(
      `SLA 扫描完成：标记超时 ${res.marked_overdue}，超时改派 ${res.timeout_reassigned}，通知 ${res.notifications_created}`,
    )
    await loadQueue()
  } catch {
    // 全局错误横幅已处理
  } finally {
    slaScanning.value = false
  }
}

function resetFilters(): void {
  filters.value = {
    region_id: undefined,
    priority: undefined,
    status: undefined,
    is_sla_overdue: false,
    keyword: '',
  }
  void loadQueue()
}

onMounted(async () => {
  bannerStore.clear()
  try {
    await Promise.all([metadataStore.loadRegions(), loadQueue()])
  } catch {
    // 全局错误横幅已处理
  }
})
</script>

<template>
  <div>
    <div class="tomori-page-header">
      <div>
        <h2 class="tomori-page-title">区域派单调度台</h2>
        <div style="font-size: 12px; color: var(--tomori-text-secondary); margin-top: 2px">
          优先展示待派工 / 返修待派工 · 严格校验区域 REPAIR 授权与在途上限
        </div>
      </div>
      <button
        type="button"
        class="tomori-btn tomori-btn--warning"
        data-testid="sla-scan-trigger-btn"
        :disabled="slaScanning"
        @click="handleSlaScan"
      >
        {{ slaScanning ? '扫描中...' : '手动触发 SLA 扫描（演示/运维用）' }}
      </button>
    </div>

    <el-alert
      v-if="lastSlaScan"
      type="info"
      show-icon
      style="margin-bottom: 12px"
      :title="`SLA 扫描结果：标记超时 ${lastSlaScan.marked_overdue} 条，超时回收 ${lastSlaScan.timeout_reassigned} 条，通知创建 ${lastSlaScan.notifications_created} 条`"
      @close="lastSlaScan = null"
    />

    <el-row :gutter="16">
      <!-- Left: Filters -->
      <el-col :xs="24" :lg="5" style="margin-bottom: 16px">
        <div class="tomori-card">
          <h3 style="margin: 0 0 12px; font-size: 15px">队列筛选</h3>
          <div style="display: flex; flex-direction: column; gap: 10px">
            <el-select v-model="filters.region_id" clearable :teleported="false" placeholder="全部授权区域" style="width: 100%">
              <el-option v-for="r in metadataStore.regions" :key="r.id" :label="r.name" :value="r.id" />
            </el-select>
            <el-select v-model="filters.priority" clearable :teleported="false" placeholder="全部优先级" style="width: 100%">
              <el-option
                v-for="p in PRIORITY_META"
                :key="p.code"
                :label="`${p.code} ${p.label}`"
                :value="p.code"
              />
            </el-select>
            <el-select v-model="filters.status" clearable :teleported="false" placeholder="全部调度态" style="width: 100%">
              <el-option
                v-for="st in QUEUE_STATUSES"
                :key="st"
                :label="`${TICKET_STATUS_META[st].label} (${st})`"
                :value="st"
              />
            </el-select>
            <input
              v-model="filters.keyword"
              type="text"
              class="tomori-input"
              placeholder="工单号 / 标题 / 点位"
              @keyup.enter="loadQueue"
            />
            <label class="tomori-checkbox-label">
              <input v-model="filters.is_sla_overdue" type="checkbox" class="tomori-checkbox" />
              <span style="font-size: 13px">仅看 SLA 已超时</span>
            </label>
            <div style="display: flex; gap: 8px">
              <button
                type="button"
                class="tomori-btn tomori-btn--primary"
                style="flex: 1"
                :disabled="loadingQueue"
                @click="loadQueue"
              >
                刷新队列
              </button>
              <button type="button" class="tomori-btn" style="flex: 1" @click="resetFilters">重置</button>
            </div>
          </div>
        </div>
      </el-col>

      <!-- Middle: Queue -->
      <el-col :xs="24" :lg="12" style="margin-bottom: 16px">
        <div class="tomori-card">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
            <h3 style="margin: 0; font-size: 15px">工单队列（{{ queue.length }}）</h3>
            <span class="hint-text">待派工 / 返修待派工置顶</span>
          </div>
          <div v-if="loadingQueue" class="hint-text">加载中...</div>
          <div v-else-if="queue.length === 0" class="hint-text">暂无匹配工单</div>
          <div v-else class="queue-list">
            <div
              v-for="t in queue"
              :key="t.id"
              :data-testid="`dispatch-queue-row-${t.ticket_no}`"
              class="queue-row"
              :class="{ 'is-selected': selectedTicket?.id === t.id, 'is-overdue': t.is_sla_overdue }"
              @click="handleSelectTicket(t)"
            >
              <div style="display: flex; justify-content: space-between; gap: 8px; align-items: center; flex-wrap: wrap">
                <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap">
                  <strong>{{ t.ticket_no }}</strong>
                  <PriorityTag :priority="t.priority" />
                  <el-tag
                    v-if="t.status === 'REWORK_PENDING_DISPATCH' || t.rework_count > 0"
                    type="danger"
                    size="small"
                  >
                    返修 (第 {{ Math.max(t.rework_count, 1) }} 轮)
                  </el-tag>
                  <el-tag v-if="t.is_sla_overdue" type="danger" size="small" effect="dark">超时</el-tag>
                </div>
                <StatusTag :status="t.status" />
              </div>
              <div class="queue-sub">
                <span>{{ FAULT_TYPE_LABELS[t.fault_type] }} · {{ t.title }}</span>
                <span>48h 截止：{{ formatRepairDeadline(t.repair_deadline_at, t.status) }}</span>
              </div>
            </div>
          </div>
        </div>
      </el-col>

      <!-- Right: Detail & Regional Dispatch -->
      <el-col :xs="24" :lg="7" style="margin-bottom: 16px">
        <div v-if="selectedTicket" class="tomori-card">
          <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 10px">
            <h3 style="margin: 0; font-size: 15px">派工处置 · {{ selectedTicket.ticket_no }}</h3>
            <router-link :to="`/tickets/${selectedTicket.id}`" class="tomori-btn" style="text-decoration: none; padding: 5px 10px; font-size: 12px">
              工单详情
            </router-link>
          </div>

          <div style="display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px">
            <el-tag data-testid="dispatch-region-badge" type="info" size="small" disable-transitions>
              区域：{{ selectedRegionDisplay }}
            </el-tag>
            <PriorityTag :priority="selectedTicket.priority" />
            <StatusTag :status="selectedTicket.status" />
            <el-tag
              v-if="selectedTicket.is_sla_overdue"
              data-testid="workorder-sla-overdue-badge"
              :data-overdue-stage="selectedTicket.overdue_stage || 'REPAIR'"
              type="danger"
              size="small"
              disable-transitions
            >
              SLA 超时 ({{ selectedTicket.overdue_stage || 'REPAIR' }})
              {{ formatOverdueDuration(selectedTicket.repair_deadline_at) }}
            </el-tag>
          </div>

          <div class="sla-meta-box">
            <div>故障点位：{{ selectedTicket.point_code || `#${selectedTicket.lighting_point_id}` }} {{ selectedTicket.point_name || '' }}</div>
            <div>响应截止：{{ formatResponseDeadline(selectedTicket.response_deadline_at) }}</div>
            <div>48h 修复截止：{{ formatRepairDeadline(selectedTicket.repair_deadline_at, selectedTicket.status) }}</div>
            <div>确认时间：{{ formatDateTime(selectedTicket.confirmed_at) }}</div>
            <div v-if="loadingDetail" class="hint-text">正在刷新工单详情...</div>
          </div>

          <el-alert
            v-if="selectedTicket.status === 'MANUAL_REVIEW'"
            type="error"
            :closable="false"
            show-icon
            style="margin-top: 10px"
            title="当前工单处于人工复核中（MANUAL_REVIEW），需先到工单详情页执行复核裁决。"
          />

          <button
            type="button"
            class="tomori-btn tomori-btn--primary"
            style="width: 100%; margin: 12px 0"
            data-testid="dispatch-open-btn"
            :disabled="!canOpenDispatch || loadingCandidates"
            @click="openCandidateDrawer"
          >
            {{ loadingCandidates ? '查询区域候选人中...' : isReassignMode ? '加载改派候选维修员' : '加载区域授权维修员推荐列表' }}
          </button>

          <div v-if="candidatesOpen && candidatesData">
            <div
              v-if="candidatesData.candidates.length === 0"
              data-testid="dispatch-candidate-empty"
              class="empty-notice"
            >
              本区域当前没有可用维修员
            </div>

            <div style="margin-bottom: 10px">
              <div class="field-label">选择维修人员（含不合格项 reason 展示）</div>
              <el-select
                v-model="selectedTechAccountId"
                :teleported="false"
                placeholder="选择区域维修人员"
                style="width: 100%"
                data-testid="dispatch-assignee-select"
              >
                <el-option
                  v-for="(item, idx) in candidatesData.candidates"
                  :key="`cand-${item.account_id}`"
                  :value="item.account_id"
                  :label="`${item.full_name} (${item.username})`"
                  :data-testid="`dispatch-assignee-option-${item.username}`"
                  data-eligible="true"
                >
                  <div class="dispatch-option-row">
                    <span>
                      {{ item.full_name }} ({{ item.username }})
                      <small style="color: #909399">
                        {{ item.active_dispatch_count }}/{{ item.max_active_dispatch }}
                      </small>
                    </span>
                    <span>
                      <el-tag
                        v-if="idx === 0"
                        :data-testid="`dispatch-recommended-tag-${item.username}`"
                        type="success"
                        size="small"
                        disable-transitions
                        style="margin-right: 4px"
                      >
                        推荐
                      </el-tag>
                      <small>{{ formatConfidence(item.recommend_score) }}</small>
                    </span>
                  </div>
                </el-option>

                <el-option
                  v-for="ex in candidatesData.excluded"
                  :key="`excl-${ex.account_id}`"
                  :value="ex.account_id"
                  :label="`${ex.full_name} (${ex.username}) - ${getNotEligibleReasonText(ex.reason)}`"
                  :disabled="true"
                  :data-testid="`dispatch-assignee-option-${ex.username}`"
                  data-eligible="false"
                  :data-reason="ex.reason"
                >
                  <div class="dispatch-option-row">
                    <span>
                      {{ ex.full_name }} ({{ ex.username }})
                      <small style="color: #909399">
                        {{ ex.active_dispatch_count }}/{{ ex.max_active_dispatch }}
                      </small>
                    </span>
                    <span style="color: var(--tomori-danger); font-size: 12px">
                      {{ getNotEligibleReasonText(ex.reason) }}
                    </span>
                  </div>
                </el-option>
              </el-select>
            </div>

            <div v-if="candidatesData.excluded.length > 0" class="excluded-box">
              <div style="font-weight: 600; margin-bottom: 4px">已排除人员及原因：</div>
              <div v-for="ex in candidatesData.excluded" :key="`excl-txt-${ex.account_id}`">
                • {{ ex.full_name }} ({{ ex.username }})：{{ getNotEligibleReasonText(ex.reason) }}
                [{{ ex.active_dispatch_count }}/{{ ex.max_active_dispatch }}]
              </div>
            </div>

            <div v-if="isReassignMode" style="margin-top: 10px">
              <label style="display: block">
                <div class="field-label">改派理由（必填）</div>
                <textarea v-model="reassignReason" class="tomori-textarea" placeholder="请输入改派原因" />
              </label>
              <label class="tomori-checkbox-label" style="margin-top: 8px">
                <input v-model="reassignEscalate" type="checkbox" class="tomori-checkbox" />
                <span style="font-size: 12px">将原派工标记为已升级 (ESCALATED)</span>
              </label>
            </div>
            <label v-else style="display: block; margin-top: 10px">
              <div class="field-label">派工备注（可选）</div>
              <input v-model="dispatchRemark" class="tomori-input" placeholder="派工要求或现场提示" />
            </label>

            <button
              type="button"
              class="tomori-btn tomori-btn--success"
              style="width: 100%; margin-top: 12px"
              data-testid="dispatch-confirm-btn"
              :disabled="!canDispatch"
              @click="handleConfirmDispatch"
            >
              {{ dispatchSubmit.isSubmitting.value ? '提交中...' : isReassignMode ? '确认改派' : '确认派工' }}
            </button>
          </div>

          <div class="quick-links">
            <router-link :to="`/tickets/${selectedTicket.id}`" class="quick-link">完整工单详情</router-link>
            <router-link :to="`/tickets/${selectedTicket.id}`" class="quick-link">手工调级 (Regrade)</router-link>
            <router-link :to="`/tickets/${selectedTicket.id}`" class="quick-link">人工复核裁决</router-link>
          </div>
        </div>
        <div v-else class="tomori-card hint-text">请在左侧队列选择一张工单进行派工调度。</div>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.hint-text {
  font-size: 13px;
  color: var(--tomori-text-muted);
}

.queue-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.queue-row {
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 10px 12px;
  cursor: pointer;
  transition: border-color 0.2s, background-color 0.2s;
}

.queue-row:hover {
  border-color: #93c5fd;
}

.queue-row.is-selected {
  border-color: var(--tomori-primary);
  background: #eff6ff;
}

.queue-row.is-overdue {
  border-left: 4px solid var(--tomori-danger);
}

.queue-sub {
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--tomori-text-secondary);
}

.sla-meta-box {
  background: #f8fafc;
  border-radius: 4px;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--tomori-text-secondary);
}

.empty-notice {
  background: #fef0f0;
  border: 1px solid #fde2e2;
  color: var(--tomori-danger);
  border-radius: 4px;
  padding: 8px 10px;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 10px;
}

.excluded-box {
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 4px;
  padding: 8px 10px;
  font-size: 12px;
  color: #991b1b;
  line-height: 1.6;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--tomori-text-secondary);
}

.quick-links {
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid #ebeef5;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.quick-link {
  font-size: 12px;
  color: var(--tomori-primary);
  text-decoration: none;
}

.quick-link:hover {
  text-decoration: underline;
}

.dispatch-option-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

@media (max-width: 767px) {
  .dispatch-option-row {
    flex-wrap: wrap;
    gap: 4px 8px;
  }
}
</style>
