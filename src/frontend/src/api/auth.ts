import { apiClient } from '@/shared/api'
import type {
  AccountOut,
  AccountStatus,
  AuthMeOut,
  LoginOut,
  PaginatedResponse,
  RoleCode,
  RoleOut,
  ScopeAuthorizationOut,
  ScopeType,
} from '@/shared/types'

export interface LoginPayload {
  username: string
  password: string
}

export interface ChangePasswordPayload {
  old_password: string
  new_password: string
}

export interface CreateAccountPayload {
  username: string
  password: string
  full_name: string
  role_code: RoleCode
  employee_no?: string | null
  phone?: string | null
  email?: string | null
  skills?: string[]
  max_active_dispatch?: number
}

export interface UpdateAccountPayload {
  full_name?: string
  employee_no?: string | null
  phone?: string | null
  email?: string | null
  skills?: string[]
  max_active_dispatch?: number
  status?: AccountStatus
}

export interface AccountListParams {
  role_code?: RoleCode
  region_id?: number
  status?: AccountStatus
  keyword?: string
  page?: number
  page_size?: number
}

export interface ScopeListParams {
  account_id?: number
  region_id?: number
  scope_type?: ScopeType
  is_active?: boolean
  page?: number
  page_size?: number
}

export interface CreateScopePayload {
  account_id: number
  region_id: number
  scope_type: ScopeType
  valid_from?: string
  valid_to?: string | null
  remark?: string | null
}

export async function loginApi(payload: LoginPayload): Promise<LoginOut> {
  const { data } = await apiClient.post<LoginOut>('/auth/login', payload, {
    metaFlags: { skipGlobalErrorBanner: true, skipAuthRedirect: true },
  })
  return data
}

export async function logoutApi(): Promise<void> {
  await apiClient.post('/auth/logout', {}, { metaFlags: { skipGlobalErrorBanner: true } })
}

export async function fetchMeApi(): Promise<AuthMeOut> {
  const { data } = await apiClient.get<AuthMeOut>('/auth/me')
  return data
}

export async function changePasswordApi(payload: ChangePasswordPayload): Promise<void> {
  await apiClient.post('/auth/change-password', payload)
}

export async function listAccountsApi(params?: AccountListParams): Promise<PaginatedResponse<AccountOut>> {
  const { data } = await apiClient.get<PaginatedResponse<AccountOut>>('/accounts', { params })
  return data
}

export async function createAccountApi(payload: CreateAccountPayload): Promise<AccountOut> {
  const { data } = await apiClient.post<AccountOut>('/accounts', payload)
  return data
}

export async function updateAccountApi(accountId: number, payload: UpdateAccountPayload): Promise<AccountOut> {
  const { data } = await apiClient.patch<AccountOut>(`/accounts/${accountId}`, payload)
  return data
}

export async function resetAccountPasswordApi(
  accountId: number,
  newSecret: string,
): Promise<{ status: string }> {
  const { data } = await apiClient.post<{ status: string }>(
    `/accounts/${accountId}/reset-password`,
    { new_password: newSecret },
  )
  return data
}

export async function listRolesApi(): Promise<RoleOut[]> {
  const { data } = await apiClient.get<RoleOut[] | PaginatedResponse<RoleOut>>('/roles')
  return Array.isArray(data) ? data : data.items
}

export async function listScopeAuthorizationsApi(
  params?: ScopeListParams,
): Promise<PaginatedResponse<ScopeAuthorizationOut>> {
  const { data } = await apiClient.get<PaginatedResponse<ScopeAuthorizationOut>>('/scope-authorizations', {
    params,
  })
  return data
}

export async function createScopeAuthorizationApi(
  payload: CreateScopePayload,
): Promise<ScopeAuthorizationOut> {
  const { data } = await apiClient.post<ScopeAuthorizationOut>('/scope-authorizations', payload)
  return data
}

export async function deactivateScopeAuthorizationApi(scopeId: number): Promise<ScopeAuthorizationOut> {
  const { data } = await apiClient.delete<ScopeAuthorizationOut>(`/scope-authorizations/${scopeId}`)
  return data
}
