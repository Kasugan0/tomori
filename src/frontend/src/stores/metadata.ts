import { ref } from 'vue'
import { defineStore } from 'pinia'
import { listFacilityAssetsApi, listLightingPointsApi, listRegionsApi } from '@/api/admin'
import { listAccountsApi } from '@/api/auth'
import { fetchAllPages } from '@/shared/pagination'
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
    points.value = await fetchAllPages((page, pageSize) =>
      listLightingPointsApi({ region_id: regionId, page, page_size: pageSize }),
    )
    return points.value
  }

  async function loadAssets(lightingPointId?: number): Promise<FacilityAssetOut[]> {
    assets.value = await fetchAllPages((page, pageSize) =>
      listFacilityAssetsApi({ lighting_point_id: lightingPointId, page, page_size: pageSize }),
    )
    return assets.value
  }

  async function loadAccounts(): Promise<AccountOut[]> {
    accounts.value = await fetchAllPages((page, pageSize) =>
      listAccountsApi({ page, page_size: pageSize }),
    )
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
