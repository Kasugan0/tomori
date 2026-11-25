import type { TicketStatus } from './types'

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/**
 * 郑州市令第 152 号无分钟级响应时限；response_deadline_at 默认恒为 NULL，界面渲染 "—"。
 */
export function formatResponseDeadline(responseDeadlineAt: string | null | undefined): string {
  if (!responseDeadlineAt) return '—'
  return formatDateTime(responseDeadlineAt)
}

/**
 * 郑州市令第 152 号第 19 条第 3 款：AI_CANDIDATE_PENDING 期间为 null（待巡查确认），
 * 自确认/建单进入 PENDING_DISPATCH 起写入 T_discover + 48h，不因未接单或改级为 null。
 */
export function formatRepairDeadline(
  repairDeadlineAt: string | null | undefined,
  status?: TicketStatus,
): string {
  if (!repairDeadlineAt) {
    return status === 'AI_CANDIDATE_PENDING' ? '待巡查确认（确认后起算 48h）' : '—'
  }
  return `${formatDateTime(repairDeadlineAt)}（48h 基准）`
}

export function isDeadlinePast(iso: string | null | undefined): boolean {
  if (!iso) return false
  const ms = new Date(iso).getTime()
  if (Number.isNaN(ms)) return false
  return ms < Date.now()
}

export function formatOverdueDuration(iso: string | null | undefined): string {
  if (!iso) return ''
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms) || ms <= 0) return ''
  const totalMinutes = Math.floor(ms / 60000)
  const hours = Math.floor(totalMinutes / 60)
  const mins = totalMinutes % 60
  if (hours > 0) return `已超时 ${hours} 小时 ${mins} 分`
  return `已超时 ${mins} 分钟`
}

export function formatConfidence(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const num = typeof value === 'number' ? value : Number(value)
  if (Number.isNaN(num)) return String(value)
  return `${(num * 100).toFixed(1)}%`
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`
}
