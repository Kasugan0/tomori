import { apiClient } from '@/shared/api'
import type {
  AssetImportance,
  AssetStatus,
  AssetType,
  BusinessRuleOut,
  FacilityAssetOut,
  LightingPointOut,
  LightingPointStatus,
  MaintenanceRegionOut,
  PaginatedResponse,
  PowerSupplyType,
  RoadLevel,
  RuleCode,
} from '@/shared/types'

export interface CreateRegionPayload {
  region_code: string
  name: string
  parent_region_id?: number | null
  level: number
  description?: string | null
}

export interface UpdateRegionPayload {
  name?: string
  parent_region_id?: number | null
  level?: number
  description?: string | null
  is_active?: boolean
}

export interface LightingPointListParams {
  region_id?: number
  road_name?: string
  status?: LightingPointStatus
  keyword?: string
  page?: number
  page_size?: number
}

export interface CreateLightingPointPayload {
  point_code: string
  region_id: number
  name: string
  road_name: string
  road_level: RoadLevel
  longitude: number
  latitude: number
  install_height_m?: number | null
  lamp_count?: number
  power_supply_type?: PowerSupplyType
  description?: string | null
}

export interface UpdateLightingPointPayload {
  region_id?: number
  name?: string
  road_name?: string
  road_level?: RoadLevel
  longitude?: number
  latitude?: number
  install_height_m?: number | null
  lamp_count?: number
  power_supply_type?: PowerSupplyType
  status?: LightingPointStatus
  description?: string | null
}

export interface FacilityAssetListParams {
  lighting_point_id?: number
  asset_type?: AssetType
  importance?: AssetImportance
  page?: number
  page_size?: number
}

export interface CreateFacilityAssetPayload {
  asset_code: string
  lighting_point_id: number
  asset_type: AssetType
  name: string
  model_spec?: string | null
  rated_power_w?: number | null
  importance: AssetImportance
  installed_at?: string | null
  description?: string | null
}

export interface UpdateFacilityAssetPayload {
  name?: string
  asset_type?: AssetType
  model_spec?: string | null
  rated_power_w?: number | null
  importance?: AssetImportance
  status?: AssetStatus
  description?: string | null
}

export interface BusinessRuleListParams {
  rule_code?: RuleCode
  is_active?: boolean
  page?: number
  page_size?: number
}

export interface CreateBusinessRulePayload {
  rule_code: RuleCode
  rule_value: Record<string, unknown>
  description?: string | null
}

export interface UpdateBusinessRuleMetaPayload {
  is_active?: boolean
  description?: string | null
}

export async function listRegionsApi(params?: {
  flat?: boolean
  page?: number
  page_size?: number
}): Promise<MaintenanceRegionOut[]> {
  const { data } = await apiClient.get<
    MaintenanceRegionOut[] | PaginatedResponse<MaintenanceRegionOut>
  >('/regions', { params: { flat: true, page_size: 100, ...params } })
  return Array.isArray(data) ? data : data.items
}

export async function createRegionApi(payload: CreateRegionPayload): Promise<MaintenanceRegionOut> {
  const { data } = await apiClient.post<MaintenanceRegionOut>('/regions', payload)
  return data
}

export async function updateRegionApi(
  regionId: number,
  payload: UpdateRegionPayload,
): Promise<MaintenanceRegionOut> {
  const { data } = await apiClient.patch<MaintenanceRegionOut>(`/regions/${regionId}`, payload)
  return data
}

export async function listLightingPointsApi(
  params?: LightingPointListParams,
): Promise<PaginatedResponse<LightingPointOut>> {
  const { data } = await apiClient.get<PaginatedResponse<LightingPointOut>>('/lighting-points', {
    params,
  })
  return data
}

export async function createLightingPointApi(
  payload: CreateLightingPointPayload,
): Promise<LightingPointOut> {
  const { data } = await apiClient.post<LightingPointOut>('/lighting-points', payload)
  return data
}

export async function updateLightingPointApi(
  pointId: number,
  payload: UpdateLightingPointPayload,
): Promise<LightingPointOut> {
  const { data } = await apiClient.patch<LightingPointOut>(`/lighting-points/${pointId}`, payload)
  return data
}

export async function listFacilityAssetsApi(
  params?: FacilityAssetListParams,
): Promise<PaginatedResponse<FacilityAssetOut>> {
  const { data } = await apiClient.get<PaginatedResponse<FacilityAssetOut>>('/facility-assets', {
    params,
  })
  return data
}

export async function createFacilityAssetApi(
  payload: CreateFacilityAssetPayload,
): Promise<FacilityAssetOut> {
  const { data } = await apiClient.post<FacilityAssetOut>('/facility-assets', payload)
  return data
}

export async function updateFacilityAssetApi(
  assetId: number,
  payload: UpdateFacilityAssetPayload,
): Promise<FacilityAssetOut> {
  const { data } = await apiClient.patch<FacilityAssetOut>(`/facility-assets/${assetId}`, payload)
  return data
}

export async function listBusinessRulesApi(
  params?: BusinessRuleListParams,
): Promise<PaginatedResponse<BusinessRuleOut> | BusinessRuleOut[]> {
  const { data } = await apiClient.get<PaginatedResponse<BusinessRuleOut> | BusinessRuleOut[]>(
    '/business-rules',
    { params },
  )
  return data
}

/**
 * 业务规则变更必须走 POST /business-rules 创建新版本，严禁使用 PUT 或原地修改 rule_value。
 */
export async function createBusinessRuleVersionApi(
  payload: CreateBusinessRulePayload,
): Promise<BusinessRuleOut> {
  const { data } = await apiClient.post<BusinessRuleOut>('/business-rules', payload)
  return data
}

export async function patchBusinessRuleMetaApi(
  ruleId: number,
  payload: UpdateBusinessRuleMetaPayload,
): Promise<BusinessRuleOut> {
  const { data } = await apiClient.patch<BusinessRuleOut>(`/business-rules/${ruleId}`, payload)
  return data
}
