<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRepoStore }     from '@/stores/repoStore'
import { useSearchStore }   from '@/stores/searchStore'
import { useAnalysisStore } from '@/stores/analysisStore'

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
import TabHistory    from '@/components/analysis/tabs/TabHistory.vue'

const repo     = useRepoStore()
const search   = useSearchStore()
const analysis = useAnalysisStore()

// ── Sidebar collapse ──────────────────────────────────────────────────────────
const sidebarCollapsed = ref(false)
const SIDEBAR_W = 260

// ── Right panel drag ──────────────────────────────────────────────────────────
const rightPanelW = ref(380)
const MIN_RIGHT = 280
const MAX_RIGHT = 640
const dragging   = ref(false)

function onDragStart(e: MouseEvent) {
  dragging.value = true
  e.preventDefault()
}
function onDragMove(e: MouseEvent) {
  if (!dragging.value) return
  const newW = window.innerWidth - e.clientX
  rightPanelW.value = Math.min(MAX_RIGHT, Math.max(MIN_RIGHT, newW))
}
function onDragEnd() { dragging.value = false }

onMounted(() => {
  repo.fetchRepos()
  window.addEventListener('mousemove', onDragMove)
  window.addEventListener('mouseup',   onDragEnd)
})
onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup',   onDragEnd)
})

// ── Grid columns ──────────────────────────────────────────────────────────────
const gridCols = computed(() =>
  sidebarCollapsed.value
    ? `0px 1fr ${rightPanelW.value}px`
    : `${SIDEBAR_W}px 1fr ${rightPanelW.value}px`
)

// ── Nav tabs ─────────────────────────────────────────────────────────────────
const activeNav = ref('explorer')

function onNavChange(id: string) {
  activeNav.value = id
  // 历史记录 → 同步右侧 panel 到 history tab
  if (id === 'history') analysis.setTab('history')
}

// ── 打开文件（从 SearchResults 调用，需切回 explorer 让 Monaco 渲染）────────────
function openFileFromSearch(path: string, lineStart: number, lineEnd: number) {
  activeNav.value = 'explorer'
}

const NAV_LABEL: Record<string, string> = {
  explorer: '文件资源',
  search:   '搜索结果',
  bug:      'Bug 扫描',
  history:  '历史记录',
}
</script>

