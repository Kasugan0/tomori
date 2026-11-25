<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { listDispatchesApi, listRepairProcessesApi } from '@/api/dispatches'
import { getTicketDetailApi, uploadTicketEvidencesApi } from '@/api/tickets'
import AuthImage from '@/components/AuthImage.vue'
import PriorityTag from '@/components/PriorityTag.vue'
import StatusTag from '@/components/StatusTag.vue'
import { isApiError } from '@/shared/api'
import {
  FAULT_TYPE_LABELS,
  MATERIAL_UNITS,
  SAFETY_CHECKLIST_ITEMS,
} from '@/shared/enums'
import {
  formatDateTime,
  formatFileSize,
  formatOverdueDuration,
  formatRepairDeadline,
  isDeadlinePast,
} from '@/shared/format'
import { handleIdempotentError, useIdempotentSubmit } from '@/shared/idempotency'
import type {
  DispatchRecordOut,
  FaultTicketDetailOut,
  MaterialUnit,
  RepairProcessOut,
  SafetyChecklistPayload,
} from '@/shared/types'
import { validateStaticImageFiles } from '@/shared/upload'
import { useDispatchesStore } from '@/stores/dispatches'
import { useErrorBannerStore } from '@/stores/errorBanner'

interface MaterialDraftRow {
  material_code: string
  material_name: string
  quantity: number
  unit: MaterialUnit
}

const route = useRoute()
const router = useRouter()
const dispatchesStore = useDispatchesStore()
const bannerStore = useErrorBannerStore()

const dispatchId = computed<number>(() => Number(route.params.dispatchId))

const dispatch = ref<DispatchRecordOut | null>(null)
const ticketDetail = ref<FaultTicketDetailOut | null>(null)
const repairProcesses = ref<RepairProcessOut[]>([])
const loading = ref(false)
const loadError = ref<string | null>(null)
const actionSuccessMessage = ref<string | null>(null)

// 接单 / 拒单
const acceptSubmit = useIdempotentSubmit()
const rejectSubmit = useIdempotentSubmit()
const rejectBoxVisible = ref(false)
const rejectReason = ref('')

// 到场打卡
const arrivedAt = ref<string | null>(null)

// 电气安全检查六件套（严禁默认勾选或批量全选）
const electricalSafetyChecked = ref(false)
const checklist = reactive<SafetyChecklistPayload>({
  power_isolated: false,
  voltage_verified_zero: false,
  insulation_intact: false,
  grounding_connected: false,
  ppe_worn: false,
})
const safetyNote = ref('')
const missingSafetyItems = ref<string[]>([])

// 维修过程与耗材
const rootCause = ref('')
const repairSummary = ref('')
const materials = ref<MaterialDraftRow[]>([])

// 维修照片上传（1..10 张 <= 15 MiB）
const pendingRepairFiles = ref<File[]>([])
const repairPhotoError = ref<string | null>(null)
const repairPhotoUploading = ref(false)
const repairFileInputRef = ref<HTMLInputElement | null>(null)

// 完工提交与异常升级
const completeSubmit = useIdempotentSubmit()
const escalateDialogVisible = ref(false)
const escalateReason = ref('')
const escalateSubmitting = ref(false)

const isAccepted = computed(() => dispatch.value?.status === 'ACCEPTED')

const existingProcess = computed<RepairProcessOut | null>(() => {
  if (!dispatch.value) return null
  return repairProcesses.value.find((p) => p.dispatch_record_id === dispatch.value?.id) ?? null
})

const allFiveChecked = computed(() =>
  SAFETY_CHECKLIST_ITEMS.every((item) => checklist[item.key] === true),
)

function hasMaterialRowError(row: MaterialDraftRow): string | null {
  if (row.material_name.trim().length === 0) return '请填写材料名称'
  if (typeof row.quantity !== 'number' || !Number.isFinite(row.quantity) || row.quantity <= 0) {
    return '耗材数量必须大于 0'
  }
  return null
}

const hasDuplicateManualMaterialCodes = computed(() => {
  const seen = new Set<string>()
  for (const row of materials.value) {
    const code = row.material_code.trim()
    if (!code) continue
    if (seen.has(code)) return true
    seen.add(code)
  }
  return false
})

const materialsValid = computed(() => {
  if (hasDuplicateManualMaterialCodes.value) return false
  return materials.value.every((row) => hasMaterialRowError(row) === null)
})

const canSubmitComplete = computed(() => {
  if (!isAccepted.value) return false
  if (!arrivedAt.value) return false
  if (!electricalSafetyChecked.value) return false
  if (!allFiveChecked.value) return false
  if (safetyNote.value.trim().length === 0) return false
  if (repairSummary.value.trim().length === 0) return false
  if (!materialsValid.value) return false
  if (repairPhotoUploading.value || completeSubmit.isSubmitting.value) return false
  return true
})

