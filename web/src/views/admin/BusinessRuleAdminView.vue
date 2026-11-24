<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  createBusinessRuleVersionApi,
  listBusinessRulesApi,
  patchBusinessRuleMetaApi,
} from '@/api/admin'
import { isApiError } from '@/shared/api'
import { ALL_RULE_CODES, RULE_CODE_META } from '@/shared/enums'
import { formatDateTime } from '@/shared/format'
import type { BusinessRuleOut, RuleCode } from '@/shared/types'
import { useErrorBannerStore } from '@/stores/errorBanner'

interface RuleGroup {
  ruleCode: RuleCode
  primary: BusinessRuleOut | null
  versions: BusinessRuleOut[]
}

const bannerStore = useErrorBannerStore()

const rules = ref<BusinessRuleOut[]>([])
const filterRuleCode = ref<RuleCode | ''>('')
const loading = ref(false)
const saving = ref(false)
const togglingId = ref<number | null>(null)
const expandedCodes = ref<Record<string, boolean>>({})

const dialogVisible = ref(false)
const dialogError = ref('')
const editorMode = ref<'new_version' | 'meta'>('new_version')
const editingRuleId = ref<number | null>(null)
const formRuleCode = ref<RuleCode>('grading_rules')
const formJsonText = ref('')
const formDescription = ref('')
const formIsActive = ref(true)

const groupedRules = computed<RuleGroup[]>(() => {
  const codes = filterRuleCode.value ? [filterRuleCode.value] : ALL_RULE_CODES
  return codes.map((code) => {
    const versions = rules.value
      .filter((r) => r.rule_code === code)
      .sort((a, b) => b.version - a.version)
    const active = versions.find((v) => v.is_active) ?? versions[0] ?? null
    return {
      ruleCode: code,
      primary: active,
      versions,
    }
  })
})

async function load(): Promise<void> {
  loading.value = true
  try {
    const res = await listBusinessRulesApi({ page_size: 100 })
    rules.value = Array.isArray(res) ? res : res.items
  } catch {
    // 全局错误横幅已处理
  } finally {
    loading.value = false
  }
}

function toggleHistory(code: RuleCode): void {
  expandedCodes.value[code] = !expandedCodes.value[code]
}

function getDefaultTemplate(code: RuleCode): Record<string, unknown> {
  switch (code) {
    case 'sla_policy':
      return {
        statutory_deadline_hours: 48,
        response_minutes: null,
        emergency_notice_hours: 24,
        applies_to_priorities: ['P1', 'P2', 'P3', 'P4'],
      }
    case 'dispatch_scoring_weights':
      return {
        weights: { workload: 0.4, skill: 0.35, on_time_rate: 0.25 },
        max_recommendations: 10,
      }
    case 'rework_policy':
      return { max_rework_rounds: 2, on_exceed_action: 'FORCE_ADMIN_REVIEW' }
    case 'ai_candidate_ttl_hours':
      return { ttl_hours: 72 }
    case 'grading_rules':
    default:
      return {
        display_names: { P1: '特急', P2: '加急', P3: '普通', P4: '观察' },
        rules: [
          {
            rule_id: 'R1_SAFETY_OR_EXPRESS_CRITICAL',
            priority: 'P1',
            any: [
              { fault_type: 'ELECTRIC_HAZARD' },
              { safety_flag: true },
              { road_level: 'EXPRESS', asset_importance_in: ['CRITICAL', 'HIGH'] },
            ],
          },
        ],
        default_priority: 'P4',
        default_rule_id: 'R4_FALLBACK_OBSERVATION',
      }
  }
}

function openNewVersion(
  ruleCode: RuleCode,
  baseValue?: Record<string, unknown>,
  baseDesc?: string | null,
): void {
  editorMode.value = 'new_version'
  editingRuleId.value = null
  dialogError.value = ''
  formRuleCode.value = ruleCode
  formJsonText.value = JSON.stringify(baseValue ?? getDefaultTemplate(ruleCode), null, 2)
  formDescription.value = baseDesc ?? ''
  dialogVisible.value = true
}

function openPatchMeta(rule: BusinessRuleOut): void {
  editorMode.value = 'meta'
  editingRuleId.value = rule.id
  dialogError.value = ''
  formRuleCode.value = rule.rule_code
  formDescription.value = rule.description ?? ''
  formIsActive.value = rule.is_active
  dialogVisible.value = true
}

