import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  analyzeAiCandidatesApi,
  confirmTicketApi,
  createManualTicketJsonApi,
  createManualTicketMultipartApi,
} from '@/api/tickets'
import { isApiError } from '@/shared/api'
import type {
  AiCandidatesResponseOut,
  CandidateDecision,
  FaultCandidateOut,
  FaultCandidateResultOut,
  FaultEvidenceOut,
  FaultTicketOut,
  FaultType,
  SceneLightingHint,
} from '@/shared/types'

export interface CandidateDecisionEntry {
  decision: CandidateDecision
  editedFaultType?: FaultType
  editedNote?: string
}

export interface FlattenedCandidateItem {
  flatIndex: number
  evidenceIndex: number
  evidenceId: number
  analysisId: string
  candidate: FaultCandidateOut
  decision: CandidateDecision
  editedFaultType?: FaultType
  editedNote?: string
}

export const useInspectionStore = defineStore('inspection', () => {
  const lightingPointId = ref<number | null>(null)
  const regionId = ref<number | null>(null)
  const facilityAssetId = ref<number | null>(null)
  const sceneLightingHint = ref<SceneLightingHint>('auto')
  const files = ref<File[]>([])
  const evidences = ref<Array<FaultEvidenceOut & Partial<FaultCandidateResultOut>>>([])
  const draftTicket = ref<FaultTicketOut | null>(null)
  const decisions = ref<Record<string, CandidateDecisionEntry>>({})

  const manualMode = ref(false)
  const faultType = ref<FaultType>('LAMP_OUT')
  const title = ref('')
  const description = ref('')
  const manualNote = ref('')
  const safetyFlag = ref(false)
  const showReferenceBoxes = ref(true)

  const photoError = ref<{ code: string; message: string } | null>(null)
  const visionFallback = ref<{ code: string; message: string } | null>(null)
  const analyzeInProgress = ref(false)
  const hasAnalyzed = ref(false)

  const flattenedCandidates = computed<FlattenedCandidateItem[]>(() => {
    const out: FlattenedCandidateItem[] = []
    let flatIndex = 0
    evidences.value.forEach((ev, evidenceIndex) => {
      const result: FaultCandidateResultOut | null | undefined = ev.ai_result_json ?? (ev.candidates ? (ev as FaultCandidateResultOut) : null)
      const list = result?.candidates ?? []
      const analysisId = result?.analysis_id ?? ev.analysis_id ?? String(ev.id)
      for (const candidate of list) {
        const key = `${analysisId}:${candidate.candidate_id}`
        const entry = decisions.value[key] ?? { decision: 'pending' }
        out.push({
          flatIndex,
          evidenceIndex,
          evidenceId: ev.id,
          analysisId,
          candidate,
          decision: entry.decision,
          editedFaultType: entry.editedFaultType,
          editedNote: entry.editedNote,
        })
        flatIndex += 1
      }
    })
    return out
  })

  const faultCandidates = computed(() =>
    flattenedCandidates.value.filter((item) => item.candidate.is_fault_candidate),
  )

  const referenceCandidates = computed(() =>
    flattenedCandidates.value.filter((item) => !item.candidate.is_fault_candidate),
  )

  const hasBaselineMode = computed(() =>
    evidences.value.some((ev) => {
      const res = ev.ai_result_json ?? ev
      return res.inference_mode === 'baseline_pretrained_opencv' || ev.ai_is_baseline_mode === true
    }),
  )

  const hasQualityFail = computed(() =>
    evidences.value.some((ev) => {
      const res = ev.ai_result_json ?? ev
      return res.image_quality?.quality_grade === 'fail'
    }),
  )

  const isAllReferenceOrEmpty = computed(
    () => hasAnalyzed.value && draftTicket.value === null && faultCandidates.value.length === 0,
  )

  const faultDisposedCount = computed(
    () => faultCandidates.value.filter((item) => item.decision !== 'pending').length,
  )

  const allFaultCandidatesDisposed = computed(
    () =>
      faultCandidates.value.length > 0 &&
      faultCandidates.value.every((item) => item.decision !== 'pending'),
  )

  function setFiles(nextFiles: File[]): void {
    files.value = nextFiles
  }

  function removeFile(idx: number): void {
    files.value = files.value.filter((_, i) => i !== idx)
  }

  function clearPhotoError(): void {
    photoError.value = null
  }

  function setPhotoError(code: string, message: string): void {
    photoError.value = { code, message }
  }

  function applyAnalysisResponse(res: AiCandidatesResponseOut): void {
    draftTicket.value = res.ticket
    evidences.value = res.evidences
    hasAnalyzed.value = true
    visionFallback.value = null
    decisions.value = {}

    for (const ev of res.evidences) {
      const result = ev.ai_result_json ?? (ev.candidates ? (ev as FaultCandidateResultOut) : null)
      const analysisId = result?.analysis_id ?? ev.analysis_id ?? String(ev.id)
      for (const cand of result?.candidates ?? []) {
        decisions.value[`${analysisId}:${cand.candidate_id}`] = { decision: 'pending' }
      }
    }

    if (res.ticket) {
      faultType.value = res.ticket.fault_type
      title.value = res.ticket.title
      description.value = res.ticket.description ?? ''
    } else {
      const firstSuggestion = res.evidences
        .map((ev) => (ev.ai_result_json ?? ev).primary_fault_type_suggestion)
        .find((s) => s !== null && s !== undefined)
      if (firstSuggestion) {
        faultType.value = firstSuggestion
      }
    }
  }

  async function analyze(idempotencyKey: string, onReplay?: () => void): Promise<AiCandidatesResponseOut> {
    analyzeInProgress.value = true
    visionFallback.value = null
    photoError.value = null
    try {
      const res = await analyzeAiCandidatesApi(
        {
          files: files.value,
          lighting_point_id: lightingPointId.value ?? undefined,
          region_id: regionId.value ?? undefined,
          scene_lighting_hint: sceneLightingHint.value,
        },
        idempotencyKey,
        onReplay,
      )
      applyAnalysisResponse(res)
      return res
    } catch (err) {
      if (isApiError(err)) {
        const code = err.error.code
        if (err.httpStatus === 503 && (code === 'VISION_DISABLED' || code === 'VISION_TIMEOUT' || code === 'VISION_INFERENCE_FAILED')) {
          visionFallback.value = {
            code,
            message: err.error.message || '智能识别当前不可用，请切换手动填报',
          }
          manualMode.value = true
        } else if (
          code === 'UNSUPPORTED_IMAGE_FORMAT' ||
          code === 'MEDIA_VIDEO_STREAM_FORBIDDEN' ||
          code === 'IMAGE_SIZE_EXCEEDED' ||
          code === 'IMAGE_PIXEL_LIMIT_EXCEEDED' ||
          code === 'TOO_MANY_FILES' ||
          code === 'NO_FILES'
        ) {
          photoError.value = {
            code,
            message: err.error.message || code,
          }
        }
      }
      throw err
    } finally {
      analyzeInProgress.value = false
    }
  }

  function decideCandidate(
    analysisId: string,
    candidateId: string,
    decision: CandidateDecision,
    editedFaultType?: FaultType,
    editedNote?: string,
  ): void {
    const key = `${analysisId}:${candidateId}`
    decisions.value[key] = {
      decision,
      editedFaultType,
      editedNote,
    }
    if (decision === 'modified' && editedFaultType) {
      faultType.value = editedFaultType
    }
  }

  async function submitConfirmedOrManual(
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<FaultTicketOut> {
    if (draftTicket.value && !manualMode.value) {
      return confirmTicketApi(
        draftTicket.value.id,
        {
          fault_type: faultType.value,
          safety_flag: safetyFlag.value,
          manual_note: manualNote.value.trim() || undefined,
          title: title.value.trim() || undefined,
          description: description.value.trim() || undefined,
        },
        idempotencyKey,
        onReplay,
      )
    }

    if (!lightingPointId.value) {
      throw new Error('LIGHTING_POINT_REQUIRED')
    }

    const basePayload = {
      lighting_point_id: lightingPointId.value,
      facility_asset_id: facilityAssetId.value ?? undefined,
      fault_type: faultType.value,
      title: title.value.trim() || `${FAULT_DEFAULT_TITLE[faultType.value]}工单`,
      description: description.value.trim() || undefined,
      safety_flag: safetyFlag.value,
      manual_note: manualNote.value.trim() || undefined,
    }

    if (files.value.length > 0) {
      return createManualTicketMultipartApi(
        { ...basePayload, files: files.value },
        idempotencyKey,
        onReplay,
      )
    }

    const evidenceIds = evidences.value.map((e) => e.id).filter((id) => typeof id === 'number')
    return createManualTicketJsonApi(
      { ...basePayload, evidence_ids: evidenceIds.length > 0 ? evidenceIds : undefined },
      idempotencyKey,
      onReplay,
    )
  }

  function reset(): void {
    files.value = []
    evidences.value = []
    draftTicket.value = null
    decisions.value = {}
    manualMode.value = false
    faultType.value = 'LAMP_OUT'
    title.value = ''
    description.value = ''
    manualNote.value = ''
    safetyFlag.value = false
    photoError.value = null
    visionFallback.value = null
    hasAnalyzed.value = false
    showReferenceBoxes.value = true
  }

  return {
    lightingPointId,
    regionId,
    facilityAssetId,
    sceneLightingHint,
    files,
    evidences,
    draftTicket,
    decisions,
    manualMode,
    faultType,
    title,
    description,
    manualNote,
    safetyFlag,
    showReferenceBoxes,
    photoError,
    visionFallback,
    analyzeInProgress,
    hasAnalyzed,
    flattenedCandidates,
    faultCandidates,
    referenceCandidates,
    hasBaselineMode,
    hasQualityFail,
    isAllReferenceOrEmpty,
    faultDisposedCount,
    allFaultCandidatesDisposed,
    setFiles,
    removeFile,
    clearPhotoError,
    setPhotoError,
    analyze,
    decideCandidate,
    submitConfirmedOrManual,
    reset,
  }
})

const FAULT_DEFAULT_TITLE: Record<FaultType, string> = {
  LAMP_OUT: '路灯灭灯故障',
  FIXTURE_DAMAGED: '灯具破损故障',
  OCCLUDED: '照明遮挡故障',
  ELECTRIC_HAZARD: '电气安全隐患',
  FLICKERING: '路灯闪烁故障',
  OTHER: '公共照明异常',
}
