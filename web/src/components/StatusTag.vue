<script setup lang="ts">
import { computed } from 'vue'
import {
  DISPATCH_STATUS_META,
  REWORK_STATUS_META,
  TICKET_STATUS_META,
  type StatusDisplayMeta,
} from '@/shared/enums'
import type { DispatchStatus, ReworkStatus, TicketStatus } from '@/shared/types'

const props = withDefaults(
  defineProps<{
    status: string
    kind?: 'ticket' | 'dispatch' | 'rework'
    testid?: string
  }>(),
  {
    kind: 'ticket',
    testid: undefined,
  },
)

const meta = computed<StatusDisplayMeta>(() => {
  if (props.kind === 'dispatch') {
    return (
      DISPATCH_STATUS_META[props.status as DispatchStatus] ?? {
        label: props.status,
        tagType: 'info',
      }
    )
  }
  if (props.kind === 'rework') {
    return (
      REWORK_STATUS_META[props.status as ReworkStatus] ?? {
        label: props.status,
        tagType: 'info',
      }
    )
  }
  return (
    TICKET_STATUS_META[props.status as TicketStatus] ?? {
      label: props.status,
      tagType: 'info',
    }
  )
})
</script>

<template>
  <el-tag
    :data-testid="testid"
    :data-status="status"
    :type="meta.tagType"
    size="small"
    disable-transitions
  >
    {{ meta.label }}
  </el-tag>
</template>
