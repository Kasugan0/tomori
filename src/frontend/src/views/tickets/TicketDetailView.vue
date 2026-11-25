<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { dispatchTicketApi, getDispatchCandidatesApi } from '@/api/dispatches'
import { uploadTicketEvidencesApi } from '@/api/tickets'
import AuthImage from '@/components/AuthImage.vue'
import PriorityTag from '@/components/PriorityTag.vue'
import StatusTag from '@/components/StatusTag.vue'
import { can } from '@/features/tickets/actions'
import {
  FAULT_TYPE_LABELS,
  getNotEligibleReasonText,
  MANUAL_NOTE_REQUIRED_FAULT_TYPES,
  PRIORITY_META,
  REVIEW_RESOLUTION_LABELS,
  ROLE_LABELS,
  SAFETY_CHECKLIST_ITEMS,
} from '@/shared/enums'
import { isApiError } from '@/shared/api'
import {
  formatConfidence,
  formatDateTime,
  formatFileSize,
  formatOverdueDuration,
  formatRepairDeadline,
  formatResponseDeadline,
  isDeadlinePast,
} from '@/shared/format'
import { handleIdempotentError, useIdempotentSubmit } from '@/shared/idempotency'
import type {
  AcceptanceDecision,
  DispatchCandidateItem,
  DispatchExcludedItem,
  FaultEvidenceOut,
  FaultTicketDetailOut,
  FaultType,
  PriorityCode,
  ReviewResolution,
  ReworkOrderOut,
} from '@/shared/types'
import { validateStaticImageFiles } from '@/shared/upload'
import { useAuthStore } from '@/stores/auth'
import { useErrorBannerStore } from '@/stores/errorBanner'
import { useTicketsStore } from '@/stores/tickets'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const ticketsStore = useTicketsStore()
const bannerStore = useErrorBannerStore()

const ticketId = computed<number>(() => Number(route.params.ticketId))
const ticket = computed<FaultTicketDetailOut | null>(() => {
  if (!ticketsStore.detail) return null
  return ticketsStore.detail.id === ticketId.value ? ticketsStore.detail : null
})

const currentUser = computed(() =>
  authStore.account
    ? { id: authStore.account.id, role_code: authStore.roleCode }
    : null,
)

const loadError = ref<string | null>(null)
const actionSuccessMessage = ref<string | null>(null)

// 权限裁决统一走 actions.ts can()
const canConfirmDraft = computed(() => can('confirm', ticket.value, currentUser.value))
const canCancelTicket = computed(() => can('cancel', ticket.value, currentUser.value))
const canRegradeTicket = computed(() => can('regrade', ticket.value, currentUser.value))
const canDispatchTicket = computed(() => can('dispatch', ticket.value, currentUser.value))
const canReassignTicket = computed(() => can('reassign', ticket.value, currentUser.value))
const canAcceptTicket = computed(() => can('acceptance', ticket.value, currentUser.value))
const canReviewResolve = computed(() => can('review-resolve', ticket.value, currentUser.value))

const isSlaOverdue = computed(() => {
  const t = ticket.value
  if (!t) return false
  if (t.is_sla_overdue) return true
  if (t.status === 'CLOSED' || t.status === 'CANCELLED') return false
  return isDeadlinePast(t.repair_deadline_at) || isDeadlinePast(t.response_deadline_at)
})

const overdueStage = computed<'RESPONSE' | 'REPAIR'>(() => {
  const t = ticket.value
  if (!t) return 'REPAIR'
  if (t.overdue_stage) return t.overdue_stage
  if (isDeadlinePast(t.response_deadline_at) && !isDeadlinePast(t.repair_deadline_at)) {
    return 'RESPONSE'
  }
  return 'REPAIR'
})

const hasReworkThresholdAlert = computed(() => {
  const t = ticket.value
  if (!t) return false
  return (
    t.status === 'MANUAL_REVIEW' ||
    t.rework_orders.some((rw) => rw.requires_manual_review)
  )
})

// AI 候选草稿编辑 / 确认
const confirmSubmit = useIdempotentSubmit()
const draftSaving = ref(false)
const draftTitle = ref('')
const draftFaultType = ref<FaultType>('LAMP_OUT')
const draftDescription = ref('')
const draftManualNote = ref('')
const draftSafetyFlag = ref(false)
const draftPriorityOverride = ref<PriorityCode | ''>('')

const draftRequiresManualNote = computed(() =>
  MANUAL_NOTE_REQUIRED_FAULT_TYPES.includes(draftFaultType.value),
)

const canSubmitDraftConfirm = computed(() => {
  if (!canConfirmDraft.value) return false
  if (confirmSubmit.isSubmitting.value || draftSaving.value) return false
  if (draftTitle.value.trim().length === 0) return false
  if (draftRequiresManualNote.value && draftManualNote.value.trim().length === 0) return false
  return true
})

// 撤销误报
const cancelSubmit = useIdempotentSubmit()
const cancelReason = ref('')
const cancelReasonInputRef = ref<HTMLTextAreaElement | null>(null)

// 管理员调级
const regradeSubmit = useIdempotentSubmit()
const regradeDialogVisible = ref(false)
const regradePriority = ref<PriorityCode>('P2')
const regradeReason = ref('')

// 派工 / 改派候选
const dispatchSubmit = useIdempotentSubmit()
const dispatchDialogVisible = ref(false)
const dispatchCandidateId = ref<number | null>(null)
const dispatchRemark = ref('')

const reassignSubmit = useIdempotentSubmit()
const reassignDialogVisible = ref(false)
const reassignCandidateId = ref<number | null>(null)
const reassignReason = ref('')
const reassignEscalate = ref(false)

const loadingCandidates = ref(false)
const candidateList = ref<DispatchCandidateItem[]>([])
const excludedList = ref<DispatchExcludedItem[]>([])

// 人工复核裁决
const resolveSubmit = useIdempotentSubmit()
const resolveResolution = ref<ReviewResolution | null>(null)
const resolveReason = ref('')
const resolveReasonInputRef = ref<HTMLTextAreaElement | null>(null)

const resolutionOptions: ReadonlyArray<{ value: ReviewResolution; label: string }> = [
  { value: 'APPROVE_REASSIGN', label: REVIEW_RESOLUTION_LABELS.APPROVE_REASSIGN },
  { value: 'SPECIAL_CLOSE', label: REVIEW_RESOLUTION_LABELS.SPECIAL_CLOSE },
  { value: 'TERMINATE_CANCEL', label: REVIEW_RESOLUTION_LABELS.TERMINATE_CANCEL },
]

const canSubmitResolve = computed(() => {
  if (!canReviewResolve.value) return false
  if (resolveSubmit.isSubmitting.value) return false
  if (resolveResolution.value === null) return false
  const trimmed = resolveReason.value.trim()
  if (trimmed.length === 0) return false
  if (resolveResolution.value === 'SPECIAL_CLOSE' && trimmed.length < 10) return false
  return true
})

// 验收 & 返修
const acceptSubmit = useIdempotentSubmit()
const acceptDecision = ref<AcceptanceDecision | null>(null)
const acceptComment = ref('')
const lastReworkCreated = ref<ReworkOrderOut | null>(null)
const pendingAcceptFiles = ref<File[]>([])
const uploadedAcceptEvidenceIds = ref<number[]>([])
const acceptPhotoError = ref<string | null>(null)
const acceptPhotoUploading = ref(false)
const acceptFileInputRef = ref<HTMLInputElement | null>(null)

