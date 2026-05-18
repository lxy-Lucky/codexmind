import { ref, onMounted, onBeforeUnmount } from 'vue'
import { statusApi } from '@/api/status'
import type { SystemStatus } from '@/types'

export function useSystemStatus(intervalMs = 10_000) {
  const status  = ref<SystemStatus | null>(null)
  const online  = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null

  async function fetch() {
    try {
      status.value = await statusApi.get()
      online.value = status.value.qdrant.online
    } catch {
      online.value = false
    }
  }

  onMounted(() => {
    fetch()
    timer = setInterval(fetch, intervalMs)
  })

  onBeforeUnmount(() => {
    if (timer) clearInterval(timer)
  })

  return { status, online }
}
