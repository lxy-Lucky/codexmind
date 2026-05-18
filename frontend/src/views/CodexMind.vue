<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRepoStore }   from '@/stores/repoStore'

import TopBar        from '@/components/layout/TopBar.vue'
import StatusBar     from '@/components/layout/StatusBar.vue'
import RepoSelector  from '@/components/sidebar/RepoSelector.vue'
import SidebarNav    from '@/components/sidebar/SidebarNav.vue'
import FileTree      from '@/components/sidebar/FileTree.vue'
import SearchPanel   from '@/components/editor/SearchPanel.vue'
import SearchResults from '@/components/editor/SearchResults.vue'
import CodeToolbar   from '@/components/editor/CodeToolbar.vue'
import CodeViewer    from '@/components/editor/CodeViewer.vue'
import AnalysisPanel from '@/components/analysis/AnalysisPanel.vue'

const repo = useRepoStore()
const activeNav = ref('explorer')   // 当前 sidebar 视图

onMounted(() => repo.fetchRepos())
</script>

<template>
  <div class="app-shell">
    <!-- Top bar -->
    <TopBar class="top-bar-row" />

    <!-- ── Left Sidebar ── -->
    <aside class="sidebar">
      <!-- Repo selector -->
      <div class="sidebar-header px-4 py-3 border-b border-border-dim">
        <span class="font-mono text-[10px] font-semibold uppercase tracking-widest text-text-muted">
          仓库
        </span>
      </div>
      <RepoSelector />

      <!-- Divider -->
      <div class="border-t border-border-dim my-1" />

      <!-- Nav -->
      <div class="px-4 py-2">
        <span class="font-mono text-[10px] font-semibold uppercase tracking-widest text-text-muted">
          {{ activeNav === 'explorer' ? '文件' : activeNav === 'search' ? '搜索结果' : activeNav }}
        </span>
      </div>
      <SidebarNav @change="activeNav = $event" />

      <div class="border-t border-border-dim mt-1 mb-2" />

      <!-- Scrollable area -->
      <div class="flex-1 overflow-y-auto">
        <!-- File tree -->
        <FileTree
          v-if="activeNav === 'explorer'"
          :nodes="repo.fileTree"
        />

        <!-- Index progress -->
        <div
          v-if="repo.indexing && repo.indexProgress"
          class="px-4 py-2 font-mono text-[11px] text-amber flex items-center gap-2"
        >
          <span class="animate-spin-slow inline-block">⟳</span>
          {{ repo.indexProgress.message }}
        </div>
      </div>
    </aside>

    <!-- ── Main Content ── -->
    <main class="main-content">
      <SearchPanel />
      <div class="flex-1 flex flex-col overflow-hidden">
        <!-- Search results overlay (when has results & no file open) -->
        <div
          v-if="activeNav === 'search'"
          class="flex-1 overflow-y-auto border-r border-border-dim"
        >
          <SearchResults />
        </div>

        <!-- Code editor area -->
        <template v-else>
          <CodeToolbar />
          <CodeViewer />
        </template>
      </div>
    </main>

    <!-- ── Right Analysis Panel ── -->
    <AnalysisPanel />

    <!-- ── Status Bar ── -->
    <StatusBar />
  </div>
</template>

<style scoped>
.app-shell {
  display: grid;
  grid-template-rows: 52px 1fr 28px;
  grid-template-columns: 260px 1fr 380px;
  height: 100vh;
  position: relative;
  z-index: 1;
}

.top-bar-row {
  grid-column: 1 / -1;
  grid-row: 1;
}

.sidebar {
  grid-column: 1;
  grid-row: 2;
  @apply flex flex-col bg-bg-base border-r border-border-dim overflow-hidden;
}

.main-content {
  grid-column: 2;
  grid-row: 2;
  @apply flex flex-col overflow-hidden bg-bg-deep;
}

/* AnalysisPanel already spans correctly via CSS */
:deep(.analysis-panel) {
  grid-column: 3;
  grid-row: 2;
}

:deep(.status-bar) {
  grid-column: 1 / -1;
  grid-row: 3;
}
</style>
