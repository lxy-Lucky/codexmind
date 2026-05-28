<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAnalysisStore } from '@/stores/analysisStore'
import InsightPanel from './InsightPanel.vue'
import TabHistory from './tabs/TabHistory.vue'

const { t } = useI18n()
const analysis = useAnalysisStore()

type Tab = { id: typeof analysis.activeTab; labelKey: string; icon: string }
const tabs = computed<Tab[]>(() => [
  { id: 'insight', labelKey: 'analysis.tabs.insight', icon: '◇' },
  { id: 'history', labelKey: 'analysis.tabs.history', icon: '⟲' },
])
</script>

<template>
  <aside class="analysis-panel">
    <div class="border-b border-border-dim flex-shrink-0">
      <div class="px-4 py-2.5 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="font-mono text-[12px] font-semibold text-text-primary">{{ t('analysis.engineTitle') }}</span>
          <span class="font-mono text-[9px] font-bold px-1.5 py-0.5 rounded
                       bg-cyan-dim text-cyan border border-cyan/20 leading-none">LLM</span>
        </div>
        <button
          v-if="analysis.insightStreaming || analysis.streaming"
          class="font-mono text-[10px] text-red-accent hover:opacity-70
                 transition-opacity flex items-center gap-1"
          @click="analysis.abort()"
        >
          <span class="animate-pulse-dot">●</span> {{ t('common.stop') }}
        </button>
      </div>

      <div class="flex px-2 gap-0 overflow-x-auto">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          class="analysis-tab flex-shrink-0"
          :class="{ active: analysis.activeTab === tab.id }"
          @click="analysis.setTab(tab.id)"
        >
          <span class="opacity-70">{{ tab.icon }}</span>
          <span>{{ t(tab.labelKey) }}</span>
          <span
            v-if="tab.id === 'insight' && analysis.insightCards.length && analysis.activeTab !== 'insight'"
            class="w-1.5 h-1.5 rounded-full bg-cyan animate-pulse-dot"
          />
        </button>
      </div>
    </div>

    <div class="flex-1 overflow-hidden min-h-0 flex flex-col">
      <InsightPanel v-if="analysis.activeTab === 'insight'" />
      <TabHistory   v-else-if="analysis.activeTab === 'history'" />
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
  @apply flex items-center gap-1 font-mono text-[11px] px-2.5 py-2
         border-b-2 border-transparent text-text-muted
         hover:text-text-secondary transition-all whitespace-nowrap;
}
.analysis-tab.active {
  @apply text-cyan border-cyan;
}
</style>
