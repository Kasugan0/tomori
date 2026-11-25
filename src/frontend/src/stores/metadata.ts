import { ref } from 'vue'
import { defineStore } from 'pinia'
import { listFacilityAssetsApi, listLightingPointsApi, listRegionsApi } from '@/api/admin'
import { listAccountsApi } from '@/api/auth'
import type {
  AccountOut,
  FacilityAssetOut,
  LightingPointOut,
  MaintenanceRegionOut,
} from '@/shared/types'

export const useMetadataStore = defineStore('metadata', () => {
  const regions = ref<MaintenanceRegionOut[]>([])
  const points = ref<LightingPointOut[]>([])
  const assets = ref<FacilityAssetOut[]>([])
  const accounts = ref<AccountOut[]>([])

  async function loadRegions(): Promise<MaintenanceRegionOut[]> {
    regions.value = await listRegionsApi({ flat: true })
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

  async function loadAccounts(): Promise<AccountOut[]> {
    const res = await listAccountsApi({ page_size: 100 })
    accounts.value = res.items
    return accounts.value
  }

  return {
    regions,
    points,
    assets,
    accounts,
    loadRegions,
    loadPoints,
    loadAssets,
    loadAccounts,
  }
})
