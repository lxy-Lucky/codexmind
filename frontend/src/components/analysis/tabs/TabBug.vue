<script setup lang="ts">
import { useAnalysisStore } from '@/stores/analysisStore'

const analysis = useAnalysisStore()

const SEV_CONFIG = {
  Critical:   { color: 'text-red-accent',  bg: 'bg-red-accent/10',  border: 'border-red-accent/30',  icon: '✕' },
  Warning:    { color: 'text-amber',        bg: 'bg-amber/10',       border: 'border-amber/30',       icon: '⚠' },
  Suggestion: { color: 'text-cyan',         bg: 'bg-cyan-dim',       border: 'border-cyan/20',        icon: '→' },
} as const
</script>

<template>
  <!-- Loading (streaming for bug is buffered, show spinner) -->
  <div v-if="analysis.streaming" class="p-8 flex flex-col items-center gap-3 text-text-muted">
    <span class="text-2xl animate-spin-slow inline-block">⟳</span>
    <span class="font-mono text-[12px]">Bug 检测中...</span>
  </div>

  <!-- Results -->
  <div v-else-if="analysis.bugItems.length" class="p-4 flex flex-col gap-3">
    <div class="flex items-center gap-2 font-mono text-[11px] text-text-muted mb-1">
      <span>检测到 {{ analysis.bugItems.length }} 个问题</span>
      <span class="text-red-accent">{{ analysis.bugItems.filter(b=>b.severity==='Critical').length }} Critical</span>
      <span class="text-amber">{{ analysis.bugItems.filter(b=>b.severity==='Warning').length }} Warning</span>
    </div>

    <div
      v-for="(bug, idx) in analysis.bugItems"
      :key="idx"
      class="rounded-lg border p-3.5 animate-fade-in"
      :class="[SEV_CONFIG[bug.severity as keyof typeof SEV_CONFIG]?.bg,
               SEV_CONFIG[bug.severity as keyof typeof SEV_CONFIG]?.border]"
    >
      <!-- Header -->
      <div class="flex items-center gap-2 mb-2">
        <span
          class="font-mono text-[10px] font-bold px-2 py-0.5 rounded-full border"
          :class="[SEV_CONFIG[bug.severity as keyof typeof SEV_CONFIG]?.color,
                   SEV_CONFIG[bug.severity as keyof typeof SEV_CONFIG]?.border]"
        >
          {{ SEV_CONFIG[bug.severity as keyof typeof SEV_CONFIG]?.icon }}
          {{ bug.severity }}
        </span>
        <span v-if="bug.line" class="font-mono text-[10px] text-text-muted">
          L{{ bug.line }}
        </span>
      </div>

      <!-- Title -->
      <div class="font-mono text-[13px] font-medium text-text-primary mb-1.5">
        {{ bug.title }}
      </div>

      <!-- Desc -->
      <div class="font-mono text-[11px] text-text-secondary mb-2 leading-relaxed">
        {{ bug.desc }}
      </div>

      <!-- Code ref -->
      <pre
        v-if="bug.code_ref"
        class="font-mono text-[11px] text-text-code bg-bg-deep rounded p-2 mb-2
               overflow-x-auto whitespace-pre-wrap"
      >{{ bug.code_ref }}</pre>

      <!-- Suggestion -->
      <div class="flex items-start gap-2">
        <span class="font-mono text-[10px] text-text-muted flex-shrink-0 mt-0.5">建议：</span>
        <span class="font-mono text-[11px] text-text-secondary">{{ bug.suggestion }}</span>
      </div>
    </div>
  </div>

  <!-- Error -->
  <div v-else-if="analysis.error" class="p-4 font-mono text-[12px] text-red-accent whitespace-pre-wrap">
    {{ analysis.error }}
  </div>

  <!-- Empty -->
  <div v-else class="p-8 text-center text-text-muted font-mono text-[12px]">
    <div class="text-3xl mb-2 opacity-20">◉</div>
    选中代码后点击工具栏「Bug」按钮
  </div>
</template>
