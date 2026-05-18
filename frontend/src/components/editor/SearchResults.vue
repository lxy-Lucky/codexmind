<script setup lang="ts">
import { useSearchStore }   from '@/stores/searchStore'
import { useEditorStore }   from '@/stores/editorStore'
import { useAnalysisStore } from '@/stores/analysisStore'

const search   = useSearchStore()
const editor   = useEditorStore()
const analysis = useAnalysisStore()

const LANG_COLOR: Record<string, string> = {
  java: '#b07219', python: '#3572a5', typescript: '#3178c6',
  javascript: '#f1e05a', go: '#00add8', kotlin: '#a97bff',
}

async function openResult(file_path: string, line_start: number, line_end: number) {
  await editor.openFile(file_path, line_start, line_end)
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
    <div v-for="i in 4" :key="i" class="h-16 bg-bg-surface rounded-lg animate-pulse" />
  </div>

  <!-- Error -->
  <div v-else-if="search.error" class="p-4 font-mono text-[12px] text-red-accent">
    ⚠ {{ search.error }}
  </div>

  <!-- Empty -->
  <div
    v-else-if="search.hasSearched && !search.results.length"
    class="p-8 text-center font-mono text-[12px] text-text-muted"
  >
    <div class="text-3xl mb-2">◎</div>
    未找到相关代码，尝试换个描述方式
  </div>

  <!-- Results -->
  <div v-else-if="search.results.length" class="flex flex-col divide-y divide-border-dim">
    <!-- Header -->
    <div class="px-5 py-2 flex items-center justify-between">
      <span class="font-mono text-[11px] text-text-muted">
        找到 {{ search.results.length }} 个匹配 · {{ search.latencyMs }}ms
      </span>
    </div>

    <!-- Items -->
    <div
      v-for="(item, idx) in search.results"
      :key="idx"
      class="result-item group"
      @click="openResult(item.file_path, item.line_start, item.line_end)"
    >
      <!-- File + score row -->
      <div class="flex items-center gap-2 mb-1.5">
        <span
          class="font-mono text-[10px] font-bold px-1.5 py-0.5 rounded"
          :style="{ color: LANG_COLOR[item.language] ?? '#556a8e', background: 'rgba(255,255,255,0.06)' }"
        >
          {{ item.language.toUpperCase() }}
        </span>
        <span class="font-mono text-[11px] text-text-secondary flex-1 truncate">
          {{ item.file_path }}
        </span>
        <span class="font-mono text-[10px]" :class="scoreColor(item.score)">
          {{ Math.round(item.score * 100) }}%
        </span>
      </div>

      <!-- Line range -->
      <div class="font-mono text-[10px] text-text-muted mb-1.5">
        L{{ item.line_start }}–{{ item.line_end }} · {{ item.chunk_type }}
      </div>

      <!-- Snippet -->
      <pre class="font-mono text-[11px] text-text-code bg-bg-deep rounded p-2
                  overflow-hidden leading-relaxed max-h-20 whitespace-pre-wrap line-clamp-4">{{ item.snippet }}</pre>

      <!-- Hover actions -->
      <div class="hidden group-hover:flex items-center gap-2 mt-2">
        <button
          class="px-2 py-0.5 rounded font-mono text-[10px] border border-cyan/30
                 text-cyan hover:bg-cyan-dim transition-colors"
          @click.stop="openResult(item.file_path, item.line_start, item.line_end)"
        >
          打开文件
        </button>
        <button
          class="px-2 py-0.5 rounded font-mono text-[10px] border border-purple/30
                 text-purple hover:bg-purple/10 transition-colors"
          @click.stop="async () => {
            await openResult(item.file_path, item.line_start, item.line_end)
            analysis.analyze('summary')
          }"
        >
          AI 分析
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.result-item {
  @apply px-5 py-3 cursor-pointer hover:bg-bg-hover transition-colors;
}
</style>