const completeDisabledReasons = computed<string[]>(() => {
  if (!isAccepted.value) return ['需先点击接单后方可提交完工']
  const reasons: string[] = []
  if (!arrivedAt.value) reasons.push('需先完成到场打卡')
  if (!electricalSafetyChecked.value) reasons.push('需勾选电气安全检查总开关')
  if (!allFiveChecked.value) reasons.push('需逐项勾选全部 5 项电气安全检查')
  if (safetyNote.value.trim().length === 0) reasons.push('需填写安全检查说明')
  if (repairSummary.value.trim().length === 0) reasons.push('需填写维修过程描述')
  if (!materialsValid.value) reasons.push('请修正耗材行名称/数量/重复编码')
  return reasons
})

const isOverdue = computed(() => {
  const d = dispatch.value
  if (!d) return false
  if (d.status !== 'PENDING_ACCEPT' && d.status !== 'ACCEPTED') return false
  if (isDeadlinePast(d.repair_deadline_at)) return true
  if (d.status === 'PENDING_ACCEPT' && isDeadlinePast(d.accept_deadline_at)) return true
  return false
})

async function confirmDialog(
  message: string,
  title: string,
  confirmButtonText = '确认',
): Promise<boolean> {
  try {
    await ElMessageBox.confirm(message, title, {
      confirmButtonText,
      cancelButtonText: '取消',
      type: 'warning',
    })
    return true
  } catch {
    return false
  }
}

function resetLocalState(): void {
  dispatch.value = null
  ticketDetail.value = null
  repairProcesses.value = []
  loadError.value = null
  actionSuccessMessage.value = null
  rejectBoxVisible.value = false
  rejectReason.value = ''
  arrivedAt.value = null
  electricalSafetyChecked.value = false
  checklist.power_isolated = false
  checklist.voltage_verified_zero = false
  checklist.insulation_intact = false
  checklist.grounding_connected = false
  checklist.ppe_worn = false
  safetyNote.value = ''
  missingSafetyItems.value = []
  rootCause.value = ''
  repairSummary.value = ''
  materials.value = []
  pendingRepairFiles.value = []
  repairPhotoError.value = null
  escalateDialogVisible.value = false
  escalateReason.value = ''
  acceptSubmit.resetKey()
  rejectSubmit.resetKey()
  completeSubmit.resetKey()
}

async function resolveDispatchRecord(id: number): Promise<DispatchRecordOut | null> {
  const cached = dispatchesStore.myDispatches.find((item) => item.id === id)
  if (cached) return cached

  await dispatchesStore.fetchMine({ page_size: 100 })
  const fromMine = dispatchesStore.myDispatches.find((item) => item.id === id)
  if (fromMine) return fromMine

  let page = 2
  while (page <= 5) {
    const res = await listDispatchesApi({ page, page_size: 100 })
    const found = res.items.find((item) => item.id === id)
    if (found) return found
    if (res.items.length < 100) break
    page += 1
  }
  return null
}

async function loadDetail(): Promise<void> {
  if (!Number.isFinite(dispatchId.value) || dispatchId.value <= 0) {
    loadError.value = '无效的派工编号'
    return
  }

  loading.value = true
  loadError.value = null
  try {
    const found = await resolveDispatchRecord(dispatchId.value)
    if (!found) {
      loadError.value = '未找到对应的派工记录'
      dispatch.value = null
      return
    }
    dispatch.value = found

    const [tDetail, procRes] = await Promise.all([
      getTicketDetailApi(found.ticket_id),
      listRepairProcessesApi({ ticket_id: found.ticket_id }),
    ])
    ticketDetail.value = tDetail
    repairProcesses.value = Array.isArray(procRes) ? procRes : procRes.items
  } catch (err: unknown) {
    loadError.value = isApiError(err) ? err.error.message || '加载维修详情失败' : '加载维修详情失败'
  } finally {
    loading.value = false
  }
}

async function refreshAfterAction(): Promise<void> {
  const id = dispatchId.value
  const fresh = dispatchesStore.myDispatches.find((item) => item.id === id) ?? null
  if (fresh) {
    dispatch.value = fresh
  } else {
    const resolved = await resolveDispatchRecord(id)
    dispatch.value = resolved
  }

  const targetTicketId = dispatch.value?.ticket_id ?? ticketDetail.value?.id
  if (targetTicketId) {
    const [tDetail, procRes] = await Promise.all([
      getTicketDetailApi(targetTicketId),
      listRepairProcessesApi({ ticket_id: targetTicketId }),
    ])
    ticketDetail.value = tDetail
    repairProcesses.value = Array.isArray(procRes) ? procRes : procRes.items
  }
}

