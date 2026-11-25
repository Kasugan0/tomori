import { ref } from 'vue'

export const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9_-]{8,128}$/

/**
 * 必需携带 Idempotency-Key 的 12 个 POST 端点（与 docs/API.md §6.4 严格一致）。
 */
export const IDEMPOTENCY_REQUIRED_PATTERNS: ReadonlyArray<RegExp> = [
  /^\/tickets\/ai-candidates$/,
  /^\/tickets$/,
  /^\/tickets\/\d+\/confirm$/,
  /^\/tickets\/\d+\/cancel$/,
  /^\/tickets\/\d+\/regrade$/,
  /^\/tickets\/\d+\/dispatch$/,
  /^\/tickets\/\d+\/reassign$/,
  /^\/tickets\/\d+\/acceptance$/,
  /^\/tickets\/\d+\/review-resolve$/,
  /^\/dispatches\/\d+\/accept$/,
  /^\/dispatches\/\d+\/reject$/,
  /^\/dispatches\/\d+\/complete$/,
]

export function isIdempotencyRequiredEndpoint(method: string, urlPath: string): boolean {
  if (method.toUpperCase() !== 'POST') return false
  const clean = urlPath.split('?')[0].replace(/^\/api\/v1/, '')
  return IDEMPOTENCY_REQUIRED_PATTERNS.some((re) => re.test(clean))
}

export function generateUuidV4(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  const b = crypto.getRandomValues(new Uint8Array(16))
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const hex = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function isValidIdempotencyKey(key: string): boolean {
  return IDEMPOTENCY_KEY_PATTERN.test(key)
}

/**
 * 统一处理幂等 POST 失败后的幂等键重置：`IDEMPOTENCY_PAYLOAD_MISMATCH`
 * （同键异体被拒）与 `DUPLICATE_ACTIVE_TICKET`（业务冲突真实在途工单）
 * 两类「payload 已变更」失败都必须换键后重试。原视图内两处逐字重复版本
 * （`task-212` P3-18）收敛至此共享实现。
 */
export function handleIdempotentError(
  err: unknown,
  submitter: { resetKey: () => void },
): void {
  const code = (err as { error?: { code?: string } } | null | undefined)?.error?.code
  if (code === 'IDEMPOTENCY_PAYLOAD_MISMATCH' || code === 'DUPLICATE_ACTIVE_TICKET') {
    submitter.resetKey()
  }
}

export function useIdempotentSubmit() {
  const isSubmitting = ref(false)
  const currentKey = ref<string | null>(null)
  const lastReplayed = ref(false)

  function ensureKey(): string {
    if (!currentKey.value || !isValidIdempotencyKey(currentKey.value)) {
      currentKey.value = generateUuidV4()
    }
    return currentKey.value
  }

  function resetKey(): void {
    currentKey.value = null
  }

  async function run<T>(fn: (idempotencyKey: string, markReplayed: () => void) => Promise<T>): Promise<T> {
    if (isSubmitting.value) {
      throw new Error('SUBMIT_IN_PROGRESS')
    }
    isSubmitting.value = true
    lastReplayed.value = false
    const key = ensureKey()
    try {
      const result = await fn(key, () => {
        lastReplayed.value = true
      })
      resetKey()
      return result
    } finally {
      isSubmitting.value = false
    }
  }

  return {
    isSubmitting,
    currentKey,
    lastReplayed,
    ensureKey,
    resetKey,
    run,
  }
}
