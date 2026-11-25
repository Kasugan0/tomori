<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  createLightingPointApi,
  listLightingPointsApi,
  updateLightingPointApi,
} from '@/api/admin'
import { isApiError } from '@/shared/api'
import {
  POINT_STATUS_LABELS,
  POWER_SUPPLY_LABELS,
  ROAD_LEVEL_LABELS,
} from '@/shared/enums'
import type {
  LightingPointOut,
  LightingPointStatus,
  PowerSupplyType,
  RoadLevel,
} from '@/shared/types'
import { useErrorBannerStore } from '@/stores/errorBanner'
import { useMetadataStore } from '@/stores/metadata'

const bannerStore = useErrorBannerStore()
const metadataStore = useMetadataStore()

const points = ref<LightingPointOut[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editing = ref<LightingPointOut | null>(null)
const dialogError = ref('')

const filters = ref<{
  region_id: number | undefined
  status: LightingPointStatus | undefined
  keyword: string
}>({
  region_id: undefined,
  status: undefined,
  keyword: '',
})

const form = ref({
  point_code: '',
  region_id: undefined as number | undefined,
  name: '',
  road_name: '',
  road_level: 'BRANCH' as RoadLevel,
  longitude: 114.3,
  latitude: 30.5,
  install_height_m: '8',
  lamp_count: 1,
  power_supply_type: 'GRID' as PowerSupplyType,
  status: 'IN_SERVICE' as LightingPointStatus,
  description: '',
})

async function load(targetPage = page.value): Promise<void> {
  page.value = targetPage
  loading.value = true
  try {
    const res = await listLightingPointsApi({
      region_id: filters.value.region_id,
      status: filters.value.status,
      keyword: filters.value.keyword.trim() || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    points.value = res.items
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
  filters.value = { region_id: undefined, status: undefined, keyword: '' }
  void load(1)
}

function openCreate(): void {
  editing.value = null
  dialogError.value = ''
  form.value = {
    point_code: '',
    region_id: metadataStore.regions[0]?.id,
    name: '',
    road_name: '',
    road_level: 'BRANCH',
    longitude: 114.3,
    latitude: 30.5,
    install_height_m: '8',
    lamp_count: 1,
    power_supply_type: 'GRID',
    status: 'IN_SERVICE',
    description: '',
  }
  dialogVisible.value = true
}

function openEdit(point: LightingPointOut): void {
  editing.value = point
  dialogError.value = ''
  form.value = {
    point_code: point.point_code,
    region_id: point.region_id,
    name: point.name,
    road_name: point.road_name,
    road_level: point.road_level,
    longitude: Number(point.longitude),
    latitude: Number(point.latitude),
    install_height_m: point.install_height_m === null ? '' : String(point.install_height_m),
    lamp_count: point.lamp_count,
    power_supply_type: point.power_supply_type,
    status: point.status,
    description: point.description ?? '',
  }
  dialogVisible.value = true
}

async function handleSave(): Promise<void> {
  if (!form.value.name.trim() || !form.value.road_name.trim()) {
    ElMessage.warning('点位名称与道路名称必填')
    return
  }
  const heightTrimmed = form.value.install_height_m.trim()
  const parsedHeight = heightTrimmed === '' ? null : Number(heightTrimmed)
  if (parsedHeight !== null && Number.isNaN(parsedHeight)) {
    ElMessage.warning('安装高度格式不正确')
    return
  }
  saving.value = true
  dialogError.value = ''
  try {
    if (editing.value) {
      await updateLightingPointApi(editing.value.id, {
        region_id: form.value.region_id,
        name: form.value.name.trim(),
        road_name: form.value.road_name.trim(),
        road_level: form.value.road_level,
        longitude: form.value.longitude,
        latitude: form.value.latitude,
        install_height_m: parsedHeight,
        lamp_count: form.value.lamp_count,
        power_supply_type: form.value.power_supply_type,
        status: form.value.status,
        description: form.value.description.trim() || null,
      })
      ElMessage.success('点位已更新')
    } else {
      if (!form.value.point_code.trim() || form.value.region_id === undefined) {
        ElMessage.warning('点位编码与所属区域必填')
        return
      }
      await createLightingPointApi({
        point_code: form.value.point_code.trim(),
        region_id: form.value.region_id,
        name: form.value.name.trim(),
        road_name: form.value.road_name.trim(),
        road_level: form.value.road_level,
        longitude: form.value.longitude,
        latitude: form.value.latitude,
        install_height_m: parsedHeight,
        lamp_count: form.value.lamp_count,
        power_supply_type: form.value.power_supply_type,
        description: form.value.description.trim() || null,
      })
      ElMessage.success('点位已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (err: unknown) {
    if (isApiError(err)) {
      if (err.error.code === 'POINT_HAS_ACTIVE_TICKET') {
        dialogError.value = '该点位存在活跃故障工单，暂不允许变更所属区域 (422 POINT_HAS_ACTIVE_TICKET)'
        ElMessage.error('该点位存在活跃故障工单，禁止变更所属区域')
      } else if (err.error.message) {
        dialogError.value = err.error.message
      }
      bannerStore.showFromApiError(err)
    }
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  bannerStore.clear()
  try {
    await Promise.all([metadataStore.loadRegions(), load(1)])
  } catch {
    // 全局错误横幅已处理
  }
})
</script>

<template>
  <div>
    <div class="tomori-page-header">
      <h2 class="tomori-page-title">照明点位档案管理</h2>
      <button type="button" class="tomori-btn tomori-btn--primary" @click="openCreate">新增照明点位</button>
    </div>

    <div class="tomori-card filter-row">
      <el-select
        v-model="filters.region_id"
        placeholder="全部区域"
        clearable
        :teleported="false"
        style="width: 200px"
      >
        <el-option
          v-for="r in metadataStore.regions"
          :key="r.id"
          :label="r.name"
          :value="r.id"
        />
      </el-select>
      <el-select v-model="filters.status" placeholder="全部状态" clearable :teleported="false" style="width: 140px">
        <el-option
          v-for="(label, code) in POINT_STATUS_LABELS"
          :key="code"
          :label="label"
          :value="code"
        />
      </el-select>
      <input
        v-model="filters.keyword"
        class="tomori-input"
        style="width: 220px"
        placeholder="点位编码 / 名称 / 道路"
        @keyup.enter="handleSearch"
      />
      <button type="button" class="tomori-btn tomori-btn--primary" :disabled="loading" @click="handleSearch">查询</button>
      <button type="button" class="tomori-btn" @click="handleReset">重置</button>
      <span class="hint-text">共 {{ total }} 个点位</span>
    </div>

    <div class="tomori-card" style="padding: 0; overflow-x: auto">
      <table class="data-table">
        <thead>
          <tr>
            <th>点位编码</th>
            <th>点位名称</th>
            <th>所属区域</th>
            <th>道路 / 等级</th>
            <th>灯头数</th>
            <th>供电</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="8" style="text-align: center">加载中...</td>
          </tr>
          <tr v-else-if="points.length === 0">
            <td colspan="8" style="text-align: center">暂无点位数据</td>
          </tr>
          <tr
            v-for="point in points"
            v-else
            :key="point.id"
            :data-testid="`point-row-${point.point_code}`"
          >
            <td><code>{{ point.point_code }}</code></td>
            <td>{{ point.name }}</td>
            <td>{{ point.region_name || `区域#${point.region_id}` }}</td>
            <td>{{ point.road_name }} / {{ ROAD_LEVEL_LABELS[point.road_level] }}</td>
            <td>{{ point.lamp_count }}</td>
            <td>{{ POWER_SUPPLY_LABELS[point.power_supply_type] }}</td>
            <td>
              <el-tag size="small" :type="point.status === 'IN_SERVICE' ? 'success' : 'info'">
                {{ POINT_STATUS_LABELS[point.status] }}
              </el-tag>
            </td>
            <td>
              <button type="button" class="tomori-btn" @click="openEdit(point)">编辑</button>
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
      :title="editing ? '编辑照明点位' : '新增照明点位'"
      width="560px"
      :teleported="false"
    >
      <el-alert
        v-if="dialogError"
        type="error"
        :closable="false"
        show-icon
        style="margin-bottom: 12px"
        :title="dialogError"
      />
      <div class="dialog-grid">
        <label>
          <div class="field-label">点位编码</div>
          <input v-model="form.point_code" class="tomori-input" :disabled="Boolean(editing)" placeholder="如 SL-WH-HGY-00187" />
        </label>
        <div>
          <div class="field-label">所属区域</div>
          <el-select v-model="form.region_id" :teleported="false" style="width: 100%">
            <el-option v-for="r in metadataStore.regions" :key="r.id" :label="r.name" :value="r.id" />
          </el-select>
        </div>
        <label>
          <div class="field-label">点位名称</div>
          <input v-model="form.name" class="tomori-input" placeholder="如 雄楚大道 K3+250 左侧第 7 杆" />
        </label>
        <label>
          <div class="field-label">道路名称</div>
          <input v-model="form.road_name" class="tomori-input" placeholder="如 雄楚大道" />
        </label>
        <div>
          <div class="field-label">道路等级（故障分级关键输入）</div>
          <el-select v-model="form.road_level" :teleported="false" style="width: 100%">
            <el-option
              v-for="(label, code) in ROAD_LEVEL_LABELS"
              :key="code"
              :label="`${label} (${code})`"
              :value="code"
            />
          </el-select>
        </div>
        <label>
          <div class="field-label">灯头数量</div>
          <input v-model.number="form.lamp_count" type="number" min="1" class="tomori-input" />
        </label>
        <label>
          <div class="field-label">经度</div>
          <input v-model.number="form.longitude" type="number" step="0.0000001" class="tomori-input" />
        </label>
        <label>
          <div class="field-label">纬度</div>
          <input v-model.number="form.latitude" type="number" step="0.0000001" class="tomori-input" />
        </label>
        <label>
          <div class="field-label">安装高度 (m，可选)</div>
          <input v-model="form.install_height_m" type="number" step="0.1" class="tomori-input" />
        </label>
        <div>
          <div class="field-label">供电方式（纯属性，无控制能力）</div>
          <el-select v-model="form.power_supply_type" :teleported="false" style="width: 100%">
            <el-option
              v-for="(label, code) in POWER_SUPPLY_LABELS"
              :key="code"
              :label="label"
              :value="code"
            />
          </el-select>
        </div>
        <div v-if="editing">
          <div class="field-label">点位状态</div>
          <el-select v-model="form.status" :teleported="false" style="width: 100%">
            <el-option
              v-for="(label, code) in POINT_STATUS_LABELS"
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
