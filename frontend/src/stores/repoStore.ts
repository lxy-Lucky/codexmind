import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { repoApi } from '@/api/repo'
import type { FileNode, IndexProgress, IndexStatus, Repo } from '@/types'

export const useRepoStore = defineStore('repo', () => {
  // ── State ──────────────────────────────────────────────────────────────────
  const repos        = ref<Repo[]>([])
  const currentRepo  = ref<Repo | null>(null)
  const fileTree     = ref<FileNode[]>([])
  const loading      = ref(false)
  const indexing     = ref(false)
  const indexProgress = ref<IndexProgress | null>(null)

  // ── Getters ────────────────────────────────────────────────────────────────
  const hasRepo     = computed(() => !!currentRepo.value)
  const isIndexDone = computed(() => currentRepo.value?.indexed === 2)

  // ── Actions ────────────────────────────────────────────────────────────────
  async function fetchRepos() {
    loading.value = true
    try {
      const res = await repoApi.list()
      repos.value = res.items
      // 若当前选中的还在列表里则同步最新数据
      if (currentRepo.value) {
        const updated = res.items.find(r => r.id === currentRepo.value!.id)
        if (updated) currentRepo.value = updated
      }
    } finally {
      loading.value = false
    }
  }

  async function registerRepo(name: string, root_path: string) {
    loading.value = true
    try {
      const repo = await repoApi.register(name, root_path)
      await fetchRepos()
      currentRepo.value = repo
      return repo
    } finally {
      loading.value = false
    }
  }

  async function selectRepo(repo: Repo) {
    currentRepo.value = repo
    fileTree.value = []
    await fetchFileTree()
  }

  async function fetchFileTree() {
    if (!currentRepo.value) return
    fileTree.value = await repoApi.fileTree(currentRepo.value.id)
  }

  async function triggerIndex() {
    if (!currentRepo.value) return
    indexing.value = true
    indexProgress.value = await repoApi.triggerIndex(currentRepo.value.id)

    // 轮询索引进度
    const poll = setInterval(async () => {
      if (!currentRepo.value) { clearInterval(poll); return }
      const prog = await repoApi.indexStatus(currentRepo.value.id)
      indexProgress.value = prog
      // 同步 repo 状态
      const idx = repos.value.findIndex(r => r.id === currentRepo.value!.id)
      if (idx !== -1) repos.value[idx].indexed = prog.status as IndexStatus

      if (prog.status === 2 || prog.status === 3) {
        clearInterval(poll)
        indexing.value = false
        // 刷新 repo 详情
        currentRepo.value = await repoApi.get(currentRepo.value.id)
      }
    }, 2000)
  }

  async function removeRepo(id: string) {
    await repoApi.remove(id)
    repos.value = repos.value.filter(r => r.id !== id)
    if (currentRepo.value?.id === id) {
      currentRepo.value = null
      fileTree.value = []
    }
  }

  return {
    repos, currentRepo, fileTree, loading, indexing, indexProgress,
    hasRepo, isIndexDone,
    fetchRepos, registerRepo, selectRepo, fetchFileTree,
    triggerIndex, removeRepo,
  }
})
