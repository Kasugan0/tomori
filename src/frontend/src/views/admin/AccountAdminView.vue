<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  createAccountApi,
  listAccountsApi,
  listRolesApi,
  resetAccountPasswordApi,
  updateAccountApi,
} from '@/api/auth'
import { isApiError } from '@/shared/api'
import { FAULT_TYPE_LABELS, ROLE_LABELS } from '@/shared/enums'
import { formatDateTime } from '@/shared/format'
import type { AccountOut, AccountStatus, RoleCode, RoleOut } from '@/shared/types'
import { useAuthStore } from '@/stores/auth'
import { useErrorBannerStore } from '@/stores/errorBanner'

const USERNAME_PATTERN = /^[a-z0-9][a-z0-9_.-]{2,63}$/

const authStore = useAuthStore()
const bannerStore = useErrorBannerStore()

const accounts = ref<AccountOut[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const roles = ref<RoleOut[]>([])
const loading = ref(false)
const saving = ref(false)

const filters = ref<{
  role_code: RoleCode | undefined
  status: AccountStatus | undefined
  keyword: string
}>({
  role_code: undefined,
  status: undefined,
  keyword: '',
})

const dialogVisible = ref(false)
const editing = ref<AccountOut | null>(null)
const form = ref({
  username: '',
  secret: '',
  full_name: '',
  role_code: 'TECHNICIAN' as RoleCode,
  employee_no: '',
  phone: '',
  email: '',
  skills: [] as string[],
  max_active_dispatch: 3,
  status: 'ACTIVE' as AccountStatus,
})

const resetDialogVisible = ref(false)
const resetTarget = ref<AccountOut | null>(null)
const resetSecret = ref('')

const isEditingSelf = computed(() => Boolean(editing.value && editing.value.id === authStore.account?.id))

const roleOptions = computed<RoleOut[]>(() => {
  if (roles.value.length > 0) return roles.value
  return (Object.keys(ROLE_LABELS) as RoleCode[]).map((code, idx) => ({
    id: idx + 1,
    code,
    name: ROLE_LABELS[code],
    description: null,
  }))
})

async function load(targetPage = page.value): Promise<void> {
  page.value = targetPage
  loading.value = true
  try {
    const [accRes, roleList] = await Promise.all([
      listAccountsApi({
        role_code: filters.value.role_code,
        status: filters.value.status,
        keyword: filters.value.keyword.trim() || undefined,
        page: page.value,
        page_size: pageSize.value,
      }),
      listRolesApi(),
    ])
    accounts.value = accRes.items
    total.value = accRes.total
    roles.value = roleList
  } catch {
    // 全局错误横幅已处理
  } finally {
    loading.value = false
  }
}

function handleSearch(): void {
  void load(1)
}

function handleResetFilters(): void {
  filters.value = { role_code: undefined, status: undefined, keyword: '' }
  void load(1)
}

function openCreate(): void {
  editing.value = null
  form.value = {
    username: '',
    secret: '',
    full_name: '',
    role_code: 'TECHNICIAN',
    employee_no: '',
    phone: '',
    email: '',
    skills: [],
    max_active_dispatch: 3,
    status: 'ACTIVE',
  }
  dialogVisible.value = true
}

function openEdit(acc: AccountOut): void {
  editing.value = acc
  form.value = {
    username: acc.username,
    secret: '',
    full_name: acc.full_name,
    role_code: acc.role_code,
    employee_no: acc.employee_no ?? '',
    phone: acc.phone ?? '',
    email: acc.email ?? '',
    skills: [...acc.skills],
    max_active_dispatch: acc.max_active_dispatch,
    status: acc.status,
  }
  dialogVisible.value = true
}

function openResetPassword(acc: AccountOut): void {
  resetTarget.value = acc
  resetSecret.value = ''
  resetDialogVisible.value = true
}

async function handleSave(): Promise<void> {
  if (!form.value.full_name.trim()) {
    ElMessage.warning('姓名必填')
    return
  }
  if (form.value.max_active_dispatch < 1 || form.value.max_active_dispatch > 20) {
    ElMessage.warning('在途派单上限须在 1–20 之间')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      const isSelf = editing.value.id === authStore.account?.id
      await updateAccountApi(editing.value.id, {
        full_name: form.value.full_name.trim(),
        employee_no: form.value.employee_no.trim() || null,
        phone: form.value.phone.trim() || null,
        email: form.value.email.trim() || null,
        skills: form.value.skills,
        max_active_dispatch: form.value.max_active_dispatch,
        status: isSelf ? undefined : form.value.status,
      })
      ElMessage.success('账号信息已更新')
    } else {
      const uname = form.value.username.trim()
      if (!USERNAME_PATTERN.test(uname)) {
        ElMessage.warning('用户名须 3–64 位小写字母/数字开头，可含 _ . -')
        return
      }
      if (form.value.secret.length < 8) {
        ElMessage.warning('初始密码不少于 8 位')
        return
      }
      await createAccountApi({
        username: uname,
        password: form.value.secret,
        full_name: form.value.full_name.trim(),
        role_code: form.value.role_code,
        employee_no: form.value.employee_no.trim() || null,
        phone: form.value.phone.trim() || null,
        email: form.value.email.trim() || null,
        skills: form.value.skills,
        max_active_dispatch: form.value.max_active_dispatch,
      })
      ElMessage.success('账号已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (err: unknown) {
    if (isApiError(err)) bannerStore.showFromApiError(err)
  } finally {
    saving.value = false
  }
}

async function handleResetPassword(): Promise<void> {
  if (!resetTarget.value || resetSecret.value.length < 8) {
    ElMessage.warning('新密码至少 8 位')
    return
  }
  saving.value = true
  try {
    await resetAccountPasswordApi(resetTarget.value.id, resetSecret.value)
    ElMessage.success(`已重置 ${resetTarget.value.username} 密码（旧 Token 已吊销）`)
    resetDialogVisible.value = false
  } catch (err: unknown) {
    if (isApiError(err)) bannerStore.showFromApiError(err)
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  bannerStore.clear()
  void load(1)
})
</script>

<template>
  <div>
    <div class="tomori-page-header">
      <h2 class="tomori-page-title">账号与角色管理（一账号一角色）</h2>
      <button type="button" class="tomori-btn tomori-btn--primary" @click="openCreate">新建账号</button>
    </div>

    <div v-if="roles.length > 0" class="tomori-card roles-strip">
      <span style="font-size: 13px; font-weight: 600; color: var(--tomori-text-secondary)">系统角色字典：</span>
      <el-tag v-for="r in roles" :key="r.id" size="small" type="info">
        {{ r.name }} ({{ r.code }}){{ r.description ? ` · ${r.description}` : '' }}
      </el-tag>
    </div>

    <div class="tomori-card filter-row">
      <el-select v-model="filters.role_code" clearable :teleported="false" placeholder="全部角色" style="width: 170px">
        <el-option v-for="r in roleOptions" :key="r.code" :label="`${r.name} (${r.code})`" :value="r.code" />
      </el-select>
      <el-select v-model="filters.status" clearable :teleported="false" placeholder="全部状态" style="width: 140px">
        <el-option label="ACTIVE" value="ACTIVE" />
        <el-option label="DISABLED" value="DISABLED" />
        <el-option label="LOCKED" value="LOCKED" />
      </el-select>
      <input
        v-model="filters.keyword"
        class="tomori-input"
        style="width: 220px"
        placeholder="用户名 / 姓名 / 工号"
        @keyup.enter="handleSearch"
      />
      <button type="button" class="tomori-btn tomori-btn--primary" :disabled="loading" @click="handleSearch">查询</button>
      <button type="button" class="tomori-btn" @click="handleResetFilters">重置</button>
    </div>

    <div class="tomori-card" style="padding: 0; overflow-x: auto">
      <table class="data-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>用户名</th>
            <th>姓名</th>
            <th>工号 / 手机</th>
            <th>角色</th>
            <th>技能标签</th>
            <th>在途上限</th>
            <th>状态</th>
            <th>最近登录</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="10" style="text-align: center">加载中...</td>
          </tr>
          <tr v-else-if="accounts.length === 0">
            <td colspan="10" style="text-align: center">暂无账号</td>
          </tr>
          <tr v-for="acc in accounts" v-else :key="acc.id" :data-testid="`account-row-${acc.id}`">
            <td>{{ acc.id }}</td>
            <td><code>{{ acc.username }}</code></td>
            <td>
              {{ acc.full_name }}
              <el-tag v-if="acc.id === authStore.account?.id" size="small" type="primary" style="margin-left: 4px">
                本人
              </el-tag>
            </td>
            <td>{{ acc.employee_no || '—' }} / {{ acc.phone || '—' }}</td>
            <td>
              <el-tag size="small">{{ ROLE_LABELS[acc.role_code] }} ({{ acc.role_code }})</el-tag>
            </td>
            <td>{{ acc.skills.join(', ') || '—' }}</td>
            <td>{{ acc.max_active_dispatch }}</td>
            <td>
              <el-tag size="small" :type="acc.status === 'ACTIVE' ? 'success' : 'danger'">
                {{ acc.status }}
              </el-tag>
            </td>
            <td>{{ formatDateTime(acc.last_login_at) }}</td>
            <td>
              <div style="display: flex; gap: 6px">
                <button type="button" class="tomori-btn" @click="openEdit(acc)">编辑</button>
                <button type="button" class="tomori-btn" @click="openResetPassword(acc)">重置密码</button>
              </div>
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
      :title="editing ? `编辑账号：${editing.username}` : '新建账号'"
      width="560px"
      :teleported="false"
    >
      <div class="dialog-grid">
        <label>
          <div class="field-label">用户名</div>
          <input
            v-model="form.username"
            class="tomori-input"
            :disabled="Boolean(editing)"
            placeholder="小写字母/数字开头，3–64位"
          />
        </label>
        <label v-if="!editing">
          <div class="field-label">初始密码</div>
          <input v-model="form.secret" type="password" class="tomori-input" placeholder="至少 8 位" />
        </label>
        <label>
          <div class="field-label">姓名</div>
          <input v-model="form.full_name" class="tomori-input" />
        </label>
        <div>
          <div class="field-label">角色</div>
          <el-select v-model="form.role_code" :disabled="Boolean(editing)" :teleported="false" style="width: 100%">
            <el-option v-for="r in roleOptions" :key="r.code" :label="`${r.name} (${r.code})`" :value="r.code" />
          </el-select>
        </div>
        <label>
          <div class="field-label">工号（可选）</div>
          <input v-model="form.employee_no" class="tomori-input" placeholder="如 EMP-001" />
        </label>
        <label>
          <div class="field-label">手机号</div>
          <input v-model="form.phone" class="tomori-input" />
        </label>
        <label>
          <div class="field-label">邮箱（可选）</div>
          <input v-model="form.email" type="email" class="tomori-input" />
        </label>
        <label>
          <div class="field-label">在途派单上限（1–20）</div>
          <input v-model.number="form.max_active_dispatch" type="number" min="1" max="20" class="tomori-input" />
        </label>
        <div v-if="editing">
          <div class="field-label">账号状态（禁止修改自身状态）</div>
          <el-select
            v-model="form.status"
            :disabled="isEditingSelf"
            :teleported="false"
            style="width: 100%"
          >
            <el-option label="ACTIVE" value="ACTIVE" />
            <el-option label="DISABLED" value="DISABLED" />
            <el-option label="LOCKED" value="LOCKED" />
          </el-select>
          <div v-if="isEditingSelf" style="font-size: 12px; color: var(--tomori-warning); margin-top: 4px">
            不能修改当前登录管理员自己的账号状态
          </div>
        </div>
        <div style="grid-column: 1 / -1">
          <div class="field-label">维修技能（多选）</div>
          <el-select v-model="form.skills" multiple :teleported="false" style="width: 100%">
            <el-option
              v-for="(label, code) in FAULT_TYPE_LABELS"
              :key="code"
              :label="label"
              :value="code"
            />
          </el-select>
        </div>
      </div>
      <template #footer>
        <button type="button" class="tomori-btn" @click="dialogVisible = false">取消</button>
        <button type="button" class="tomori-btn tomori-btn--primary" :disabled="saving" @click="handleSave">保存</button>
      </template>
    </el-dialog>

    <el-dialog v-model="resetDialogVisible" title="重置账号密码" width="380px" :teleported="false">
      <label>
        <div class="field-label">为 {{ resetTarget?.username }} 设置新密码</div>
        <input v-model="resetSecret" type="password" class="tomori-input" placeholder="至少 8 位" />
      </label>
      <template #footer>
        <button type="button" class="tomori-btn" @click="resetDialogVisible = false">取消</button>
        <button type="button" class="tomori-btn tomori-btn--primary" :disabled="saving" @click="handleResetPassword">
          确认重置
        </button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.roles-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
}

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

.dialog-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 12px;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--tomori-text-secondary);
}
</style>
