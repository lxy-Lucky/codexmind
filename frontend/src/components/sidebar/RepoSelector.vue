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
const confirmDeleteId = ref<string | null>(null)

const STATUS_LABEL: Record<number, string> = {
  0: '未索引', 1: '索引中...', 2: '已就绪', 3: '失败',
}
const STATUS_COLOR: Record<number, string> = {
  0: 'text-text-muted', 1: 'text-amber animate-pulse-dot',
  2: 'text-green-accent', 3: 'text-red-accent',
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

async function confirmDelete(id: string) {
  await repo.removeRepo(id)
  confirmDeleteId.value = null
}

function select(r: Repo) { repo.selectRepo(r) }
</script>

<template>
  <div class="flex flex-col gap-0.5 px-2 pb-2">

    <!-- All repos (current highlighted) -->
    <div
      v-for="r in repo.repos"
      :key="r.id"
      class="repo-card group"
      :class="r.id === repo.currentRepo?.id ? 'active' : 'cursor-pointer hover:bg-bg-hover'"
      @click="r.id !== repo.currentRepo?.id && select(r)"
    >
      <div
        class="repo-icon flex-shrink-0"
        :style="r.id === repo.currentRepo?.id
          ? 'background: linear-gradient(135deg,#00d4ff,#a55eea)'
          : 'background: linear-gradient(135deg,#1a3a2a,#2d5a3f)'"
      >
        {{ r.name[0].toUpperCase() }}
      </div>

      <div class="flex-1 min-w-0">
        <div
          class="font-mono text-[12px] font-medium truncate"
          :class="r.id === repo.currentRepo?.id ? 'text-text-primary' : 'text-text-secondary'"
        >
          {{ r.name }}
        </div>
        <div class="font-mono text-[10px] flex gap-2">
          <span class="text-text-muted">{{ r.file_count }} 文件</span>
          <span :class="STATUS_COLOR[r.indexed]">{{ STATUS_LABEL[r.indexed] }}</span>
        </div>
      </div>

      <!-- Action buttons (visible on hover) -->
      <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
        <!-- Index button -->
        <button
          v-if="r.indexed !== 2 && r.id === repo.currentRepo?.id"
          class="px-1.5 py-0.5 rounded font-mono text-[10px] border border-cyan/40
                 text-cyan hover:bg-cyan-dim transition-colors"
          :class="{ 'cursor-wait opacity-50': repo.indexing }"
          :disabled="repo.indexing"
          @click.stop="repo.triggerIndex()"
        >
          {{ repo.indexing ? '...' : '索引' }}
        </button>

        <!-- Delete button -->
        <button
          v-if="confirmDeleteId !== r.id"
          class="w-5 h-5 flex items-center justify-center rounded
                 font-mono text-[10px] text-text-muted
                 hover:text-red-accent hover:bg-red-accent/10 transition-colors"
          title="删除仓库"
          @click.stop="confirmDeleteId = r.id"
        >
          ✕
        </button>
        <!-- Confirm delete -->
        <template v-else>
          <button
            class="px-1.5 py-0.5 rounded font-mono text-[10px] bg-red-accent/20
                   border border-red-accent/50 text-red-accent hover:bg-red-accent/30 transition-colors"
            @click.stop="confirmDelete(r.id)"
          >
            确认
          </button>
          <button
            class="px-1.5 py-0.5 rounded font-mono text-[10px] border border-border-dim
                   text-text-muted hover:text-text-primary transition-colors"
            @click.stop="confirmDeleteId = null"
          >
            取消
          </button>
        </template>
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
    <Transition name="slide-down">
      <div v-if="showAdd" class="mt-1 p-3 bg-bg-surface border border-border-dim rounded-md flex flex-col gap-2">
        <input v-model="newName" placeholder="仓库名称" class="input-sm" />
        <input
          v-model="newPath"
          placeholder="/home/user/projects/my-project"
          class="input-sm font-mono text-[11px]"
          @keydown.enter="addRepo"
        />
        <div v-if="err" class="text-red-accent text-[11px] font-mono leading-snug">{{ err }}</div>
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
    </Transition>
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
  @apply w-6 h-6 rounded flex items-center justify-center font-mono text-[10px] font-bold text-white;
}
.input-sm {
  @apply w-full bg-bg-elevated border border-border-dim rounded px-2.5 py-1.5
         font-mono text-[12px] text-text-primary placeholder-text-muted outline-none
         focus:border-cyan transition-colors;
}

/* slide-down transition */
.slide-down-enter-active { transition: all 0.18s ease-out; }
.slide-down-leave-active { transition: all 0.12s ease-in; }
.slide-down-enter-from, .slide-down-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}
</style>