const canSubmitAcceptance = computed(() => {
  if (!canAcceptTicket.value) return false
  if (acceptSubmit.isSubmitting.value || acceptPhotoUploading.value) return false
  if (acceptDecision.value === null) return false
  if (acceptDecision.value === 'REJECTED' && acceptComment.value.trim().length === 0) return false
  return true
})

// 证据分类对照
const preRepairEvidences = computed<FaultEvidenceOut[]>(() =>
  (ticket.value?.evidences ?? []).filter(
    (ev) => ev.evidence_type === 'INSPECTION_PHOTO' || ev.evidence_type === 'ANNOTATED_PHOTO',
  ),
)

const postRepairEvidences = computed<FaultEvidenceOut[]>(() =>
  (ticket.value?.evidences ?? []).filter(
    (ev) => ev.evidence_type === 'REPAIR_PHOTO' || ev.evidence_type === 'ACCEPTANCE_PHOTO',
  ),
)

const faultTypeOptions = computed(() =>
  (Object.keys(FAULT_TYPE_LABELS) as FaultType[]).map((code) => ({
    value: code,
    label: FAULT_TYPE_LABELS[code],
  })),
)

const priorityOptions = computed(() =>
  (Object.keys(PRIORITY_META) as PriorityCode[]).map((code) => ({
    value: code,
    label: `${PRIORITY_META[code].code} ${PRIORITY_META[code].label}`,
  })),
)

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

function syncDraftFromTicket(detail: FaultTicketDetailOut): void {
  draftTitle.value = detail.title
  draftFaultType.value = detail.fault_type
  draftDescription.value = detail.description ?? ''
  draftManualNote.value = ''
  draftSafetyFlag.value = false
  draftPriorityOverride.value = ''
}

function resetTransientForms(): void {
  loadError.value = null
  actionSuccessMessage.value = null
  cancelReason.value = ''
  cancelSubmit.resetKey()
  confirmSubmit.resetKey()
  regradeDialogVisible.value = false
  regradeReason.value = ''
  regradeSubmit.resetKey()
  dispatchDialogVisible.value = false
  dispatchCandidateId.value = null
  dispatchRemark.value = ''
  dispatchSubmit.resetKey()
  reassignDialogVisible.value = false
  reassignCandidateId.value = null
  reassignReason.value = ''
  reassignEscalate.value = false
  reassignSubmit.resetKey()
  resolveResolution.value = null
  resolveReason.value = ''
  resolveSubmit.resetKey()
  acceptDecision.value = null
  acceptComment.value = ''
  lastReworkCreated.value = null
  pendingAcceptFiles.value = []
  uploadedAcceptEvidenceIds.value = []
  acceptPhotoError.value = null
  acceptSubmit.resetKey()
}

async function loadDetail(): Promise<void> {
  if (!Number.isFinite(ticketId.value) || ticketId.value <= 0) {
    loadError.value = '无效的工单编号'
    return
  }
  loadError.value = null
  try {
    const detail = await ticketsStore.fetchDetail(ticketId.value)
    syncDraftFromTicket(detail)
  } catch (err: unknown) {
    if (isApiError(err)) {
      loadError.value = err.error.message || '加载工单详情失败'
    } else {
      loadError.value = '加载工单详情失败'
    }
  }
}

// 表单修改时重置幂等键，防止同键异体
watch([draftTitle, draftFaultType, draftDescription, draftManualNote, draftSafetyFlag, draftPriorityOverride], () => {
  if (!confirmSubmit.isSubmitting.value) confirmSubmit.resetKey()
})
watch(cancelReason, () => {
  if (!cancelSubmit.isSubmitting.value) cancelSubmit.resetKey()
})
watch([regradePriority, regradeReason], () => {
  if (!regradeSubmit.isSubmitting.value) regradeSubmit.resetKey()
})
watch([reassignCandidateId, reassignReason, reassignEscalate], () => {
  if (!reassignSubmit.isSubmitting.value) reassignSubmit.resetKey()
})
watch([resolveResolution, resolveReason], () => {
  if (!resolveSubmit.isSubmitting.value) resolveSubmit.resetKey()
})
watch([acceptDecision, acceptComment], () => {
  if (!acceptSubmit.isSubmitting.value) acceptSubmit.resetKey()
})

watch(
  () => route.params.ticketId,
  () => {
    resetTransientForms()
    void loadDetail()
  },
  { immediate: true },
)

async function handleSaveDraft(): Promise<void> {
  const t = ticket.value
  if (!t || !canConfirmDraft.value) return
  if (draftRequiresManualNote.value && draftManualNote.value.trim().length === 0) return
  bannerStore.clear()
  actionSuccessMessage.value = null
  draftSaving.value = true
  try {
    const detail = await ticketsStore.updateDraft(t.id, {
      title: draftTitle.value.trim(),
      fault_type: draftFaultType.value,
      description: draftDescription.value.trim() || null,
      manual_note: draftManualNote.value.trim() || null,
    })
    syncDraftFromTicket(detail)
    actionSuccessMessage.value = '草稿修改已保存'
  } catch {
    // 全局错误横幅处理
  } finally {
    draftSaving.value = false
  }
}

async function handleConfirmDraft(): Promise<void> {
  const t = ticket.value
  if (!t || !canSubmitDraftConfirm.value) return
  const ok = await confirmDialog(
    '确认后将按业务规则完成分级，并自确认时刻起算 48 小时法定修复期限，是否确认转入待派工？',
    '确认 AI 候选工单',
    '确认提交',
  )
  if (!ok) return

  bannerStore.clear()
  actionSuccessMessage.value = null
  try {
    const detail = await confirmSubmit.run((key, onReplay) =>
      ticketsStore.confirm(
        t.id,
        {
          title: draftTitle.value.trim(),
          fault_type: draftFaultType.value,
          description: draftDescription.value.trim() || null,
          safety_flag: draftSafetyFlag.value,
          manual_note: draftManualNote.value.trim() || null,
          priority_override: draftPriorityOverride.value || null,
        },
        key,
        onReplay,
      ),
    )
    syncDraftFromTicket(detail)
    actionSuccessMessage.value = '已确认工单并进入待派工（PENDING_DISPATCH）'
  } catch (err: unknown) {
    handleIdempotentError(err, confirmSubmit)
  }
}

function focusCancelReason(): void {
  if (!canCancelTicket.value) return
  void nextTick(() => {
    cancelReasonInputRef.value?.focus()
  })
}

async function handleCancelConfirm(): Promise<void> {
  const t = ticket.value
  if (!t || !canCancelTicket.value || cancelReason.value.trim().length === 0) return
  const ok = await confirmDialog(
    '撤销后该点位释放重复报障限制，可重新报障。确认撤销本工单？',
    '确认撤销误报',
    '确认撤销',
  )
  if (!ok) return

  bannerStore.clear()
  actionSuccessMessage.value = null
  try {
    await cancelSubmit.run((key, onReplay) =>
      ticketsStore.cancel(t.id, cancelReason.value.trim(), key, onReplay),
    )
    cancelReason.value = ''
    actionSuccessMessage.value = '工单已撤销（CANCELLED），点位防重限制已释放'
  } catch (err: unknown) {
    handleIdempotentError(err, cancelSubmit)
  }
}

