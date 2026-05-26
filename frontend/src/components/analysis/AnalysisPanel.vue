<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAnalysisStore } from '@/stores/analysisStore'
import { useRepoStore }     from '@/stores/repoStore'
import TabSummary from './tabs/TabSummary.vue'
import TabBug     from './tabs/TabBug.vue'
import TabDeps    from './tabs/TabDeps.vue'
import TabHistory from './tabs/TabHistory.vue'
import TabChat    from './tabs/TabChat.vue'
import TabDocs    from './tabs/TabDocs.vue'

const { t } = useI18n()
const analysis = useAnalysisStore()
const repo     = useRepoStore()

type Tab = { id: typeof analysis.activeTab; labelKey: string; icon: string }
const tabs = computed<Tab[]>(() => [
  { id: 'summary', labelKey: 'analysis.tabs.summary', icon: '◆' },
  { id: 'bug',     labelKey: 'analysis.tabs.bug',     icon: '◉' },
  { id: 'deps',    labelKey: 'analysis.tabs.deps',    icon: '⟳' },
  { id: 'history', labelKey: 'analysis.tabs.history', icon: '⟲' },
  { id: 'chat',    labelKey: 'analysis.tabs.chat',    icon: '💬' },
  { id: 'docs',    labelKey: 'analysis.tabs.docs',    icon: '📄' },
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
          v-if="(analysis.streaming && analysis.activeTab !== 'chat' && analysis.activeTab !== 'docs') ||
                (analysis.chatStreaming && analysis.activeTab === 'chat') ||
                (analysis.docsStreaming && analysis.activeTab === 'docs')"
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
          :class="[
            analysis.activeTab === tab.id ? 'active' : '',
            tab.id === 'chat' ? 'chat-tab' : '',
            tab.id === 'docs' ? 'docs-tab' : '',
          ]"
          @click="analysis.setTab(tab.id)"
        >
          <span class="opacity-70">{{ tab.icon }}</span>
          <span>{{ t(tab.labelKey) }}</span>
          <span
            v-if="tab.id === 'chat' && analysis.chatHistory.length && analysis.activeTab !== 'chat'"
            class="w-1.5 h-1.5 rounded-full bg-cyan animate-pulse-dot"
          />
        </button>
      </div>
    </div>

    <div class="flex-1 overflow-hidden min-h-0 flex flex-col">
      <TabSummary v-if="analysis.activeTab === 'summary'" />
      <TabBug     v-else-if="analysis.activeTab === 'bug'" />
      <TabDeps
        v-else-if="analysis.activeTab === 'deps'"
        :repoId="repo.currentRepo?.id ?? ''"
        :symbolId="analysis.currentSymbolId"
        :className="analysis.currentClassName || undefined"
        :requestedView="analysis.depsView"
        :reloadTick="analysis.depsReloadTick"
      />
      <TabHistory v-else-if="analysis.activeTab === 'history'" />
      <TabChat    v-else-if="analysis.activeTab === 'chat'" />
      <TabDocs    v-else-if="analysis.activeTab === 'docs'" />
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
.analysis-tab.chat-tab.active {
  @apply text-purple border-purple;
}
.analysis-tab.chat-tab:not(.active):hover {
  @apply text-purple/70;
}
.analysis-tab.docs-tab.active {
  @apply text-green-accent border-green-accent;
}
.analysis-tab.docs-tab:not(.active):hover {
  @apply text-green-accent/70;
}
</style>
