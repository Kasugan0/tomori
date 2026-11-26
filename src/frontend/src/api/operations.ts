import { apiClient } from '@/shared/api'
import type {
  AcceptanceDecision,
  AcceptanceRecordOut,
  AcceptanceResponseOut,
  AuditLogOut,
  FaultTicketOut,
  NotificationTaskOut,
  PaginatedResponse,
  ReviewResolution,
  ReworkOrderOut,
  ReworkStatus,
  SlaScanResponseOut,
} from '@/shared/types'

export interface AcceptanceSubmitPayload {
  decision: AcceptanceDecision
  comment?: string | null
  evidence_ids?: number[]
}

export interface AuditLogQueryParams {
  actor_account_id?: number
  action?: string
  resource_type?: string
  resource_id?: string
  ticket_id?: number
  created_from?: string
  created_to?: string
  response_status?: number
  page?: number
  page_size?: number
}

export async function submitAcceptanceApi(
  ticketId: number,
  payload: AcceptanceSubmitPayload,
  idempotencyKey: string,
  onReplay?: () => void,
): Promise<AcceptanceResponseOut> {
  const res = await apiClient.post<AcceptanceResponseOut>(
    `/tickets/${ticketId}/acceptance`,
    payload,
    { headers: { 'Idempotency-Key': idempotencyKey } },
  )
  if (String(res.headers['x-idempotent-replay']).toLowerCase() === 'true') {
    onReplay?.()
  }
  return res.data
}

export async function listAcceptancesApi(params?: {
  ticket_id?: number
  decision?: AcceptanceDecision
  page?: number
  page_size?: number
}): Promise<PaginatedResponse<AcceptanceRecordOut>> {
  const { data } = await apiClient.get<PaginatedResponse<AcceptanceRecordOut>>('/acceptances', {
    params,
  })
  return data
}

export async function listReworkOrdersApi(params?: {
  status?: ReworkStatus
  ticket_id?: number
  requires_manual_review?: boolean
  page?: number
  page_size?: number
}): Promise<PaginatedResponse<ReworkOrderOut>> {
  const { data } = await apiClient.get<PaginatedResponse<ReworkOrderOut>>('/rework-orders', {
    params,
  })
  return data
}

export async function getReworkOrderApi(reworkOrderId: number): Promise<ReworkOrderOut> {
  const { data } = await apiClient.get<ReworkOrderOut>(`/rework-orders/${reworkOrderId}`)
  return data
}

// 与后端 schemas/operations.py ReviewResolveIn 对齐：
// SPECIAL_CLOSE 必须携带 urged_at（带时区 ISO）、urged_unit（非空）、urge_evidence_ids（≥1）；
// 其他 resolution 不得携带（服务端对非空字段回 422）。
export interface ReviewResolvePayload {
  resolution: ReviewResolution
  reason: string
  urged_at?: string | null
  urged_unit?: string | null
  urge_evidence_ids?: number[]
}

export async function reviewResolveTicketApi(
  ticketId: number,
  payload: ReviewResolvePayload,
  idempotencyKey: string,
  onReplay?: () => void,
): Promise<FaultTicketOut> {
  const res = await apiClient.post<FaultTicketOut>(
    `/tickets/${ticketId}/review-resolve`,
    payload,
    { headers: { 'Idempotency-Key': idempotencyKey } },
  )
  if (String(res.headers['x-idempotent-replay']).toLowerCase() === 'true') {
    onReplay?.()
  }
  return res.data
}

export async function listNotificationsApi(params?: {
  is_read?: boolean
  event_type?: string
  page?: number
  page_size?: number
}): Promise<PaginatedResponse<NotificationTaskOut>> {
  const { data } = await apiClient.get<PaginatedResponse<NotificationTaskOut>>('/notifications', {
    params,
  })
  return data
}

export async function markNotificationReadApi(notificationId: number): Promise<NotificationTaskOut> {
  const { data } = await apiClient.post<NotificationTaskOut>(`/notifications/${notificationId}/read`)
  return data
}

export async function listDeadLetterNotificationsApi(params?: {
  page?: number
  page_size?: number
}): Promise<PaginatedResponse<NotificationTaskOut>> {
  const { data } = await apiClient.get<PaginatedResponse<NotificationTaskOut>>(
    '/notifications/dead-letters',
    { params },
  )
  return data
}

export async function retryNotificationApi(notificationId: number): Promise<NotificationTaskOut> {
  const { data } = await apiClient.post<NotificationTaskOut>(
    `/notifications/${notificationId}/retry`,
  )
  return data
}

export async function acknowledgeNotificationApi(
  notificationId: number,
): Promise<NotificationTaskOut> {
  const { data } = await apiClient.post<NotificationTaskOut>(
    `/notifications/${notificationId}/acknowledge`,
  )
  return data
}

export async function listAuditLogsApi(
  params?: AuditLogQueryParams,
): Promise<PaginatedResponse<AuditLogOut>> {
  const { data } = await apiClient.get<PaginatedResponse<AuditLogOut>>('/audit-logs', { params })
  return data
}

export async function triggerSlaScanApi(): Promise<SlaScanResponseOut> {
  const { data } = await apiClient.post<SlaScanResponseOut>('/sla/scan')
  return data
}
