import { defineStore } from 'pinia'
import { ref } from 'vue'
import { searchApi } from '@/api/search'
import { useRepoStore } from './repoStore'
import type { QueryHistory, SearchMode, SearchResultItem } from '@/types'

export const useSearchStore = defineStore('search', () => {
  // ── State ──────────────────────────────────────────────────────────────────
  const query        = ref('')
  const mode         = ref<SearchMode>('semantic')
  // 语言过滤：null=全部；选中 'java'/'javascript'/'xml' 时仅返回该 language 的 chunk
  const languageFilter = ref<string | null>(null)
  const results      = ref<SearchResultItem[]>([])
  const history      = ref<QueryHistory[]>([])
  const loading      = ref(false)
  const latencyMs    = ref(0)
  const error        = ref<string | null>(null)
  const hasSearched  = ref(false)

  // ── Actions ────────────────────────────────────────────────────────────────
  async function doSearch(q?: string) {
    const repoStore = useRepoStore()
    if (!repoStore.currentRepo) return
    if (q !== undefined) query.value = q
    if (!query.value.trim()) return

    loading.value  = true
    error.value    = null
    hasSearched.value = true
    try {
      const res = await searchApi.search({
        query:   query.value,
        repo_id: repoStore.currentRepo.id,
        mode:    'semantic',
        top_k:   20,
        language_filter: languageFilter.value ?? undefined,
      })
      results.value  = res.results
      latencyMs.value = res.latency_ms
      // 刷新历史
      await fetchHistory()
    } catch (e: any) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }

  async function fetchHistory() {
    const repoStore = useRepoStore()
    if (!repoStore.currentRepo) return
    try {
      history.value = await searchApi.history(repoStore.currentRepo.id)
    } catch { /* 静默 */ }
  }

  async function deleteHistory(id: number) {
    await searchApi.deleteHistory(id)
    history.value = history.value.filter(h => h.id !== id)
  }

  function clearResults() {
    results.value  = []
    hasSearched.value = false
    error.value    = null
  }

  return {
    query, mode, languageFilter, results, history, loading, latencyMs, error, hasSearched,
    doSearch, fetchHistory, deleteHistory, clearResults,
  }
})
