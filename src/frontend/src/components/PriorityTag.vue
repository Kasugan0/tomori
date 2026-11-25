<script setup lang="ts">
import { computed } from 'vue'
import { PRIORITY_META } from '@/shared/enums'
import type { PriorityCode } from '@/shared/types'

const props = defineProps<{
  priority: PriorityCode | null | undefined
  testid?: string
}>()

const meta = computed(() => (props.priority ? PRIORITY_META[props.priority] : null))
</script>

<template>
  <el-tag
    v-if="meta"
    :data-testid="testid"
    :data-priority="meta.code"
    :type="meta.tagType"
    :effect="meta.effect"
    size="small"
    disable-transitions
  >
    {{ meta.code }} {{ meta.label }}
  </el-tag>
  <el-tag
    v-else
    :data-testid="testid"
    data-priority=""
    type="info"
    effect="plain"
    size="small"
    disable-transitions
  >
    未分级
  </el-tag>
</template>
