<script setup lang="ts">
import { ref } from 'vue'
import { useRepoStore } from '@/stores/repoStore'
import type { Repo } from '@/types'

const repo    = useRepoStore()
const showAdd = ref(false)
const newName = ref('')
const newPath = ref('')
const adding  = ref(false)
const err     = ref('')

const STATUS_LABEL: Record<number, string> = {
  0: '未索引', 1: '索引中...', 2: '已就绪', 3: '失败',
}

async function addRepo() {
  if (!newName.value.trim() || !newPath.value.trim()) return
  adding.value = true; err.value = ''
  try {
    await repo.registerRepo(newName.value.trim(), newPath.value.trim())
    showAdd.value = false
    newName.value = ''; newPath.value = ''
  } catch (e: any) {
    err.value = e.message
  } finally {
    adding.value = false
  }
}

function select(r: Repo) { repo.selectRepo(r) }
</script>

<template>
  <div class="flex flex-col gap-1 px-2 pb-2">
    <!-- Current repo -->
    <div
      v-if="repo.currentRepo"
      class="repo-card active"
    >
      <div class="repo-icon">{{ repo.currentRepo.name[0].toUpperCase() }}</div>
      <div class="flex-1 min-w-0">
        <div class="font-mono text-[12px] font-medium text-text-primary truncate">
          {{ repo.currentRepo.name }}
        </div>
        <div class="font-mono text-[10px] text-text-muted flex gap-2">
          <span>{{ repo.currentRepo.file_count }} 文件</span>
          <span>{{ STATUS_LABEL[repo.currentRepo.indexed] }}</span>
        </div>
      </div>
      <!-- Index button -->
      <button
        v-if="repo.currentRepo.indexed !== 2"
        class="px-2 py-0.5 rounded font-mono text-[10px] border
               border-cyan text-cyan hover:bg-cyan-dim transition-colors"
        :class="{ 'opacity-50 cursor-wait': repo.indexing }"
        :disabled="repo.indexing"
        @click="repo.triggerIndex()"
      >
        {{ repo.indexing ? '索引中' : '建索引' }}
      </button>
    </div>

    <!-- Repo list -->
    <div
      v-for="r in repo.repos.filter(r => r.id !== repo.currentRepo?.id)"
      :key="r.id"
      class="repo-card cursor-pointer hover:bg-bg-hover"
      @click="select(r)"
    >
      <div class="repo-icon" style="background: linear-gradient(135deg,#2d5a27,#4a8c3f)">
        {{ r.name[0].toUpperCase() }}
      </div>
      <div class="flex-1 min-w-0">
        <div class="font-mono text-[12px] text-text-secondary truncate">{{ r.name }}</div>
        <div class="font-mono text-[10px] text-text-muted">{{ STATUS_LABEL[r.indexed] }}</div>
      </div>
    </div>

    <!-- Add repo button -->
    <button
      class="mt-1 flex items-center gap-2 px-3 py-2 rounded-md font-mono text-[11px]
             text-text-muted border border-dashed border-border-dim hover:border-cyan
             hover:text-cyan transition-colors"
      @click="showAdd = !showAdd"
    >
      <span>＋</span> 添加仓库
    </button>

    <!-- Add repo form -->
    <div v-if="showAdd" class="mt-1 p-3 bg-bg-surface border border-border-dim rounded-md flex flex-col gap-2">
      <input
        v-model="newName"
        placeholder="仓库名称"
        class="input-sm"
      />
      <input
        v-model="newPath"
        placeholder="/home/user/projects/my-project"
        class="input-sm font-mono text-[11px]"
      />
      <div v-if="err" class="text-red-accent text-[11px] font-mono">{{ err }}</div>
      <div class="flex gap-2">
        <button
          class="flex-1 py-1.5 rounded font-mono text-[11px] font-semibold
                 bg-cyan text-bg-deep hover:opacity-90 transition-opacity"
          :disabled="adding"
          @click="addRepo"
        >
          {{ adding ? '添加中...' : '确认添加' }}
        </button>
        <button
          class="px-3 py-1.5 rounded font-mono text-[11px] border border-border-dim
                 text-text-muted hover:text-text-primary transition-colors"
          @click="showAdd = false"
        >
          取消
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.repo-card {
  @apply flex items-center gap-2 px-2 py-2 rounded-md border border-transparent transition-all;
}
.repo-card.active {
  @apply bg-cyan-dim border-cyan/20;
}
.repo-icon {
  @apply w-6 h-6 rounded flex items-center justify-center font-mono text-[10px] font-bold text-white flex-shrink-0;
  background: linear-gradient(135deg, #00d4ff, #a55eea);
}
.input-sm {
  @apply w-full bg-bg-elevated border border-border-dim rounded px-2.5 py-1.5
         font-mono text-[12px] text-text-primary placeholder-text-muted outline-none
         focus:border-cyan transition-colors;
}
</style>
