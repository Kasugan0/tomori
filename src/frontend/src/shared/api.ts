import axios, { AxiosError, type AxiosInstance, type InternalAxiosRequestConfig } from 'axios'
import { ERROR_CODE_TEXT } from '@/shared/enums'
import { isIdempotencyRequiredEndpoint, isValidIdempotencyKey, generateUuidV4 } from '@/shared/idempotency'
import type { ApiError, ApiErrorPayload } from '@/shared/types'
import { pinia } from '@/stores/pinia'
import { useAuthStore } from '@/stores/auth'
import { useErrorBannerStore } from '@/stores/errorBanner'

export interface RequestMetaFlags {
  skipGlobalErrorBanner?: boolean
  skipAuthRedirect?: boolean
}

declare module 'axios' {
  export interface AxiosRequestConfig {
    metaFlags?: RequestMetaFlags
    sessionGeneration?: number
  }
}

let unauthorizedRedirectHandler: ((redirectTo?: string) => void) | null = null

export function registerUnauthorizedHandler(handler: (redirectTo?: string) => void): void {
  unauthorizedRedirectHandler = handler
}

export function isApiError(value: unknown): value is ApiError {
  return (
    typeof value === 'object' &&
    value !== null &&
    'httpStatus' in value &&
    'error' in value &&
    typeof (value as ApiError).httpStatus === 'number' &&
    typeof (value as ApiError).error?.code === 'string'
  )
}

function normalizeAxiosError(err: AxiosError): ApiError {
  const httpStatus = err.response?.status ?? 503
  const headerReqId = err.response?.headers?.['x-request-id']
  const data = err.response?.data as { error?: Partial<ApiErrorPayload> } | undefined

  if (data && typeof data === 'object' && data.error && typeof data.error.code === 'string') {
    return {
      httpStatus,
      error: {
        code: data.error.code,
        message: data.error.message ?? ERROR_CODE_TEXT[data.error.code],
        message_key: data.error.message_key,
        details: (data.error.details ?? null) as ApiErrorPayload['details'],
        request_id: data.error.request_id || String(headerReqId || '-'),
        timestamp: data.error.timestamp,
      },
    }
  }

  const fallbackCode =
    httpStatus === 401
      ? 'TOKEN_INVALID'
      : httpStatus === 403
        ? 'FORBIDDEN_ROLE'
        : httpStatus === 404
          ? 'RESOURCE_NOT_FOUND'
          : httpStatus === 413
            ? 'IMAGE_SIZE_EXCEEDED'
            : httpStatus === 415
              ? 'UNSUPPORTED_IMAGE_FORMAT'
              : httpStatus >= 500
                ? httpStatus === 503
                  ? 'SERVICE_UNAVAILABLE'
                  : 'INTERNAL_ERROR'
                : 'VALIDATION_ERROR'

  return {
    httpStatus,
    error: {
      code: fallbackCode,
      message: ERROR_CODE_TEXT[fallbackCode] || err.message || '网络或服务异常',
      details: null,
      request_id: String(headerReqId || '-'),
    },
  }
}

export const apiClient: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 65000,
})

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const authStore = useAuthStore(pinia)
  config.sessionGeneration = authStore.sessionGeneration
  if (authStore.token) {
    config.headers.set('Authorization', `Bearer ${authStore.token}`)
  }

  const method = (config.method || 'GET').toUpperCase()
  const urlPath = config.url || ''
  if (isIdempotencyRequiredEndpoint(method, urlPath)) {
    const existing = config.headers.get('Idempotency-Key')
    if (!existing || !isValidIdempotencyKey(String(existing))) {
      config.headers.set('Idempotency-Key', generateUuidV4())
    }
  }

  return config
}, undefined, { synchronous: true })

apiClient.interceptors.response.use(
  (response) => {
    if (response.config.sessionGeneration !== useAuthStore(pinia).sessionGeneration) {
      return Promise.reject(new Error('STALE_SESSION'))
    }
    const replayHeader = response.headers['x-idempotent-replay']
    if (String(replayHeader).toLowerCase() === 'true') {
      // X-Idempotent-Replay: true 是本项目扩展响应头（非 IETF 草案内容），按首次状态码正常放行
      if (import.meta.env.DEV) {
        console.info('[tomori] X-Idempotent-Replay: true', response.config.url, response.status)
      }
    }
    return response
  },
  (rawErr: unknown) => {
    const authStore = useAuthStore(pinia)
    const bannerStore = useErrorBannerStore(pinia)

    if (axios.isAxiosError(rawErr)) {
      if (rawErr.config?.sessionGeneration !== undefined && rawErr.config.sessionGeneration !== authStore.sessionGeneration) {
        return Promise.reject(new Error('STALE_SESSION'))
      }
      const apiErr = normalizeAxiosError(rawErr)
      const metaFlags = rawErr.config?.metaFlags
      const requestUrl = rawErr.config?.url || ''

      if (apiErr.httpStatus === 401 && !requestUrl.includes('/auth/login') && !metaFlags?.skipAuthRedirect) {
        authStore.clearSession()
        bannerStore.clear()
        unauthorizedRedirectHandler?.()
        return Promise.reject(apiErr)
      }

      if (!metaFlags?.skipGlobalErrorBanner && apiErr.httpStatus !== 401) {
        bannerStore.showFromApiError(apiErr)
      }

      return Promise.reject(apiErr)
    }

    const unexpected: ApiError = {
      httpStatus: 500,
      error: {
        code: 'INTERNAL_ERROR',
        message: rawErr instanceof Error ? rawErr.message : '未知前端错误',
        details: null,
        request_id: '-',
      },
    }
    bannerStore.showFromApiError(unexpected)
    return Promise.reject(unexpected)
  },
)

export async function fetchAuthenticatedMediaBlobUrl(
  evidenceId: number,
  variant: 'raw' | 'annotated',
): Promise<string> {
  const res = await apiClient.get<Blob>(`/evidence/${evidenceId}/${variant}`, {
    responseType: 'blob',
    metaFlags: { skipGlobalErrorBanner: true },
  })
  return URL.createObjectURL(res.data)
}

export async function fetchAuthenticatedUrlBlobUrl(pathOrUrl: string): Promise<string> {
  if (pathOrUrl.startsWith('blob:') || pathOrUrl.startsWith('data:')) {
    return pathOrUrl
  }
  const normalized = pathOrUrl.startsWith('/api/v1/')
    ? pathOrUrl.slice('/api/v1'.length)
    : pathOrUrl
  const res = await apiClient.get<Blob>(normalized, {
    responseType: 'blob',
    metaFlags: { skipGlobalErrorBanner: true },
  })
  return URL.createObjectURL(res.data)
}
