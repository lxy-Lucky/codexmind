<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { BugItem } from '@/types'

const { t } = useI18n()
defineProps<{ data: BugItem[] }>()

const sevStyle: Record<string, { bg: string; text: string }> = {
  Critical:   { bg: 'bg-red-accent/15', text: 'text-red-accent' },
  Warning:    { bg: 'bg-amber/15', text: 'text-amber' },
  Suggestion: { bg: 'bg-cyan/15', text: 'text-cyan' },
}
</script>

<template>
  <div class="rounded-xl border border-red-accent/20 overflow-hidden
    bg-gradient-to-br from-red-accent/5 to-transparent">
    <div class="px-3 py-2 flex items-center gap-1.5 text-[10px] font-mono text-red-accent">
      <span class="text-[12px]">⚠</span> {{ t('insight.bugTitle') }}
    </div>
    <div class="px-3 pb-2.5 flex flex-col gap-1.5">
      <div v-for="(bug, i) in data" :key="i"
        class="flex items-start gap-1.5 text-[10px] font-mono">
        <span class="px-1 py-0.5 rounded text-[8px] font-bold flex-shrink-0 mt-0.5"
          :class="[sevStyle[bug.severity]?.bg, sevStyle[bug.severity]?.text]">
          {{ bug.severity === 'Critical' ? 'CRIT' : bug.severity === 'Warning' ? 'WARN' : 'INFO' }}
        </span>
        <div class="min-w-0">
          <div class="text-text-secondary">{{ bug.title }}</div>
          <div v-if="bug.line" class="text-text-muted text-[9px]">L{{ bug.line }}</div>
        </div>
      </div>
    </div>
  </div>
</template>
