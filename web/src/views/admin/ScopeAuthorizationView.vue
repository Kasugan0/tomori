<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createScopeAuthorizationApi,
  deactivateScopeAuthorizationApi,
  listScopeAuthorizationsApi,
} from '@/api/auth'
import { isApiError } from '@/shared/api'
import { ROLE_LABELS, SCOPE_TYPE_LABELS } from '@/shared/enums'
import { formatDateTime } from '@/shared/format'
import type { AccountOut, ScopeAuthorizationOut, ScopeType } from '@/shared/types'
import { useErrorBannerStore } from '@/stores/errorBanner'
import { useMetadataStore } from '@/stores/metadata'

const bannerStore = useErrorBannerStore()
const metadataStore = useMetadataStore()

const scopes = ref<ScopeAuthorizationOut[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)

const filters = ref<{
  account_id: number | undefined
  region_id: number | undefined
  scope_type: ScopeType | undefined
  is_active: 'all' | 'true' | 'false'
}>({
  account_id: undefined,
  region_id: undefined,
  scope_type: undefined,
  is_active: 'true',
})

const form = ref({
  account_id: undefined as number | undefined,
  region_id: undefined as number | undefined,
  scope_type: 'REPAIR' as ScopeType,
  valid_from: '',
  valid_to: '',
  remark: '',
})

const accountMap = computed(() => {
  const map = new Map<number, AccountOut>()
  for (const acc of metadataStore.accounts) {
    map.set(acc.id, acc)
  }
  return map
})

const regionMap = computed(() => {
  const map = new Map<number, string>()
  for (const r of metadataStore.regions) {
    map.set(r.id, `${r.name} (${r.region_code})`)
  }
  return map
})

