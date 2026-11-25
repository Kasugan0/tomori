<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useErrorBannerStore } from '@/stores/errorBanner'

const router = useRouter()
const bannerStore = useErrorBannerStore()

function navigateConflict(ticketId: number): void {
  bannerStore.clear()
  void router.push(`/tickets/${ticketId}`)
}
</script>

<template>
  <div v-if="bannerStore.banner" style="margin-bottom: 16px">
    <el-alert
      data-testid="error-banner"
      :data-http-status="String(bannerStore.banner.httpStatus)"
      :data-code="bannerStore.banner.code"
      :data-reason="bannerStore.banner.reason || undefined"
      :type="bannerStore.banner.variant"
      :closable="false"
      show-icon
    >
      <template #title>
        <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%">
          <div>
            <strong data-testid="error-banner-code" style="margin-right: 8px">{{ bannerStore.banner.code }}</strong>
            <span data-testid="error-banner-message">{{ bannerStore.banner.message }}</span>
          </div>
          <button
            type="button"
            class="tomori-btn"
            style="padding: 4px 10px; font-size: 12px"
            data-testid="error-banner-close"
            @click="bannerStore.clear()"
          >
            关闭
          </button>
        </div>
      </template>

      <div v-if="bannerStore.banner.conflict" style="margin-top: 8px">
        <a
          data-testid="conflict-existing-workorder-link"
          :href="`/tickets/${bannerStore.banner.conflict.existingTicketId}`"
          style="color: var(--el-color-primary); font-weight: 600; text-decoration: underline"
          @click.prevent="navigateConflict(bannerStore.banner.conflict.existingTicketId)"
        >
          查看既有工单 #{{ bannerStore.banner.conflict.existingTicketNo }}
          <span v-if="bannerStore.banner.conflict.existingStatus">
            （{{ bannerStore.banner.conflict.existingStatus }}）
          </span>
        </a>
      </div>
    </el-alert>
  </div>
</template>