async function handleToggleActive(rule: BusinessRuleOut): Promise<void> {
  togglingId.value = rule.id
  try {
    await patchBusinessRuleMetaApi(rule.id, { is_active: !rule.is_active })
    ElMessage.success(
      `${rule.rule_code} v${rule.version} 已${rule.is_active ? '停用' : '启用'}`,
    )
    await load()
  } catch (err: unknown) {
    if (isApiError(err)) bannerStore.showFromApiError(err)
  } finally {
    togglingId.value = null
  }
}

async function handleSave(): Promise<void> {
  dialogError.value = ''
  saving.value = true
  try {
    if (editorMode.value === 'new_version') {
      let parsed: Record<string, unknown>
      try {
        const val: unknown = JSON.parse(formJsonText.value)
        if (typeof val !== 'object' || val === null || Array.isArray(val)) {
          dialogError.value = 'rule_value 必须是合法的 JSON 对象'
          return
        }
        parsed = val as Record<string, unknown>
      } catch {
        dialogError.value = 'JSON 格式错误，请检查括号与逗号'
        return
      }
      await createBusinessRuleVersionApi({
        rule_code: formRuleCode.value,
        rule_value: parsed,
        description: formDescription.value.trim() || null,
      })
      ElMessage.success(`已发布 ${formRuleCode.value} 新版本（POST /business-rules）`)
    } else if (editingRuleId.value !== null) {
      await patchBusinessRuleMetaApi(editingRuleId.value, {
        is_active: formIsActive.value,
        description: formDescription.value.trim() || null,
      })
      ElMessage.success('规则状态与描述已更新（PATCH /business-rules/{id}）')
    }
    dialogVisible.value = false
    await load()
  } catch (err: unknown) {
    if (isApiError(err)) {
      dialogError.value = err.error.message || err.error.code
      bannerStore.showFromApiError(err)
    }
  } finally {
    saving.value = false
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
      <h2 class="tomori-page-title">业务规则版本配置（5 类 rule_code · 不可原地修改）</h2>
      <div style="display: flex; gap: 8px; flex-wrap: wrap">
        <el-select v-model="filterRuleCode" :teleported="false" clearable placeholder="全部规则编码" style="width: 240px">
          <el-option
            v-for="code in ALL_RULE_CODES"
            :key="code"
            :label="RULE_CODE_META[code].label"
            :value="code"
          />
        </el-select>
        <button type="button" class="tomori-btn" :disabled="loading" @click="load">刷新</button>
        <button
          type="button"
          class="tomori-btn tomori-btn--primary"
          @click="openNewVersion(filterRuleCode || 'grading_rules')"
        >
          发布新规则版本 (POST)
        </button>
      </div>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="规则载荷不可就地覆盖（仅通过 POST /business-rules 提交新版本，PATCH 仅调整 is_active 与 description），保证历史工单分级与派单快照可审计追溯。"
      style="margin-bottom: 12px"
    />

    <div v-if="loading" class="tomori-card">加载中...</div>

    <div v-else class="rule-list">
      <div
        v-for="group in groupedRules"
        :key="group.ruleCode"
        class="tomori-card"
        :data-testid="`rule-row-${group.ruleCode}`"
      >
        <div class="rule-header">
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
            <strong>{{ RULE_CODE_META[group.ruleCode].label }}</strong>
            <template v-if="group.primary">
              <el-tag size="small">v{{ group.primary.version }}</el-tag>
              <el-tag size="small" :type="group.primary.is_active ? 'success' : 'info'">
                {{ group.primary.is_active ? '当前生效' : '未启用' }}
              </el-tag>
            </template>
            <el-tag v-else size="small" type="warning">尚未配置</el-tag>
          </div>

          <div style="display: flex; gap: 8px; flex-wrap: wrap">
            <button
              type="button"
              class="tomori-btn tomori-btn--primary"
              @click="openNewVersion(group.ruleCode, group.primary?.rule_value, group.primary?.description)"
            >
              {{ group.primary ? '发布新版本 (POST)' : '创建首版 (POST)' }}
            </button>
            <template v-if="group.primary">
              <button type="button" class="tomori-btn" @click="openPatchMeta(group.primary)">
                编辑元信息 (PATCH)
              </button>
              <button
                type="button"
                class="tomori-btn"
                :class="group.primary.is_active ? 'tomori-btn--danger' : 'tomori-btn--success'"
                :disabled="togglingId === group.primary.id"
                @click="handleToggleActive(group.primary)"
              >
                {{ group.primary.is_active ? '停用' : '启用' }}
              </button>
            </template>
          </div>
        </div>

        <p class="rule-summary">
          {{ group.primary?.description || RULE_CODE_META[group.ruleCode].summary }}
        </p>

        <template v-if="group.primary">
          <div class="rule-time">
            生效时间：{{ formatDateTime(group.primary.effective_from) }} · 创建时间：{{ formatDateTime(group.primary.created_at) }}
          </div>
          <pre class="rule-json">{{ JSON.stringify(group.primary.rule_value, null, 2) }}</pre>

          <div v-if="group.versions.length > 1" style="margin-top: 10px">
            <button
              type="button"
              class="tomori-btn"
              style="padding: 4px 10px; font-size: 12px"
              @click="toggleHistory(group.ruleCode)"
            >
              {{ expandedCodes[group.ruleCode] ? '收起历史版本' : `查看全部版本 (${group.versions.length})` }}
            </button>

            <div v-if="expandedCodes[group.ruleCode]" class="history-list">
              <div
                v-for="ver in group.versions"
                :key="ver.id"
                :data-testid="`rule-version-${ver.id}`"
                class="history-item"
              >
                <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap">
                  <div>
                    <strong>v{{ ver.version }}</strong>
                    <el-tag size="small" :type="ver.is_active ? 'success' : 'info'" style="margin-left: 6px">
                      {{ ver.is_active ? '生效中' : '历史/停用' }}
                    </el-tag>
                    <span style="margin-left: 8px; font-size: 12px; color: var(--tomori-text-muted)">
                      {{ formatDateTime(ver.created_at) }} · {{ ver.description || '无说明' }}
                    </span>
                  </div>
                  <div style="display: flex; gap: 6px">
                    <button
                      type="button"
                      class="tomori-btn"
                      style="padding: 4px 8px; font-size: 12px"
                      @click="openNewVersion(ver.rule_code, ver.rule_value, ver.description)"
                    >
                      基于此版新建
                    </button>
                    <button
                      type="button"
                      class="tomori-btn"
                      style="padding: 4px 8px; font-size: 12px"
                      @click="openPatchMeta(ver)"
                    >
                      编辑元信息
                    </button>
                    <button
                      type="button"
                      class="tomori-btn"
                      style="padding: 4px 8px; font-size: 12px"
                      :disabled="togglingId === ver.id"
                      @click="handleToggleActive(ver)"
                    >
                      {{ ver.is_active ? '停用' : '启用' }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editorMode === 'new_version' ? `发布新版本：${formRuleCode}` : `更新规则元信息：${formRuleCode}`"
      width="640px"
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
      <div style="display: flex; flex-direction: column; gap: 12px">
        <div v-if="editorMode === 'new_version'">
          <div class="field-label">规则编码 (rule_code)</div>
          <el-select v-model="formRuleCode" :teleported="false" style="width: 100%">
            <el-option v-for="code in ALL_RULE_CODES" :key="code" :label="`${code} · ${RULE_CODE_META[code].label}`" :value="code" />
          </el-select>
        </div>

        <label v-if="editorMode === 'new_version'">
          <div class="field-label">规则载荷 JSON (rule_value)</div>
          <textarea
            v-model="formJsonText"
            class="tomori-textarea"
            style="min-height: 240px; font-family: monospace; font-size: 12px"
          />
        </label>

        <label v-if="editorMode === 'meta'" class="tomori-checkbox-label">
          <input v-model="formIsActive" type="checkbox" class="tomori-checkbox" />
          <span>启用该规则版本（is_active）</span>
        </label>

        <label>
          <div class="field-label">变更说明</div>
          <input v-model="formDescription" class="tomori-input" placeholder="简述规则变更原因" />
        </label>
      </div>
      <template #footer>
        <button type="button" class="tomori-btn" @click="dialogVisible = false">取消</button>
        <button type="button" class="tomori-btn tomori-btn--primary" :disabled="saving" @click="handleSave">
          {{ editorMode === 'new_version' ? '发布新版本 (POST)' : '保存元信息 (PATCH)' }}
        </button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.rule-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.rule-header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.rule-summary {
  margin: 8px 0 4px;
  font-size: 13px;
  color: var(--tomori-text-secondary);
}

.rule-time {
  font-size: 12px;
  color: var(--tomori-text-muted);
  margin-bottom: 8px;
}

.rule-json {
  margin: 0;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 10px;
  font-size: 12px;
  overflow-x: auto;
}

.history-list {
  margin-top: 8px;
  border-top: 1px dashed #e5e7eb;
  padding-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.history-item {
  padding: 8px 10px;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--tomori-text-secondary);
}
</style>
