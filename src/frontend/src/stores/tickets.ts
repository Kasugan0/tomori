import { ref, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { defineStore } from 'pinia'
import {
  cancelTicketApi,
  confirmTicketApi,
  getTicketDetailApi,
  listTicketsApi,
  patchTicketApi,
  regradeTicketApi,
  uploadTicketEvidencesApi,
  type ConfirmTicketPayload,
  type TicketListParams,
  type UpdateTicketDraftPayload,
} from '@/api/tickets'
import {
  submitAcceptanceApi,
  reviewResolveTicketApi,
  type AcceptanceSubmitPayload,
  type ReviewResolvePayload,
} from '@/api/operations'
import { dispatchTicketApi, reassignTicketApi, type DispatchTicketPayload, type ReassignTicketPayload } from '@/api/dispatches'
import type {
  AcceptanceResponseOut,
  EvidenceType,
  FaultEvidenceOut,
  FaultTicketDetailOut,
  FaultTicketOut,
  PriorityCode,
} from '@/shared/types'

export type TicketUploadPurpose = 'acceptance' | 'urge'
function uploadPurposeKey(ticketId: number, purpose: TicketUploadPurpose): string {
  return `${ticketId}:${purpose}`
}

export const useTicketsStore = defineStore('tickets', () => {
  const list = ref<FaultTicketOut[]>([])
  const total = ref(0)
  const detail = ref<FaultTicketDetailOut | null>(null)
  const loadingList = ref(false)
  const loadingDetail = ref(false)
  const staleTicketIds = ref<number[]>([])
  const writingTicketIds = ref<number[]>([])
  const uploadedEvidenceByTicket = ref<Record<string, number[]>>({})
  const writeRevision = ref(0)
  const ticketVersions = new Map<number, number>()
  const writeOwners = new Map<number, symbol>()
  const latestTicketRequests = new Map<number, number>()
  let requestSequence = 0
  let latestDetailRequest = 0
  const filters = ref<TicketListParams>({ page: 1, page_size: 20 })
  const auth = useAuthStore()
  let sessionVersion = 0
  watch(() => auth.sessionGeneration, () => {
    sessionVersion++
    uploadedEvidenceByTicket.value = {}
    detail.value = null
    list.value = []
    total.value = 0
    staleTicketIds.value = []
    writingTicketIds.value = []
    writeOwners.clear()
    latestDetailRequest = ++requestSequence
    latestTicketRequests.clear()
    loadingDetail.value = false
    loadingList.value = false
  }, { flush: 'sync' })

  async function fetchList(override?: TicketListParams): Promise<void> {
    const session = sessionVersion
    loadingList.value = true
    try {
      const merged = { ...filters.value, ...override }
      filters.value = merged
      const res = await listTicketsApi(merged)
      if (session !== sessionVersion) return
      list.value = res.items
      total.value = res.total
    } finally {
      if (session === sessionVersion) loadingList.value = false
    }
  }

  async function fetchDetail(ticketId: number): Promise<FaultTicketDetailOut> {
    const request = ++requestSequence
    latestDetailRequest = request
    latestTicketRequests.set(ticketId, request)
    const version = ticketVersions.get(ticketId) ?? 0
    const session = sessionVersion
    loadingDetail.value = true
    try {
      const data = await getTicketDetailApi(ticketId)
      if (session !== sessionVersion || version !== (ticketVersions.get(ticketId) ?? 0) || latestTicketRequests.get(ticketId) !== request) {
        throw new Error('STALE_TICKET_RESPONSE')
      }
      if (latestDetailRequest !== request) throw new Error('STALE_TICKET_RESPONSE')
      detail.value = data
      staleTicketIds.value = staleTicketIds.value.filter((id) => id !== ticketId)
      return data
    } finally {
      if (latestDetailRequest === request) loadingDetail.value = false
    }
  }

  function ensureFresh(ticketId: number): void {
    if (staleTicketIds.value.includes(ticketId) || writingTicketIds.value.includes(ticketId)) throw new Error('TICKET_REFRESH_REQUIRED')
  }

  function releaseWrite(ticketId: number, owner: symbol | undefined): void {
    if (owner && writeOwners.get(ticketId) === owner) {
      writeOwners.delete(ticketId)
      writingTicketIds.value = writingTicketIds.value.filter((id) => id !== ticketId)
    }
  }

  async function runWrite<T>(ticketId: number, write: () => Promise<T>): Promise<T> {
    ensureFresh(ticketId)
    const session = sessionVersion
    const owner = Symbol('ticket-write')
    writeOwners.set(ticketId, owner)
    writingTicketIds.value.push(ticketId)
    try {
      const result = await write()
      if (session !== sessionVersion) throw new Error('STALE_SESSION')
      writeRevision.value++
      return result
    } catch (error) {
      releaseWrite(ticketId, owner)
      throw error
    }
  }

  async function refreshAfterWrite(ticketId: number): Promise<void> {
    const owner = writeOwners.get(ticketId)
    ticketVersions.set(ticketId, (ticketVersions.get(ticketId) ?? 0) + 1)
    if (!staleTicketIds.value.includes(ticketId)) staleTicketIds.value.push(ticketId)
    try {
      await fetchDetail(ticketId)
    } catch {
      // The GET interceptor displays failures; a later successful refresh clears the lock.
    } finally {
      releaseWrite(ticketId, owner)
    }
  }

  // Preserve fields confirmed by the write response until the full detail reloads.
  function mergeTicketOut(updated: FaultTicketOut): void {
    if (detail.value?.id === updated.id) {
      detail.value = { ...detail.value, ...updated }
    }
  }

  async function uploadEvidence(ticketId: number, files: File[], evidenceType: EvidenceType, purpose: TicketUploadPurpose): Promise<FaultEvidenceOut[]> {
    const session = sessionVersion
    const result = await runWrite(ticketId, () => uploadTicketEvidencesApi(ticketId, files, evidenceType))
    if (session !== sessionVersion) throw new Error('STALE_SESSION')
    const ids = result.map((ev) => ev.id)
    const key = uploadPurposeKey(ticketId, purpose)
    uploadedEvidenceByTicket.value[key] = [...(uploadedEvidenceByTicket.value[key] ?? []), ...ids]
    await refreshAfterWrite(ticketId)
    return result
  }

  function getUploadedEvidence(ticketId: number, purpose: TicketUploadPurpose): number[] {
    return uploadedEvidenceByTicket.value[uploadPurposeKey(ticketId, purpose)] ?? []
  }

  function clearUploadedEvidence(ticketId: number, purpose: TicketUploadPurpose): void {
    delete uploadedEvidenceByTicket.value[uploadPurposeKey(ticketId, purpose)]
  }

  async function updateDraft(ticketId: number, payload: UpdateTicketDraftPayload): Promise<FaultTicketOut> {
    const session = sessionVersion
    const result = await runWrite(ticketId, () => patchTicketApi(ticketId, payload))
    if (session !== sessionVersion) throw new Error('STALE_SESSION')
    mergeTicketOut(result)
    await refreshAfterWrite(ticketId)
    return result
  }

  async function confirm(
    ticketId: number,
    payload: ConfirmTicketPayload,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<FaultTicketOut> {
    const session = sessionVersion
    const result = await runWrite(ticketId, () => confirmTicketApi(ticketId, payload, idempotencyKey, onReplay))
    if (session !== sessionVersion) throw new Error('STALE_SESSION')
    mergeTicketOut(result)
    await refreshAfterWrite(ticketId)
    return result
  }

  async function cancel(
    ticketId: number,
    cancelReason: string,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<FaultTicketOut> {
    const session = sessionVersion
    const result = await runWrite(ticketId, () => cancelTicketApi(ticketId, cancelReason, idempotencyKey, onReplay))
    if (session !== sessionVersion) throw new Error('STALE_SESSION')
    mergeTicketOut(result)
    await refreshAfterWrite(ticketId)
    return result
  }

  async function regrade(
    ticketId: number,
    payload: { priority: PriorityCode; reason: string },
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<FaultTicketOut> {
    const session = sessionVersion
    const result = await runWrite(ticketId, () => regradeTicketApi(ticketId, payload, idempotencyKey, onReplay))
    if (session !== sessionVersion) throw new Error('STALE_SESSION')
    mergeTicketOut(result)
    await refreshAfterWrite(ticketId)
    return result
  }

  async function dispatch(
    ticketId: number,
    payload: DispatchTicketPayload,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<void> {
    const session = sessionVersion
    const dispatch = await runWrite(ticketId, () => dispatchTicketApi(ticketId, payload, idempotencyKey, onReplay))
    if (session !== sessionVersion) throw new Error('STALE_SESSION')
    if (detail.value?.id === dispatch.ticket_id && dispatch.ticket_status) {
      detail.value = { ...detail.value, status: dispatch.ticket_status, priority: dispatch.ticket_priority ?? detail.value.priority }
    }
    await refreshAfterWrite(ticketId)
  }

  async function reassign(
    ticketId: number,
    payload: ReassignTicketPayload,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<void> {
    const session = sessionVersion
    const dispatch = await runWrite(ticketId, () => reassignTicketApi(ticketId, payload, idempotencyKey, onReplay))
    if (session !== sessionVersion) throw new Error('STALE_SESSION')
    if (detail.value?.id === dispatch.ticket_id && dispatch.ticket_status) {
      detail.value = { ...detail.value, status: dispatch.ticket_status, priority: dispatch.ticket_priority ?? detail.value.priority }
    }
    await refreshAfterWrite(ticketId)
  }

  async function submitAcceptance(
    ticketId: number,
    payload: AcceptanceSubmitPayload,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<AcceptanceResponseOut> {
    const session = sessionVersion
    const res = await runWrite(ticketId, () => submitAcceptanceApi(ticketId, payload, idempotencyKey, onReplay))
    if (session !== sessionVersion) throw new Error('STALE_SESSION')
    mergeTicketOut(res.ticket)
    clearUploadedEvidence(ticketId, 'acceptance')
    await refreshAfterWrite(ticketId)
    return res
  }

  async function reviewResolve(
    ticketId: number,
    payload: ReviewResolvePayload,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<FaultTicketOut> {
    const session = sessionVersion
    const result = await runWrite(ticketId, () => reviewResolveTicketApi(ticketId, payload, idempotencyKey, onReplay))
    if (session !== sessionVersion) throw new Error('STALE_SESSION')
    mergeTicketOut(result)
    clearUploadedEvidence(ticketId, 'urge')
    await refreshAfterWrite(ticketId)
    return result
  }

  return {
    list,
    total,
    detail,
    loadingList,
    loadingDetail,
    staleTicketIds,
    writingTicketIds,
    uploadedEvidenceByTicket,
    writeRevision,
    filters,
    fetchList,
    fetchDetail,
    uploadEvidence,
    getUploadedEvidence,
    clearUploadedEvidence,
    updateDraft,
    confirm,
    cancel,
    regrade,
    dispatch,
    reassign,
    submitAcceptance,
    reviewResolve,
  }
})
