<script setup lang="ts">
import { useAnalysisStore } from '@/stores/analysisStore'
import { useRepoStore }     from '@/stores/repoStore'
import TabSummary from './tabs/TabSummary.vue'
import TabBug     from './tabs/TabBug.vue'
import TabDeps    from './tabs/TabDeps.vue'
import TabHistory from './tabs/TabHistory.vue'
import TabChat    from './tabs/TabChat.vue'

const analysis = useAnalysisStore()
const repo     = useRepoStore()

type Tab = { id: typeof analysis.activeTab; label: string; icon: string }
const tabs: Tab[] = [
  { id: 'summary', label: '代码解读', icon: '◆' },
  { id: 'bug',     label: 'Bug',      icon: '◉' },
  { id: 'deps',    label: '依赖图',   icon: '⟳' },
  { id: 'history', label: '历史',     icon: '⟲' },
  { id: 'chat',    label: '问答',     icon: '💬' },
]
</script>

<template>
  <aside class="analysis-panel">
    <!-- Header -->
    <div class="border-b border-border-dim flex-shrink-0">
      <div class="px-4 py-2.5 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="font-mono text-[12px] font-semibold text-text-primary">AI 分析引擎</span>
          <span class="font-mono text-[9px] font-bold px-1.5 py-0.5 rounded
                       bg-cyan-dim text-cyan border border-cyan/20 leading-none">LLM</span>
        </div>
        <!-- 当前 tab 流式输出时显示停止按钮 -->
        <button
          v-if="(analysis.streaming && analysis.activeTab !== 'chat') ||
                (analysis.chatStreaming && analysis.activeTab === 'chat')"
          class="font-mono text-[10px] text-red-accent hover:opacity-70
                 transition-opacity flex items-center gap-1"
          @click="analysis.abort()"
        >
          <span class="animate-pulse-dot">●</span> 停止
        </button>
      </div>

      <!-- Tabs -->
      <div class="flex px-2 gap-0 overflow-x-auto">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          class="analysis-tab flex-shrink-0"
          :class="[
            analysis.activeTab === tab.id ? 'active' : '',
            tab.id === 'chat' ? 'chat-tab' : '',
          ]"
          @click="analysis.setTab(tab.id)"
        >
          <span class="opacity-70">{{ tab.icon }}</span>
          <span>{{ tab.label }}</span>
          <!-- 问答 tab 有未读消息时显示圆点 -->
          <span
            v-if="tab.id === 'chat' && analysis.chatHistory.length && analysis.activeTab !== 'chat'"
            class="w-1.5 h-1.5 rounded-full bg-cyan animate-pulse-dot"
          />
        </button>
      </div>
    </div>

    <!-- Content -->
    <div class="flex-1 overflow-hidden min-h-0 flex flex-col">
      <TabSummary v-if="analysis.activeTab === 'summary'" />
      <TabBug     v-else-if="analysis.activeTab === 'bug'" />
      <TabDeps
        v-else-if="analysis.activeTab === 'deps'"
        :repoId="repo.currentRepo?.id ?? ''"
        :symbolId="analysis.currentSymbolId"
        :className="analysis.currentClassName || undefined"
        :requestedView="analysis.depsView"
      />
      <TabHistory v-else-if="analysis.activeTab === 'history'" />
      <TabChat    v-else-if="analysis.activeTab === 'chat'" />
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
</style>
