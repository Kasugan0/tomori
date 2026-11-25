import type { RoleCode, TicketStatus } from '@/shared/types'

export type TicketAction =
  | 'confirm'
  | 'cancel'
  | 'regrade'
  | 'dispatch'
  | 'reassign'
  | 'accept'
  | 'reject'
  | 'complete'
  | 'acceptance'
  | 'review-resolve'

interface ActionRule {
  allow: ReadonlyArray<RoleCode>
  statuses: ReadonlyArray<TicketStatus>
}

const RULES: Record<TicketAction, ActionRule> = {
  confirm: { allow: ['INSPECTOR'], statuses: ['AI_CANDIDATE_PENDING'] },
  cancel: {
    allow: ['INSPECTOR', 'ADMIN'],
    statuses: ['AI_CANDIDATE_PENDING', 'PENDING_DISPATCH'],
  },
  regrade: {
    allow: ['ADMIN'],
    statuses: ['PENDING_DISPATCH', 'DISPATCHED', 'IN_REPAIR', 'REWORK_PENDING_DISPATCH'],
  },
  dispatch: { allow: ['ADMIN'], statuses: ['PENDING_DISPATCH', 'REWORK_PENDING_DISPATCH'] },
  reassign: { allow: ['ADMIN'], statuses: ['DISPATCHED', 'IN_REPAIR'] },
  accept: { allow: ['TECHNICIAN'], statuses: ['DISPATCHED'] },
  reject: { allow: ['TECHNICIAN'], statuses: ['DISPATCHED'] },
  complete: { allow: ['TECHNICIAN'], statuses: ['IN_REPAIR'] },
  acceptance: { allow: ['INSPECTOR'], statuses: ['PENDING_ACCEPTANCE'] },
  'review-resolve': { allow: ['ADMIN'], statuses: ['MANUAL_REVIEW'] },
}

export interface ActionUserContext {
  id: number
  role_code: RoleCode | null
}

export function can(
  action: TicketAction,
  ticket: { status: TicketStatus; reporter_account_id?: number } | null | undefined,
  user: ActionUserContext | null | undefined,
): boolean {
  if (!ticket || !user || !user.role_code) return false
  const rule = RULES[action]
  if (!rule.allow.includes(user.role_code)) return false
  if (!rule.statuses.includes(ticket.status)) return false
  if (
    action === 'cancel' &&
    user.role_code === 'INSPECTOR' &&
    ticket.reporter_account_id !== undefined &&
    ticket.reporter_account_id !== user.id
  ) {
    return false
  }
  return true
}
