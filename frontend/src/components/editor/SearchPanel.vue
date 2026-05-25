<script setup lang="ts">
import { useSearchStore } from '@/stores/searchStore'
import { useRepoStore }   from '@/stores/repoStore'

const emit   = defineEmits<{ (e: 'searched'): void }>()
const search = useSearchStore()
const repo   = useRepoStore()

// 语言过滤选项：UI label → indexer 写入 payload.language 时实际使用的值
// （由 EXT_TO_LANG 决定：.java→java，.js/.jsx→javascript，.xml→xml）
const LANG_FILTERS: { label: string; value: string }[] = [
  { label: 'JS',   value: 'javascript' },
  { label: 'Java', value: 'java'       },
  { label: 'XML',  value: 'xml'        },
]

async function submit() {
  if (!repo.isIndexDone || !search.query.trim() || search.loading) return
  await search.doSearch()
  emit('searched')
}

function toggleLang(value: string) {
  // 单选：再点一次即取消
  search.languageFilter = search.languageFilter === value ? null : value
}
</script>

<template>
  <div class="search-panel">
    <!-- 模式标识：只剩语义检索一种 -->
    <div class="flex items-center gap-1.5 mb-3 font-mono text-[11px] font-medium text-cyan">
      <span class="w-1.5 h-1.5 rounded-full bg-cyan" />
      语义检索
    </div>

    <!-- Input + button row -->
    <div class="flex gap-2.5 items-start">
      <div class="flex-1">
        <textarea
          v-model="search.query"
          placeholder="输入要检索的内容..."
          rows="1"
          class="w-full bg-bg-surface border border-border-dim rounded-lg px-4 py-3
                 font-mono text-[13px] text-text-primary placeholder-text-muted outline-none
                 resize-none leading-relaxed transition-all
                 focus:border-cyan focus:shadow-[0_0_0_3px_rgba(0,212,255,0.12)]"
          @keydown.enter.exact.prevent="submit"
        />
        <!-- 语言过滤：单选；选中后只返回该语言的文件 -->
        <div class="flex items-center gap-2 mt-2">
          <span class="font-mono text-[10px] text-text-muted">仅检索：</span>
          <button
            v-for="opt in LANG_FILTERS"
            :key="opt.value"
            class="lang-chip font-mono text-[10px] px-2.5 py-1 rounded-full border transition-all"
            :class="search.languageFilter === opt.value
              ? 'border-cyan text-cyan bg-cyan-dim'
              : 'border-border-dim bg-bg-surface text-text-muted hover:border-cyan hover:text-cyan'"
            @click="toggleLang(opt.value)"
          >
            {{ opt.label }}
          </button>
        </div>
      </div>

      <!-- Submit button -->
      <button
        class="px-5 py-3 rounded-lg font-mono text-[12px] font-semibold tracking-wide
               flex items-center gap-1.5 transition-all whitespace-nowrap flex-shrink-0"
        :class="repo.isIndexDone && search.query.trim() && !search.loading
          ? 'bg-gradient-to-br from-cyan to-[#00a5cc] text-bg-deep hover:-translate-y-px hover:shadow-[0_4px_16px_rgba(0,212,255,0.3)]'
          : 'bg-bg-surface text-text-muted border border-border-dim cursor-not-allowed opacity-60'"
        :disabled="!repo.isIndexDone || !search.query.trim() || search.loading"
        @click="submit"
      >
        <span v-if="search.loading" class="animate-spin-slow inline-block">⟳</span>
        <span v-else>⌕</span>
        {{ search.loading ? '检索中...' : '检索' }}
      </button>
    </div>

    <!-- Warnings -->
    <div v-if="repo.currentRepo && !repo.isIndexDone"
         class="mt-2 flex items-center gap-2 font-mono text-[11px] text-amber">
      <span>⚠</span> 仓库尚未索引，请在左侧点击「建索引」
    </div>
    <div v-else-if="!repo.currentRepo"
         class="mt-2 flex items-center gap-2 font-mono text-[11px] text-text-muted">
      <span>○</span> 请先在左侧添加并选择仓库
    </div>
  </div>
</template>

<style scoped>
.search-panel {
  @apply px-6 py-4 border-b border-border-dim bg-bg-base flex-shrink-0;
}
.lang-chip {
  min-width: 44px;
  text-align: center;
}
</style>
