import { apiClient } from '@/shared/api'
import type {
  AiCandidatesResponseOut,
  EvidenceType,
  FaultEvidenceOut,
  FaultTicketDetailOut,
  FaultTicketOut,
  FaultType,
  PaginatedResponse,
  PriorityCode,
  SceneLightingHint,
  TicketStatus,
} from '@/shared/types'

export interface TicketListParams {
  status?: TicketStatus
  priority?: PriorityCode
  region_id?: number
  lighting_point_id?: number
  reporter_account_id?: number
  technician_account_id?: number
  is_sla_overdue?: boolean
  keyword?: string
  created_from?: string
  created_to?: string
  page?: number
  page_size?: number
}

export interface CreateManualTicketJsonPayload {
  lighting_point_id: number
  facility_asset_id?: number | null
  fault_type: FaultType
  title: string
  description?: string | null
  safety_flag?: boolean
  manual_note?: string | null
  evidence_ids?: number[]
  candidate_decisions?: CandidateDecisionPayload[]
}

export interface UpdateTicketDraftPayload {
  title?: string
  description?: string | null
  fault_type?: FaultType
  facility_asset_id?: number | null
  manual_note?: string | null
}

export interface CandidateDecisionPayload {
  evidence_id: number
  candidate_id: string
  decision: 'confirmed' | 'modified' | 'rejected'
  fault_type?: FaultType
  manual_note?: string
}

export interface ConfirmTicketPayload {
  candidate_decisions: CandidateDecisionPayload[]
  fault_type: FaultType
  priority_override?: PriorityCode | null
  safety_flag?: boolean
  manual_note?: string | null
  title?: string
  description?: string | null
}

export async function analyzeAiCandidatesApi(
  params: {
    files: File[]
    lighting_point_id?: number
    region_id?: number
    facility_asset_id?: number
    scene_lighting_hint?: SceneLightingHint
  },
  idempotencyKey: string,
  onReplay?: () => void,
): Promise<AiCandidatesResponseOut> {
  const form = new FormData()
  for (const file of params.files) {
    form.append('files', file)
  }
  if (params.lighting_point_id !== undefined) {
    form.append('lighting_point_id', String(params.lighting_point_id))
  }
  if (params.region_id !== undefined) {
    form.append('region_id', String(params.region_id))
  }
  if (params.facility_asset_id !== undefined) {
    form.append('facility_asset_id', String(params.facility_asset_id))
  }
  form.append('scene_lighting_hint', params.scene_lighting_hint ?? 'auto')

  const res = await apiClient.post<AiCandidatesResponseOut>('/tickets/ai-candidates', form, {
    headers: { 'Idempotency-Key': idempotencyKey },
  })
  if (String(res.headers['x-idempotent-replay']).toLowerCase() === 'true') {
    onReplay?.()
  }
  return res.data
}

export async function createManualTicketJsonApi(
  payload: CreateManualTicketJsonPayload,
  idempotencyKey: string,
  onReplay?: () => void,
): Promise<FaultTicketOut> {
  const res = await apiClient.post<FaultTicketOut>('/tickets', payload, {
    headers: { 'Idempotency-Key': idempotencyKey },
  })
  if (String(res.headers['x-idempotent-replay']).toLowerCase() === 'true') {
    onReplay?.()
  }
  return res.data
}

export async function createManualTicketMultipartApi(
  params: CreateManualTicketJsonPayload & { files: File[] },
  idempotencyKey: string,
  onReplay?: () => void,
): Promise<FaultTicketOut> {
  const form = new FormData()
  form.append('lighting_point_id', String(params.lighting_point_id))
  if (params.facility_asset_id !== undefined && params.facility_asset_id !== null) {
    form.append('facility_asset_id', String(params.facility_asset_id))
  }
  form.append('fault_type', params.fault_type)
  form.append('title', params.title)
  if (params.description) form.append('description', params.description)
  if (params.safety_flag !== undefined) form.append('safety_flag', String(params.safety_flag))
  if (params.manual_note) form.append('manual_note', params.manual_note)
  for (const file of params.files) {
    form.append('files', file)
  }

  const res = await apiClient.post<FaultTicketOut>('/tickets', form, {
    headers: { 'Idempotency-Key': idempotencyKey },
  })
  if (String(res.headers['x-idempotent-replay']).toLowerCase() === 'true') {
    onReplay?.()
  }
  return res.data
}

export async function listTicketsApi(
  params?: TicketListParams,
): Promise<PaginatedResponse<FaultTicketOut>> {
  const { data } = await apiClient.get<PaginatedResponse<FaultTicketOut>>('/tickets', { params })
  return data
}

export async function getTicketDetailApi(ticketId: number): Promise<FaultTicketDetailOut> {
  const { data } = await apiClient.get<FaultTicketDetailOut>(`/tickets/${ticketId}`)
  return data
}

export async function patchTicketApi(
  ticketId: number,
  payload: UpdateTicketDraftPayload,
): Promise<FaultTicketOut> {
  const { data } = await apiClient.patch<FaultTicketOut>(`/tickets/${ticketId}`, payload)
  return data
}

export async function confirmTicketApi(
  ticketId: number,
  payload: ConfirmTicketPayload,
  idempotencyKey: string,
  onReplay?: () => void,
): Promise<FaultTicketOut> {
  const res = await apiClient.post<FaultTicketOut>(`/tickets/${ticketId}/confirm`, payload, {
    headers: { 'Idempotency-Key': idempotencyKey },
  })
  if (String(res.headers['x-idempotent-replay']).toLowerCase() === 'true') {
    onReplay?.()
  }
  return res.data
}

export async function cancelTicketApi(
  ticketId: number,
  cancelReason: string,
  idempotencyKey: string,
  onReplay?: () => void,
): Promise<FaultTicketOut> {
  const res = await apiClient.post<FaultTicketOut>(
    `/tickets/${ticketId}/cancel`,
    { cancel_reason: cancelReason },
    { headers: { 'Idempotency-Key': idempotencyKey } },
  )
  if (String(res.headers['x-idempotent-replay']).toLowerCase() === 'true') {
    onReplay?.()
  }
  return res.data
}

export async function regradeTicketApi(
  ticketId: number,
  payload: { priority: PriorityCode; reason: string },
  idempotencyKey: string,
  onReplay?: () => void,
): Promise<FaultTicketOut> {
  const res = await apiClient.post<FaultTicketOut>(`/tickets/${ticketId}/regrade`, payload, {
    headers: { 'Idempotency-Key': idempotencyKey },
  })
  if (String(res.headers['x-idempotent-replay']).toLowerCase() === 'true') {
    onReplay?.()
  }
  return res.data
}

export async function listTicketEvidencesApi(ticketId: number): Promise<FaultEvidenceOut[]> {
  const { data } = await apiClient.get<FaultEvidenceOut[] | PaginatedResponse<FaultEvidenceOut>>(
    `/tickets/${ticketId}/evidences`,
  )
  return Array.isArray(data) ? data : data.items
}

export async function uploadTicketEvidencesApi(
  ticketId: number,
  files: File[],
  evidenceType: EvidenceType,
): Promise<FaultEvidenceOut[]> {
  const form = new FormData()
  for (const f of files) {
    form.append('files', f)
  }
  form.append('evidence_type', evidenceType)
  const { data } = await apiClient.post<FaultEvidenceOut[] | { evidences: FaultEvidenceOut[] }>(
    `/tickets/${ticketId}/evidences`,
    form,
  )
  return Array.isArray(data) ? data : data.evidences
}
