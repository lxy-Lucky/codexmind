<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { SimilarItem } from '@/types'

const { t } = useI18n()
defineProps<{ data: SimilarItem[] }>()
</script>

<template>
  <div class="rounded-xl border border-blue-500/20 overflow-hidden
    bg-gradient-to-br from-blue-500/5 to-transparent">
    <div class="px-3 py-2 flex items-center gap-1.5 text-[10px] font-mono text-blue-400">
      <span class="text-[12px]">◇</span> {{ t('insight.similarTitle') }}
    </div>
    <div class="px-3 pb-2.5 flex flex-col gap-1">
      <div v-for="item in data.slice(0, 5)" :key="`${item.file_path}:${item.line_start}`"
        class="py-1.5 px-2 rounded-md bg-blue-500/5 hover:bg-blue-500/10
          cursor-pointer transition-colors">
        <div class="flex items-center justify-between">
          <span class="font-mono text-[11px] text-blue-300 truncate">
            {{ item.class_name ? `${item.class_name}.` : '' }}{{ item.symbol }}
          </span>
          <span class="font-mono text-[9px] px-1.5 py-0.5 rounded-full
            bg-blue-500/15 text-blue-400 flex-shrink-0 ml-2">
            {{ Math.round(item.score * 100) }}%
          </span>
        </div>
        <div class="font-mono text-[9px] text-text-muted mt-0.5 truncate">
          {{ item.file_path }}:{{ item.line_start }}
        </div>
      </div>
    </div>
  </div>
</template>
