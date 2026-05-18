<script setup lang="ts">
import { useAnalysisStore } from '@/stores/analysisStore'
import TabSummary from './tabs/TabSummary.vue'
import TabBug     from './tabs/TabBug.vue'
import TabDeps    from './tabs/TabDeps.vue'
import TabHistory from './tabs/TabHistory.vue'

const analysis = useAnalysisStore()

type Tab = { id: typeof analysis.activeTab; label: string; icon: string }
const tabs: Tab[] = [
  { id: 'summary', label: '代码解读', icon: '◆' },
  { id: 'bug',     label: 'Bug',      icon: '◉' },
  { id: 'deps',    label: '依赖图',   icon: '⟳' },
  { id: 'history', label: '历史',     icon: '⟲' },
]
</script>

<template>
  <aside class="analysis-panel">
    <!-- Panel header -->
    <div class="border-b border-border-dim flex-shrink-0">
      <div class="px-4 py-2.5 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="font-mono text-[12px] font-semibold text-text-primary">AI 分析引擎</span>
          <span class="font-mono text-[9px] font-bold px-1.5 py-0.5 rounded
                       bg-cyan-dim text-cyan border border-cyan/20 leading-none">LLM</span>
        </div>
        <button
          v-if="analysis.streaming"
          class="font-mono text-[10px] text-red-accent hover:opacity-70 transition-opacity
                 flex items-center gap-1"
          @click="analysis.abort()"
        >
          <span class="animate-pulse-dot">●</span> 停止
        </button>
      </div>

      <!-- Tabs -->
      <div class="flex px-3 gap-0">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          class="analysis-tab"
          :class="analysis.activeTab === tab.id ? 'active' : ''"
          @click="analysis.setTab(tab.id)"
        >
          <span class="opacity-60">{{ tab.icon }}</span>
          <span>{{ tab.label }}</span>
        </button>
      </div>
    </div>

    <!-- Tab content -->
    <div class="flex-1 overflow-y-auto min-h-0">
      <TabSummary v-if="analysis.activeTab === 'summary'" />
      <TabBug     v-else-if="analysis.activeTab === 'bug'" />
      <TabDeps    v-else-if="analysis.activeTab === 'deps'" />
      <TabHistory v-else-if="analysis.activeTab === 'history'" />
    </div>
  </aside>
</template>

<style scoped>
.analysis-panel {
  @apply flex flex-col bg-bg-base border-l border-border-dim overflow-hidden;
  grid-column: 3;
  grid-row: 2;
}
.analysis-tab {
  @apply flex items-center gap-1 font-mono text-[11px] px-3 py-2
         border-b-2 border-transparent text-text-muted
         hover:text-text-secondary transition-all;
}
.analysis-tab.active {
  @apply text-cyan border-cyan;
}
</style>
