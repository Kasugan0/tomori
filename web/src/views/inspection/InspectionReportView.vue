<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import AuthImage from '@/components/AuthImage.vue'
import { isApiError } from '@/shared/api'
import {
  FAULT_TYPE_LABELS,
  MANUAL_NOTE_REQUIRED_FAULT_TYPES,
} from '@/shared/enums'
import { formatConfidence, formatFileSize } from '@/shared/format'
import { useIdempotentSubmit } from '@/shared/idempotency'
import type {
  FaultCandidateResultOut,
  FaultType,
  LightingPointOut,
  SceneLightingHint,
} from '@/shared/types'
import { MAX_UPLOAD_FILES, validateStaticImageFiles } from '@/shared/upload'
import { useAuthStore } from '@/stores/auth'
import { useInspectionStore, type FlattenedCandidateItem } from '@/stores/inspection'
import { useMetadataStore } from '@/stores/metadata'

const DISCLAIMER_TEXT =
  'AI 仅生成静态巡查照片故障候选，必须经巡查员人工确认后方可生成工单；严禁替代现场断电与电气安全检查，且不连接任何远程灯控设备。'

const SCENE_LIGHTING_OPTIONS: ReadonlyArray<{ value: SceneLightingHint; label: string }> = [
  { value: 'auto', label: '自动测光 (auto)' },
  { value: 'night', label: '夜间 (night)' },
  { value: 'twilight', label: '黄昏/黎明 (twilight)' },
  { value: 'day', label: '白天 (day)' },
]

const FAULT_TYPE_OPTIONS: ReadonlyArray<{ value: FaultType; label: string }> = (
  Object.keys(FAULT_TYPE_LABELS) as FaultType[]
).map((code) => ({
  value: code,
  label: `${FAULT_TYPE_LABELS[code]} (${code})`,
}))

const router = useRouter()
const authStore = useAuthStore()
const store = useInspectionStore()
const metadataStore = useMetadataStore()

const analyzeSubmit = useIdempotentSubmit()
const ticketSubmit = useIdempotentSubmit()

const fileInputRef = ref<HTMLInputElement | null>(null)
const activeEvidenceIndex = ref(0)
const showAnnotatedImage = ref(false)
const submitting = ref(false)
const submittedSuccess = ref(false)

// Edit candidate dialog state
const editDialogVisible = ref(false)
const editingItem = ref<FlattenedCandidateItem | null>(null)
const editFaultType = ref<FaultType>('LAMP_OUT')
const editNote = ref('')

const authorizedInspectRegionIds = computed<Set<number>>(() => {
  const scopes = authStore.scopeAuthorizations.filter(
    (s) => s.is_active && s.scope_type === 'INSPECT',
  )
  return new Set(scopes.map((s) => s.region_id))
})

const visiblePoints = computed<LightingPointOut[]>(() => {
  const pts = metadataStore.points
  if (authStore.roleCode === 'ADMIN' || authorizedInspectRegionIds.value.size === 0) {
    return pts
  }
  return pts.filter((p) => authorizedInspectRegionIds.value.has(p.region_id))
})

const selectedPoint = computed<LightingPointOut | null>(
  () => metadataStore.points.find((p) => p.id === store.lightingPointId) ?? null,
)

const pointAssets = computed(() => {
  if (!store.lightingPointId) return []
  return metadataStore.assets.filter((a) => a.lighting_point_id === store.lightingPointId)
})

const activeEvidence = computed(() => {
  return store.evidences[activeEvidenceIndex.value] ?? store.evidences[0] ?? null
})

const activeEvidenceResult = computed<FaultCandidateResultOut | null>(() => {
  const ev = activeEvidence.value
  if (!ev) return null
  if (ev.ai_result_json) return ev.ai_result_json
  if (ev.candidates) return ev as FaultCandidateResultOut
  return null
})

const activeImageWidth = computed<number>(
  () => activeEvidenceResult.value?.image_width || activeEvidence.value?.image_width || 16,
)

const activeImageHeight = computed<number>(
  () => activeEvidenceResult.value?.image_height || activeEvidence.value?.image_height || 9,
)

const activeOriginalUrl = computed<string | null>(
  () =>
    activeEvidenceResult.value?.original_image_url ??
    activeEvidence.value?.raw_url ??
    activeEvidence.value?.url ??
    null,
)

const activeAnnotatedUrl = computed<string | null>(
  () =>
    activeEvidenceResult.value?.annotated_image_url ??
    activeEvidence.value?.annotated_url ??
    null,
)

const effectiveShowAnnotated = computed<boolean>(
  () => showAnnotatedImage.value && Boolean(activeAnnotatedUrl.value),
)

const activeEvidenceCandidates = computed<FlattenedCandidateItem[]>(() =>
  store.flattenedCandidates.filter((item) => item.evidenceIndex === activeEvidenceIndex.value),
)

