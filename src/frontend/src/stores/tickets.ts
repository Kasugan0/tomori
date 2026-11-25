import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  cancelTicketApi,
  confirmTicketApi,
  getTicketDetailApi,
  listTicketsApi,
  patchTicketApi,
  regradeTicketApi,
  type ConfirmTicketPayload,
  type TicketListParams,
  type UpdateTicketDraftPayload,
} from '@/api/tickets'
import { submitAcceptanceApi, reviewResolveTicketApi, type AcceptanceSubmitPayload } from '@/api/operations'
import { dispatchTicketApi, reassignTicketApi, type DispatchTicketPayload, type ReassignTicketPayload } from '@/api/dispatches'
import type {
  AcceptanceResponseOut,
  FaultTicketDetailOut,
  FaultTicketOut,
  PriorityCode,
  ReviewResolution,
} from '@/shared/types'

export const useTicketsStore = defineStore('tickets', () => {
  const list = ref<FaultTicketOut[]>([])
  const total = ref(0)
  const detail = ref<FaultTicketDetailOut | null>(null)
  const loadingList = ref(false)
  const loadingDetail = ref(false)
  const filters = ref<TicketListParams>({ page: 1, page_size: 20 })

  async function fetchList(override?: TicketListParams): Promise<void> {
    loadingList.value = true
    try {
      const merged = { ...filters.value, ...override }
      filters.value = merged
      const res = await listTicketsApi(merged)
      list.value = res.items
      total.value = res.total
    } finally {
      loadingList.value = false
    }
  }

  async function fetchDetail(ticketId: number): Promise<FaultTicketDetailOut> {
    loadingDetail.value = true
    try {
      const data = await getTicketDetailApi(ticketId)
      detail.value = data
      return data
    } finally {
      loadingDetail.value = false
    }
  }

  async function updateDraft(ticketId: number, payload: UpdateTicketDraftPayload): Promise<FaultTicketDetailOut> {
    await patchTicketApi(ticketId, payload)
    return fetchDetail(ticketId)
  }

  async function confirm(
    ticketId: number,
    payload: ConfirmTicketPayload,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<FaultTicketDetailOut> {
    await confirmTicketApi(ticketId, payload, idempotencyKey, onReplay)
    return fetchDetail(ticketId)
  }

  async function cancel(
    ticketId: number,
    cancelReason: string,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<FaultTicketDetailOut> {
    await cancelTicketApi(ticketId, cancelReason, idempotencyKey, onReplay)
    return fetchDetail(ticketId)
  }

  async function regrade(
    ticketId: number,
    payload: { priority: PriorityCode; reason: string },
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<FaultTicketDetailOut> {
    await regradeTicketApi(ticketId, payload, idempotencyKey, onReplay)
    return fetchDetail(ticketId)
  }

  async function dispatch(
    ticketId: number,
    payload: DispatchTicketPayload,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<FaultTicketDetailOut> {
    await dispatchTicketApi(ticketId, payload, idempotencyKey, onReplay)
    return fetchDetail(ticketId)
  }

  async function reassign(
    ticketId: number,
    payload: ReassignTicketPayload,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<FaultTicketDetailOut> {
    await reassignTicketApi(ticketId, payload, idempotencyKey, onReplay)
    return fetchDetail(ticketId)
  }

  async function submitAcceptance(
    ticketId: number,
    payload: AcceptanceSubmitPayload,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<AcceptanceResponseOut> {
    const res = await submitAcceptanceApi(ticketId, payload, idempotencyKey, onReplay)
    await fetchDetail(ticketId)
    return res
  }

  async function reviewResolve(
    ticketId: number,
    payload: { resolution: ReviewResolution; reason: string },
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<FaultTicketDetailOut> {
    await reviewResolveTicketApi(ticketId, payload, idempotencyKey, onReplay)
    return fetchDetail(ticketId)
  }

  return {
    list,
    total,
    detail,
    loadingList,
    loadingDetail,
    filters,
    fetchList,
    fetchDetail,
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
