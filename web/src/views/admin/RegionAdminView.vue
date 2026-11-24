<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createRegionApi, listRegionsApi, updateRegionApi } from '@/api/admin'
import { isApiError } from '@/shared/api'
import { formatDateTime } from '@/shared/format'
import type { MaintenanceRegionOut } from '@/shared/types'
import { useErrorBannerStore } from '@/stores/errorBanner'
import { useMetadataStore } from '@/stores/metadata'

const bannerStore = useErrorBannerStore()
const metadataStore = useMetadataStore()

const regions = ref<MaintenanceRegionOut[]>([])
const loading = ref(false)
const saving = ref(false)
const togglingId = ref<number | null>(null)
const dialogVisible = ref(false)
const editing = ref<MaintenanceRegionOut | null>(null)

const filterKeyword = ref('')
const filterActive = ref<'all' | 'active' | 'inactive'>('all')

const form = ref({
  region_code: '',
  name: '',
  parent_region_id: null as number | null,
  level: 3,
  description: '',
  is_active: true,
})

const regionMap = computed(() => {
  const map = new Map<number, MaintenanceRegionOut>()
  for (const r of regions.value) {
    map.set(r.id, r)
  }
  return map
})

const parentCandidates = computed(() => {
  if (!editing.value) return regions.value
  return regions.value.filter((r) => r.id !== editing.value?.id)
})

const filteredRegions = computed(() => {
  const kw = filterKeyword.value.trim().toLowerCase()
  return regions.value.filter((r) => {
    if (filterActive.value === 'active' && !r.is_active) return false
    if (filterActive.value === 'inactive' && r.is_active) return false
    if (kw) {
      return (
        r.region_code.toLowerCase().includes(kw) ||
        r.name.toLowerCase().includes(kw) ||
        (r.description ?? '').toLowerCase().includes(kw)
      )
    }
    return true
  })
})

