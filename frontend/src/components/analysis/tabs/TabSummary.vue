<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import { useAnalysisStore } from '@/stores/analysisStore'

const analysis = useAnalysisStore()

const rendered = computed(() => {
  if (!analysis.streamingText) return ''
  return marked.parse(analysis.streamingText) as string
})
</script>

<template>
  <!-- Streaming / result -->
  <div v-if="analysis.streaming || analysis.hasResult" class="p-4 flex flex-col gap-4">
    <!-- AI summary card -->
    <div class="bg-bg-surface border border-border-dim rounded-lg p-4 animate-fade-in">
      <div class="flex items-center gap-2 mb-3">
        <span class="text-cyan text-[10px] font-mono font-semibold tracking-widest">◆ AI 语义分析</span>
        <span v-if="analysis.streaming" class="font-mono text-[10px] text-text-muted animate-pulse">
          生成中...
        </span>
        <span v-else class="font-mono text-[10px] text-green-accent">完成</span>
      </div>

      <div
        class="prose prose-sm prose-dark max-w-none"
        :class="{ 'streaming-cursor': analysis.streaming }"
        v-html="rendered || '&nbsp;'"
      />
    </div>

    <!-- Confidence meter -->
    <div v-if="!analysis.streaming && analysis.confidence" class="flex items-center gap-3">
      <span class="font-mono text-[10px] text-text-muted">分析置信度</span>
      <div class="flex-1 h-1.5 bg-bg-elevated rounded-full overflow-hidden">
        <div
          class="h-full rounded-full transition-all duration-500"
          :style="{
            width: `${analysis.confidence * 100}%`,
            background: 'linear-gradient(90deg, #00d4ff, #26de81)',
          }"
        />
      </div>
      <span class="font-mono text-[11px] text-cyan">{{ Math.round(analysis.confidence * 100) }}%</span>
    </div>
  </div>

  <!-- Empty -->
  <div v-else-if="analysis.error" class="p-4 font-mono text-[12px] text-red-accent">
    ⚠ {{ analysis.error }}
  </div>

  <div v-else class="p-8 text-center text-text-muted font-mono text-[12px]">
    <div class="text-3xl mb-2 opacity-20">◆</div>
    选中代码后点击工具栏「解读」按钮
  </div>
</template>