watch(rejectReason, () => {
  if (!rejectSubmit.isSubmitting.value) rejectSubmit.resetKey()
})

watch(
  [
    arrivedAt,
    electricalSafetyChecked,
    () => checklist.power_isolated,
    () => checklist.voltage_verified_zero,
    () => checklist.insulation_intact,
    () => checklist.grounding_connected,
    () => checklist.ppe_worn,
    safetyNote,
    rootCause,
    repairSummary,
    materials,
  ],
  () => {
    if (!completeSubmit.isSubmitting.value) completeSubmit.resetKey()
  },
  { deep: true },
)

watch(electricalSafetyChecked, (checked) => {
  if (checked) {
    missingSafetyItems.value = missingSafetyItems.value.filter(
      (k) => k !== 'electrical_safety_checked',
    )
  }
})

watch(
  () => ({ ...checklist }),
  (val) => {
    missingSafetyItems.value = missingSafetyItems.value.filter(
      (k) => !(k in val) || !val[k as keyof SafetyChecklistPayload],
    )
  },
)

watch(safetyNote, (val) => {
  if (val.trim().length > 0) {
    missingSafetyItems.value = missingSafetyItems.value.filter((k) => k !== 'safety_note')
  }
})

watch(
  () => route.params.dispatchId,
  () => {
    resetLocalState()
    void loadDetail()
  },
  { immediate: true },
)

async function handleAccept(): Promise<void> {
  const d = dispatch.value
  if (!d || d.status !== 'PENDING_ACCEPT') return
  bannerStore.clear()
  actionSuccessMessage.value = null
  try {
    await acceptSubmit.run((key, onReplay) => dispatchesStore.accept(d.id, key, onReplay))
    await refreshAfterAction()
    actionSuccessMessage.value = '接单成功，工单已进入维修中（IN_REPAIR）'
  } catch (err: unknown) {
    handleIdempotentError(err, acceptSubmit)
  }
}

async function handleReject(): Promise<void> {
  const d = dispatch.value
  if (!d || d.status !== 'PENDING_ACCEPT' || rejectReason.value.trim().length < 2) return
  const ok = await confirmDialog(
    '拒单后本单将退回待派工池等待管理员改派，是否确认拒单？',
    '确认拒单',
    '确认拒单',
  )
  if (!ok) return

  bannerStore.clear()
  actionSuccessMessage.value = null
  try {
    await rejectSubmit.run((key, onReplay) =>
      dispatchesStore.reject(d.id, rejectReason.value.trim(), key, onReplay),
    )
    rejectBoxVisible.value = false
    rejectReason.value = ''
    await refreshAfterAction()
    actionSuccessMessage.value = '已拒单（REJECTED），工单已退回待派工'
  } catch (err: unknown) {
    handleIdempotentError(err, rejectSubmit)
  }
}

function handleArrivalCheckin(): void {
  if (!isAccepted.value) return
  if (!arrivedAt.value) {
    arrivedAt.value = new Date().toISOString()
  }
}

function addMaterial(): void {
  if (!isAccepted.value) return
  materials.value.push({
    material_code: '',
    material_name: '',
    quantity: 1,
    unit: 'PCS',
  })
}

function removeMaterial(idx: number): void {
  if (!isAccepted.value) return
  materials.value = materials.value.filter((_, i) => i !== idx)
}

async function onRepairFilesChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement | null
  const files = input?.files ? Array.from(input.files) : []
  repairPhotoError.value = null
  if (files.length === 0) return

  const combined = [...pendingRepairFiles.value, ...files]
  const validation = await validateStaticImageFiles(combined)
  if (!validation.valid) {
    repairPhotoError.value = validation.message || '图片校验未通过'
    if (input) input.value = ''
    return
  }

  pendingRepairFiles.value = combined
  if (input) input.value = ''
}

function removePendingRepairFile(idx: number): void {
  pendingRepairFiles.value = pendingRepairFiles.value.filter((_, i) => i !== idx)
  repairPhotoError.value = null
}

async function uploadPendingRepairPhotos(): Promise<void> {
  const d = dispatch.value
  if (!d || pendingRepairFiles.value.length === 0) return
  const validation = await validateStaticImageFiles(pendingRepairFiles.value)
  if (!validation.valid) {
    repairPhotoError.value = validation.message || '图片校验未通过'
    throw new Error(validation.code || 'VALIDATION_ERROR')
  }
  repairPhotoUploading.value = true
  try {
    await uploadTicketEvidencesApi(d.ticket_id, pendingRepairFiles.value, 'REPAIR_PHOTO')
    pendingRepairFiles.value = []
    if (repairFileInputRef.value) {
      repairFileInputRef.value.value = ''
    }
  } finally {
    repairPhotoUploading.value = false
  }
}