function openRegradeDialog(): void {
  if (!ticket.value || !canRegradeTicket.value) return
  regradePriority.value = ticket.value.priority ?? 'P2'
  regradeReason.value = ''
  regradeDialogVisible.value = true
}

async function handleRegradeSubmit(): Promise<void> {
  const t = ticket.value
  if (!t || !canRegradeTicket.value || regradeReason.value.trim().length === 0) return
  bannerStore.clear()
  actionSuccessMessage.value = null
  try {
    await regradeSubmit.run((key, onReplay) =>
      ticketsStore.regrade(
        t.id,
        { priority: regradePriority.value, reason: regradeReason.value.trim() },
        key,
        onReplay,
      ),
    )
    regradeDialogVisible.value = false
    regradeReason.value = ''
    actionSuccessMessage.value = `优先级已调整为 ${regradePriority.value}（法定 48h 修复截止时间不变）`
  } catch (err: unknown) {
    handleIdempotentError(err, regradeSubmit)
  }
}

async function loadDispatchCandidates(id: number): Promise<void> {
  loadingCandidates.value = true
  candidateList.value = []
  excludedList.value = []
  try {
    const res = await getDispatchCandidatesApi(id)
    candidateList.value = res.candidates
    excludedList.value = res.excluded
  } catch {
    // 全局错误横幅处理
  } finally {
    loadingCandidates.value = false
  }
}

function openDispatchDialog(): void {
  const t = ticket.value
  if (!t || !canDispatchTicket.value) return
  dispatchCandidateId.value = null
  dispatchRemark.value = ''
  dispatchDialogVisible.value = true
  void loadDispatchCandidates(t.id)
}

async function handleDispatchSubmit(): Promise<void> {
  const t = ticket.value
  if (!t || !canDispatchTicket.value || dispatchCandidateId.value === null) return
  const candidate = candidateList.value.find((c) => c.account_id === dispatchCandidateId.value)
  if (!candidate) return

  bannerStore.clear()
  actionSuccessMessage.value = null
  try {
    await dispatchSubmit.run(async (key, onReplay) => {
      await dispatchTicketApi(
        t.id,
        {
          technician_account_id: candidate.account_id,
          scope_authorization_id: candidate.scope_authorization_id,
          dispatch_mode: 'MANUAL',
          remark: dispatchRemark.value.trim() || null,
        },
        key,
        onReplay,
      )
      return ticketsStore.fetchDetail(t.id)
    })
    dispatchDialogVisible.value = false
    actionSuccessMessage.value = `已派工给 ${candidate.full_name}（${candidate.username}）`
  } catch (err: unknown) {
    handleIdempotentError(err, dispatchSubmit)
  }
}

function openReassignDialog(): void {
  const t = ticket.value
  if (!t || !canReassignTicket.value) return
  reassignCandidateId.value = null
  reassignReason.value = ''
  reassignEscalate.value = isSlaOverdue.value
  reassignDialogVisible.value = true
  void loadDispatchCandidates(t.id)
}

async function handleReassignSubmit(): Promise<void> {
  const t = ticket.value
  if (!t || !canReassignTicket.value) return
  if (reassignCandidateId.value === null || reassignReason.value.trim().length === 0) return
  const candidate = candidateList.value.find((c) => c.account_id === reassignCandidateId.value)
  if (!candidate) return

  bannerStore.clear()
  actionSuccessMessage.value = null
  try {
    await reassignSubmit.run((key, onReplay) =>
      ticketsStore.reassign(
        t.id,
        {
          technician_account_id: candidate.account_id,
          scope_authorization_id: candidate.scope_authorization_id,
          reason: reassignReason.value.trim(),
          escalate: reassignEscalate.value,
        },
        key,
        onReplay,
      ),
    )
    reassignDialogVisible.value = false
    reassignReason.value = ''
    actionSuccessMessage.value = `已改派给 ${candidate.full_name}（${candidate.username}）`
  } catch (err: unknown) {
    handleIdempotentError(err, reassignSubmit)
  }
}

function focusResolveSection(): void {
  if (!canReviewResolve.value) return
  void nextTick(() => {
    resolveReasonInputRef.value?.focus()
  })
}

async function handleReviewResolve(): Promise<void> {
  const t = ticket.value
  if (!t || !canSubmitResolve.value || resolveResolution.value === null) return
  const chosen = resolveResolution.value
  const reasonText = resolveReason.value.trim()

  const ok = await confirmDialog(
    `确认执行人工复核裁决「${REVIEW_RESOLUTION_LABELS[chosen]}」？`,
    '确认复核裁决',
    '确认裁决',
  )
  if (!ok) return

  bannerStore.clear()
  actionSuccessMessage.value = null
  try {
    await resolveSubmit.run((key, onReplay) =>
      ticketsStore.reviewResolve(t.id, { resolution: chosen, reason: reasonText }, key, onReplay),
    )
    resolveResolution.value = null
    resolveReason.value = ''
    actionSuccessMessage.value = '管理员人工复核裁决已完成'
  } catch (err: unknown) {
    handleIdempotentError(err, resolveSubmit)
  }
}

function selectAcceptDecision(decision: AcceptanceDecision): void {
  if (!canAcceptTicket.value) return
  acceptDecision.value = decision
}

async function onAcceptanceFilesChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement | null
  const files = input?.files ? Array.from(input.files) : []
  acceptPhotoError.value = null
  if (files.length === 0) return

  const combined = [...pendingAcceptFiles.value, ...files]
  const validation = await validateStaticImageFiles(combined)
  if (!validation.valid) {
    acceptPhotoError.value = validation.message || '图片校验未通过'
    if (input) input.value = ''
    return
  }

  pendingAcceptFiles.value = combined
  if (input) input.value = ''
}

function removePendingAcceptFile(idx: number): void {
  pendingAcceptFiles.value = pendingAcceptFiles.value.filter((_, i) => i !== idx)
  acceptPhotoError.value = null
}

async function uploadPendingAcceptPhotos(): Promise<number[]> {
  const t = ticket.value
  if (!t || pendingAcceptFiles.value.length === 0) return []
  const validation = await validateStaticImageFiles(pendingAcceptFiles.value)
  if (!validation.valid) {
    acceptPhotoError.value = validation.message || '图片校验未通过'
    throw new Error(validation.code || 'VALIDATION_ERROR')
  }
  acceptPhotoUploading.value = true
  try {
    const uploaded = await uploadTicketEvidencesApi(
      t.id,
      pendingAcceptFiles.value,
      'ACCEPTANCE_PHOTO',
    )
    const ids = uploaded.map((ev) => ev.id)
    uploadedAcceptEvidenceIds.value = [...uploadedAcceptEvidenceIds.value, ...ids]
    pendingAcceptFiles.value = []
    if (acceptFileInputRef.value) {
      acceptFileInputRef.value.value = ''
    }
    return ids
  } finally {
    acceptPhotoUploading.value = false
  }
}

async function handleManualUploadAcceptPhotos(): Promise<void> {
  const t = ticket.value
  if (!t || pendingAcceptFiles.value.length === 0) return
  bannerStore.clear()
  try {
    await uploadPendingAcceptPhotos()
    await ticketsStore.fetchDetail(t.id)
    actionSuccessMessage.value = '验收照片上传成功'
  } catch {
    // 校验提示或全局错误横幅处理
  }
}

