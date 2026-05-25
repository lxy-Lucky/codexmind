<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { marked } from 'marked'
import { useAnalysisStore } from '@/stores/analysisStore'

const { t } = useI18n()
const analysis = useAnalysisStore()
const rendered = computed(() =>
  analysis.streamingText ? marked.parse(analysis.streamingText) as string : ''
)
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div v-if="analysis.streaming || analysis.hasResult" class="p-4 flex flex-col gap-4">

      <div class="bg-bg-surface border border-border-dim rounded-lg p-4 animate-fade-in">
        <div class="flex items-center gap-2 mb-3">
          <span class="text-cyan text-[10px] font-mono font-semibold tracking-widest">◆ {{ t('summary.title') }}</span>
          <span v-if="analysis.streaming" class="font-mono text-[10px] text-text-muted animate-pulse">{{ t('summary.generating') }}</span>
          <span v-else class="font-mono text-[10px] text-green-accent">{{ t('common.done') }}</span>
        </div>
        <div
          class="prose prose-sm prose-dark max-w-none"
          :class="{ 'streaming-cursor': analysis.streaming }"
          v-html="rendered || '&nbsp;'"
        />
      </div>

      <div v-if="!analysis.streaming && analysis.confidence" class="flex items-center gap-3">
        <span class="font-mono text-[10px] text-text-muted">{{ t('summary.confidence') }}</span>
        <div class="flex-1 h-1.5 bg-bg-elevated rounded-full overflow-hidden">
          <div
            class="h-full rounded-full transition-all duration-500"
            :style="{ width: `${analysis.confidence * 100}%`, background: 'linear-gradient(90deg,#00d4ff,#26de81)' }"
          />
        </div>
        <span class="font-mono text-[11px] text-cyan">{{ Math.round(analysis.confidence * 100) }}%</span>
      </div>

      <div
        v-if="!analysis.streaming && (analysis.currentCallers?.length || analysis.currentCallees?.length)"
        class="bg-bg-surface border border-border-dim rounded-lg p-4"
      >
        <div class="font-mono text-[10px] text-text-muted mb-3 tracking-widest">◇ {{ t('summary.callChainTitle') }}</div>

        <div v-if="analysis.currentCallers?.length" class="mb-3">
          <div class="font-mono text-[10px] text-amber mb-1.5">{{ t('summary.callers') }}</div>
          <div
            v-for="c in analysis.currentCallers" :key="c.symbol_id"
            class="flex items-center gap-2 py-1 border-b border-border-dim/30 last:border-0"
          >
            <span class="font-mono text-[11px] text-text-primary">
              {{ c.class_name ? `${c.class_name}.` : '' }}{{ c.method_name }}
            </span>
            <span class="font-mono text-[10px] text-text-muted ml-auto truncate max-w-[180px]">
              {{ c.file_path }}
            </span>
          </div>
        </div>

        <div v-if="analysis.currentCallees?.length">
          <div class="font-mono text-[10px] text-cyan mb-1.5">{{ t('summary.callees') }}</div>
          <div
            v-for="c in analysis.currentCallees" :key="c.symbol_id"
            class="flex items-center gap-2 py-1 border-b border-border-dim/30 last:border-0"
          >
            <span class="font-mono text-[11px] text-text-primary">
              {{ c.class_name ? `${c.class_name}.` : '' }}{{ c.method_name }}
            </span>
            <span class="font-mono text-[10px] text-text-muted ml-auto truncate max-w-[180px]">
              {{ c.file_path }}
            </span>
          </div>
        </div>
      </div>

    </div>

    <div v-else-if="analysis.error" class="p-4 font-mono text-[12px] text-red-accent">
      ⚠ {{ analysis.error }}
    </div>

    <div v-else class="p-8 text-center text-text-muted font-mono text-[12px]">
      <div class="text-3xl mb-2 opacity-20">◆</div>
      {{ t('summary.empty') }}
    </div>
  </div>
</template>