async function handleManualUploadRepairPhotos(): Promise<void> {
  if (!isAccepted.value || pendingRepairFiles.value.length === 0) return
  bannerStore.clear()
  try {
    await uploadPendingRepairPhotos()
    await refreshAfterAction()
    actionSuccessMessage.value = '维修照片上传成功'
  } catch {
    // 校验错误或全局横幅处理
  }
}

async function handleComplete(): Promise<void> {
  const d = dispatch.value
  if (!d || !canSubmitComplete.value || !arrivedAt.value) return
  const currentArrivedAt = arrivedAt.value

  bannerStore.clear()
  actionSuccessMessage.value = null
  missingSafetyItems.value = []

  try {
    if (pendingRepairFiles.value.length > 0) {
      await uploadPendingRepairPhotos()
    }

    const res = await completeSubmit.run((key, onReplay) =>
      dispatchesStore.complete(
        d.id,
        {
          electrical_safety_checked: electricalSafetyChecked.value,
          safety_checklist: {
            power_isolated: checklist.power_isolated,
            voltage_verified_zero: checklist.voltage_verified_zero,
            insulation_intact: checklist.insulation_intact,
            grounding_connected: checklist.grounding_connected,
            ppe_worn: checklist.ppe_worn,
          },
          safety_note: safetyNote.value.trim(),
          arrived_at: currentArrivedAt,
          root_cause: rootCause.value.trim() || null,
          repair_summary: repairSummary.value.trim(),
          materials: materials.value.map((mat, idx) => ({
            material_code: mat.material_code.trim() || `MAT-${idx + 1}`,
            material_name: mat.material_name.trim(),
            quantity: Number(mat.quantity),
            unit: mat.unit,
          })),
        },
        key,
        onReplay,
      ),
    )

    await refreshAfterAction()
    const updatedDispatch = res.dispatch
    actionSuccessMessage.value = `维修完工已提交（派工状态：${updatedDispatch?.status ?? 'COMPLETED'}，工单状态：${res.ticket.status}）`
  } catch (err: unknown) {
    if (isApiError(err) && err.error.code === 'ELECTRICAL_SAFETY_CHECK_REQUIRED') {
      const rawMissing = err.error.details?.missing_items
      missingSafetyItems.value = Array.isArray(rawMissing) ? rawMissing.map(String) : []
    }
    handleIdempotentError(err, completeSubmit)
  }
}

function openEscalateDialog(): void {
  if (!isAccepted.value) return
  escalateReason.value = ''
  escalateDialogVisible.value = true
}

async function handleEscalateSubmit(): Promise<void> {
  const d = dispatch.value
  if (!d || !isAccepted.value || escalateReason.value.trim().length === 0) return

  bannerStore.clear()
  actionSuccessMessage.value = null
  escalateSubmitting.value = true
  try {
    await dispatchesStore.escalate(d.id, escalateReason.value.trim())
    escalateDialogVisible.value = false
    escalateReason.value = ''
    await refreshAfterAction()
    actionSuccessMessage.value = '已提交异常升级，工单已进入管理员人工复核（MANUAL_REVIEW）'
  } catch {
    // 全局错误横幅处理
  } finally {
    escalateSubmitting.value = false
  }
}
</script>