const activeEvidenceOverlayItems = computed<FlattenedCandidateItem[]>(() =>
  activeEvidenceCandidates.value.filter((item) => {
    if (!store.showReferenceBoxes && !item.candidate.is_fault_candidate) return false
    return true
  }),
)

const visibleCandidateCards = computed<FlattenedCandidateItem[]>(() => {
  if (store.showReferenceBoxes) return store.flattenedCandidates
  return store.flattenedCandidates.filter((item) => item.candidate.is_fault_candidate)
})

const disclaimerText = computed<string>(() => {
  for (const ev of store.evidences) {
    const res = ev.ai_result_json ?? ev
    if (res.disclaimer && res.disclaimer.trim().length > 0) {
      return res.disclaimer
    }
  }
  return DISCLAIMER_TEXT
})

const qualityFailSummary = computed<string | null>(() => {
  const messages: string[] = []
  store.evidences.forEach((ev, idx) => {
    const res = ev.ai_result_json ?? ev
    const quality = res.image_quality
    if (quality?.quality_grade === 'fail') {
      const warnText = quality.warnings?.length ? quality.warnings.join('；') : '画面过暗、过曝或模糊'
      messages.push(`第 ${idx + 1} 张质量不合格（${warnText}）`)
    }
  })
  return messages.length > 0 ? `${messages.join('；')}，请删除或重新拍摄合格图片后再确认` : null
})

const activePhotoError = computed<{ code: string; message: string } | null>(() => {
  if (store.photoError) return store.photoError
  if (qualityFailSummary.value) {
    return { code: 'IMAGE_QUALITY_FAIL', message: qualityFailSummary.value }
  }
  return null
})

const requiresManualNote = computed<boolean>(() =>
  MANUAL_NOTE_REQUIRED_FAULT_TYPES.includes(store.faultType),
)

const canAnalyze = computed<boolean>(
  () =>
    Boolean(store.lightingPointId) &&
    store.files.length >= 1 &&
    store.files.length <= MAX_UPLOAD_FILES &&
    !store.photoError &&
    !store.analyzeInProgress &&
    !analyzeSubmit.isSubmitting.value,
)

const canSubmit = computed<boolean>(() => {
  if (submitting.value || ticketSubmit.isSubmitting.value) return false
  if (!store.lightingPointId) return false
  if (store.hasQualityFail) return false
  if (requiresManualNote.value && store.manualNote.trim().length === 0) return false
  if (store.manualMode) return true
  if (!store.draftTicket) return false
  return store.allFaultCandidatesDisposed
})

const submitGateHint = computed<string>(() => {
  if (!store.lightingPointId) return '请先在 Step 1 选择照明点位'
  if (store.hasQualityFail) return '存在图像质量不合格（fail）的巡查照片，请重新上传后再提交'
  if (requiresManualNote.value && store.manualNote.trim().length === 0) {
    return '当前故障类型为「闪烁 / 电气安全隐患」，请先填写人工判定依据'
  }
  if (!store.manualMode) {
    if (!store.hasAnalyzed) return '请先完成 AI 智能分析（或切换手动填报模式）'
    if (!store.draftTicket) return '本次识别未生成故障单，如现场确有异常请切换手动填报模式'
    if (!store.allFaultCandidatesDisposed) {
      return `请逐条处置全部 AI 故障候选（已处置 ${store.faultDisposedCount} / ${store.faultCandidates.length}）`
    }
  }
  return ''
})

function syncRegionFromPoint(): void {
  if (!store.lightingPointId) {
    store.regionId = null
    return
  }
  const pt = metadataStore.points.find((p) => p.id === store.lightingPointId)
  if (pt) {
    store.regionId = pt.region_id
  }
}

watch(
  () => [store.lightingPointId, store.regionId, store.sceneLightingHint, store.files],
  () => {
    analyzeSubmit.resetKey()
  },
)

watch(
  () => [
    store.lightingPointId,
    store.facilityAssetId,
    store.faultType,
    store.title,
    store.description,
    store.manualNote,
    store.safetyFlag,
    store.manualMode,
    store.files,
  ],
  () => {
    ticketSubmit.resetKey()
  },
)

onMounted(async () => {
  await Promise.all([metadataStore.loadPoints(), metadataStore.loadAssets()])
  syncRegionFromPoint()
})

onBeforeRouteLeave(async () => {
  if (!authStore.isAuthenticated || submitting.value || submittedSuccess.value) {
    return true
  }
  const hasUnsaved =
    store.files.length > 0 ||
    store.evidences.length > 0 ||
    store.faultDisposedCount > 0 ||
    store.description.trim().length > 0 ||
    store.manualNote.trim().length > 0
  if (!hasUnsaved) return true
  try {
    await ElMessageBox.confirm('当前有未提交的巡查图片或候选处置记录，确定离开本页吗？', '离开确认', {
      confirmButtonText: '离开',
      cancelButtonText: '留下',
      type: 'warning',
    })
    return true
  } catch {
    return false
  }
})

