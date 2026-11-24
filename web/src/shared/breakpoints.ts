import { onMounted, onUnmounted, ref } from 'vue'

export const BP_MOBILE_MAX = 767
export const BP_DESKTOP_MIN = 992

export function useIsDesktop() {
  const query = `(min-width: ${BP_DESKTOP_MIN}px)`
  const isDesktop = ref<boolean>(
    typeof window !== 'undefined' ? window.matchMedia(query).matches : true,
  )

  let mql: MediaQueryList | null = null
  const update = (event: MediaQueryListEvent) => {
    isDesktop.value = event.matches
  }

  onMounted(() => {
    if (typeof window === 'undefined') return
    mql = window.matchMedia(query)
    isDesktop.value = mql.matches
    mql.addEventListener('change', update)
  })

  onUnmounted(() => {
    mql?.removeEventListener('change', update)
  })

  return { isDesktop }
}
