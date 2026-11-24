<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { fetchAuthenticatedMediaBlobUrl, fetchAuthenticatedUrlBlobUrl } from '@/shared/api'

const props = withDefaults(
  defineProps<{
    evidenceId?: number
    variant?: 'raw' | 'annotated'
    src?: string | null
    alt?: string
    aspectRatio?: string
  }>(),
  {
    evidenceId: undefined,
    variant: 'raw',
    src: null,
    alt: '巡查证据图',
    aspectRatio: undefined,
  },
)

const objectUrl = ref<string | null>(null)
const loadFailed = ref(false)
const loading = ref(false)

function revoke(): void {
  if (objectUrl.value && objectUrl.value.startsWith('blob:')) {
    URL.revokeObjectURL(objectUrl.value)
  }
  objectUrl.value = null
}

async function loadImage(): Promise<void> {
  revoke()
  loadFailed.value = false

  if (!props.evidenceId && !props.src) return

  if (props.src && (props.src.startsWith('blob:') || props.src.startsWith('data:'))) {
    objectUrl.value = props.src
    return
  }

  loading.value = true
  try {
    if (props.evidenceId !== undefined) {
      objectUrl.value = await fetchAuthenticatedMediaBlobUrl(props.evidenceId, props.variant)
    } else if (props.src) {
      objectUrl.value = await fetchAuthenticatedUrlBlobUrl(props.src)
    }
  } catch {
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.evidenceId, props.variant, props.src],
  () => {
    void loadImage()
  },
  { immediate: true },
)

onUnmounted(() => {
  revoke()
})
</script>

<template>
  <div
    class="auth-image-box"
    :style="aspectRatio ? { aspectRatio } : undefined"
  >
    <div v-if="loading" class="auth-image-placeholder">加载中...</div>
    <img
      v-else-if="objectUrl"
      :src="objectUrl"
      :alt="alt"
      class="auth-image-el"
    />
    <div v-else class="auth-image-placeholder">
      {{ loadFailed ? '暂无图像或无权访问' : '无图像' }}
    </div>
    <slot />
  </div>
</template>

<style scoped>
.auth-image-box {
  position: relative;
  width: 100%;
  background: #111827;
  border-radius: 4px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

.auth-image-el {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.auth-image-placeholder {
  padding: 24px 12px;
  color: #9ca3af;
  font-size: 13px;
}
</style>
