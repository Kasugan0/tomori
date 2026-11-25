import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  acceptDispatchApi,
  completeDispatchApi,
  escalateDispatchApi,
  listDispatchesApi,
  rejectDispatchApi,
  type CompleteRepairPayload,
  type CompleteRepairResponseOut,
  type DispatchListParams,
} from '@/api/dispatches'
import type { DispatchRecordOut } from '@/shared/types'

export const useDispatchesStore = defineStore('dispatches', () => {
  const myDispatches = ref<DispatchRecordOut[]>([])
  const total = ref(0)
  const loading = ref(false)

  async function fetchMine(params?: DispatchListParams): Promise<void> {
    loading.value = true
    try {
      const res = await listDispatchesApi({ page_size: 50, ...params })
      myDispatches.value = res.items
      total.value = res.total
    } finally {
      loading.value = false
    }
  }

  async function accept(
    dispatchId: number,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<DispatchRecordOut> {
    const res = await acceptDispatchApi(dispatchId, idempotencyKey, onReplay)
    await fetchMine()
    return res
  }

  async function reject(
    dispatchId: number,
    rejectReason: string,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<DispatchRecordOut> {
    const res = await rejectDispatchApi(dispatchId, rejectReason, idempotencyKey, onReplay)
    await fetchMine()
    return res
  }

  async function escalate(dispatchId: number, escalateReason: string): Promise<DispatchRecordOut> {
    const res = await escalateDispatchApi(dispatchId, escalateReason)
    await fetchMine()
    return res
  }

  async function complete(
    dispatchId: number,
    payload: CompleteRepairPayload,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<CompleteRepairResponseOut> {
    const res = await completeDispatchApi(dispatchId, payload, idempotencyKey, onReplay)
    await fetchMine()
    return res
  }

  return {
    myDispatches,
    total,
    loading,
    fetchMine,
    accept,
    reject,
    escalate,
    complete,
  }
})