async function handleSubmitAcceptance(): Promise<void> {
  const t = ticket.value
  if (!t || !canSubmitAcceptance.value || acceptDecision.value === null) return
  const decision = acceptDecision.value

  if (decision === 'APPROVED') {
    const ok = await confirmDialog(
      '通过后工单将关闭，不可再次返修，是否确认验收通过？',
      '确认验收通过',
      '确认通过',
    )
    if (!ok) return
  }

  bannerStore.clear()
  actionSuccessMessage.value = null
  lastReworkCreated.value = null

  try {
    if (pendingAcceptFiles.value.length > 0) {
      await uploadPendingAcceptPhotos()
    }
    const evidenceIds =
      uploadedAcceptEvidenceIds.value.length > 0 ? uploadedAcceptEvidenceIds.value : undefined
    const res = await acceptSubmit.run((key, onReplay) =>
      ticketsStore.submitAcceptance(
        t.id,
        {
          decision,
          comment: acceptComment.value.trim() || null,
          evidence_ids: evidenceIds,
        },
        key,
        onReplay,
      ),
    )
    if (res.decision === 'REJECTED' && res.rework_order) {
      lastReworkCreated.value = res.rework_order
    }
    acceptDecision.value = null
    acceptComment.value = ''
    uploadedAcceptEvidenceIds.value = []
    actionSuccessMessage.value =
      decision === 'APPROVED' ? '验收已通过，工单已关闭（CLOSED）' : '已驳回返修'
  } catch (err: unknown) {
    handleIdempotentError(err, acceptSubmit)
  }
}
</script>

