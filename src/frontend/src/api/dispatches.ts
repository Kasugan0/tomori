import { apiClient } from '@/shared/api'
import type {
  DispatchCandidatesResponseOut,
  DispatchRecordOut,
  DispatchStatus,
  FaultTicketOut,
  MaterialUsageOut,
  PaginatedResponse,
  RepairProcessOut,
  SafetyChecklistPayload,
} from '@/shared/types'

export interface DispatchTicketPayload {
  technician_account_id: number
  scope_authorization_id: number
  dispatch_mode?: 'MANUAL' | 'AUTO_RECOMMENDED'
  remark?: string | null
}

export interface ReassignTicketPayload {
  technician_account_id: number
  scope_authorization_id: number
  reason: string
  escalate?: boolean
}

export interface DispatchListParams {
  status?: DispatchStatus
  ticket_id?: number
  technician_account_id?: number
  page?: number
  page_size?: number
}

export interface CompleteRepairPayload {
  electrical_safety_checked: boolean
  safety_checklist: SafetyChecklistPayload
  safety_note: string
  arrived_at: string
  root_cause?: string | null
  repair_summary: string
  materials: Array<{
    material_code: string
    material_name: string
    quantity: number
    unit: 'PCS' | 'M' | 'KG' | 'SET'
    unit_price?: number | null
  }>
}

export interface CompleteRepairResponseOut {
  dispatch: DispatchRecordOut
  ticket: FaultTicketOut
  repair_process_id: number
  material_usage_ids: number[]
}

export async function getDispatchCandidatesApi(
  ticketId: number,
): Promise<DispatchCandidatesResponseOut> {
  const { data } = await apiClient.get<DispatchCandidatesResponseOut>(
    `/tickets/${ticketId}/dispatch-candidates`,
  )
  return data
}

export async function dispatchTicketApi(
  ticketId: number,
  payload: DispatchTicketPayload,
  idempotencyKey: string,
  onReplay?: () => void,
): Promise<DispatchRecordOut> {
  const res = await apiClient.post<DispatchRecordOut>(`/tickets/${ticketId}/dispatch`, payload, {
    headers: { 'Idempotency-Key': idempotencyKey },
  })
  if (String(res.headers['x-idempotent-replay']).toLowerCase() === 'true') {
    onReplay?.()
  }
  return res.data
}

export async function reassignTicketApi(
  ticketId: number,
  payload: ReassignTicketPayload,
  idempotencyKey: string,
  onReplay?: () => void,
): Promise<DispatchRecordOut> {
  const res = await apiClient.post<DispatchRecordOut>(`/tickets/${ticketId}/reassign`, payload, {
    headers: { 'Idempotency-Key': idempotencyKey },
  })
  if (String(res.headers['x-idempotent-replay']).toLowerCase() === 'true') {
    onReplay?.()
  }
  return res.data
}

export async function listDispatchesApi(
  params?: DispatchListParams,
): Promise<PaginatedResponse<DispatchRecordOut>> {
  const { data } = await apiClient.get<PaginatedResponse<DispatchRecordOut>>('/dispatches', {
    params,
  })
  return data
}

export async function acceptDispatchApi(
  dispatchId: number,
  idempotencyKey: string,
  onReplay?: () => void,
): Promise<DispatchRecordOut> {
  const res = await apiClient.post<DispatchRecordOut>(
    `/dispatches/${dispatchId}/accept`,
    {},
    { headers: { 'Idempotency-Key': idempotencyKey } },
  )
  if (String(res.headers['x-idempotent-replay']).toLowerCase() === 'true') {
    onReplay?.()
  }
  return res.data
}

export async function rejectDispatchApi(
  dispatchId: number,
  rejectReason: string,
  idempotencyKey: string,
  onReplay?: () => void,
): Promise<DispatchRecordOut> {
  const res = await apiClient.post<DispatchRecordOut>(
    `/dispatches/${dispatchId}/reject`,
    { reject_reason: rejectReason },
    { headers: { 'Idempotency-Key': idempotencyKey } },
  )
  if (String(res.headers['x-idempotent-replay']).toLowerCase() === 'true') {
    onReplay?.()
  }
  return res.data
}

export async function escalateDispatchApi(
  dispatchId: number,
  escalateReason: string,
): Promise<DispatchRecordOut> {
  const { data } = await apiClient.post<DispatchRecordOut>(`/dispatches/${dispatchId}/escalate`, {
    escalate_reason: escalateReason,
  })
  return data
}

export async function completeDispatchApi(
  dispatchId: number,
  payload: CompleteRepairPayload,
  idempotencyKey: string,
  onReplay?: () => void,
): Promise<CompleteRepairResponseOut> {
  const res = await apiClient.post<CompleteRepairResponseOut>(
    `/dispatches/${dispatchId}/complete`,
    payload,
    { headers: { 'Idempotency-Key': idempotencyKey } },
  )
  if (String(res.headers['x-idempotent-replay']).toLowerCase() === 'true') {
    onReplay?.()
  }
  return res.data
}

export async function listRepairProcessesApi(params?: {
  ticket_id?: number
  technician_account_id?: number
  page?: number
  page_size?: number
}): Promise<PaginatedResponse<RepairProcessOut> | RepairProcessOut[]> {
  const { data } = await apiClient.get<PaginatedResponse<RepairProcessOut> | RepairProcessOut[]>(
    '/repair-processes',
    { params },
  )
  return data
}

export async function getRepairProcessApi(repairProcessId: number): Promise<RepairProcessOut> {
  const { data } = await apiClient.get<RepairProcessOut>(`/repair-processes/${repairProcessId}`)
  return data
}

export async function patchRepairProcessApi(
  repairProcessId: number,
  payload: Partial<CompleteRepairPayload>,
): Promise<RepairProcessOut> {
  const { data } = await apiClient.patch<RepairProcessOut>(
    `/repair-processes/${repairProcessId}`,
    payload,
  )
  return data
}

export async function listMaterialUsagesApi(params?: {
  ticket_id?: number
  repair_process_id?: number
  page?: number
  page_size?: number
}): Promise<PaginatedResponse<MaterialUsageOut> | MaterialUsageOut[]> {
  const { data } = await apiClient.get<PaginatedResponse<MaterialUsageOut> | MaterialUsageOut[]>(
    '/material-usages',
    { params },
  )
  return data
}
