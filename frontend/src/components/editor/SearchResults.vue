<script setup lang="ts">
import { ref } from 'vue'
import { useSearchStore }   from '@/stores/searchStore'
import { useEditorStore }   from '@/stores/editorStore'
import { useAnalysisStore } from '@/stores/analysisStore'

defineProps<{ compact?: boolean }>()
const emit = defineEmits<{ (e: 'open-file', path: string, start: number, end: number): void }>()

const search   = useSearchStore()
const editor   = useEditorStore()
const analysis = useAnalysisStore()

// 每张卡片独立记录展开状态
const expanded = ref<Record<number, boolean>>({})
function toggleExpand(idx: number) {
  expanded.value[idx] = !expanded.value[idx]
}

const LANG_COLOR: Record<string, string> = {
  java: '#b07219', python: '#3572a5', typescript: '#3178c6',
  javascript: '#f1e05a', go: '#00add8', kotlin: '#a97bff',
}

async function openFile(file_path: string, line_start: number, line_end: number, symbol_id?: string, class_name?: string) {
  emit('open-file', file_path, line_start, line_end)
  if (symbol_id) analysis.setGraphSymbol(symbol_id, class_name)
  await new Promise(r => setTimeout(r, 30))
  await editor.openFile(file_path, line_start, line_end)
}

async function openAndAnalyze(file_path: string, line_start: number, line_end: number, symbol_id?: string, class_name?: string) {
  await openFile(file_path, line_start, line_end, symbol_id, class_name)
  analysis.analyze('summary')
}

function scoreColor(s: number) {
  if (s >= 0.85) return 'text-green-accent'
  if (s >= 0.65) return 'text-amber'
  return 'text-text-muted'
}

// 文件路径只显示最后 2 段，鼠标悬浮 title 显示完整路径
function shortPath(fp: string) {
  const parts = fp.replace(/\\/g, '/').split('/')
  return parts.length > 3 ? '…/' + parts.slice(-2).join('/') : fp
}

// snippet 行数统计
function lineCount(snippet: string) {
  return snippet.split('\n').length
}
</script>

<template>
  <!-- Loading -->
  <div v-if="search.loading" class="p-4 flex flex-col gap-2">
    <div v-for="i in 4" :key="i" class="h-14 bg-bg-surface rounded-lg animate-pulse" />
  </div>

  <!-- Error -->
  <div v-else-if="search.error"
    class="p-4 font-mono text-[12px] text-red-accent flex items-start gap-2">
    <span>⚠</span><span>{{ search.error }}</span>
  </div>

  <!-- No results -->
  <div v-else-if="search.hasSearched && !search.results.length"
    class="p-8 text-center font-mono text-[12px] text-text-muted">
    <div class="text-3xl mb-2 opacity-30">◎</div>
    未找到相关代码，尝试换个描述方式
  </div>

  <!-- Results -->
  <div v-else-if="search.results.length" class="flex flex-col divide-y divide-border-dim">
    <!-- Sticky header -->
    <div class="px-3 py-1.5 bg-bg-base sticky top-0 z-10 border-b border-border-dim">
      <span class="font-mono text-[11px] text-text-muted">
        <span class="text-text-secondary">{{ search.results.length }}</span> 个匹配 ·
        <span class="text-text-secondary">{{ search.latencyMs }}ms</span>
      </span>
    </div>

    <!-- Items -->
    <div
      v-for="(item, idx) in search.results"
      :key="idx"
      class="result-card group"
    >
      <!-- ① 顶部行：语言徽章 + 文件路径 + 相似度 -->
      <div
        class="flex items-center gap-1.5 cursor-pointer"
        @click="openFile(item.file_path, item.line_start, item.line_end, item.symbol_id, item.class_name)"
      >
        <span
          class="font-mono text-[9px] font-bold px-1.5 py-0.5 rounded flex-shrink-0"
          :style="{ color: LANG_COLOR[item.language] ?? '#556a8e', background: 'rgba(255,255,255,0.07)' }"
        >{{ item.language.toUpperCase() }}</span>

        <span
          class="font-mono text-[11px] text-text-secondary flex-1 truncate min-w-0"
          :title="item.file_path"
        >{{ shortPath(item.file_path) }}</span>

        <span class="font-mono text-[10px] flex-shrink-0" :class="scoreColor(item.score)">
          {{ Math.round(item.score * 100) }}%
        </span>
      </div>

      <!-- ② 行号 + chunk 类型 -->
      <div class="font-mono text-[10px] text-text-muted mt-1 mb-1.5 flex items-center gap-2">
        <span>L{{ item.line_start }}–{{ item.line_end }}</span>
        <span class="px-1.5 py-0.5 rounded bg-bg-surface border border-border-dim text-[9px]">
          {{ item.chunk_type }}
        </span>
        <span v-if="item.chunk_type === 'method' && lineCount(item.snippet) > 1">
          · {{ lineCount(item.snippet) }} 行
        </span>
      </div>

      <!-- ③ Snippet 代码块，支持展开 -->
      <div
        v-if="item.snippet"
        class="rounded border border-border-dim bg-bg-deep overflow-hidden"
      >
        <!-- 代码内容：收起时限高，展开时不限 -->
        <pre
          class="font-mono text-[11px] text-text-code px-3 py-2 leading-relaxed
                 overflow-x-auto whitespace-pre transition-all"
          :class="expanded[idx]
            ? 'max-h-none'
            : 'max-h-[120px] overflow-y-hidden'"
        >{{ item.snippet }}</pre>

        <!-- 展开/收起按钮（超过 6 行才显示）-->
        <button
          v-if="lineCount(item.snippet) > 6"
          class="w-full flex items-center justify-center gap-1 py-1
                 font-mono text-[10px] text-text-muted border-t border-border-dim
                 hover:bg-bg-hover hover:text-cyan transition-colors"
          @click.stop="toggleExpand(idx)"
        >
          <span>{{ expanded[idx] ? '▲ 收起' : `▼ 展开全部 (${lineCount(item.snippet)} 行)` }}</span>
        </button>
      </div>

      <!-- snippet 为空时的提示 -->
      <div v-else
        class="rounded border border-border-dim bg-bg-deep px-3 py-1.5
               font-mono text-[10px] text-text-muted italic">
        重新触发索引后可显示代码片段
      </div>

      <!-- ④ Hover 操作按钮 -->
      <div class="hidden group-hover:flex items-center gap-2 mt-2">
        <button
          class="action-btn border-cyan/30 text-cyan hover:bg-cyan-dim"
          @click.stop="openFile(item.file_path, item.line_start, item.line_end, item.symbol_id, item.class_name)"
        >
          <span>◫</span> 打开文件
        </button>
        <button
          class="action-btn border-purple/30 text-purple hover:bg-purple/10"
          @click.stop="openAndAnalyze(item.file_path, item.line_start, item.line_end, item.symbol_id, item.class_name)"
        >
          <span>◆</span> AI 分析
        </button>
      </div>
    </div>
  </div>

  <!-- Initial state -->
  <div v-else class="p-8 text-center text-text-muted font-mono text-[12px]">
    <div class="text-3xl mb-2 opacity-20">⌕</div>
    在上方输入自然语言查询
  </div>
</template>

<style scoped>
.result-card {
  @apply px-3 py-3 hover:bg-bg-hover transition-colors border-b border-border-dim;
}
.action-btn {
  @apply px-2.5 py-1 rounded font-mono text-[10px] border
         transition-colors flex items-center gap-1;
}
</style>
