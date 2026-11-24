<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { listAuditLogsApi } from '@/api/operations'
import { formatDateTime } from '@/shared/format'
import type { AuditLogOut } from '@/shared/types'
import { useErrorBannerStore } from '@/stores/errorBanner'
import { useMetadataStore } from '@/stores/metadata'

const bannerStore = useErrorBannerStore()
const metadataStore = useMetadataStore()

const logs = ref<AuditLogOut[]>([])
const total = ref(0)
const loading = ref(false)
const pageSize = 30

const detailDialogVisible = ref(false)
const activeLog = ref<AuditLogOut | null>(null)

const filters = ref<{
  actor_account_id: number | undefined
  action: string
  resource_type: string
  ticket_id: string
  response_status: string
  page: number
}>({
  actor_account_id: undefined,
  action: '',
  resource_type: '',
  ticket_id: '',
  response_status: '',
  page: 1,
})

async function load(targetPage = filters.value.page): Promise<void> {
  filters.value.page = targetPage
  loading.value = true
  try {
    const ticketIdNum = filters.value.ticket_id.trim() ? Number(filters.value.ticket_id.trim()) : undefined
    const statusNum = filters.value.response_status.trim()
      ? Number(filters.value.response_status.trim())
      : undefined

    const res = await listAuditLogsApi({
      actor_account_id: filters.value.actor_account_id,
      action: filters.value.action.trim() || undefined,
      resource_type: filters.value.resource_type.trim() || undefined,
      ticket_id: ticketIdNum !== undefined && !Number.isNaN(ticketIdNum) ? ticketIdNum : undefined,
      response_status: statusNum !== undefined && !Number.isNaN(statusNum) ? statusNum : undefined,
      page: filters.value.page,
      page_size: pageSize,
    })
    logs.value = res.items
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
    actor_account_id: undefined,
    action: '',
    resource_type: '',
    ticket_id: '',
    response_status: '',
    page: 1,
  }
  void load(1)
}

function openDetail(log: AuditLogOut): void {
  activeLog.value = log
  detailDialogVisible.value = true
}

onMounted(async () => {
  bannerStore.clear()
  try {
    await Promise.all([metadataStore.loadAccounts(), load(1)])
  } catch {
    // 全局错误横幅已处理
  }
})
</script>

<template>
  <div>
    <div class="tomori-page-header">
      <h2 class="tomori-page-title">操作审计日志（只读）</h2>
      <button type="button" class="tomori-btn" :disabled="loading" @click="load()">刷新</button>
    </div>

    <div class="tomori-card filter-row">
      <el-select
        v-model="filters.actor_account_id"
        clearable
        filterable
        :teleported="false"
        placeholder="全部操作人"
        style="width: 190px"
      >
        <el-option
          v-for="acc in metadataStore.accounts"
          :key="acc.id"
          :label="`${acc.full_name} (${acc.username})`"
          :value="acc.id"
        />
      </el-select>
      <input
        v-model="filters.action"
        class="tomori-input"
        style="width: 190px"
        placeholder="动作 (如 TICKET_DISPATCHED)"
        @keyup.enter="handleSearch"
      />
      <input
        v-model="filters.resource_type"
        class="tomori-input"
        style="width: 150px"
        placeholder="资源类型"
        @keyup.enter="handleSearch"
      />
      <input
        v-model="filters.ticket_id"
        type="number"
        class="tomori-input"
        style="width: 110px"
        placeholder="工单 ID"
        @keyup.enter="handleSearch"
      />
      <input
        v-model="filters.response_status"
        type="number"
        class="tomori-input"
        style="width: 110px"
        placeholder="HTTP 状态"
        @keyup.enter="handleSearch"
      />
      <button type="button" class="tomori-btn tomori-btn--primary" :disabled="loading" @click="handleSearch">
        筛选
      </button>
      <button type="button" class="tomori-btn" @click="handleReset">重置</button>
      <span class="hint-text">共 {{ total }} 条</span>
    </div>

    <div class="tomori-card" style="padding: 0; overflow-x: auto" data-testid="audit-log-table">
      <table class="data-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>操作人</th>
            <th>动作</th>
            <th>资源 / 工单</th>
            <th>HTTP / 错误码</th>
            <th>request_id</th>
            <th>幂等键</th>
            <th>明细</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="8" style="text-align: center">加载中...</td>
          </tr>
          <tr v-else-if="logs.length === 0">
            <td colspan="8" style="text-align: center">暂无审计记录</td>
          </tr>
          <tr v-for="(log, idx) in logs" v-else :key="log.id" :data-testid="`audit-log-row-${idx}`">
            <td>{{ formatDateTime(log.created_at) }}</td>
            <td>{{ log.actor_username || '匿名' }} ({{ log.actor_role_code || '-' }})</td>
            <td><code>{{ log.action }}</code></td>
            <td>
              <span>{{ log.resource_type }} #{{ log.resource_id || '-' }}</span>
              <router-link
                v-if="log.ticket_id"
                :to="`/tickets/${log.ticket_id}`"
                style="margin-left: 6px; color: var(--tomori-primary); text-decoration: none"
              >
                [工单#{{ log.ticket_id }}]
              </router-link>
            </td>
            <td>
              <el-tag size="small" :type="log.response_status < 400 ? 'success' : 'danger'">
                {{ log.response_status }}
              </el-tag>
              <code v-if="log.error_code" style="margin-left: 6px">{{ log.error_code }}</code>
            </td>
            <td><code>{{ log.request_id }}</code></td>
            <td><code>{{ log.idempotency_key || '—' }}</code></td>
            <td>
              <button
                type="button"
                class="tomori-btn"
                style="padding: 4px 8px; font-size: 12px"
                @click="openDetail(log)"
              >
                快照
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="total > pageSize" style="display: flex; justify-content: flex-end; margin-top: 12px">
      <el-pagination
        v-model:current-page="filters.page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="(p: number) => load(p)"
      />
    </div>

    <el-dialog v-model="detailDialogVisible" title="审计记录明细（只读）" width="640px" :teleported="false">
      <div v-if="activeLog" style="display: flex; flex-direction: column; gap: 10px; font-size: 13px">
        <div>
          <strong>{{ activeLog.method }}</strong> <code>{{ activeLog.path }}</code> · 客户端 IP：{{ activeLog.client_ip || '—' }}
        </div>
        <div>
          <div style="font-weight: 600; margin-bottom: 4px">请求载荷 (request_payload)</div>
          <pre class="json-pre">{{ JSON.stringify(activeLog.request_payload, null, 2) || 'null' }}</pre>
        </div>
        <div>
          <div style="font-weight: 600; margin-bottom: 4px">业务快照 (business_snapshot_json)</div>
          <pre class="json-pre">{{ JSON.stringify(activeLog.business_snapshot_json, null, 2) || 'null' }}</pre>
        </div>
      </div>
      <template #footer>
        <button type="button" class="tomori-btn" @click="detailDialogVisible = false">关闭</button>
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
  font-size: 12px;
}

.data-table th,
.data-table td {
  padding: 9px 10px;
  border-bottom: 1px solid #ebeef5;
  text-align: left;
  white-space: nowrap;
}

.data-table th {
  background: #fafafa;
  font-weight: 600;
  color: var(--tomori-text-secondary);
}

.json-pre {
  margin: 0;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 10px;
  font-size: 12px;
  max-height: 240px;
  overflow: auto;
}
</style>