async function load(): Promise<void> {
  loading.value = true
  try {
    regions.value = await listRegionsApi({ flat: true, page_size: 100 })
    metadataStore.regions = regions.value
  } catch {
    // 全局错误横幅已处理
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editing.value = null
  form.value = {
    region_code: '',
    name: '',
    parent_region_id: null,
    level: 3,
    description: '',
    is_active: true,
  }
  dialogVisible.value = true
}

function openEdit(region: MaintenanceRegionOut): void {
  editing.value = region
  form.value = {
    region_code: region.region_code,
    name: region.name,
    parent_region_id: region.parent_region_id,
    level: region.level,
    description: region.description ?? '',
    is_active: region.is_active,
  }
  dialogVisible.value = true
}

async function handleSave(): Promise<void> {
  if (!form.value.name.trim()) {
    ElMessage.warning('区域名称必填')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await updateRegionApi(editing.value.id, {
        name: form.value.name.trim(),
        parent_region_id: form.value.parent_region_id,
        level: form.value.level,
        description: form.value.description.trim() || null,
        is_active: form.value.is_active,
      })
      ElMessage.success('区域信息已更新')
    } else {
      if (!form.value.region_code.trim()) {
        ElMessage.warning('区域编码必填')
        return
      }
      await createRegionApi({
        region_code: form.value.region_code.trim(),
        name: form.value.name.trim(),
        parent_region_id: form.value.parent_region_id,
        level: form.value.level,
        description: form.value.description.trim() || null,
      })
      ElMessage.success('区域已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (err: unknown) {
    if (isApiError(err)) bannerStore.showFromApiError(err)
  } finally {
    saving.value = false
  }
}

async function toggleActive(region: MaintenanceRegionOut): Promise<void> {
  togglingId.value = region.id
  try {
    await updateRegionApi(region.id, { is_active: !region.is_active })
    ElMessage.success(region.is_active ? `已停用 ${region.name}` : `已启用 ${region.name}`)
    await load()
  } catch (err: unknown) {
    if (isApiError(err)) bannerStore.showFromApiError(err)
  } finally {
    togglingId.value = null
  }
}

onMounted(() => {
  bannerStore.clear()
  void load()
})
</script>

<template>
  <div>
    <div class="tomori-page-header">
      <h2 class="tomori-page-title">运维区域档案管理</h2>
      <div style="display: flex; gap: 8px">
        <button type="button" class="tomori-btn" :disabled="loading" @click="load">刷新</button>
        <button type="button" class="tomori-btn tomori-btn--primary" @click="openCreate">新增区域</button>
      </div>
    </div>

    <div class="tomori-card filter-row">
      <input
        v-model="filterKeyword"
        type="text"
        class="tomori-input"
        style="width: 240px"
        placeholder="区域编码 / 名称 / 说明"
      />
      <el-select v-model="filterActive" :teleported="false" style="width: 140px">
        <el-option label="全部状态" value="all" />
        <el-option label="仅启用" value="active" />
        <el-option label="仅停用" value="inactive" />
      </el-select>
    </div>

    <div class="tomori-card" style="padding: 0; overflow-x: auto">
      <table class="data-table">
        <thead>
          <tr>
            <th>区域编码</th>
            <th>区域名称</th>
            <th>层级</th>
            <th>父区域</th>
            <th>状态</th>
            <th>说明</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="8" style="text-align: center">加载中...</td>
          </tr>
          <tr v-else-if="filteredRegions.length === 0">
            <td colspan="8" style="text-align: center">暂无区域数据</td>
          </tr>
          <tr
            v-for="region in filteredRegions"
            v-else
            :key="region.id"
            :data-testid="`region-row-${region.id}`"
          >
            <td><code>{{ region.region_code }}</code></td>
            <td>{{ region.name }}</td>
            <td>{{ region.level }}</td>
            <td>
              <span v-if="region.parent_region_id">
                {{ regionMap.get(region.parent_region_id)?.name || `#${region.parent_region_id}` }}
              </span>
              <span v-else>—</span>
            </td>
            <td>
              <el-tag :type="region.is_active ? 'success' : 'info'" size="small">
                {{ region.is_active ? '启用' : '停用' }}
              </el-tag>
            </td>
            <td>{{ region.description || '—' }}</td>
            <td>{{ formatDateTime(region.created_at) }}</td>
            <td class="action-cell">
              <button type="button" class="tomori-btn" @click="openEdit(region)">编辑</button>
              <button
                type="button"
                class="tomori-btn"
                :class="region.is_active ? 'tomori-btn--danger' : 'tomori-btn--success'"
                :disabled="togglingId === region.id"
                @click="toggleActive(region)"
              >
                {{ region.is_active ? '停用' : '启用' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑运维区域' : '新增运维区域'"
      width="460px"
      :teleported="false"
    >
      <div style="display: flex; flex-direction: column; gap: 12px">
        <label>
          <div class="field-label">区域编码</div>
          <input v-model="form.region_code" class="tomori-input" :disabled="Boolean(editing)" placeholder="如 WH-HGY-01" />
        </label>
        <label>
          <div class="field-label">区域名称</div>
          <input v-model="form.name" class="tomori-input" placeholder="如 洪山区-虎泉街" />
        </label>
        <label>
          <div class="field-label">层级（1 片区 / 2 道路 / 3 路段）</div>
          <input v-model.number="form.level" type="number" min="1" max="3" class="tomori-input" />
        </label>
        <div>
          <div class="field-label">上级区域（可选）</div>
          <el-select
            v-model="form.parent_region_id"
            :teleported="false"
            clearable
            placeholder="无上级区域"
            style="width: 100%"
          >
            <el-option
              v-for="cand in parentCandidates"
              :key="cand.id"
              :label="`${cand.name} (${cand.region_code})`"
              :value="cand.id"
            />
          </el-select>
        </div>
        <label>
          <div class="field-label">说明</div>
          <textarea v-model="form.description" class="tomori-textarea" />
        </label>
        <label v-if="editing" class="tomori-checkbox-label">
          <input v-model="form.is_active" type="checkbox" class="tomori-checkbox" />
          <span>启用该区域（停用后禁止向此区域新派单）</span>
        </label>
      </div>
      <template #footer>
        <button type="button" class="tomori-btn" @click="dialogVisible = false">取消</button>
        <button type="button" class="tomori-btn tomori-btn--primary" :disabled="saving" @click="handleSave">
          保存
        </button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.filter-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.data-table th,
.data-table td {
  padding: 10px 12px;
  border-bottom: 1px solid #ebeef5;
  text-align: left;
  white-space: nowrap;
}

.data-table th {
  background: #fafafa;
  font-weight: 600;
  color: var(--tomori-text-secondary);
}

.action-cell {
  display: flex;
  gap: 6px;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--tomori-text-secondary);
}
</style>
