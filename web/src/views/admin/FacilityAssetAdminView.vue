<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  createFacilityAssetApi,
  listFacilityAssetsApi,
  updateFacilityAssetApi,
} from '@/api/admin'
import { isApiError } from '@/shared/api'
import {
  ASSET_IMPORTANCE_LABELS,
  ASSET_STATUS_LABELS,
  ASSET_TYPE_LABELS,
} from '@/shared/enums'
import type {
  AssetImportance,
  AssetStatus,
  AssetType,
  FacilityAssetOut,
} from '@/shared/types'
import { useErrorBannerStore } from '@/stores/errorBanner'
import { useMetadataStore } from '@/stores/metadata'

const bannerStore = useErrorBannerStore()
const metadataStore = useMetadataStore()

const assets = ref<FacilityAssetOut[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editing = ref<FacilityAssetOut | null>(null)

const filters = ref<{
  lighting_point_id: number | undefined
  asset_type: AssetType | undefined
  importance: AssetImportance | undefined
}>({
  lighting_point_id: undefined,
  asset_type: undefined,
  importance: undefined,
})

const form = ref({
  asset_code: '',
  lighting_point_id: undefined as number | undefined,
  asset_type: 'LUMINAIRE' as AssetType,
  name: '',
  model_spec: '',
  rated_power_w: '150',
  importance: 'NORMAL' as AssetImportance,
  installed_at: '',
  status: 'IN_SERVICE' as AssetStatus,
  description: '',
})

async function load(targetPage = page.value): Promise<void> {
  page.value = targetPage
  loading.value = true
  try {
    const res = await listFacilityAssetsApi({
      lighting_point_id: filters.value.lighting_point_id,
      asset_type: filters.value.asset_type,
      importance: filters.value.importance,
      page: page.value,
      page_size: pageSize.value,
    })
    assets.value = res.items
    total.value = res.total
  } catch {
    // 全局错误横幅已处理
  } finally {
    loading.value = false
  }
}

function handleSearch(): void {
  void load(1)
}

function handleReset(): void {
  filters.value = { lighting_point_id: undefined, asset_type: undefined, importance: undefined }
  void load(1)
}

function openCreate(): void {
  editing.value = null
  form.value = {
    asset_code: '',
    lighting_point_id: metadataStore.points[0]?.id,
    asset_type: 'LUMINAIRE',
    name: '',
    model_spec: '',
    rated_power_w: '150',
    importance: 'NORMAL',
    installed_at: '',
    status: 'IN_SERVICE',
    description: '',
  }
  dialogVisible.value = true
}

function openEdit(asset: FacilityAssetOut): void {
  editing.value = asset
  form.value = {
    asset_code: asset.asset_code,
    lighting_point_id: asset.lighting_point_id,
    asset_type: asset.asset_type,
    name: asset.name,
    model_spec: asset.model_spec ?? '',
    rated_power_w: asset.rated_power_w === null ? '' : String(asset.rated_power_w),
    importance: asset.importance,
    installed_at: asset.installed_at ?? '',
    status: asset.status,
    description: asset.description ?? '',
  }
  dialogVisible.value = true
}

async function handleSave(): Promise<void> {
  if (!form.value.name.trim()) {
    ElMessage.warning('资产名称必填')
    return
  }
  const powerTrimmed = form.value.rated_power_w.trim()
  const parsedPower = powerTrimmed === '' ? null : Number(powerTrimmed)
  if (parsedPower !== null && (Number.isNaN(parsedPower) || parsedPower < 0)) {
    ElMessage.warning('额定功率须为非负数')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await updateFacilityAssetApi(editing.value.id, {
        name: form.value.name.trim(),
        asset_type: form.value.asset_type,
        model_spec: form.value.model_spec.trim() || null,
        rated_power_w: parsedPower,
        importance: form.value.importance,
        status: form.value.status,
        description: form.value.description.trim() || null,
      })
      ElMessage.success('资产信息已更新')
    } else {
      if (!form.value.asset_code.trim() || form.value.lighting_point_id === undefined) {
        ElMessage.warning('资产编码与所属点位必填')
        return
      }
      await createFacilityAssetApi({
        asset_code: form.value.asset_code.trim(),
        lighting_point_id: form.value.lighting_point_id,
        asset_type: form.value.asset_type,
        name: form.value.name.trim(),
        model_spec: form.value.model_spec.trim() || null,
        rated_power_w: parsedPower,
        importance: form.value.importance,
        installed_at: form.value.installed_at.trim() || null,
        description: form.value.description.trim() || null,
      })
      ElMessage.success('资产已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (err: unknown) {
    if (isApiError(err)) bannerStore.showFromApiError(err)
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  bannerStore.clear()
  try {
    await Promise.all([metadataStore.loadPoints(), load(1)])
  } catch {
    // 全局错误横幅已处理
  }
})
</script>

<template>
  <div>
    <div class="tomori-page-header">
      <h2 class="tomori-page-title">设施资产台账</h2>
      <button type="button" class="tomori-btn tomori-btn--primary" @click="openCreate">新增资产</button>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="额定功率 rated_power_w 仅作铭牌属性与备件录入，系统内不做任何能耗 / 负荷 / 电费计算或预测（硬红线 N3）。"
      style="margin-bottom: 12px"
    />

    <div class="tomori-card filter-row">
      <el-select
        v-model="filters.lighting_point_id"
        placeholder="全部照明点位"
        clearable
        filterable
        :teleported="false"
        style="width: 240px"
      >
        <el-option
          v-for="p in metadataStore.points"
          :key="p.id"
          :label="`${p.point_code} · ${p.name}`"
          :value="p.id"
        />
      </el-select>
      <el-select v-model="filters.asset_type" placeholder="全部资产类型" clearable :teleported="false" style="width: 150px">
        <el-option
          v-for="(label, code) in ASSET_TYPE_LABELS"
          :key="code"
          :label="label"
          :value="code"
        />
      </el-select>
      <el-select v-model="filters.importance" placeholder="全部重要度" clearable :teleported="false" style="width: 180px">
        <el-option
          v-for="(label, code) in ASSET_IMPORTANCE_LABELS"
          :key="code"
          :label="label"
          :value="code"
        />
      </el-select>
      <button type="button" class="tomori-btn tomori-btn--primary" :disabled="loading" @click="handleSearch">查询</button>
      <button type="button" class="tomori-btn" @click="handleReset">重置</button>
      <span class="hint-text">共 {{ total }} 项资产</span>
    </div>

    <div class="tomori-card" style="padding: 0; overflow-x: auto">
      <table class="data-table">
        <thead>
          <tr>
            <th>资产编码</th>
            <th>名称</th>
            <th>所属点位</th>
            <th>类型</th>
            <th>型号</th>
            <th>额定功率（仅铭牌）</th>
            <th>重要度</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="9" style="text-align: center">加载中...</td>
          </tr>
          <tr v-else-if="assets.length === 0">
            <td colspan="9" style="text-align: center">暂无资产数据</td>
          </tr>
          <tr v-for="asset in assets" v-else :key="asset.id" :data-testid="`asset-row-${asset.id}`">
            <td><code>{{ asset.asset_code }}</code></td>
            <td>{{ asset.name }}</td>
            <td>{{ asset.point_code || `点位#${asset.lighting_point_id}` }}</td>
            <td>{{ ASSET_TYPE_LABELS[asset.asset_type] }}</td>
            <td>{{ asset.model_spec ?? '—' }}</td>
            <td>{{ asset.rated_power_w !== null && asset.rated_power_w !== undefined ? `${asset.rated_power_w} W` : '—' }}</td>
            <td>
              <el-tag
                size="small"
                :type="asset.importance === 'CRITICAL' ? 'danger' : asset.importance === 'HIGH' ? 'warning' : 'info'"
              >
                {{ ASSET_IMPORTANCE_LABELS[asset.importance] }}
              </el-tag>
            </td>
            <td>{{ ASSET_STATUS_LABELS[asset.status] }}</td>
            <td>
              <button type="button" class="tomori-btn" @click="openEdit(asset)">编辑</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="total > pageSize" style="display: flex; justify-content: flex-end; margin-top: 12px">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="(p: number) => load(p)"
      />
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑设施资产' : '新增设施资产'"
      width="540px"
      :teleported="false"
    >
      <div class="dialog-grid">
        <label>
          <div class="field-label">资产编码</div>
          <input v-model="form.asset_code" class="tomori-input" :disabled="Boolean(editing)" placeholder="如 ASSET-LED-001" />
        </label>
        <div>
          <div class="field-label">所属照明点位</div>
          <el-select
            v-model="form.lighting_point_id"
            :teleported="false"
            :disabled="Boolean(editing)"
            style="width: 100%"
            filterable
          >
            <el-option
              v-for="p in metadataStore.points"
              :key="p.id"
              :label="`${p.point_code} · ${p.name}`"
              :value="p.id"
            />
          </el-select>
        </div>
        <div>
          <div class="field-label">资产类型</div>
          <el-select v-model="form.asset_type" :teleported="false" style="width: 100%">
            <el-option
              v-for="(label, code) in ASSET_TYPE_LABELS"
              :key="code"
              :label="label"
              :value="code"
            />
          </el-select>
        </div>
        <label>
          <div class="field-label">资产名称</div>
          <input v-model="form.name" class="tomori-input" placeholder="如 150W LED 灯具" />
        </label>
        <label>
          <div class="field-label">型号规格</div>
          <input v-model="form.model_spec" class="tomori-input" placeholder="如 LED-150W-4000K" />
        </label>
        <label>
          <div class="field-label">额定功率 (W，仅铭牌，不做能耗预测)</div>
          <input v-model="form.rated_power_w" type="number" min="0" step="0.1" class="tomori-input" />
        </label>
        <div>
          <div class="field-label">重要度（分级关键输入）</div>
          <el-select v-model="form.importance" :teleported="false" style="width: 100%">
            <el-option
              v-for="(label, code) in ASSET_IMPORTANCE_LABELS"
              :key="code"
              :label="label"
              :value="code"
            />
          </el-select>
        </div>
        <label v-if="!editing">
          <div class="field-label">安装投运日期（可选）</div>
          <input v-model="form.installed_at" type="date" class="tomori-input" />
        </label>
        <div v-else>
          <div class="field-label">资产状态</div>
          <el-select v-model="form.status" :teleported="false" style="width: 100%">
            <el-option
              v-for="(label, code) in ASSET_STATUS_LABELS"
              :key="code"
              :label="label"
              :value="code"
            />
          </el-select>
        </div>
        <label style="grid-column: 1 / -1">
          <div class="field-label">说明</div>
          <textarea v-model="form.description" class="tomori-textarea" />
        </label>
      </div>
      <template #footer>
        <button type="button" class="tomori-btn" @click="dialogVisible = false">取消</button>
        <button type="button" class="tomori-btn tomori-btn--primary" :disabled="saving" @click="handleSave">保存</button>
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

.hint-text {
  font-size: 12px;
  color: var(--tomori-text-muted);
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

.dialog-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--tomori-text-secondary);
}
</style>