function handlePointChange(pointId: number | string | null | undefined): void {
  const numericId = typeof pointId === 'number' ? pointId : null
  store.lightingPointId = numericId
  store.facilityAssetId = null
  const pt = metadataStore.points.find((p) => p.id === numericId)
  store.regionId = pt ? pt.region_id : null
}

async function handleFileChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const rawList = input.files ? Array.from(input.files) : []
  store.clearPhotoError()
  analyzeSubmit.resetKey()

  const check = await validateStaticImageFiles(rawList)
  if (!check.valid) {
    store.setPhotoError(check.code ?? 'UNSUPPORTED_IMAGE_FORMAT', check.message || '图片校验未通过')
    store.setFiles([])
    input.value = ''
    return
  }

  store.clearPhotoError()
  store.setFiles(rawList)
}

function removeSelectedFile(idx: number): void {
  store.removeFile(idx)
  store.clearPhotoError()
  if (fileInputRef.value) {
    fileInputRef.value.value = ''
  }
  analyzeSubmit.resetKey()
  ticketSubmit.resetKey()
}

async function handleAnalyze(): Promise<void> {
  if (!canAnalyze.value) return
  try {
    activeEvidenceIndex.value = 0
    showAnnotatedImage.value = false
    await analyzeSubmit.run((key, markReplayed) => store.analyze(key, markReplayed))
  } catch (err: unknown) {
    if (
      isApiError(err) &&
      (err.error.code === 'IDEMPOTENCY_PAYLOAD_MISMATCH' ||
        err.error.code === 'IDEMPOTENCY_KEY_INVALID')
    ) {
      analyzeSubmit.resetKey()
    }
  }
}

function bboxStyle(bbox: [number, number, number, number]): Record<string, string> {
  const x1 = Math.max(0, Math.min(1, bbox[0]))
  const y1 = Math.max(0, Math.min(1, bbox[1]))
  const x2 = Math.max(x1, Math.min(1, bbox[2]))
  const y2 = Math.max(y1, Math.min(1, bbox[3]))
  return {
    left: `${x1 * 100}%`,
    top: `${y1 * 100}%`,
    width: `${(x2 - x1) * 100}%`,
    height: `${(y2 - y1) * 100}%`,
  }
}

function getReferenceTagLabel(item: FlattenedCandidateItem): string {
  if (item.candidate.visual_signal_state === 'DAYTIME_UNPOWERED_EXPECTED') {
    return '白天正常灭灯参照 · 不生成故障'
  }
  return '正常亮灯参照 · 不生成故障'
}

function getCandidateDisplayTitle(item: FlattenedCandidateItem): string {
  if (item.decision === 'modified' && item.editedFaultType) {
    return `${FAULT_TYPE_LABELS[item.editedFaultType]}（已人工改判）`
  }
  if (item.candidate.candidate_fault_type) {
    return FAULT_TYPE_LABELS[item.candidate.candidate_fault_type]
  }
  if (item.candidate.visual_signal_state === 'NORMAL_LIT') {
    return '正常亮灯'
  }
  if (item.candidate.visual_signal_state === 'DAYTIME_UNPOWERED_EXPECTED') {
    return '白天正常灭灯'
  }
  return item.candidate.raw_class
}

function getDecisionTagLabel(item: FlattenedCandidateItem): string {
  switch (item.decision) {
    case 'confirmed':
      return '已确认故障'
    case 'modified':
      return `已人工改判：${item.editedFaultType ? FAULT_TYPE_LABELS[item.editedFaultType] : ''}`
    case 'rejected':
      return '已剔除'
    default:
      return item.candidate.is_fault_candidate ? '待巡查员确认' : '正常参照（不生成故障）'
  }
}

function getCardBorderClass(item: FlattenedCandidateItem): string {
  if (!item.candidate.is_fault_candidate) return 'is-ref-card'
  if (
    item.candidate.opencv_roi_metrics.fusion_status === 'low_confidence_conflict' ||
    item.candidate.candidate_fault_type === 'OCCLUDED'
  ) {
    return 'is-warn-fault-card'
  }
  return 'is-fault-card'
}

function getBboxBorderClass(item: FlattenedCandidateItem): string {
  if (!item.candidate.is_fault_candidate) return 'is-reference'
  if (
    item.candidate.opencv_roi_metrics.fusion_status === 'low_confidence_conflict' ||
    item.candidate.candidate_fault_type === 'OCCLUDED'
  ) {
    return 'is-fault-warn'
  }
  return 'is-fault'
}

function confirmCandidate(item: FlattenedCandidateItem): void {
  if (!item.candidate.is_fault_candidate || store.hasQualityFail) return
  store.decideCandidate(item.analysisId, item.candidate.candidate_id, 'confirmed')
  if (item.candidate.candidate_fault_type) {
    store.faultType = item.candidate.candidate_fault_type
  }
}

function openEditCandidate(item: FlattenedCandidateItem): void {
  if (store.hasQualityFail) return
  editingItem.value = item
  editFaultType.value = item.editedFaultType ?? item.candidate.candidate_fault_type ?? 'OTHER'
  editNote.value = item.editedNote ?? ''
  editDialogVisible.value = true
}

