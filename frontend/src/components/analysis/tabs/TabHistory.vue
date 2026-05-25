<script setup lang="ts">
import { onMounted } from 'vue'
import { useSearchStore } from '@/stores/searchStore'
import type { QueryHistory } from '@/types'

const search = useSearchStore()
onMounted(() => search.fetchHistory())

const MODE_LABEL: Record<string, string> = {
  semantic: '语义', bug: 'Bug', explain: '解释', deps: '依赖',
}
const MODE_COLOR: Record<string, string> = {
  semantic: 'text-cyan border-cyan/30',
  bug:      'text-red-accent border-red-accent/30',
  explain:  'text-amber border-amber/30',
  deps:     'text-purple border-purple/30',
}

function rerun(h: QueryHistory) {
  // 现在只剩"语义检索"一种模式，直接复用 query 即可
  search.query = h.query
  search.doSearch()
}

function fmt(dateStr: string) {
  const d = new Date(dateStr)
  return d.toLocaleString('zh-CN', { month:'short', day:'numeric', hour:'2-digit', minute:'2-digit' })
}
</script>

<template>
  <!-- h-full + overflow-y-auto：右侧面板约束高度后这里才能滚动 -->
  <div class="h-full flex flex-col overflow-hidden">
    <!-- sticky 头部 -->
    <div class="flex-shrink-0 px-4 py-2.5 border-b border-border-dim flex items-center justify-between bg-bg-base">
      <span class="font-mono text-[11px] text-text-muted">
        最近 {{ search.history.length }} 条查询
      </span>
      <button
        v-if="search.history.length"
        class="font-mono text-[10px] text-text-muted hover:text-red-accent transition-colors"
        @click="search.history.forEach(h => search.deleteHistory(h.id))"
      >
        清空
      </button>
    </div>

    <!-- 滚动区 -->
    <div class="flex-1 overflow-y-auto min-h-0">
      <div v-if="!search.history.length" class="p-8 text-center font-mono text-[12px] text-text-muted">
        <div class="text-3xl mb-2 opacity-20">⟲</div>
        暂无历史记录
      </div>

      <div
        v-for="h in search.history" :key="h.id"
        class="px-4 py-3 border-b border-border-dim hover:bg-bg-hover transition-colors group"
      >
        <div class="flex items-start gap-2">
          <span
            class="font-mono text-[10px] px-1.5 py-0.5 rounded border flex-shrink-0 mt-0.5"
            :class="MODE_COLOR[h.mode] ?? 'text-text-muted border-border-dim'"
          >{{ MODE_LABEL[h.mode] ?? h.mode }}</span>
          <div class="flex-1 min-w-0">
            <div class="font-mono text-[12px] text-text-secondary truncate">{{ h.query }}</div>
            <div class="font-mono text-[10px] text-text-muted mt-0.5 flex gap-3">
              <span>{{ fmt(h.created_at) }}</span>
              <span>{{ h.result_count }} 结果</span>
              <span>{{ h.latency_ms }}ms</span>
            </div>
          </div>
          <div class="hidden group-hover:flex gap-1.5 flex-shrink-0">
            <button
              class="font-mono text-[10px] px-2 py-0.5 rounded border border-cyan/30
                     text-cyan hover:bg-cyan-dim transition-colors"
              @click="rerun(h)"
            >重新执行</button>
            <button
              class="font-mono text-[10px] px-1.5 py-0.5 rounded
                     text-text-muted hover:text-red-accent transition-colors"
              @click="search.deleteHistory(h.id)"
            >✕</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
