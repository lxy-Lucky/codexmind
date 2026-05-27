<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ImpactNode } from '@/types'

const { t } = useI18n()
const props = defineProps<{ data: { nodes: ImpactNode[]; total: number } }>()

const grouped = computed(() => {
  const map = new Map<number, ImpactNode[]>()
  for (const n of props.data.nodes) {
    const arr = map.get(n.depth) || []
    arr.push(n)
    map.set(n.depth, arr)
  }
  return [...map.entries()].sort((a, b) => a[0] - b[0])
})
</script>

<template>
  <div class="rounded-xl border border-purple/20 overflow-hidden
    bg-gradient-to-br from-purple/5 to-transparent">
    <div class="px-3 py-2 flex items-center justify-between text-[10px] font-mono">
      <span class="text-purple flex items-center gap-1.5">
        <span class="text-[12px]">◈</span> {{ t('insight.impactTitle') }}
      </span>
      <span class="text-text-muted">{{ data.total }} {{ t('insight.methods') }}</span>
    </div>
    <div class="px-3 pb-2.5">
      <template v-for="[depth, nodes] in grouped" :key="depth">
        <div v-for="node in nodes.slice(0, 6)" :key="node.name"
          class="py-1 px-2 my-0.5 rounded-md text-[10px] font-mono
            bg-purple/5 hover:bg-purple/10 cursor-pointer transition-colors
            flex items-center gap-1.5">
          <span class="text-purple/70 text-[9px] min-w-[20px]">{{ depth }}{{ t('insight.hop') }}</span>
          <span class="text-text-secondary truncate">
            {{ node.class_name ? `${node.class_name}.` : '' }}{{ node.name }}
          </span>
        </div>
      </template>
      <div v-if="data.total > 6" class="text-[9px] text-text-muted font-mono mt-1 px-2">
        +{{ data.total - 6 }} {{ t('insight.more') }}
      </div>
    </div>
  </div>
</template>
