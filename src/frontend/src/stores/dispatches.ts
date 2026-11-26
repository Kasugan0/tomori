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
import { fetchAllPages } from '@/shared/pagination'
import type { DispatchRecordOut } from '@/shared/types'

export const useDispatchesStore = defineStore('dispatches', () => {
  const myDispatches = ref<DispatchRecordOut[]>([])
  const total = ref(0)
  const loading = ref(false)

  async function fetchMine(params?: DispatchListParams): Promise<void> {
    loading.value = true
    try {
      myDispatches.value = await fetchAllPages((page, pageSize) =>
        listDispatchesApi({ ...params, page, page_size: pageSize }),
      )
      total.value = myDispatches.value.length
    } finally {
      loading.value = false
    }
  }

  // 写操作已成功后，列表刷新失败不得吞掉写入结果；
  // 刷新错误由响应拦截器的全局横幅呈现。
  async function refreshMineBestEffort(): Promise<void> {
    try {
      await fetchMine()
    } catch {
      // 非致命：列表将保持旧数据直至下次加载
    }
  }

  async function accept(
    dispatchId: number,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<DispatchRecordOut> {
    const res = await acceptDispatchApi(dispatchId, idempotencyKey, onReplay)
    upsertLocal(res)
    await refreshMineBestEffort()
    return res
  }

  async function reject(
    dispatchId: number,
    rejectReason: string,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<DispatchRecordOut> {
    const res = await rejectDispatchApi(dispatchId, rejectReason, idempotencyKey, onReplay)
    upsertLocal(res)
    await refreshMineBestEffort()
    return res
  }

  async function escalate(dispatchId: number, escalateReason: string): Promise<DispatchRecordOut> {
    const res = await escalateDispatchApi(dispatchId, escalateReason)
    upsertLocal(res)
    await refreshMineBestEffort()
    return res
  }

  async function complete(
    dispatchId: number,
    payload: CompleteRepairPayload,
    idempotencyKey: string,
    onReplay?: () => void,
  ): Promise<CompleteRepairResponseOut> {
    const res = await completeDispatchApi(dispatchId, payload, idempotencyKey, onReplay)
    upsertLocal(res.dispatch)
    await refreshMineBestEffort()
    return res
  }

  function upsertLocal(record: DispatchRecordOut): void {
    const idx = myDispatches.value.findIndex((d) => d.id === record.id)
    if (idx >= 0) {
      myDispatches.value[idx] = record
    } else {
      myDispatches.value = [record, ...myDispatches.value]
    }
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
