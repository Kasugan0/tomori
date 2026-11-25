import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  listBusinessRulesApi,
  listFacilityAssetsApi,
  listLightingPointsApi,
  listRegionsApi,
} from '@/api/admin'
import { listAccountsApi, listRolesApi } from '@/api/auth'
import type {
  AccountOut,
  BusinessRuleOut,
  FacilityAssetOut,
  LightingPointOut,
  MaintenanceRegionOut,
  RoleOut,
} from '@/shared/types'

export const useMetadataStore = defineStore('metadata', () => {
  const regions = ref<MaintenanceRegionOut[]>([])
  const points = ref<LightingPointOut[]>([])
  const assets = ref<FacilityAssetOut[]>([])
  const rules = ref<BusinessRuleOut[]>([])
  const roles = ref<RoleOut[]>([])
  const accounts = ref<AccountOut[]>([])

  async function loadRegions(): Promise<MaintenanceRegionOut[]> {
    regions.value = await listRegionsApi({ flat: true, page_size: 100 })
    return regions.value
  }

  async function loadPoints(regionId?: number): Promise<LightingPointOut[]> {
    const res = await listLightingPointsApi({ region_id: regionId, page_size: 100 })
    points.value = res.items
    return points.value
  }

  async function loadAssets(lightingPointId?: number): Promise<FacilityAssetOut[]> {
    const res = await listFacilityAssetsApi({ lighting_point_id: lightingPointId, page_size: 100 })
    assets.value = res.items
    return assets.value
  }

  async function loadRules(isActive?: boolean): Promise<BusinessRuleOut[]> {
    const res = await listBusinessRulesApi({ is_active: isActive, page_size: 100 })
    rules.value = Array.isArray(res) ? res : res.items
    return rules.value
  }

  async function loadRoles(): Promise<RoleOut[]> {
    roles.value = await listRolesApi()
    return roles.value
  }

  async function loadAccounts(): Promise<AccountOut[]> {
    const res = await listAccountsApi({ page_size: 100 })
    accounts.value = res.items
    return accounts.value
  }

  return {
    regions,
    points,
    assets,
    rules,
    roles,
    accounts,
    loadRegions,
    loadPoints,
    loadAssets,
    loadRules,
    loadRoles,
    loadAccounts,
  }
})
