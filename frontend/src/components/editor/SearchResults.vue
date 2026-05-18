<script setup lang="ts">
import { useSearchStore }   from '@/stores/searchStore'
import { useEditorStore }   from '@/stores/editorStore'
import { useAnalysisStore } from '@/stores/analysisStore'

const props = defineProps<{ compact?: boolean }>()
const emit  = defineEmits<{ (e: 'open-file', path: string, start: number, end: number): void }>()

const search   = useSearchStore()
const editor   = useEditorStore()
const analysis = useAnalysisStore()

const LANG_COLOR: Record<string, string> = {
  java: '#b07219', python: '#3572a5', typescript: '#3178c6',
  javascript: '#f1e05a', go: '#00add8', kotlin: '#a97bff',
}

async function openFile(file_path: string, line_start: number, line_end: number) {
  // 1. 通知父级切换到 explorer（让 Monaco 渲染）
  emit('open-file', file_path, line_start, line_end)
  // 2. 等一个 tick 让 DOM 切换完毕，再加载文件
  await new Promise(r => setTimeout(r, 30))
  await editor.openFile(file_path, line_start, line_end)
}

async function openAndAnalyze(file_path: string, line_start: number, line_end: number) {
  await openFile(file_path, line_start, line_end)
  analysis.analyze('summary')
}

function scoreColor(s: number) {
  if (s >= 0.85) return 'text-green-accent'
  if (s >= 0.65) return 'text-amber'
  return 'text-text-muted'
}
</script>

<template>
  <!-- Loading skeleton -->
  <div v-if="search.loading" class="p-4 flex flex-col gap-2">
    <div v-for="i in 4" :key="i" class="h-14 bg-bg-surface rounded-lg animate-pulse" />
  </div>

  <!-- Error -->
  <div v-else-if="search.error" class="p-4 font-mono text-[12px] text-red-accent flex items-start gap-2">
    <span class="flex-shrink-0">⚠</span>
    <span>{{ search.error }}</span>
  </div>

  <!-- No results -->
  <div
    v-else-if="search.hasSearched && !search.results.length"
    class="p-8 text-center font-mono text-[12px] text-text-muted"
  >
    <div class="text-3xl mb-2 opacity-30">◎</div>
    未找到相关代码，尝试换个描述方式
  </div>

  <!-- Results list -->
  <div v-else-if="search.results.length" class="flex flex-col divide-y divide-border-dim">
    <!-- Header bar -->
    <div class="px-4 py-2 flex items-center justify-between bg-bg-base sticky top-0 z-10">
      <span class="font-mono text-[11px] text-text-muted">
        找到 <span class="text-text-secondary">{{ search.results.length }}</span> 个匹配 · {{ search.latencyMs }}ms
      </span>
    </div>

    <!-- Items -->
    <div
      v-for="(item, idx) in search.results"
      :key="idx"
      class="result-item group"
      :class="compact ? 'px-3 py-2.5' : 'px-5 py-3'"
      @click="openFile(item.file_path, item.line_start, item.line_end)"
    >
      <!-- Lang badge + file path + score -->
      <div class="flex items-center gap-2 mb-1.5">
        <span
          class="font-mono text-[10px] font-bold px-1.5 py-0.5 rounded flex-shrink-0"
          :style="{ color: LANG_COLOR[item.language] ?? '#556a8e', background: 'rgba(255,255,255,0.06)' }"
        >
          {{ item.language.toUpperCase() }}
        </span>
        <span class="font-mono text-[11px] text-text-secondary flex-1 truncate min-w-0">
          {{ item.file_path }}
        </span>
        <span class="font-mono text-[10px] flex-shrink-0" :class="scoreColor(item.score)">
          {{ Math.round(item.score * 100) }}%
        </span>
      </div>

      <!-- Line range + chunk type -->
      <div class="font-mono text-[10px] text-text-muted mb-1.5">
        L{{ item.line_start }}–{{ item.line_end }} · {{ item.chunk_type }}
      </div>

      <!-- Snippet block -->
      <div class="rounded overflow-hidden bg-bg-deep border border-border-dim">
        <pre
          v-if="item.snippet"
          class="font-mono text-[11px] text-text-code p-2 leading-relaxed
                 overflow-hidden max-h-[80px] whitespace-pre-wrap"
        >{{ item.snippet }}</pre>
        <div
          v-else
          class="font-mono text-[10px] text-text-muted px-2 py-1.5 italic"
        >
          重新触发索引后可显示代码片段
        </div>
      </div>

      <!-- Hover action buttons -->
      <div class="hidden group-hover:flex items-center gap-2 mt-2">
        <button
          class="px-2.5 py-1 rounded font-mono text-[10px] border border-cyan/30
                 text-cyan hover:bg-cyan-dim transition-colors flex items-center gap-1"
          @click.stop="openFile(item.file_path, item.line_start, item.line_end)"
        >
          <span>◫</span> 打开文件
        </button>
        <button
          class="px-2.5 py-1 rounded font-mono text-[10px] border border-purple/30
                 text-purple hover:bg-purple/10 transition-colors flex items-center gap-1"
          @click.stop="openAndAnalyze(item.file_path, item.line_start, item.line_end)"
        >
          <span>◆</span> AI 分析
        </button>
      </div>
    </div>
  </div>

  <!-- Initial empty state -->
  <div v-else class="p-8 text-center text-text-muted font-mono text-[12px]">
    <div class="text-3xl mb-2 opacity-20">⌕</div>
    在上方输入自然语言查询
  </div>
</template>

<style scoped>
.result-item {
  @apply cursor-pointer hover:bg-bg-hover transition-colors;
}
</style>