<template>
  <div class="ticket-detail-page">
    <div class="tomori-page-header">
      <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
        <button type="button" class="tomori-btn" @click="router.push('/tickets')">
          返回工单列表
        </button>
        <h1 class="tomori-page-title">工单详情</h1>
      </div>
      <button
        type="button"
        class="tomori-btn"
        :disabled="ticketsStore.loadingDetail"
        @click="loadDetail"
      >
        刷新详情
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

    <div v-if="ticketsStore.loadingDetail && !ticket" class="tomori-card state-box">
      正在加载工单详情...
    </div>

    <div v-else-if="loadError && !ticket" class="tomori-card state-box">
      <div style="margin-bottom: 12px">{{ loadError }}</div>
      <button type="button" class="tomori-btn" @click="loadDetail">重新加载</button>
    </div>

    <template v-else-if="ticket">
      <!-- 基本信息与 SLA 摘要 -->
      <section class="tomori-card">
        <div class="detail-top-bar">
          <div class="detail-no-group">
            <strong data-testid="workorder-detail-no" class="detail-ticket-no">
              {{ ticket.ticket_no }}
            </strong>
            <PriorityTag :priority="ticket.priority" testid="workorder-detail-priority" />
            <StatusTag :status="ticket.status" testid="workorder-detail-status" />
            <el-tag
              v-if="ticket.confirmed_by_inspector"
              data-testid="workorder-detail-confirmed-badge"
              type="success"
              size="small"
              disable-transitions
            >
              巡查员已确认
            </el-tag>
            <el-tag
              v-if="isSlaOverdue"
              data-testid="workorder-sla-overdue-badge"
              :data-overdue-stage="overdueStage"
              type="danger"
              effect="dark"
              size="small"
              disable-transitions
            >
              SLA 已超时（{{ overdueStage === 'RESPONSE' ? '响应' : '修复 48h' }}）
            </el-tag>
          </div>
          <div class="detail-actions-inline">
            <button
              v-if="canRegradeTicket"
              type="button"
              class="tomori-btn"
              @click="openRegradeDialog"
            >
              手工调级
            </button>
            <button
              v-if="canDispatchTicket"
              type="button"
              class="tomori-btn tomori-btn--primary"
              @click="openDispatchDialog"
            >
              立即派工
            </button>
          </div>
        </div>

        <h2 class="detail-title">{{ ticket.title }}</h2>
        <p v-if="ticket.description" class="detail-desc">{{ ticket.description }}</p>

        <div class="detail-meta-grid">
          <div class="meta-cell">
            <span class="meta-label">故障类型</span>
            <span class="meta-value">{{ FAULT_TYPE_LABELS[ticket.fault_type] }}</span>
          </div>
          <div class="meta-cell">
            <span class="meta-label">报障来源</span>
            <span class="meta-value">{{ ticket.report_source }}</span>
          </div>
          <div class="meta-cell">
            <span class="meta-label">所属区域</span>
            <span class="meta-value">{{ ticket.region_name || `区域 #${ticket.region_id}` }}</span>
          </div>
          <div class="meta-cell">
            <span class="meta-label">照明点位</span>
            <span class="meta-value">
              {{ ticket.point_name || ticket.point_code || `点位 #${ticket.lighting_point_id}` }}
              <span v-if="ticket.point_code" class="cell-sub">（{{ ticket.point_code }}）</span>
            </span>
          </div>
          <div class="meta-cell">
            <span class="meta-label">响应时限（response_deadline_at）</span>
            <span
              data-testid="workorder-detail-response-deadline"
              :data-overdue="String(ticket.is_sla_overdue && ticket.overdue_stage === 'RESPONSE')"
              class="meta-value"
              :class="{ 'is-overdue-text': ticket.is_sla_overdue && ticket.overdue_stage === 'RESPONSE' }"
            >
              {{ formatResponseDeadline(ticket.response_deadline_at) }}
            </span>
          </div>
          <div class="meta-cell">
            <span class="meta-label">法定 48h 修复截止（repair_deadline_at）</span>
            <span
              data-testid="workorder-detail-repair-deadline"
              :data-overdue="String(ticket.is_sla_overdue && ticket.overdue_stage === 'REPAIR')"
              class="meta-value"
              :class="{ 'is-overdue-text': ticket.is_sla_overdue && ticket.overdue_stage === 'REPAIR' }"
            >
              {{ formatRepairDeadline(ticket.repair_deadline_at, ticket.status) }}
              <span
                v-if="isSlaOverdue && ticket.repair_deadline_at"
                class="is-overdue-text"
                style="margin-left: 6px; font-size: 12px"
              >
                {{ formatOverdueDuration(ticket.repair_deadline_at) }}
              </span>
            </span>
          </div>
          <div class="meta-cell">
            <span class="meta-label">返修次数</span>
            <span class="meta-value">
              <span data-testid="workorder-detail-rework-count">{{ ticket.rework_count }}</span>
              <el-tag
                v-if="hasReworkThresholdAlert"
                type="danger"
                size="small"
                style="margin-left: 8px"
              >
                已达返修阈值，本单需管理员强制复核
              </el-tag>
            </span>
          </div>
          <div class="meta-cell">
            <span class="meta-label">拒单累计 / 创建时间</span>
            <span class="meta-value">
              {{ ticket.dispatch_reject_count }} 次 · {{ formatDateTime(ticket.created_at) }}
            </span>
          </div>
        </div>

        <div v-if="ticket.cancel_reason" class="status-note-box">
          <strong>撤销原因：</strong>{{ ticket.cancel_reason }}
          <span v-if="ticket.cancelled_at" class="cell-sub">
            （{{ formatDateTime(ticket.cancelled_at) }}）
          </span>
        </div>

        <!-- 等待管理员人工复核通知（所有角色可见） -->
        <div
          v-if="ticket.status === 'MANUAL_REVIEW'"
          data-testid="workorder-manual-review-notice"
          class="manual-review-banner"
        >
          工单已进入「等待管理员人工复核」状态（{{ ticket.manual_review_reason || '返修次数达阈值或多次异常' }}）
        </div>
      </section>

      <!-- AI 候选草稿人工确认 / 编辑（仅 AI_CANDIDATE_PENDING 且具备 confirm 权限） -->
      <section v-if="ticket.status === 'AI_CANDIDATE_PENDING' && canConfirmDraft" class="tomori-card">
        <h3 class="section-heading">AI 候选草稿人工确认与编辑</h3>

        <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px">
          AI 候选仅作辅助提示，须由巡查员核对故障类型与现场情况后确认立单（确认后即起算 48h 修复期限）。
        </el-alert>

        <div v-if="ticket.evidences.length > 0" class="draft-evidences-grid">
          <div v-for="ev in ticket.evidences" :key="ev.id" class="draft-evidence-card">
            <AuthImage
              :evidence-id="ev.id"
              :variant="ev.annotated_url ? 'annotated' : 'raw'"
              :alt="`AI 候选证据 #${ev.id}`"
              aspect-ratio="4 / 3"
            />
            <div v-if="ev.ai_is_baseline_mode" class="baseline-note">
              当前为基线视觉模式（confidence ≤ 0.65），请重点人工核对
            </div>
            <div v-if="ev.ai_result_json" class="draft-ai-summary">
              <div class="cell-sub">{{ ev.ai_result_json.disclaimer }}</div>
              <div
                v-for="cand in ev.ai_result_json.candidates"
                :key="cand.candidate_id"
                class="draft-cand-row"
              >
                <el-tag :type="cand.is_fault_candidate ? 'danger' : 'info'" size="small">
                  {{ cand.is_fault_candidate ? (cand.candidate_fault_type ? FAULT_TYPE_LABELS[cand.candidate_fault_type] : cand.raw_class) : '正常参照' }}
                </el-tag>
                <span>置信度 {{ formatConfidence(cand.fused_confidence) }}</span>
                <span class="cell-sub">{{ cand.diagnostic_summary }}</span>
              </div>
            </div>
          </div>
        </div>

        <div class="form-grid" style="margin-top: 12px">
          <div class="form-field">
            <label class="filter-label">工单标题（必填）</label>
            <input v-model="draftTitle" type="text" class="tomori-input" placeholder="请输入工单标题" />
          </div>

          <div class="form-field">
            <label class="filter-label">故障类型</label>
            <el-select v-model="draftFaultType" :teleported="false" style="width: 100%">
              <el-option
                v-for="opt in faultTypeOptions"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
          </div>

          <div class="form-field">
            <label class="filter-label">人工覆盖优先级（可选，留空按规则表自动分级）</label>
            <el-select
              v-model="draftPriorityOverride"
              clearable
              placeholder="按规则自动分级（推荐）"
              :teleported="false"
              style="width: 100%"
            >
              <el-option
                v-for="opt in priorityOptions"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
          </div>

          <div class="form-field" style="justify-content: flex-end">
            <label class="tomori-checkbox-label">
              <input v-model="draftSafetyFlag" type="checkbox" class="tomori-checkbox" />
              <span>存在紧急安全隐患（优先提示处置）</span>
            </label>
          </div>
        </div>

        <div class="form-field" style="margin-top: 10px">
          <label class="filter-label">现场补充描述</label>
          <textarea
            v-model="draftDescription"
            class="tomori-textarea"
            placeholder="可补充现场位置、灯杆状况等说明"
          />
        </div>

        <div v-if="draftRequiresManualNote" class="form-field" style="margin-top: 10px">
          <label class="filter-label" style="color: var(--tomori-danger)">
            人工判定依据（改为「闪烁 / 电气安全隐患」时必填）
          </label>
          <textarea
            v-model="draftManualNote"
            class="tomori-textarea"
            placeholder="请填写人工现场观察依据（必填）"
          />
        </div>

        <div class="form-actions-row">
          <button
            type="button"
            class="tomori-btn"
            :disabled="draftSaving || confirmSubmit.isSubmitting.value || draftTitle.trim().length === 0 || (draftRequiresManualNote && draftManualNote.trim().length === 0)"
            @click="handleSaveDraft"
          >
            {{ draftSaving ? '保存中...' : '保存草稿修改' }}
          </button>
          <button
            type="button"
            class="tomori-btn tomori-btn--primary"
            :disabled="!canSubmitDraftConfirm"
            @click="handleConfirmDraft"
          >
            {{ confirmSubmit.isSubmitting.value ? '确认中...' : '确认立单并进入待派工' }}
          </button>
        </div>
      </section>

      <!-- 巡查员验收与证据对照（H 组锚点） -->
      <section class="tomori-card">
        <h3 class="section-heading">维修前后证据对照与验收</h3>

        <div data-testid="accept-evidence-compare" class="evidence-compare-grid">
          <div class="evidence-column">
            <div class="evidence-col-title">巡查 / 报障前证据（INSPECTION_PHOTO / ANNOTATED_PHOTO）</div>
            <div v-if="preRepairEvidences.length === 0" class="evidence-empty">暂无巡查证据图</div>
            <div v-else class="evidence-thumb-list">
              <div v-for="ev in preRepairEvidences" :key="ev.id" class="evidence-thumb-card">
                <AuthImage :evidence-id="ev.id" variant="raw" :alt="`巡查原图 #${ev.id}`" aspect-ratio="4 / 3" />
                <div class="cell-sub" style="margin-top: 4px">
                  {{ ev.evidence_type }} · #{{ ev.id }} · {{ formatDateTime(ev.created_at) }}
                </div>
                <AuthImage
                  v-if="ev.annotated_url"
                  :evidence-id="ev.id"
                  variant="annotated"
                  :alt="`AI 标注图 #${ev.id}`"
                  aspect-ratio="4 / 3"
                  style="margin-top: 6px"
                />
              </div>
            </div>
          </div>

          <div class="evidence-column">
            <div class="evidence-col-title">维修后 / 验收证据（REPAIR_PHOTO / ACCEPTANCE_PHOTO）</div>
            <div v-if="postRepairEvidences.length === 0" class="evidence-empty">暂无维修后或验收照片</div>
            <div v-else class="evidence-thumb-list">
              <div v-for="ev in postRepairEvidences" :key="ev.id" class="evidence-thumb-card">
                <AuthImage :evidence-id="ev.id" variant="raw" :alt="`维修验收图 #${ev.id}`" aspect-ratio="4 / 3" />
                <div class="cell-sub" style="margin-top: 4px">
                  {{ ev.evidence_type }} · #{{ ev.id }} · {{ formatDateTime(ev.created_at) }}
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 巡查员验收操作区 -->
        <div v-if="canAcceptTicket" class="acceptance-action-panel">
          <h4 class="sub-heading">巡查员现场验收结论</h4>

          <!-- 可选 1..10 张验收附图上传 -->
          <div class="upload-box">
            <label class="filter-label">可选上传验收照片（1–10 张静态 JPEG/PNG/WebP，单张 ≤ 15 MiB）</label>
            <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 6px">
              <input
                ref="acceptFileInputRef"
                type="file"
                multiple
                accept="image/jpeg,image/png,image/webp"
                @change="onAcceptanceFilesChange"
              />
              <button
                v-if="pendingAcceptFiles.length > 0"
                type="button"
                class="tomori-btn"
                :disabled="acceptPhotoUploading"
                @click="handleManualUploadAcceptPhotos"
              >
                {{ acceptPhotoUploading ? '上传中...' : `立即上传 (${pendingAcceptFiles.length} 张)` }}
              </button>
            </div>
            <div v-if="acceptPhotoError" class="tomori-field-error">{{ acceptPhotoError }}</div>
            <ul v-if="pendingAcceptFiles.length > 0" class="pending-file-list">
              <li v-for="(file, idx) in pendingAcceptFiles" :key="`${file.name}-${idx}`" class="pending-file-item">
                <span>{{ file.name }}（{{ formatFileSize(file.size) }}）</span>
                <button type="button" class="tomori-btn" style="padding: 2px 8px; font-size: 12px" @click="removePendingAcceptFile(idx)">
                  移除
                </button>
              </li>
            </ul>
          </div>

          <div class="accept-decision-buttons">
            <button
              type="button"
              data-testid="accept-pass-btn"
              class="tomori-btn"
              :class="{ 'tomori-btn--success': acceptDecision === 'APPROVED' }"
              @click="selectAcceptDecision('APPROVED')"
            >
              验收通过（APPROVED）
            </button>
            <button
              type="button"
              data-testid="accept-reject-btn"
              class="tomori-btn"
              :class="{ 'tomori-btn--danger': acceptDecision === 'REJECTED' }"
              @click="selectAcceptDecision('REJECTED')"
            >
              驳回返修（REJECTED）
            </button>
          </div>

          <el-alert
            v-if="acceptDecision === 'APPROVED'"
            type="warning"
            :closable="false"
            show-icon
            style="margin-top: 10px"
          >
            通过后工单将关闭，不可再次返修
          </el-alert>

          <div class="form-field" style="margin-top: 10px">
            <textarea
              v-model="acceptComment"
              data-testid="accept-comment-input"
              class="tomori-textarea"
              placeholder="验收意见（驳回返修时必填）"
            />
          </div>

          <div class="form-actions-row">
            <button
              type="button"
              data-testid="accept-submit-btn"
              class="tomori-btn tomori-btn--primary"
              :disabled="!canSubmitAcceptance"
              @click="handleSubmitAcceptance"
            >
              {{ acceptSubmit.isSubmitting.value ? '提交中...' : '提交验收结论' }}
            </button>
          </div>
        </div>

        <!-- 驳回后同事务生成的返修单即时回显（不依赖 canAcceptTicket，状态切换后仍可见） -->
        <el-alert
          v-if="lastReworkCreated"
          data-testid="acceptance-rework-created"
          :data-round-no="String(lastReworkCreated.round_no)"
          type="warning"
          :closable="false"
          show-icon
          style="margin-top: 12px"
        >
          <template #title>
            已生成第 {{ lastReworkCreated.round_no }} 轮返修单（{{ lastReworkCreated.rework_no }}）
          </template>
          <div v-if="lastReworkCreated.requires_manual_review">
            已超返修轮次阈值，工单已进入等待管理员复核态（MANUAL_REVIEW）
          </div>
          <div v-else>
            已自动生成返修单，工单将重新进入派工（REWORK_PENDING_DISPATCH）
          </div>
        </el-alert>
      </section>

      <!-- 返修历史 -->
      <section class="tomori-card">
        <h3 class="section-heading">返修历史（{{ ticket.rework_orders.length }}）</h3>
        <div data-testid="rework-history-list" class="rework-history-list">
          <div v-if="ticket.rework_orders.length === 0" class="evidence-empty">
            暂无返修记录
          </div>
          <div
            v-for="(rw, idx) in ticket.rework_orders"
            :key="rw.id"
            :data-testid="`rework-item-${idx}`"
            class="rework-item-card"
          >
            <div class="rework-item-head">
              <strong class="cell-mono">{{ rw.rework_no }}</strong>
              <span>第 {{ rw.round_no }} 轮</span>
              <StatusTag :status="rw.status" kind="rework" />
              <el-tag v-if="rw.requires_manual_review" type="danger" size="small">
                超阈值强制复核
              </el-tag>
            </div>
            <div class="rework-item-reason">驳回原因：{{ rw.reject_reason }}</div>
            <div v-if="rw.manual_review_decision" class="cell-sub" style="margin-top: 4px">
              复核结论：{{ rw.manual_review_decision }}
              <span v-if="rw.manual_review_note"> · {{ rw.manual_review_note }}</span>
              <span v-if="rw.manual_review_decided_at">（{{ formatDateTime(rw.manual_review_decided_at) }}）</span>
            </div>
          </div>
        </div>
      </section>

      <!-- 管理员异常升级与人工复核裁决 -->
      <section v-if="authStore.roleCode === 'ADMIN'" class="tomori-card">
        <h3 class="section-heading">管理员改派与人工复核</h3>

        <div class="escalation-toolbar">
          <button
            type="button"
            data-testid="escalation-reassign-btn"
            class="tomori-btn tomori-btn--warning"
            :disabled="!canReassignTicket"
            @click="openReassignDialog"
          >
            改派 / 超时升级改派
          </button>
          <button
            type="button"
            data-testid="escalation-manual-review-btn"
            class="tomori-btn"
            :disabled="ticket.status !== 'MANUAL_REVIEW'"
            @click="focusResolveSection"
          >
            进入人工复核裁决
          </button>
        </div>

        <div class="review-resolve-panel">
          <div class="filter-label" style="margin-bottom: 8px">
            复核裁决选项（仅 MANUAL_REVIEW 状态可提交，不提供默认选中，须显式选择）
          </div>
          <div class="radio-group-vertical">
            <label
              v-for="opt in resolutionOptions"
              :key="opt.value"
              class="tomori-checkbox-label"
              :class="{ 'is-disabled': !canReviewResolve }"
            >
              <input
                v-model="resolveResolution"
                type="radio"
                name="review-resolution"
                :value="opt.value"
                :disabled="!canReviewResolve"
              />
              <span>{{ opt.label }}</span>
            </label>
          </div>

          <div class="form-field" style="margin-top: 10px">
            <label class="filter-label">
              复核裁决理由（必填；选择 SPECIAL_CLOSE 时至少 10 个字）
            </label>
            <textarea
              ref="resolveReasonInputRef"
              v-model="resolveReason"
              data-testid="escalation-resolve-reason-input"
              class="tomori-textarea"
              :disabled="!canReviewResolve"
              placeholder="请填写人工复核裁决理由"
            />
            <div
              v-if="resolveResolution === 'SPECIAL_CLOSE' && resolveReason.trim().length > 0 && resolveReason.trim().length < 10"
              class="tomori-field-error"
            >
              特批关闭结案（SPECIAL_CLOSE）理由不得少于 10 个字（当前 {{ resolveReason.trim().length }} 字）
            </div>
          </div>

          <div class="form-actions-row">
            <button
              type="button"
              data-testid="escalation-resolve-btn"
              class="tomori-btn tomori-btn--primary"
              :disabled="!canSubmitResolve"
              @click="handleReviewResolve"
            >
              {{ resolveSubmit.isSubmitting.value ? '裁决中...' : '提交人工复核裁决' }}
            </button>
          </div>
        </div>
      </section>

      <!-- 派工前撤销误报（保持 DOM 可见 + disabled，符合 §8.5 / R6） -->
      <section class="tomori-card">
        <div style="display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap">
          <h3 class="section-heading" style="margin: 0">派工前撤销误报</h3>
          <button
            type="button"
            data-testid="workorder-cancel-btn"
            class="tomori-btn tomori-btn--danger"
            :disabled="!canCancelTicket"
            @click="focusCancelReason"
          >
            撤销误报
          </button>
        </div>
        <div class="cell-sub" style="margin-top: 6px">
          仅 AI_CANDIDATE_PENDING / PENDING_DISPATCH 状态（且巡查员仅可撤销本人报障）允许撤销；REWORK_PENDING_DISPATCH 不可撤销，需由管理员走人工复核裁决。
        </div>
        <div class="form-field" style="margin-top: 10px">
          <textarea
            ref="cancelReasonInputRef"
            v-model="cancelReason"
            data-testid="workorder-cancel-reason-input"
            class="tomori-textarea"
            :disabled="!canCancelTicket"
            placeholder="请填写撤销误报原因（必填）"
          />
        </div>
        <div class="form-actions-row">
          <button
            type="button"
            data-testid="workorder-cancel-confirm-btn"
            class="tomori-btn tomori-btn--danger"
            :disabled="!canCancelTicket || cancelReason.trim().length === 0 || cancelSubmit.isSubmitting.value"
            @click="handleCancelConfirm"
          >
            {{ cancelSubmit.isSubmitting.value ? '撤销中...' : '确认撤销' }}
          </button>
        </div>
      </section>

      <!-- 维修过程与安全检查记录汇总 -->
      <section class="tomori-card">
        <h3 class="section-heading">维修过程与耗材记录（{{ ticket.repair_processes.length }}）</h3>
        <div v-if="ticket.repair_processes.length === 0" class="evidence-empty">
          暂无维修完工记录
        </div>
        <div v-else class="process-list">
          <div v-for="proc in ticket.repair_processes" :key="proc.id" class="process-card">
            <div class="process-header">
              <strong>维修记录 #{{ proc.id }}</strong>
              <el-tag :type="proc.electrical_safety_checked ? 'success' : 'danger'" size="small">
                {{ proc.electrical_safety_checked ? '电气安全六件套已确认' : '未完成安全检查' }}
              </el-tag>
              <span class="cell-sub">到场：{{ formatDateTime(proc.arrived_at) }}</span>
            </div>
            <div class="safety-flags-row">
              <el-tag
                v-for="item in SAFETY_CHECKLIST_ITEMS"
                :key="item.key"
                :type="proc.safety_checklist_json?.[item.key] ? 'success' : 'info'"
                size="small"
              >
                {{ item.label }}
              </el-tag>
            </div>
            <div class="process-field"><strong>安全说明：</strong>{{ proc.safety_note }}</div>
            <div v-if="proc.root_cause" class="process-field"><strong>故障根因：</strong>{{ proc.root_cause }}</div>
            <div class="process-field"><strong>维修过程：</strong>{{ proc.repair_summary }}</div>
            <div v-if="proc.materials && proc.materials.length > 0" class="process-field">
              <strong>使用耗材：</strong>
              <span>
                {{
                  proc.materials
                    .map((m) => `${m.material_name} (${m.material_code}) × ${m.quantity} ${m.unit}`)
                    .join('；')
                }}
              </span>
            </div>
          </div>
        </div>
      </section>

      <!-- 派工记录与验收记录 -->
      <section class="tomori-card">
        <h3 class="section-heading">派工与验收历史</h3>
        <div class="two-col-history">
          <div>
            <h4 class="sub-heading">派工记录（{{ ticket.dispatches.length }}）</h4>
            <div v-if="ticket.dispatches.length === 0" class="evidence-empty">暂无派工记录</div>
            <div v-for="d in ticket.dispatches" :key="d.id" class="history-item">
              <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px">
                <span>
                  派工 #{{ d.id }} · {{ d.technician_full_name || d.technician_username || `维修员#${d.technician_account_id}` }}
                </span>
                <StatusTag :status="d.status" kind="dispatch" />
              </div>
              <div class="cell-sub">
                派单：{{ formatDateTime(d.dispatched_at) }} · 接单截止：{{ formatDateTime(d.accept_deadline_at) }}
              </div>
              <div v-if="d.reject_reason" class="cell-sub" style="color: var(--tomori-danger)">
                拒单原因：{{ d.reject_reason }}
              </div>
              <div v-if="d.escalate_reason" class="cell-sub" style="color: var(--tomori-warning)">
                升级原因：{{ d.escalate_reason }}
              </div>
            </div>
          </div>

          <div>
            <h4 class="sub-heading">验收记录（{{ ticket.acceptances.length }}）</h4>
            <div v-if="ticket.acceptances.length === 0" class="evidence-empty">暂无验收记录</div>
            <div v-for="acc in ticket.acceptances" :key="acc.id" class="history-item">
              <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px">
                <span>验收 #{{ acc.id }}</span>
                <el-tag :type="acc.decision === 'APPROVED' ? 'success' : 'danger'" size="small">
                  {{ acc.decision === 'APPROVED' ? '验收通过 (APPROVED)' : '驳回返修 (REJECTED)' }}
                </el-tag>
              </div>
              <div class="cell-sub">时间：{{ formatDateTime(acc.accepted_at) }}</div>
              <div v-if="acc.comment" class="cell-sub">意见：{{ acc.comment }}</div>
            </div>
          </div>
        </div>
      </section>

      <!-- 操作审计时间线 -->
      <section class="tomori-card">
        <h3 class="section-heading">操作审计时间线（{{ ticket.timeline.length }}）</h3>
        <div v-if="ticket.timeline.length === 0" class="evidence-empty">暂无审计记录</div>
        <div v-else class="timeline-list">
          <div v-for="entry in ticket.timeline" :key="entry.id" class="timeline-row">
            <div class="timeline-dot" />
            <div class="timeline-body">
              <div class="timeline-head">
                <strong>{{ entry.action }}</strong>
                <span class="cell-sub">
                  {{ entry.actor_username || 'SYSTEM' }}
                  <span v-if="entry.actor_role_code">
                    （{{ ROLE_LABELS[entry.actor_role_code] }}）
                  </span>
                </span>
                <span class="cell-sub">{{ formatDateTime(entry.created_at) }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 手工调级弹窗 (:teleported="false") -->
      <el-dialog
        v-model="regradeDialogVisible"
        title="手工调整工单优先级"
        width="440px"
        :teleported="false"
      >
        <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px">
          依据郑州市令第 152 号 48 小时基线，调整优先级不重置或顺延法定修复截止时间。
        </el-alert>
        <div class="form-field">
          <label class="filter-label">目标优先级</label>
          <el-select v-model="regradePriority" :teleported="false" style="width: 100%">
            <el-option
              v-for="opt in priorityOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </div>
        <div class="form-field" style="margin-top: 12px">
          <label class="filter-label">调级原因（必填）</label>
          <textarea
            v-model="regradeReason"
            class="tomori-textarea"
            placeholder="请填写优先级调整理由"
          />
        </div>
        <template #footer>
          <button type="button" class="tomori-btn" @click="regradeDialogVisible = false">取消</button>
          <button
            type="button"
            class="tomori-btn tomori-btn--primary"
            :disabled="regradeReason.trim().length === 0 || regradeSubmit.isSubmitting.value"
            @click="handleRegradeSubmit"
          >
            {{ regradeSubmit.isSubmitting.value ? '提交中...' : '确认调级' }}
          </button>
        </template>
      </el-dialog>

      <!-- 派工弹窗 (:teleported="false") -->
      <el-dialog
        v-model="dispatchDialogVisible"
        title="指派区域授权维修员"
        width="480px"
        :teleported="false"
      >
        <div v-if="loadingCandidates" class="evidence-empty">正在查询本区域合规维修员...</div>
        <template v-else>
          <div class="form-field">
            <label class="filter-label">选择维修员</label>
            <el-select
              v-model="dispatchCandidateId"
              placeholder="请选择接单维修员"
              :teleported="false"
              style="width: 100%"
            >
              <el-option
                v-for="c in candidateList"
                :key="c.account_id"
                :label="`${c.full_name} (${c.username}) · 在途 ${c.active_dispatch_count}/${c.max_active_dispatch} · 得分 ${c.recommend_score.toFixed(2)}`"
                :value="c.account_id"
              />
              <el-option
                v-for="ex in excludedList"
                :key="`ex-${ex.account_id}`"
                :label="`${ex.full_name} (${ex.username}) · ${getNotEligibleReasonText(ex.reason)}`"
                :value="ex.account_id"
                disabled
              />
            </el-select>
          </div>
          <div class="form-field" style="margin-top: 12px">
            <label class="filter-label">派工备注（可选）</label>
            <textarea v-model="dispatchRemark" class="tomori-textarea" placeholder="可填写现场注意事项" />
          </div>
        </template>
        <template #footer>
          <button type="button" class="tomori-btn" @click="dispatchDialogVisible = false">取消</button>
          <button
            type="button"
            class="tomori-btn tomori-btn--primary"
            :disabled="dispatchCandidateId === null || dispatchSubmit.isSubmitting.value"
            @click="handleDispatchSubmit"
          >
            {{ dispatchSubmit.isSubmitting.value ? '派工中...' : '确认派工' }}
          </button>
        </template>
      </el-dialog>

      <!-- 改派 / 超时升级改派弹窗 (:teleported="false") -->
      <el-dialog
        v-model="reassignDialogVisible"
        title="改派 / 超时升级改派"
        width="480px"
        :teleported="false"
      >
        <div v-if="loadingCandidates" class="evidence-empty">正在查询本区域合规维修员...</div>
        <template v-else>
          <div class="form-field">
            <label class="filter-label">选择新接单维修员</label>
            <el-select
              v-model="reassignCandidateId"
              placeholder="请选择接单维修员"
              :teleported="false"
              style="width: 100%"
            >
              <el-option
                v-for="c in candidateList"
                :key="c.account_id"
                :label="`${c.full_name} (${c.username}) · 在途 ${c.active_dispatch_count}/${c.max_active_dispatch}`"
                :value="c.account_id"
              />
              <el-option
                v-for="ex in excludedList"
                :key="`ex-${ex.account_id}`"
                :label="`${ex.full_name} (${ex.username}) · ${getNotEligibleReasonText(ex.reason)}`"
                :value="ex.account_id"
                disabled
              />
            </el-select>
          </div>
          <div style="margin-top: 12px">
            <label class="tomori-checkbox-label">
              <input v-model="reassignEscalate" type="checkbox" class="tomori-checkbox" />
              <span>标记为超时/异常升级改派（旧派工置为 ESCALATED）</span>
            </label>
          </div>
          <div class="form-field" style="margin-top: 12px">
            <label class="filter-label">改派原因（必填）</label>
            <textarea
              v-model="reassignReason"
              class="tomori-textarea"
              placeholder="请填写改派或超时升级改派原因"
            />
          </div>
        </template>
        <template #footer>
          <button type="button" class="tomori-btn" @click="reassignDialogVisible = false">取消</button>
          <button
            type="button"
            class="tomori-btn tomori-btn--warning"
            :disabled="reassignCandidateId === null || reassignReason.trim().length === 0 || reassignSubmit.isSubmitting.value"
            @click="handleReassignSubmit"
          >
            {{ reassignSubmit.isSubmitting.value ? '改派中...' : '确认改派' }}
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

.detail-top-bar {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.detail-no-group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.detail-ticket-no {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 16px;
}

.detail-actions-inline {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.detail-title {
  margin: 4px 0 8px;
  font-size: 17px;
  font-weight: 600;
}

.detail-desc {
  margin: 0 0 12px;
  font-size: 14px;
  color: var(--tomori-text-secondary);
  line-height: 1.5;
}

.detail-meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px 16px;
  padding-top: 10px;
  border-top: 1px solid #ebeef5;
}

.meta-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.meta-label {
  font-size: 12px;
  color: var(--tomori-text-muted);
}

.meta-value {
  font-size: 13px;
  color: var(--tomori-text);
  font-weight: 500;
}

.is-overdue-text {
  color: var(--tomori-danger);
  font-weight: 600;
}

.status-note-box {
  margin-top: 12px;
  padding: 10px 12px;
  background: #f4f4f5;
  border-radius: 4px;
  font-size: 13px;
}

.manual-review-banner {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 4px;
  border: 1px solid #fab6b6;
  background: #fef0f0;
  color: #c45656;
  font-size: 13px;
  font-weight: 600;
}

.section-heading {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
}

.sub-heading {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
}

.draft-evidences-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}

.draft-evidence-card {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 10px;
}

.baseline-note {
  margin-top: 6px;
  padding: 4px 8px;
  border-radius: 4px;
  background: #fdf6ec;
  color: #b88230;
  font-size: 12px;
}

.draft-ai-summary {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.draft-cand-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 12px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.filter-label {
  font-size: 12px;
  color: var(--tomori-text-secondary);
}

.form-actions-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.evidence-compare-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 16px;
}

.evidence-column {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 12px;
  background: #fafafa;
}

.evidence-col-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 10px;
}