<template>
  <div class="repair-detail-page">
    <div class="tomori-page-header">
      <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
        <button type="button" class="tomori-btn" @click="router.push('/repairs')">
          返回维修待办
        </button>
        <h1 class="tomori-page-title">维修处置详情</h1>
      </div>
      <button type="button" class="tomori-btn" :disabled="loading" @click="loadDetail">
        刷新
      </button>
    </div>

    <el-alert
      v-if="actionSuccessMessage"
      type="success"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    >
      {{ actionSuccessMessage }}
    </el-alert>

    <div v-if="loading && !dispatch" class="tomori-card state-box">
      正在加载维修任务...
    </div>

    <div v-else-if="loadError && !dispatch" class="tomori-card state-box">
      <div style="margin-bottom: 12px">{{ loadError }}</div>
      <button type="button" class="tomori-btn" @click="loadDetail">重新加载</button>
    </div>

    <template v-else-if="dispatch">
      <!-- 派工与工单摘要 -->
      <section class="tomori-card" :class="{ 'is-overdue-card': isOverdue }">
        <div class="detail-header-row">
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
            <strong class="cell-mono">{{ dispatch.ticket_no || ticketDetail?.ticket_no || `工单 #${dispatch.ticket_id}` }}</strong>
            <PriorityTag :priority="dispatch.ticket_priority ?? ticketDetail?.priority ?? null" />
            <StatusTag :status="dispatch.status" kind="dispatch" />
            <StatusTag v-if="ticketDetail" :status="ticketDetail.status" />
          </div>
          <span class="cell-sub">派工单 #{{ dispatch.id }}</span>
        </div>

        <h2 class="detail-title">{{ dispatch.ticket_title || ticketDetail?.title || '故障维修任务' }}</h2>
        <p v-if="ticketDetail?.description" class="detail-desc">{{ ticketDetail.description }}</p>

        <div class="meta-grid">
          <div class="meta-item">
            <span class="meta-label">故障类型</span>
            <span>{{ FAULT_TYPE_LABELS[dispatch.ticket_fault_type ?? ticketDetail?.fault_type ?? 'OTHER'] }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">点位 / 区域</span>
            <span>
              {{ dispatch.point_name || ticketDetail?.point_name || dispatch.point_code || `点位 #${ticketDetail?.lighting_point_id ?? ''}` }}
              · {{ dispatch.region_name || ticketDetail?.region_name || `区域 #${dispatch.region_id}` }}
            </span>
          </div>
          <div class="meta-item">
            <span class="meta-label">接单截止</span>
            <span>{{ formatDateTime(dispatch.accept_deadline_at) }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">法定 48h 修复截止</span>
            <span :class="{ 'overdue-text': isOverdue }">
              {{ formatRepairDeadline(dispatch.repair_deadline_at ?? ticketDetail?.repair_deadline_at, dispatch.ticket_status ?? ticketDetail?.status) }}
              <span v-if="isOverdue && (dispatch.repair_deadline_at ?? ticketDetail?.repair_deadline_at)">
                （{{ formatOverdueDuration(dispatch.repair_deadline_at ?? ticketDetail?.repair_deadline_at) }}）
              </span>
            </span>
          </div>
        </div>

        <!-- 巡查报障现场照片参照 -->
        <div v-if="ticketDetail && ticketDetail.evidences.length > 0" class="ref-evidences">
          <div class="meta-label" style="margin-bottom: 6px">现场报障影像参照</div>
          <div class="ref-evidences-grid">
            <div v-for="ev in ticketDetail.evidences" :key="ev.id" class="ref-evidence-item">
              <AuthImage :evidence-id="ev.id" variant="raw" :alt="`现场照片 #${ev.id}`" aspect-ratio="4 / 3" />
            </div>
          </div>
        </div>
      </section>

      <!-- 接单 / 拒单操作区（PENDING_ACCEPT） -->
      <section v-if="dispatch.status === 'PENDING_ACCEPT'" class="tomori-card">
        <h3 class="section-heading">待接单处置</h3>
        <div class="action-buttons-row">
          <button
            type="button"
            data-testid="repair-accept-btn"
            class="tomori-btn tomori-btn--primary action-touch-btn"
            :disabled="acceptSubmit.isSubmitting.value || rejectSubmit.isSubmitting.value"
            @click="handleAccept"
          >
            {{ acceptSubmit.isSubmitting.value ? '接单中...' : '确认接单' }}
          </button>
          <button
            type="button"
            data-testid="repair-reject-btn"
            class="tomori-btn tomori-btn--danger action-touch-btn"
            :disabled="acceptSubmit.isSubmitting.value || rejectSubmit.isSubmitting.value"
            @click="rejectBoxVisible = !rejectBoxVisible"
          >
            拒单退回
          </button>
        </div>

        <div v-if="rejectBoxVisible" class="reject-panel">
          <label class="meta-label">拒单原因（至少 2 个字，必填）</label>
          <textarea
            v-model="rejectReason"
            data-testid="repair-reject-reason-input"
            class="tomori-textarea"
            placeholder="请填写拒单原因（必填）"
          />
          <div
            v-if="rejectReason.trim().length === 1"
            class="tomori-field-error"
          >
            拒单原因至少需要 2 个字符
          </div>
          <div style="margin-top: 10px">
            <button
              type="button"
              data-testid="repair-reject-confirm-btn"
              class="tomori-btn tomori-btn--danger action-touch-btn"
              :disabled="rejectReason.trim().length < 2 || rejectSubmit.isSubmitting.value"
              @click="handleReject"
            >
              {{ rejectSubmit.isSubmitting.value ? '提交拒单...' : '确认拒单' }}
            </button>
          </div>
        </div>
      </section>

      <!-- 到场打卡 -->
      <section class="tomori-card">
        <div class="checkin-row">
          <div>
            <h3 class="section-heading" style="margin-bottom: 4px">步骤 1：到场打卡</h3>
            <div class="cell-sub">
              <template v-if="arrivedAt">
                已登记到场时间：<strong>{{ formatDateTime(arrivedAt) }}</strong>
              </template>
              <template v-else-if="existingProcess">
                历史到场时间：{{ formatDateTime(existingProcess.arrived_at) }}
              </template>
              <template v-else>
                接单后抵达照明点位请先打卡记录到场时间
              </template>
            </div>
          </div>
          <button
            type="button"
            data-testid="repair-arrival-checkin-btn"
            class="tomori-btn tomori-btn--primary action-touch-btn"
            :disabled="dispatch.status !== 'ACCEPTED'"
            @click="handleArrivalCheckin"
          >
            {{ arrivedAt ? '已到场打卡' : '到场打卡' }}
          </button>
        </div>
      </section>

      <!-- 电气安全检查六件套硬门禁（原生 checkbox） -->
      <el-card data-testid="repair-safety-block" class="safety-card">
        <template #header>
          <div style="font-weight: 600; font-size: 15px">
            步骤 2：现场断电与电气安全检查确认（必填硬门禁）
          </div>
        </template>

        <el-alert type="warning" :closable="false" show-icon style="margin-bottom: 14px">
          AI 识别不能替代现场断电与电气安全检查，必须逐项人工确认
        </el-alert>

        <!-- 总开关 -->
        <div
          class="safety-master-row"
          :class="{ 'is-missing-error': missingSafetyItems.includes('electrical_safety_checked') }"
        >
          <label class="tomori-checkbox-label" :class="{ 'is-disabled': !isAccepted }">
            <input
              v-model="electricalSafetyChecked"
              type="checkbox"
              class="tomori-checkbox"
              data-testid="repair-safety-checkbox"
              :disabled="!isAccepted"
            />
            <strong>已完成现场断电隔离与安全准备（勾选后方可逐项核验下列 5 项）</strong>
          </label>
        </div>

        <!-- 5 项逐条核验 -->
        <div class="safety-items-list">
          <div
            v-for="item in SAFETY_CHECKLIST_ITEMS"
            :key="item.key"
            class="safety-item-row"
            :class="{ 'is-missing-error': missingSafetyItems.includes(item.key) }"
          >
            <label
              class="tomori-checkbox-label"
              :class="{ 'is-disabled': !isAccepted || !electricalSafetyChecked }"
            >
              <input
                v-model="checklist[item.key]"
                type="checkbox"
                class="tomori-checkbox"
                :data-testid="item.testid"
                :disabled="!isAccepted || !electricalSafetyChecked"
              />
              <span>{{ item.label }}</span>
            </label>
          </div>
        </div>

        <!-- 安全检查说明 -->
        <div
          class="safety-note-wrap"
          :class="{ 'is-missing-error': missingSafetyItems.includes('safety_note') }"
        >
          <label class="meta-label">现场断电与验电安全说明（必填）</label>
          <textarea
            v-model="safetyNote"
            data-testid="repair-safety-note-input"
            class="tomori-textarea"
            :disabled="!isAccepted"
            placeholder="现场断电与验电安全说明（必填）"
          />
        </div>
      </el-card>

      <!-- 维修过程、耗材与完工照片 -->
      <section class="tomori-card">
        <h3 class="section-heading">步骤 3：维修处置、耗材与现场完工照片</h3>

        <div class="form-field">
          <label class="meta-label">故障根因（可选）</label>
          <input
            v-model="rootCause"
            type="text"
            data-testid="repair-root-cause-input"
            class="tomori-input"
            :disabled="!isAccepted"
            placeholder="故障根因（如：驱动电源烧毁）"
          />
        </div>

        <div class="form-field" style="margin-top: 12px">
          <label class="meta-label">维修过程与处置描述（必填）</label>
          <textarea
            v-model="repairSummary"
            data-testid="repair-process-note-input"
            class="tomori-textarea"
            :disabled="!isAccepted"
            placeholder="维修过程与处置描述（必填）"
          />
        </div>

        <!-- 耗材明细 -->
        <div class="materials-block">
          <div class="materials-head">
            <span class="meta-label" style="font-weight: 600; color: var(--tomori-text)">
              耗材使用明细（数量必须 &gt; 0）
            </span>
            <button
              type="button"
              data-testid="repair-material-add-btn"
              class="tomori-btn"
              style="padding: 6px 12px; font-size: 13px"
              :disabled="!isAccepted"
              @click="addMaterial"
            >
              + 添加耗材
            </button>
          </div>

          <div v-if="hasDuplicateManualMaterialCodes" class="tomori-field-error">
            同一维修单内手工填写的耗材编码不能重复
          </div>

          <div v-if="materials.length === 0" class="cell-sub" style="margin-top: 6px">
            如本次维修未使用耗材，可直接留空；如需登记请点击「添加耗材」。
          </div>

          <div v-for="(mat, idx) in materials" :key="idx" class="material-row-card">
            <div class="material-row-grid">
              <div class="form-field">
                <label class="meta-label">耗材编码（可选）</label>
                <input
                  v-model="mat.material_code"
                  type="text"
                  :data-testid="`repair-material-code-${idx}`"
                  class="tomori-input"
                  :disabled="!isAccepted"
                  placeholder="编码(如 LED-DRV-150)"
                />
              </div>

              <div class="form-field">
                <label class="meta-label">材料名称（必填）</label>
                <input
                  v-model="mat.material_name"
                  type="text"
                  :data-testid="`repair-material-name-${idx}`"
                  class="tomori-input"
                  :disabled="!isAccepted"
                  placeholder="材料名称"
                />
              </div>

              <div class="form-field">
                <label class="meta-label">数量（&gt; 0）</label>
                <input
                  v-model.number="mat.quantity"
                  type="number"
                  min="0.01"
                  step="0.01"
                  :data-testid="`repair-material-qty-${idx}`"
                  class="tomori-input"
                  :disabled="!isAccepted"
                />
              </div>

              <div class="form-field">
                <label class="meta-label">单位</label>
                <select
                  v-model="mat.unit"
                  :data-testid="`repair-material-unit-${idx}`"
                  class="tomori-select"
                  :disabled="!isAccepted"
                >
                  <option v-for="u in MATERIAL_UNITS" :key="u.value" :value="u.value">
                    {{ u.label }}
                  </option>
                </select>
              </div>

              <div class="material-remove-wrap">
                <button
                  type="button"
                  :data-testid="`repair-material-remove-${idx}`"
                  class="tomori-btn tomori-btn--danger"
                  style="padding: 8px 12px"
                  :disabled="!isAccepted"
                  @click="removeMaterial(idx)"
                >
                  删除
                </button>
              </div>
            </div>
            <div v-if="hasMaterialRowError(mat)" class="tomori-field-error">
              {{ hasMaterialRowError(mat) }}
            </div>
          </div>
        </div>

        <!-- 维修后照片上传（1..10 张 <= 15 MiB） -->
        <div class="upload-box">
          <label class="meta-label">可选上传维修完工照片（1–10 张静态 JPEG/PNG/WebP，单张 ≤ 15 MiB）</label>
          <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 6px">
            <input
              ref="repairFileInputRef"
              type="file"
              multiple
              accept="image/jpeg,image/png,image/webp"
              :disabled="!isAccepted"
              @change="onRepairFilesChange"
            />
            <button
              v-if="pendingRepairFiles.length > 0"
              type="button"
              class="tomori-btn"
              :disabled="!isAccepted || repairPhotoUploading"
              @click="handleManualUploadRepairPhotos"
            >
              {{ repairPhotoUploading ? '上传中...' : `上传维修照片 (${pendingRepairFiles.length} 张)` }}
            </button>
          </div>
          <div v-if="repairPhotoError" class="tomori-field-error">{{ repairPhotoError }}</div>
          <ul v-if="pendingRepairFiles.length > 0" class="pending-file-list">
            <li v-for="(file, idx) in pendingRepairFiles" :key="`${file.name}-${idx}`" class="pending-file-item">
              <span>{{ file.name }}（{{ formatFileSize(file.size) }}）</span>
              <button
                type="button"
                class="tomori-btn"
                style="padding: 2px 8px; font-size: 12px"
                :disabled="!isAccepted"
                @click="removePendingRepairFile(idx)"
              >
                移除
              </button>
            </li>
          </ul>
        </div>

        <!-- 硬门禁提交与异常升级 -->
        <div v-if="completeDisabledReasons.length > 0 && isAccepted" class="gate-hints-box">
          待满足以下门禁条件后方可提交完工：{{ completeDisabledReasons.join('；') }}
        </div>

        <div class="submit-footer-bar">
          <button
            type="button"
            data-testid="repair-complete-submit-btn"
            class="tomori-btn tomori-btn--success action-touch-btn"
            :disabled="!canSubmitComplete"
            @click="handleComplete"
          >
            {{ completeSubmit.isSubmitting.value ? '提交完工中...' : '提交维修完工（进入待验收）' }}
          </button>
          <button
            type="button"
            data-testid="repair-escalate-btn"
            class="tomori-btn tomori-btn--warning action-touch-btn"
            :disabled="!isAccepted"
            @click="openEscalateDialog"
          >
            无法现场处置 / 异常升级
          </button>
        </div>
      </section>

      <!-- 已提交完工记录回显 -->
      <section v-if="existingProcess" class="tomori-card">
        <h3 class="section-heading">本次完工记录（#{{ existingProcess.id }}）</h3>
        <div class="meta-grid">
          <div class="meta-item">
            <span class="meta-label">到场时间</span>
            <span>{{ formatDateTime(existingProcess.arrived_at) }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">电气安全核验</span>
            <span>{{ existingProcess.electrical_safety_checked ? '全部 6 件套已确认' : '未完成' }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">安全说明</span>
            <span>{{ existingProcess.safety_note }}</span>
          </div>
          <div v-if="existingProcess.root_cause" class="meta-item">
            <span class="meta-label">故障根因</span>
            <span>{{ existingProcess.root_cause }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">维修总结</span>
            <span>{{ existingProcess.repair_summary }}</span>
          </div>
        </div>
      </section>

      <!-- 异常升级弹窗 (:teleported="false") -->
      <el-dialog
        v-model="escalateDialogVisible"
        title="异常升级（转管理员人工复核）"
        width="440px"
        :teleported="false"
      >
        <el-alert type="warning" :closable="false" show-icon style="margin-bottom: 12px">
          提交后本派工单将标记为 ESCALATED，工单进入 MANUAL_REVIEW 由管理员裁决。
        </el-alert>
        <div class="form-field">
          <label class="meta-label">异常升级原因（必填）</label>
          <textarea
            v-model="escalateReason"
            class="tomori-textarea"
            placeholder="请说明无法安全作业、缺特种备件或需封路停电等具体原因"
          />
        </div>
        <template #footer>
          <button type="button" class="tomori-btn" @click="escalateDialogVisible = false">取消</button>
          <button
            type="button"
            class="tomori-btn tomori-btn--warning"
            :disabled="escalateReason.trim().length === 0 || escalateSubmitting"
            @click="handleEscalateSubmit"
          >
            {{ escalateSubmitting ? '提交中...' : '确认异常升级' }}
          </button>
        </template>
      </el-dialog>
    </template>
  </div>
</template>

<style scoped>
.state-box {
  padding: 28px 16px;
  text-align: center;
  color: var(--tomori-text-secondary);
}

.is-overdue-card {
  border: 2px solid var(--tomori-danger);
}

.detail-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.detail-title {
  margin: 4px 0 6px;
  font-size: 17px;
  font-weight: 600;
}

.detail-desc {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--tomori-text-secondary);
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px 14px;
  font-size: 13px;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.meta-label {
  font-size: 12px;
  color: var(--tomori-text-muted);
}

.overdue-text {
  color: var(--tomori-danger);
  font-weight: 600;
}

.ref-evidences {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #ebeef5;
}

.ref-evidences-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 8px;
}

.ref-evidence-item {
  border-radius: 4px;
  overflow: hidden;
}

.section-heading {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
}

.action-buttons-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.action-touch-btn {
  min-height: 40px;
}

.reject-panel {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid #ebeef5;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.checkin-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.safety-card {
  margin-bottom: 16px;
  border: 2px solid #e6a23c;
}

.safety-master-row {
  padding: 10px 12px;
  border: 1px solid #faecd8;
  background: #fdf6ec;
  border-radius: 6px;
  margin-bottom: 12px;
}

.safety-items-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 14px;
}

.safety-item-row {
  padding: 8px 12px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #ffffff;
}

.safety-note-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border-radius: 6px;
  border: 1px solid transparent;
}

.is-missing-error {
  border: 2px solid var(--tomori-danger) !important;
  background: #fef0f0 !important;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.materials-block {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid #ebeef5;
}

.materials-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.material-row-card {
  margin-top: 10px;
  padding: 10px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fafafa;
}

.material-row-grid {
  display: grid;
  grid-template-columns: 1fr 1.2fr 0.7fr 0.9fr auto;
  gap: 8px;
  align-items: end;
}

@media (max-width: 767px) {
  .material-row-grid {
    grid-template-columns: 1fr;
  }
}

.material-remove-wrap {
  display: flex;
  align-items: flex-end;
}

.upload-box {
  margin-top: 16px;
  padding: 10px 12px;
  border: 1px dashed var(--tomori-border);
  border-radius: 6px;
}

.pending-file-list {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.pending-file-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.gate-hints-box {
  margin-top: 14px;
  padding: 8px 12px;
  border-radius: 4px;
  background: #f4f4f5;
  color: var(--tomori-text-secondary);
  font-size: 12px;
}

.submit-footer-bar {
  margin-top: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.cell-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.cell-sub {
  font-size: 12px;
  color: var(--tomori-text-muted);
}
</style>