async function load(targetPage = page.value): Promise<void> {
  page.value = targetPage
  loading.value = true
  try {
    const res = await listScopeAuthorizationsApi({
      account_id: filters.value.account_id,
      region_id: filters.value.region_id,
      scope_type: filters.value.scope_type,
      is_active:
        filters.value.is_active === 'true'
          ? true
          : filters.value.is_active === 'false'
            ? false
            : undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    scopes.value = res.items
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
  filters.value = {
    account_id: undefined,
    region_id: undefined,
    scope_type: undefined,
    is_active: 'all',
  }
  void load(1)
}

function openGrantDialog(): void {
  form.value = {
    account_id: undefined,
    region_id: metadataStore.regions[0]?.id,
    scope_type: 'REPAIR',
    valid_from: '',
    valid_to: '',
    remark: '',
  }
  dialogVisible.value = true
}

function handleAccountPick(accountId: number | undefined): void {
  if (accountId === undefined) return
  const acc = accountMap.value.get(accountId)
  if (!acc) return
  if (acc.role_code === 'INSPECTOR') form.value.scope_type = 'INSPECT'
  else if (acc.role_code === 'TECHNICIAN') form.value.scope_type = 'REPAIR'
  else if (acc.role_code === 'ADMIN') form.value.scope_type = 'MANAGE'
}

async function handleCreate(): Promise<void> {
  if (form.value.account_id === undefined || form.value.region_id === undefined) {
    ElMessage.warning('请选择账号与区域')
    return
  }
  if (form.value.valid_from && form.value.valid_to) {
    if (new Date(form.value.valid_to).getTime() <= new Date(form.value.valid_from).getTime()) {
      ElMessage.warning('过期时间必须晚于生效时间')
      return
    }
  }
  saving.value = true
  try {
    await createScopeAuthorizationApi({
      account_id: form.value.account_id,
      region_id: form.value.region_id,
      scope_type: form.value.scope_type,
      valid_from: form.value.valid_from ? new Date(form.value.valid_from).toISOString() : undefined,
      valid_to: form.value.valid_to ? new Date(form.value.valid_to).toISOString() : null,
      remark: form.value.remark.trim() || null,
    })
    ElMessage.success('授权已授予')
    dialogVisible.value = false
    await load(1)
  } catch (err: unknown) {
    if (isApiError(err)) bannerStore.showFromApiError(err)
  } finally {
    saving.value = false
  }
}

async function handleDeactivate(scope: ScopeAuthorizationOut): Promise<void> {
  try {
    await ElMessageBox.confirm('软停用授权记录后保留历史派单外键引用，确认停用？', '停用授权确认', {
      confirmButtonText: '确认停用',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  try {
    await deactivateScopeAuthorizationApi(scope.id)
    ElMessage.success('已软停用该区域授权')
    await load()
  } catch (err: unknown) {
    if (isApiError(err)) bannerStore.showFromApiError(err)
  }
}

onMounted(async () => {
  bannerStore.clear()
  try {
    await Promise.all([metadataStore.loadAccounts(), metadataStore.loadRegions(), load(1)])
  } catch {
    // 全局错误横幅已处理
  }
})
</script>

<template>
  <div>
    <div class="tomori-page-header">
      <h2 class="tomori-page-title">业务范围授权管理（区域 × 账号）</h2>
      <button type="button" class="tomori-btn tomori-btn--primary" @click="openGrantDialog">新增区域授权</button>
    </div>

    <div class="tomori-card filter-row">
      <el-select v-model="filters.account_id" clearable filterable :teleported="false" placeholder="全部账号" style="width: 210px">
        <el-option
          v-for="acc in metadataStore.accounts"
          :key="acc.id"
          :label="`${acc.full_name} (${acc.username})`"
          :value="acc.id"
        />
      </el-select>
      <el-select v-model="filters.region_id" clearable :teleported="false" placeholder="全部区域" style="width: 180px">
        <el-option v-for="r in metadataStore.regions" :key="r.id" :label="r.name" :value="r.id" />
      </el-select>
      <el-select v-model="filters.scope_type" clearable :teleported="false" placeholder="全部授权类型" style="width: 190px">
        <el-option
          v-for="(label, code) in SCOPE_TYPE_LABELS"
          :key="code"
          :label="label"
          :value="code"
        />
      </el-select>
      <el-select v-model="filters.is_active" :teleported="false" style="width: 130px">
        <el-option label="全部状态" value="all" />
        <el-option label="仅有效" value="true" />
        <el-option label="仅已停用" value="false" />
      </el-select>
      <button type="button" class="tomori-btn tomori-btn--primary" :disabled="loading" @click="handleSearch">查询</button>
      <button type="button" class="tomori-btn" @click="handleReset">重置</button>
    </div>

    <div class="tomori-card" style="padding: 0; overflow-x: auto">
      <table class="data-table">
        <thead>
          <tr>
            <th>授权ID</th>
            <th>账号</th>
            <th>运维区域</th>
            <th>权限类型</th>
            <th>有效期</th>
            <th>状态</th>
            <th>备注</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="8" style="text-align: center">加载中...</td>
          </tr>
          <tr v-else-if="scopes.length === 0">
            <td colspan="8" style="text-align: center">暂无授权记录</td>
          </tr>
          <tr
            v-for="scope in scopes"
            v-else
            :key="scope.id"
            :data-testid="`scope-row-${scope.account_id}-${scope.region_id}`"
          >
            <td>{{ scope.id }}</td>
            <td>
              <template v-if="accountMap.get(scope.account_id)">
                {{ accountMap.get(scope.account_id)?.full_name }}
                <code>({{ accountMap.get(scope.account_id)?.username }})</code>
              </template>
              <template v-else>#{{ scope.account_id }}</template>
            </td>
            <td>{{ scope.region_name || regionMap.get(scope.region_id) || `区域#${scope.region_id}` }}</td>
            <td><code>{{ scope.scope_type }}</code></td>
            <td>{{ formatDateTime(scope.valid_from) }} ~ {{ scope.valid_to ? formatDateTime(scope.valid_to) : '长期有效' }}</td>
            <td>
              <el-tag size="small" :type="scope.is_active ? 'success' : 'info'">
                {{ scope.is_active ? '有效' : '已停用' }}
              </el-tag>
            </td>
            <td>{{ scope.remark || '—' }}</td>
            <td>
              <button
                v-if="scope.is_active"
                type="button"
                class="tomori-btn tomori-btn--danger"
                @click="handleDeactivate(scope)"
              >
                软停用 (DELETE)
              </button>
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

    <el-dialog v-model="dialogVisible" title="授予区域业务授权" width="480px" :teleported="false">
      <div style="display: flex; flex-direction: column; gap: 12px">
        <div>
          <div class="field-label">被授权账号</div>
          <el-select
            v-model="form.account_id"
            filterable
            :teleported="false"
            style="width: 100%"
            @change="handleAccountPick"
          >
            <el-option
              v-for="acc in metadataStore.accounts"
              :key="acc.id"
              :label="`${acc.full_name} (${acc.username} · ${ROLE_LABELS[acc.role_code]})`"
              :value="acc.id"
            />
          </el-select>
        </div>
        <div>
          <div class="field-label">运维区域</div>
          <el-select v-model="form.region_id" :teleported="false" style="width: 100%">
            <el-option v-for="r in metadataStore.regions" :key="r.id" :label="`${r.name} (${r.region_code})`" :value="r.id" />
          </el-select>
        </div>
        <div>
          <div class="field-label">授权类型 (scope_type)</div>
          <el-select v-model="form.scope_type" :teleported="false" style="width: 100%">
            <el-option
              v-for="(label, code) in SCOPE_TYPE_LABELS"
              :key="code"
              :label="label"
              :value="code"
            />
          </el-select>
        </div>
        <label>
          <div class="field-label">生效时间（留空表示立即生效）</div>
          <input v-model="form.valid_from" type="datetime-local" class="tomori-input" />
        </label>
        <label>
          <div class="field-label">过期时间（留空表示长期有效）</div>
          <input v-model="form.valid_to" type="datetime-local" class="tomori-input" />
        </label>
        <label>
          <div class="field-label">备注</div>
          <input v-model="form.remark" class="tomori-input" placeholder="可选" />
        </label>
      </div>
      <template #footer>
        <button type="button" class="tomori-btn" @click="dialogVisible = false">取消</button>
        <button type="button" class="tomori-btn tomori-btn--primary" :disabled="saving" @click="handleCreate">确认授予</button>
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

.field-label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--tomori-text-secondary);
}
</style>