.evidence-thumb-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.evidence-thumb-card {
  background: #ffffff;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  padding: 8px;
}

.evidence-empty {
  padding: 16px 8px;
  text-align: center;
  color: var(--tomori-text-muted);
  font-size: 13px;
}

.acceptance-action-panel {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid #ebeef5;
}

.upload-box {
  padding: 10px 12px;
  border: 1px dashed var(--tomori-border);
  border-radius: 6px;
  margin-bottom: 12px;
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

.accept-decision-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.rework-history-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.rework-item-card {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 10px 12px;
}

.rework-item-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  font-size: 13px;
}

.rework-item-reason {
  font-size: 13px;
  color: var(--tomori-text-secondary);
}

.escalation-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
}

.review-resolve-panel {
  padding-top: 12px;
  border-top: 1px solid #ebeef5;
}

.radio-group-vertical {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.process-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.process-card {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 12px;
}

.process-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.safety-flags-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.process-field {
  font-size: 13px;
  line-height: 1.5;
  margin-top: 4px;
}

.two-col-history {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 16px;
}

.history-item {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
  font-size: 13px;
}

.timeline-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.timeline-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.timeline-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--tomori-primary);
  margin-top: 6px;
  flex-shrink: 0;
}

.timeline-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  font-size: 13px;
}

.cell-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.cell-sub {
  font-size: 12px;
  color: var(--tomori-text-muted);
}
</style>