<template>
  <div
    class="app-shell"
    :style="{ gridTemplateColumns: gridCols }"
    :class="{ 'cursor-col-resize select-none': dragging }"
  >
    <!-- Top bar -->
    <TopBar class="top-bar-row" />

    <!-- ── Left Sidebar ── -->
    <aside class="sidebar" :class="{ collapsed: sidebarCollapsed }">
      <!-- Sidebar toggle button (right edge) -->
      <button
        class="sidebar-toggle"
        :title="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
        @click="sidebarCollapsed = !sidebarCollapsed"
      >
        {{ sidebarCollapsed ? '›' : '‹' }}
      </button>

      <template v-if="!sidebarCollapsed">
        <!-- Repo section -->
        <div class="px-4 pt-3 pb-1.5 flex items-center justify-between">
          <span class="font-mono text-[10px] font-semibold uppercase tracking-widest text-text-muted">
            仓库
          </span>
        </div>
        <RepoSelector />

        <div class="border-t border-border-dim my-1" />

        <!-- Nav section label -->
        <div class="px-4 py-1.5">
          <span class="font-mono text-[10px] font-semibold uppercase tracking-widest text-text-muted">
            {{ NAV_LABEL[activeNav] ?? activeNav }}
          </span>
        </div>
        <SidebarNav :active="activeNav" @change="onNavChange" />

        <div class="border-t border-border-dim mt-1 mb-2" />

        <!-- Scrollable content area, switches by nav -->
        <div class="flex-1 overflow-y-auto min-h-0">

          <!-- 文件资源 -->
          <template v-if="activeNav === 'explorer'">
            <div
              v-if="!repo.fileTree.length && repo.currentRepo"
              class="px-4 py-3 font-mono text-[11px] text-text-muted"
            >
              文件树加载中...
            </div>
            <div
              v-else-if="!repo.currentRepo"
              class="px-4 py-3 font-mono text-[11px] text-text-muted"
            >
              请先添加仓库
            </div>
            <FileTree v-else :nodes="repo.fileTree" />
            <!-- 索引进度条 -->
            <div
              v-if="repo.indexing && repo.indexProgress"
              class="mx-3 mb-2 px-3 py-2 rounded-md bg-amber/10 border border-amber/20
                     font-mono text-[11px] text-amber flex items-center gap-2"
            >
              <span class="animate-spin-slow inline-block flex-shrink-0">⟳</span>
              <span class="truncate">{{ repo.indexProgress.message }}</span>
            </div>
          </template>

          <!-- 搜索结果 (侧边栏模式，较紧凑) -->
          <template v-else-if="activeNav === 'search'">
            <SearchResults compact @open-file="openFileFromSearch" />
          </template>

          <!-- Bug 扫描占位 -->
          <template v-else-if="activeNav === 'bug'">
            <div class="p-4 flex flex-col gap-3">
              <div class="font-mono text-[11px] text-text-muted leading-relaxed">
                选择文件后点击工具栏「Bug」按钮，或在此处一键扫描整个仓库。
              </div>
              <button
                class="flex items-center justify-center gap-2 py-2.5 rounded-lg font-mono text-[12px]
                       font-semibold border border-red-accent/40 text-red-accent
                       hover:bg-red-accent/10 transition-colors"
                :disabled="!repo.isIndexDone"
                :class="!repo.isIndexDone ? 'opacity-40 cursor-not-allowed' : ''"
                @click="analysis.setTab('bug')"
              >
                <span>◉</span> 打开 Bug 检测面板
              </button>
            </div>
          </template>

          <!-- 历史记录 -->
          <template v-else-if="activeNav === 'history'">
            <TabHistory />
          </template>

        </div>
      </template>

      <!-- Collapsed icon strip -->
      <template v-else>
        <div class="flex flex-col items-center gap-3 pt-4">
          <button
            v-for="item in ['◫','⌕','◉','⟲']" :key="item"
            class="w-8 h-8 flex items-center justify-center rounded text-text-muted
                   hover:bg-bg-hover hover:text-text-primary transition-colors font-mono text-sm"
            @click="sidebarCollapsed = false"
          >{{ item }}</button>
        </div>
      </template>
    </aside>

    <!-- ── Main Content ── -->
    <main class="main-content">
      <SearchPanel />
      <div class="flex-1 flex flex-col overflow-hidden">
        <!-- Always render CodeViewer so Monaco stays mounted -->
        <CodeToolbar />
        <CodeViewer />
      </div>
    </main>

    <!-- ── Right drag handle ── -->
    <div
      class="drag-handle"
      :class="{ active: dragging }"
      @mousedown="onDragStart"
    />

    <!-- ── Right Analysis Panel ── -->
    <AnalysisPanel :style="{ width: rightPanelW + 'px' }" />

    <!-- ── Status Bar ── -->
    <StatusBar />
  </div>
</template>

<style scoped>
.app-shell {
  display: grid;
  grid-template-rows: 52px 1fr 28px;
  height: 100vh;
  position: relative;
  z-index: 1;
  transition: grid-template-columns 0.2s ease;
}

.top-bar-row {
  grid-column: 1 / -1;
  grid-row: 1;
}

/* sidebar */
.sidebar {
  grid-column: 1;
  grid-row: 2;
  position: relative;
  @apply flex flex-col bg-bg-base border-r border-border-dim overflow-hidden;
  transition: width 0.2s ease;
}
.sidebar.collapsed {
  @apply border-r border-border-dim;
}

/* toggle button: sits on the right edge of sidebar */
.sidebar-toggle {
  position: absolute;
  right: -12px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 20;
  width: 20px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #141d33;
  border: 1px solid #1c2a4a;
  border-radius: 0 6px 6px 0;
  color: #556a8e;
  font-size: 12px;
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
}
.sidebar-toggle:hover {
  color: #00d4ff;
  background: #0f1628;
}

.main-content {
  grid-column: 2;
  grid-row: 2;
  @apply flex flex-col overflow-hidden bg-bg-deep;
}

/* drag handle — right edge of main content = window.width - rightPanelW */
.drag-handle {
  position: fixed;
  top: 52px;
  bottom: 28px;
  right: v-bind('rightPanelW + "px"');
  width: 5px;
  cursor: col-resize;
  background: transparent;
  transition: background 0.15s;
  z-index: 30;
  transform: translateX(50%);
}
.drag-handle:hover,
.drag-handle.active {
  background: rgba(0, 212, 255, 0.3);
}

:deep(.analysis-panel) {
  grid-column: 3;
  grid-row: 2;
  min-width: v-bind('MIN_RIGHT + "px"');
  max-width: v-bind('MAX_RIGHT + "px"');
}

:deep(.status-bar) {
  grid-column: 1 / -1;
  grid-row: 3;
}
</style>
