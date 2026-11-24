import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ERROR_CODE_TEXT, getNotEligibleReasonText, VISION_FALLBACK_MESSAGES } from '@/shared/enums'
import type { ApiError } from '@/shared/types'

export interface ConflictLinkState {
  existingTicketId: number
  existingTicketNo: string
  existingStatus: string
}

export interface BannerState {
  httpStatus: number
  code: string
  reason?: string
  message: string
  variant: 'error' | 'warning' | 'info'
  requestId: string
  conflict: ConflictLinkState | null
}

export const useErrorBannerStore = defineStore('errorBanner', () => {
  const banner = ref<BannerState | null>(null)
  const hasBanner = computed(() => banner.value !== null)

  function buildMessage(apiErr: ApiError): string {
    const { code, message, message_key, details } = apiErr.error
    if (code === 'DISPATCH_TECHNICIAN_NOT_ELIGIBLE' && details?.reason) {
      return `${ERROR_CODE_TEXT.DISPATCH_TECHNICIAN_NOT_ELIGIBLE}：${getNotEligibleReasonText(String(details.reason))}`
    }
    if (code === 'ELECTRICAL_SAFETY_CHECK_REQUIRED' && Array.isArray(details?.missing_items) && details.missing_items.length > 0) {
      return `${ERROR_CODE_TEXT.ELECTRICAL_SAFETY_CHECK_REQUIRED}（缺失项：${details.missing_items.join(', ')}）`
    }
    if (message_key && VISION_FALLBACK_MESSAGES[message_key]) {
      return VISION_FALLBACK_MESSAGES[message_key]
    }
    if (message && message.trim().length > 0) {
      return message
    }
    if (ERROR_CODE_TEXT[code]) {
      return ERROR_CODE_TEXT[code]
    }
    return `请求处理失败 (${apiErr.httpStatus})`
  }

  function showFromApiError(apiErr: ApiError): void {
    const code = apiErr.error.code
    const details = apiErr.error.details

    let conflict: ConflictLinkState | null = null
    if (
      code === 'DUPLICATE_ACTIVE_TICKET' &&
      details &&
      typeof details.existing_ticket_id === 'number' &&
      typeof details.existing_ticket_no === 'string'
    ) {
      conflict = {
        existingTicketId: details.existing_ticket_id,
        existingTicketNo: details.existing_ticket_no,
        existingStatus: String(details.existing_status ?? ''),
      }
    }

    let variant: 'error' | 'warning' | 'info' = 'error'
    if (code === 'IDEMPOTENCY_REQUEST_IN_PROGRESS' || code === 'DUPLICATE_ACTIVE_TICKET') {
      variant = 'warning'
    } else if (code === 'VISION_DISABLED' || code === 'VISION_TIMEOUT' || code === 'VISION_INFERENCE_FAILED') {
      variant = 'info'
    }

    let msg = buildMessage(apiErr)
    if (apiErr.httpStatus >= 500 && variant === 'error') {
      const reqId = apiErr.error.request_id || '-'
      msg = `${msg}（请求编号：${reqId}）`
    }

    banner.value = {
      httpStatus: apiErr.httpStatus,
      code,
      reason: details?.reason ? String(details.reason) : undefined,
      message: msg,
      variant,
      requestId: apiErr.error.request_id || '-',
      conflict,
    }
  }

  function showCustom(state: Omit<BannerState, 'requestId'> & { requestId?: string }): void {
    banner.value = {
      ...state,
      requestId: state.requestId ?? '-',
    }
  }

  function clear(): void {
    banner.value = null
  }

  return {
    banner,
    hasBanner,
    showFromApiError,
    showCustom,
    clear,
  }
})
