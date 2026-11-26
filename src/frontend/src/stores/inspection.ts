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
  const selectedMainFaultType = ref<FaultType | null>(null)
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
        const key = `${ev.id}:${candidate.candidate_id}`
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

  const retainedFaultTypes = computed<FaultType[]>(() => {
    const types = flattenedCandidates.value.map((item) => {
      if (item.decision === 'confirmed' && item.candidate.is_fault_candidate) return item.candidate.candidate_fault_type
      if (item.decision === 'modified') return item.editedFaultType
      return null
    })
    return [...new Set(types.filter((type): type is FaultType => type != null))]
  })

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
    () => faultCandidates.value.every((item) => item.decision !== 'pending'),
  )

  const allFaultCandidatesRejected = computed(
    () =>
      faultCandidates.value.length > 0 &&
      faultCandidates.value.every((item) => item.decision === 'rejected'),
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
    selectedMainFaultType.value = null

    for (const ev of res.evidences) {
      const result = ev.ai_result_json ?? (ev.candidates ? (ev as FaultCandidateResultOut) : null)
      for (const cand of result?.candidates ?? []) {
        decisions.value[`${ev.id}:${cand.candidate_id}`] = { decision: 'pending' }
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

  function resumeDraft(ticket: FaultTicketOut, draftEvidences: FaultEvidenceOut[]): void {
    reset()
    lightingPointId.value = ticket.lighting_point_id
    regionId.value = ticket.region_id
    facilityAssetId.value = ticket.facility_asset_id
    applyAnalysisResponse({ ticket, evidences: draftEvidences.map((ev) => ({ ...ev, analysis_id: ev.analysis_id ?? String(ev.id) })) })
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
          facility_asset_id: facilityAssetId.value ?? undefined,
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
    evidenceId: number,
    candidateId: string,
    decision: CandidateDecision,
    editedFaultType?: FaultType,
    editedNote?: string,
  ): void {
    const key = `${evidenceId}:${candidateId}`
    decisions.value[key] = { decision, editedFaultType, editedNote }
    selectedMainFaultType.value = null
  }

  async function submitConfirmedOrManual(
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<FaultTicketOut> {
    if (draftTicket.value && !manualMode.value) {
      if (!allFaultCandidatesDisposed.value || !selectedMainFaultType.value || !retainedFaultTypes.value.includes(selectedMainFaultType.value)) {
        throw new Error('CANDIDATE_DECISIONS_INCOMPLETE')
      }
      const candidateDecisions = flattenedCandidates.value.filter((item) => item.candidate.is_fault_candidate || item.decision !== 'pending').map((item) => {
        if (item.decision === 'pending') throw new Error('CANDIDATE_DECISIONS_INCOMPLETE')
        if (item.decision === 'modified') {
          if (!item.editedFaultType) throw new Error('CANDIDATE_DECISIONS_INCOMPLETE')
          return { evidence_id: item.evidenceId, candidate_id: item.candidate.candidate_id, decision: item.decision, fault_type: item.editedFaultType, manual_note: item.editedNote }
        }
        return { evidence_id: item.evidenceId, candidate_id: item.candidate.candidate_id, decision: item.decision }
      })
      return confirmTicketApi(
        draftTicket.value.id,
        {
          candidate_decisions: candidateDecisions,
          fault_type: selectedMainFaultType.value,
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

    if (hasAnalyzed.value && !draftTicket.value && evidences.value.length > 0) {
      const candidateDecisions = referenceCandidates.value
        .filter((item) => item.decision === 'modified' || item.decision === 'rejected')
        .map((item) => {
          if (item.decision === 'modified') {
            if (!item.editedFaultType) throw new Error('CANDIDATE_DECISIONS_INCOMPLETE')
            return { evidence_id: item.evidenceId, candidate_id: item.candidate.candidate_id, decision: 'modified' as const, fault_type: item.editedFaultType, manual_note: item.editedNote }
          }
          return { evidence_id: item.evidenceId, candidate_id: item.candidate.candidate_id, decision: 'rejected' as const }
        })
      return createManualTicketJsonApi({ ...basePayload, evidence_ids: evidences.value.map((ev) => ev.id), candidate_decisions: candidateDecisions }, idempotencyKey, onReplay)
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

  // 草稿与候选绑定分析时的点位：更换点位必须显式作废分析产物
  // （保留已选图片与填报内容，便于直接重新分析或转人工报障）。
  function discardAnalysis(): void {
    evidences.value = []
    draftTicket.value = null
    decisions.value = {}
    selectedMainFaultType.value = null
    hasAnalyzed.value = false
    visionFallback.value = null
  }

  function reset(): void {
    files.value = []
    evidences.value = []
    draftTicket.value = null
    decisions.value = {}
    selectedMainFaultType.value = null
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
    selectedMainFaultType,
    retainedFaultTypes,
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
    allFaultCandidatesRejected,
    setFiles,
    removeFile,
    clearPhotoError,
    setPhotoError,
    analyze,
    resumeDraft,
    decideCandidate,
    submitConfirmedOrManual,
    discardAnalysis,
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