function saveEditCandidate(): void {
  if (!editingItem.value) return
  const trimmedNote = editNote.value.trim()
  store.decideCandidate(
    editingItem.value.analysisId,
    editingItem.value.candidate.candidate_id,
    'modified',
    editFaultType.value,
    trimmedNote || undefined,
  )
  if (trimmedNote) {
    store.manualNote = trimmedNote
  }
  if (!store.draftTicket) {
    store.manualMode = true
  }
  editDialogVisible.value = false
}

async function rejectCandidate(item: FlattenedCandidateItem): Promise<void> {
  if (store.hasQualityFail) return
  try {
    await ElMessageBox.confirm(
      `确认剔除候选 #${item.flatIndex + 1}（${getCandidateDisplayTitle(item)}，置信度 ${formatConfidence(item.candidate.fused_confidence)}）？`,
      '剔除候选确认',
      {
        confirmButtonText: '确认剔除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    store.decideCandidate(item.analysisId, item.candidate.candidate_id, 'rejected')
  } catch {
    // User cancelled
  }
}

async function handleSubmit(): Promise<void> {
  if (!canSubmit.value) return
  const confirmedCount = store.faultCandidates.filter((c) => c.decision === 'confirmed').length
  const modifiedCount = store.faultCandidates.filter((c) => c.decision === 'modified').length
  const rejectedCount = store.faultCandidates.filter((c) => c.decision === 'rejected').length
  const pointDisplay = selectedPoint.value
    ? `${selectedPoint.value.point_code} · ${selectedPoint.value.name}`
    : `#${store.lightingPointId}`
  const photoCount = store.files.length || store.evidences.length

  try {
    await ElMessageBox.confirm(
      store.manualMode
        ? `将提交 1 个点位人工报障单（${pointDisplay}，附 ${photoCount} 张静态图片，故障类型：${FAULT_TYPE_LABELS[store.faultType]}），确认提交？`
        : `将提交 1 个故障点位（${pointDisplay}，附 ${photoCount} 张静态图），处置故障候选 ${store.faultCandidates.length} 条（确认 ${confirmedCount} / 修改 ${modifiedCount} / 剔除 ${rejectedCount}）。确认提交并进入待派工？`,
      '提交故障工单确认',
      {
        confirmButtonText: '确认提交',
        cancelButtonText: '再核对一下',
        type: 'info',
      },
    )
  } catch {
    return
  }

  submitting.value = true
  try {
    const created = await ticketSubmit.run((key, markReplayed) =>
      store.submitConfirmedOrManual(key, markReplayed),
    )
    submittedSuccess.value = true
    ElMessage.success(
      ticketSubmit.lastReplayed.value
        ? `工单 ${created.ticket_no} 已存在（幂等重放）`
        : `工单 ${created.ticket_no} 已提交`,
    )
    store.reset()
    await router.push(`/tickets/${created.id}`)
  } catch (err: unknown) {
    if (
      isApiError(err) &&
      (err.error.code === 'IDEMPOTENCY_PAYLOAD_MISMATCH' ||
        err.error.code === 'DUPLICATE_ACTIVE_TICKET' ||
        err.error.code === 'IDEMPOTENCY_KEY_INVALID')
    ) {
      ticketSubmit.resetKey()
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="inspection-page">
    <div class="tomori-page-header">
      <h2 class="tomori-page-title">巡查报障与静态图 AI 候选确认</h2>
      <div class="header-actions">
        <button
          type="button"
          class="tomori-btn"
          data-testid="inspection-manual-mode-toggle"
          @click="store.manualMode = !store.manualMode"
        >
          {{ store.manualMode ? '切回 AI 候选确认模式' : '切换人工填报模式' }}
        </button>
      </div>
    </div>

    <!-- Step 1: Point Selection -->
    <div class="tomori-card">
      <h3 class="section-heading">Step 1 · 选择照明点位与设备上下文</h3>
      <div class="form-grid">
        <div>
          <div class="field-label">照明点位（必填）</div>
          <el-select
            v-model="store.lightingPointId"
            placeholder="请选择授权区域内的照明点位"
            filterable
            :teleported="false"
            style="width: 100%"
            data-testid="inspection-pole-select"
            @change="handlePointChange"
          >
            <el-option
              v-for="pt in visiblePoints"
              :key="pt.id"
              :label="`${pt.point_code} · ${pt.name}`"
              :value="pt.id"
              :data-testid="`inspection-pole-option-${pt.point_code}`"
            />
          </el-select>
          <div v-if="selectedPoint" class="hint-text" style="margin-top: 6px">
            路段：{{ selectedPoint.road_name }}
            <span v-if="selectedPoint.region_name"> · 区域：{{ selectedPoint.region_name }}</span>
          </div>
        </div>

        <div>
          <div class="field-label">关联设施资产（可选）</div>
          <el-select
            v-model="store.facilityAssetId"
            placeholder="未选择（按点位与故障类型分级）"
            clearable
            filterable
            :teleported="false"
            :disabled="!store.lightingPointId"
            style="width: 100%"
          >
            <el-option
              v-for="asset in pointAssets"
              :key="asset.id"
              :label="`${asset.asset_code} · ${asset.name}`"
              :value="asset.id"
            />
          </el-select>
        </div>

        <div>
          <div class="field-label">巡查光照提示</div>
          <el-select
            v-model="store.sceneLightingHint"
            :teleported="false"
            style="width: 100%"
          >
            <el-option
              v-for="opt in SCENE_LIGHTING_OPTIONS"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </div>
      </div>
    </div>

    <!-- Step 2 & 3: Static Photo Batch Upload (1..10, <= 15 MiB) & AI Analyze -->
    <div class="tomori-card">
      <h3 class="section-heading">Step 2 &amp; 3 · 上传静态巡查图片（1–10 张，单张 ≤ 15 MiB）与 AI 分析</h3>
      <p v-if="!store.lightingPointId" class="hint-text" style="margin-top: 0">
        未选择照明点位时上传控件已禁用，请先在 Step 1 选择照明点位。
      </p>

      <div class="upload-action-row">
        <input
          ref="fileInputRef"
          type="file"
          multiple
          accept="image/jpeg,image/png,image/webp"
          class="photo-file-input"
          data-testid="inspection-photo-input"
          :disabled="!store.lightingPointId"
          @change="handleFileChange"
        />

        <button
          type="button"
          class="tomori-btn tomori-btn--primary"
          data-testid="inspection-ai-analyze-btn"
          :disabled="!canAnalyze"
          @click="handleAnalyze"
        >
          {{ store.analyzeInProgress ? '分析中...' : `开始智能分析（已选 ${store.files.length}/10 张）` }}
        </button>

        <span data-testid="inspection-ai-status" class="hint-text">
          <template v-if="store.analyzeInProgress">
            正在分析 {{ store.files.length }} 张静态巡查图片，请稍候...
          </template>
          <template v-else-if="store.visionFallback">
            视觉识别暂不可用（{{ store.visionFallback.code }}），已开启人工报障模式
          </template>
          <template v-else-if="store.hasAnalyzed">
            已完成 {{ store.evidences.length }} 张图分析：故障候选 {{ store.faultCandidates.length }} 条，参照框 {{ store.referenceCandidates.length }} 条
          </template>
          <template v-else>
            仅支持静态 JPEG / PNG / WebP（≤ 15 MiB，≤ 40 MP），严禁视频或实时流
          </template>
        </span>
      </div>

      <div v-if="store.files.length > 0" class="file-chips">
        <div v-for="(file, idx) in store.files" :key="`${file.name}-${idx}`" class="file-chip">
          <span class="file-chip-name">{{ idx + 1 }}. {{ file.name }}（{{ formatFileSize(file.size) }}）</span>
          <button
            type="button"
            class="chip-remove"
            :aria-label="`移除第 ${idx + 1} 张图片 ${file.name}`"
            @click="removeSelectedFile(idx)"
          >
            移除
          </button>
        </div>
      </div>

      <el-alert
        v-if="activePhotoError"
        data-testid="inspection-photo-error"
        :data-code="activePhotoError.code"
        :title="`${activePhotoError.code}: ${activePhotoError.message}`"
        type="error"
        :closable="false"
        show-icon
        style="margin-top: 10px"
      />

      <el-alert
        v-if="store.visionFallback"
        data-testid="inspection-vision-disabled"
        :data-code="store.visionFallback.code"
        data-fallback-action="MANUAL_REPORT"
        :title="`${store.visionFallback.code}：${store.visionFallback.message}`"
        type="info"
        :closable="false"
        show-icon
        style="margin-top: 10px"
      >
        <div>
          兜底动作：<code>data-fallback-action="MANUAL_REPORT"</code> — 已为您开启人工报障模式，请直接在下方 Step 5 填写故障类型与现场描述后提交（已选图片将随工单上传）。
        </div>
      </el-alert>

      <el-alert
        v-if="store.hasBaselineMode"
        data-testid="inspection-baseline-banner"
        title="当前为基线兜底推理模式（baseline_pretrained_opencv），融合置信度已封顶 ≤ 0.65，严禁自动勾选，必须由巡查员逐条人工复核。"
        type="warning"
        :closable="false"
        show-icon
        style="margin-top: 10px"
      />
    </div>

    <!-- Step 4: Disclaimers + Multi-image Overlay + Candidate Cards -->
    <div class="tomori-card">
      <h3 class="section-heading">Step 4 · 多图 bbox_norm 叠加预览与候选逐条人工确认</h3>

      <!-- Always-visible non-collapsible AI safety disclaimers -->
      <el-alert
        data-testid="inspection-ai-disclaimer"
        :title="disclaimerText"
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 10px"
      />
      <div
        data-testid="inspection-ai-requires-confirmation"
        class="confirm-notice-bar"
      >
        本结果仅为 AI 候选，须由巡查员逐条确认后方可生成工单；AI 识别不能替代现场断电与电气安全检查。
      </div>

      <!-- Reference box toggle & disposition progress -->
      <div v-if="store.hasAnalyzed" class="toolbar-row">
        <label class="tomori-checkbox-label">
          <input
            v-model="store.showReferenceBoxes"
            type="checkbox"
            class="tomori-checkbox"
          />
          <span>
            显示夜间亮灯/白天灭灯参照框 (is_fault_candidate=false，共 {{ store.referenceCandidates.length }} 条)
          </span>
        </label>

        <span class="hint-text">
          故障候选处置进度：<strong>{{ store.faultDisposedCount }} / {{ store.faultCandidates.length }}</strong>
        </span>
      </div>

      <div
        v-if="store.hasAnalyzed && store.flattenedCandidates.length === 0"
        data-testid="inspection-candidates-empty"
        class="notice-box notice-box--info"
      >
        未识别到路灯目标，请手动填报
      </div>

      <div
        v-if="store.isAllReferenceOrEmpty"
        data-testid="inspection-no-ticket-notice"
        class="notice-box notice-box--info"
      >
        本次识别（共 {{ store.evidences.length }} 张）未发现故障候选，巡查影像已归档（未建故障单）
      </div>

      <!-- Multi-image Evidence Switcher & Interactive bbox_norm Viewer -->
      <div v-if="activeEvidence" class="evidence-viewer-wrap">
        <div class="evidence-tabs">
          <button
            v-for="(ev, idx) in store.evidences"
            :key="ev.id || idx"
            type="button"
            class="tomori-btn"
            :class="{ 'tomori-btn--primary': activeEvidenceIndex === idx }"
            @click="activeEvidenceIndex = idx"
          >
            第 {{ idx + 1 }} 张
          </button>
        </div>

        <div class="evidence-meta-bar">
          <span v-if="activeEvidenceResult?.image_quality" class="hint-text">
            图像质量：<strong>{{ activeEvidenceResult.image_quality.quality_grade }}</strong> ·
            场景光照：<strong>{{ activeEvidenceResult.image_quality.scene_lighting }}</strong> ·
            分辨率：{{ activeImageWidth }}×{{ activeImageHeight }}
          </span>
          <label
            class="tomori-checkbox-label"
            :class="{ 'is-disabled': !activeAnnotatedUrl }"
          >
            <input
              v-model="showAnnotatedImage"
              type="checkbox"
              class="tomori-checkbox"
              :disabled="!activeAnnotatedUrl"
            />
            <span>查看后端标注图 (annotated_image_url)</span>
          </label>
        </div>

        <el-alert
          v-if="activeEvidenceResult?.image_quality?.quality_grade === 'warn' && activeEvidenceResult.image_quality.warnings?.length"
          type="warning"
          :closable="false"
          show-icon
          style="margin-bottom: 10px"
          :title="`第 ${activeEvidenceIndex + 1} 张图像质量提示：${activeEvidenceResult.image_quality.warnings.join('；')}`"
        />

        <div class="bbox-canvas-container">
          <AuthImage
            :evidence-id="activeEvidence.id"
            :variant="effectiveShowAnnotated ? 'annotated' : 'raw'"
            :src="effectiveShowAnnotated ? activeAnnotatedUrl : activeOriginalUrl"
            :aspect-ratio="`${activeImageWidth} / ${activeImageHeight}`"
          >
            <template v-if="!effectiveShowAnnotated">
              <div
                v-for="item in activeEvidenceOverlayItems"
                :key="`box-${item.flatIndex}`"
                :data-testid="`inspection-candidate-bbox-${item.flatIndex}`"
                :data-is-fault-candidate="String(item.candidate.is_fault_candidate)"
                class="bbox-overlay-rect"
                :class="getBboxBorderClass(item)"
                :style="bboxStyle(item.candidate.bbox_norm)"
              >
                <span
                  class="bbox-badge"
                  :class="{ 'is-inside': item.candidate.bbox_norm[1] < 0.08 }"
                >
                  #{{ item.flatIndex + 1 }} {{ getCandidateDisplayTitle(item) }}
                </span>
              </div>
            </template>
          </AuthImage>
        </div>

        <p
          v-if="store.flattenedCandidates.length > 0 && activeEvidenceCandidates.length === 0"
          class="hint-text"
          style="margin-top: 8px"
        >
          当前第 {{ activeEvidenceIndex + 1 }} 张图像未检出候选框，请切换上方其他图片标签查看。
        </p>
      </div>

      <!-- Candidate Review Cards -->
      <div data-testid="inspection-candidates-list" class="candidate-grid">
        <div
          v-for="item in visibleCandidateCards"
          :key="`${item.analysisId}-${item.candidate.candidate_id}`"
          :data-testid="`inspection-candidate-item-${item.flatIndex}`"
          :data-is-fault-candidate="String(item.candidate.is_fault_candidate)"
          :data-fusion-status="item.candidate.opencv_roi_metrics.fusion_status"
          :data-decision="item.decision"
          class="candidate-card"
          :class="getCardBorderClass(item)"
        >
          <div class="candidate-header">
            <div class="candidate-title-wrap">
              <el-tag
                v-if="item.candidate.is_fault_candidate"
                type="danger"
                size="small"
              >
                故障候选
              </el-tag>
              <el-tag
                v-else
                type="info"
                size="small"
              >
                {{ getReferenceTagLabel(item) }}
              </el-tag>
              <span
                :data-testid="`inspection-candidate-label-${item.flatIndex}`"
                class="candidate-label-text"
              >
                #{{ item.flatIndex + 1 }} · {{ getCandidateDisplayTitle(item) }}
              </span>
            </div>

            <div class="confidence-wrap">
              <span class="confidence-prefix">置信度</span>
              <span
                :data-testid="`inspection-candidate-confidence-${item.flatIndex}`"
                class="confidence-text"
              >
                {{ formatConfidence(item.candidate.fused_confidence) }}
              </span>
            </div>
          </div>

          <div class="candidate-meta">
            <button
              type="button"
              class="evidence-jump-btn"
              @click="activeEvidenceIndex = item.evidenceIndex"
            >
              第 {{ item.evidenceIndex + 1 }} 张图
            </button>
            <span>处置状态：<strong>{{ getDecisionTagLabel(item) }}</strong></span>
          </div>

          <p class="candidate-summary">{{ item.candidate.diagnostic_summary }}</p>

          <el-alert
            v-if="item.candidate.opencv_roi_metrics.fusion_status === 'low_confidence_conflict'"
            :data-testid="`inspection-candidate-conflict-${item.flatIndex}`"
            type="warning"
            :closable="false"
            show-icon
            style="margin-bottom: 10px"
          >
            AI 判断存在物理矛盾，请重点复核
          </el-alert>

          <div class="candidate-actions">
            <button
              type="button"
              class="tomori-btn tomori-btn--primary"
              :data-testid="`inspection-candidate-confirm-${item.flatIndex}`"
              :disabled="!item.candidate.is_fault_candidate || store.hasQualityFail"
              :title="item.candidate.is_fault_candidate ? '确认为故障' : '正常参照框不可直接确认成故障，请使用修改或剔除'"
              @click="confirmCandidate(item)"
            >
              确认故障
            </button>
            <button
              type="button"
              class="tomori-btn"
              :data-testid="`inspection-candidate-edit-${item.flatIndex}`"
              :disabled="store.hasQualityFail"
              @click="openEditCandidate(item)"
            >
              修改判定
            </button>
            <button
              type="button"
              class="tomori-btn tomori-btn--danger"
              :data-testid="`inspection-candidate-reject-${item.flatIndex}`"
              :disabled="store.hasQualityFail"
              @click="rejectCandidate(item)"
            >
              剔除
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Step 5: Fault Form & Hard-Gated Submit -->
    <div class="tomori-card">
      <h3 class="section-heading">Step 5 · 故障详情填报与提交</h3>
      <div class="form-grid">
        <div>
          <div class="field-label">故障类型</div>
          <el-select
            v-model="store.faultType"
            :teleported="false"
            style="width: 100%"
            data-testid="inspection-fault-type-select"
          >
            <el-option
              v-for="opt in FAULT_TYPE_OPTIONS"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </div>

        <div>
          <div class="field-label">工单标题（可选）</div>
          <input
            v-model="store.title"
            type="text"
            class="tomori-input"
            placeholder="工单标题（可选）"
          />
        </div>
      </div>

      <div style="margin-top: 12px">
        <div class="field-label">现场情况描述</div>
        <textarea
          v-model="store.description"
          class="tomori-textarea"
          data-testid="inspection-fault-desc-input"
          placeholder="现场情况描述"
        />
      </div>

      <div v-if="requiresManualNote" style="margin-top: 12px">
        <div class="field-label" style="color: var(--tomori-danger)">
          人工判定依据（闪烁 / 电气安全隐患必填）
        </div>
        <textarea
          v-model="store.manualNote"
          class="tomori-textarea"
          data-testid="inspection-manual-note-input"
          placeholder="请填写人工判定依据（闪烁/电气隐患必填）"
        />
        <div v-if="store.manualNote.trim().length === 0" class="tomori-field-error">
          AI 严禁自动判定闪烁或电气安全隐患，请先填写现场人工判定依据
        </div>
      </div>

      <div style="margin-top: 12px">
        <label class="tomori-checkbox-label">
          <input v-model="store.safetyFlag" type="checkbox" class="tomori-checkbox" />
          <span>标记存在紧急公共安全风险（safety_flag，触发 P1 特急分级）</span>
        </label>
      </div>

      <div class="submit-row">
        <button
          type="button"
          class="tomori-btn tomori-btn--success"
          data-testid="inspection-submit-btn"
          :disabled="!canSubmit"
          @click="handleSubmit"
        >
          {{ submitting || ticketSubmit.isSubmitting.value ? '提交中...' : store.manualMode ? '提交人工报障单' : '确认候选并提交工单' }}
        </button>
        <span v-if="!canSubmit && submitGateHint" class="hint-text">
          {{ submitGateHint }}
        </span>
      </div>
    </div>

    <!-- Edit candidate modal (:teleported="false") -->
    <el-dialog
      v-model="editDialogVisible"
      title="修改 AI 候选判定"
      width="min(92vw, 420px)"
      :teleported="false"
    >
      <div style="display: flex; flex-direction: column; gap: 12px">
        <div>
          <div class="field-label">改判故障类型</div>
          <el-select v-model="editFaultType" :teleported="false" style="width: 100%">
            <el-option
              v-for="opt in FAULT_TYPE_OPTIONS"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
          <div
            v-if="MANUAL_NOTE_REQUIRED_FAULT_TYPES.includes(editFaultType)"
            class="hint-text"
            style="margin-top: 4px; color: var(--tomori-warning)"
          >
            闪烁与电气隐患提交前须在 Step 5（或下方说明）填写人工判定依据
          </div>
        </div>
        <div>
          <div class="field-label">改判说明（可选）</div>
          <textarea
            v-model="editNote"
            class="tomori-textarea"
            placeholder="可填写修改理由或现场人工依据"
          />
        </div>
      </div>
      <template #footer>
        <button type="button" class="tomori-btn" @click="editDialogVisible = false">取消</button>
        <button type="button" class="tomori-btn tomori-btn--primary" @click="saveEditCandidate">保存改判</button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.inspection-page {
  min-width: 0;
}

.header-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.section-heading {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 220px), 1fr));
  gap: 12px;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--tomori-text-secondary);
}

.hint-text {
  font-size: 12px;
  color: var(--tomori-text-muted);
  line-height: 1.5;
}

.upload-action-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.photo-file-input {
  max-width: 100%;
  font-size: 13px;
}

.file-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.file-chip {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  max-width: 100%;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
}

.file-chip-name {
  word-break: break-word;
}

.chip-remove {
  border: none;
  background: transparent;
  color: var(--tomori-danger);
  cursor: pointer;
  font-size: 12px;
  padding: 0 4px;
}

.confirm-notice-bar {
  background: #fffbeb;
  border: 1px solid #fde68a;
  color: #92400e;
  border-radius: 4px;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.5;
  margin-bottom: 12px;
}

.toolbar-row {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.notice-box {
  border-radius: 4px;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.5;
  margin-bottom: 12px;
}

.notice-box--info {
  background: #f4f4f5;
  border: 1px solid #e9e9eb;
  color: #606266;
}

.evidence-viewer-wrap {
  margin-bottom: 16px;
}

.evidence-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.evidence-meta-bar {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.bbox-canvas-container {
  position: relative;
  width: 100%;
  max-width: 720px;
}

.bbox-overlay-rect {
  position: absolute;
  pointer-events: none;
  box-sizing: border-box;
}

.bbox-overlay-rect.is-fault {
  border: 2px solid #f56c6c;
  background: rgba(245, 108, 108, 0.14);
}

.bbox-overlay-rect.is-fault-warn {
  border: 2px solid #e6a23c;
  background: rgba(230, 162, 60, 0.14);
}

.bbox-overlay-rect.is-reference {
  border: 2px dashed #909399;
  background: rgba(144, 147, 153, 0.12);
}

.bbox-badge {
  position: absolute;
  top: -20px;
  left: 0;
  background: rgba(17, 24, 39, 0.85);
  color: #ffffff;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  white-space: nowrap;
}

.bbox-badge.is-inside {
  top: 2px;
  left: 2px;
}

.candidate-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr));
  gap: 12px;
}

.candidate-card {
  border-radius: 6px;
  padding: 12px;
  background: #ffffff;
  min-width: 0;
}

.candidate-card.is-fault-card {
  border: 2px solid #f56c6c;
}

.candidate-card.is-warn-fault-card {
  border: 2px solid #e6a23c;
}

.candidate-card.is-ref-card {
  border: 2px dashed #909399;
  background: #f8fafc;
}

.candidate-header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 6px;
}

.candidate-title-wrap {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.candidate-label-text {
  font-weight: 600;
  font-size: 14px;
  word-break: break-word;
}

.confidence-wrap {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
  font-size: 12px;
  color: var(--tomori-text-secondary);
}

.confidence-text {
  font-weight: 600;
}

.candidate-meta {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--tomori-text-secondary);
  margin-bottom: 8px;
}

.evidence-jump-btn {
  border: 1px solid #dcdfe6;
  background: #f5f7fa;
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 12px;
  color: var(--tomori-primary);
  cursor: pointer;
}

.candidate-summary {
  margin: 0 0 10px;
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
}

.candidate-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.submit-row {
  margin-top: 16px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
</style>
