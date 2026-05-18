<script setup lang="ts">
import { useSystemStatus } from '@/composables/useSystemStatus'
import { useSearchStore }  from '@/stores/searchStore'
import { useAnalysisStore } from '@/stores/analysisStore'

const { status, online } = useSystemStatus()
const search   = useSearchStore()
const analysis = useAnalysisStore()
</script>

<template>
  <footer class="status-bar">
    <div class="flex items-center gap-4">
      <span class="status-item">
        <span class="dot" :class="online ? 'bg-green-accent' : 'bg-red-accent'" />
        向量索引: {{ online ? '在线' : '离线' }}
      </span>
      <span v-if="status" class="status-item">
        {{ status.embedding_device.toUpperCase() }} · {{ status.embedding_model.split('/')[1] }}
      </span>
      <span v-if="status" class="status-item">
        上下文窗口: {{ status.context_window }}
      </span>
    </div>
    <div class="flex items-center gap-4">
      <span v-if="search.latencyMs" class="status-item">
        检索耗时: {{ search.latencyMs }}ms
      </span>
      <span v-if="analysis.latencyMs" class="status-item">
        分析耗时: {{ (analysis.latencyMs / 1000).toFixed(1) }}s
      </span>
      <span v-if="analysis.confidence" class="status-item">
        置信度: {{ Math.round(analysis.confidence * 100) }}%
      </span>
      <span class="status-item">UTF-8 · LF</span>
    </div>
  </footer>
</template>

<style scoped>
.status-bar {
  @apply col-span-3 flex items-center justify-between px-4 h-7
         bg-bg-base border-t border-border-dim z-50;
}
.status-item {
  @apply flex items-center gap-1.5 font-mono text-[11px] text-text-muted;
}
.dot {
  @apply w-1.5 h-1.5 rounded-full;
}
</style>
