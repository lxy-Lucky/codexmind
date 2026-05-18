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

// ── Left sidebar ──────────────────────────────────────────────────────────────
const leftW         = ref(260)
const leftCollapsed = ref(false)
const MIN_LEFT      = 180
const MAX_LEFT      = 480
const COLLAPSED_W   = 40          // icon-strip 宽度，始终可见
const dragLeft      = ref(false)

// ── Right panel ───────────────────────────────────────────────────────────────
const rightW       = ref(380)
const MIN_RIGHT    = 280
const MAX_RIGHT    = 640
const dragRight    = ref(false)

// ── 当前拖拽目标 ───────────────────────────────────────────────────────────────
type DragTarget = 'left' | 'right' | null
const dragTarget = ref<DragTarget>(null)

function startDrag(target: DragTarget, e: MouseEvent) {
  dragTarget.value = target
  e.preventDefault()
}

function onMouseMove(e: MouseEvent) {
  if (!dragTarget.value) return
  if (dragTarget.value === 'left') {
    // 左侧：鼠标 x 就是宽度
    const w = e.clientX
    if (w < MIN_LEFT / 2) {
      // 拖到很左边 → 收缩
      leftCollapsed.value = true
    } else {
      leftCollapsed.value = false
      leftW.value = Math.min(MAX_LEFT, Math.max(MIN_LEFT, w))
    }
  } else {
    // 右侧：距右边缘
    const w = window.innerWidth - e.clientX
    rightW.value = Math.min(MAX_RIGHT, Math.max(MIN_RIGHT, w))
  }
}

function onMouseUp() { dragTarget.value = null }

onMounted(() => {
  repo.fetchRepos()
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('mouseup',   onMouseUp)
})
onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('mouseup',   onMouseUp)
})

// ── Grid columns（收缩时用 COLLAPSED_W 而非 0，保证 toggle 可见）──────────────
const gridCols = computed(() => {
  const lw = leftCollapsed.value ? COLLAPSED_W : leftW.value
  return `${lw}px 1fr ${rightW.value}px`
})

// ── Nav ───────────────────────────────────────────────────────────────────────
const activeNav = ref('explorer')

function onNavChange(id: string) {
  activeNav.value = id
  if (id === 'history') analysis.setTab('history')
  // 收缩状态下切换 nav 自动展开
  if (leftCollapsed.value) leftCollapsed.value = false
}

function openFileFromSearch() {
  activeNav.value = 'explorer'
}

const NAV_LABEL: Record<string, string> = {
  explorer: '文件资源',
  search:   '搜索结果',
  bug:      'Bug 扫描',
  history:  '历史记录',
}

// icon strip 对应 nav id
const NAV_ICONS = [
  { id: 'explorer', icon: '◫' },
  { id: 'search',   icon: '⌕' },
  // { id: 'bug',      icon: '◉' },
  // { id: 'history',  icon: '⟲' },
]
</script>

<template>
  <div
    class="app-shell"
    :style="{ gridTemplateColumns: gridCols }"
    :class="{ 'cursor-col-resize select-none': dragTarget }"
  >
    <!-- ── Top bar ── -->
    <TopBar class="top-bar-row" />

    <!-- ════════════════ Left Sidebar ════════════════ -->
    <aside class="sidebar">

      <!-- ① Collapsed state：icon strip + expand toggle -->
      <template v-if="leftCollapsed">
        <div class="flex flex-col items-center pt-2 gap-1 w-full">
          <!-- Expand button (top, always visible) -->
          <button
            class="icon-btn text-cyan"
            title="展开侧边栏"
            @click="leftCollapsed = false"
          >›</button>
          <div class="w-5 border-t border-border-dim my-1" />
          <!-- Nav icons -->
          <button
            v-for="nav in NAV_ICONS" :key="nav.id"
            class="icon-btn"
            :class="activeNav === nav.id ? 'text-cyan' : 'text-text-muted hover:text-text-primary'"
            @click="onNavChange(nav.id)"
          >{{ nav.icon }}</button>
        </div>
      </template>

      <!-- ② Expanded state -->
      <template v-else>
        <!-- Header row: label + collapse button -->
        <div class="px-3 pt-3 pb-1.5 flex items-center justify-between flex-shrink-0">
          <span class="font-mono text-[10px] font-semibold uppercase tracking-widest text-text-muted">
            仓库
          </span>
          <button
            class="w-5 h-5 flex items-center justify-center rounded font-mono text-[11px]
                   text-text-muted hover:text-cyan hover:bg-cyan-dim transition-colors"
            title="收起侧边栏"
            @click="leftCollapsed = true"
          >‹</button>
        </div>

        <RepoSelector />

        <div class="border-t border-border-dim my-1 flex-shrink-0" />

        <div class="px-3 py-1 flex-shrink-0">
          <span class="font-mono text-[10px] font-semibold uppercase tracking-widest text-text-muted">
            {{ NAV_LABEL[activeNav] ?? activeNav }}
          </span>
        </div>
        <SidebarNav :active="activeNav" @change="onNavChange" />

        <div class="border-t border-border-dim mt-1 mb-1 flex-shrink-0" />

        <!-- Scrollable content -->
        <div class="flex-1 overflow-y-auto min-h-0">

          <template v-if="activeNav === 'explorer'">
            <div v-if="!repo.currentRepo"
              class="px-4 py-3 font-mono text-[11px] text-text-muted">
              请先添加仓库
            </div>
            <div v-else-if="!repo.fileTree.length"
              class="px-4 py-3 font-mono text-[11px] text-text-muted animate-pulse">
              文件树加载中...
            </div>
            <FileTree v-else :nodes="repo.fileTree" />
            <div
              v-if="repo.indexing && repo.indexProgress"
              class="mx-3 mb-2 px-3 py-2 rounded-md bg-amber/10 border border-amber/20
                     font-mono text-[11px] text-amber flex items-center gap-2"
            >
              <span class="animate-spin-slow flex-shrink-0">⟳</span>
              <span class="truncate">{{ repo.indexProgress.message }}</span>
            </div>
          </template>

          <template v-else-if="activeNav === 'search'">
            <SearchResults compact @open-file="openFileFromSearch" />
          </template>

          <template v-else-if="activeNav === 'bug'">
            <div class="p-4 flex flex-col gap-3">
              <p class="font-mono text-[11px] text-text-muted leading-relaxed">
                选择文件后点击工具栏「Bug」按钮分析当前文件。
              </p>
              <button
                class="py-2.5 rounded-lg font-mono text-[12px] font-semibold
                       border border-red-accent/40 text-red-accent hover:bg-red-accent/10
                       transition-colors flex items-center justify-center gap-2"
                :disabled="!repo.isIndexDone"
                :class="{ 'opacity-40 cursor-not-allowed': !repo.isIndexDone }"
                @click="analysis.setTab('bug')"
              >
                <span>◉</span> 打开 Bug 检测面板
              </button>
            </div>
          </template>

          <template v-else-if="activeNav === 'history'">
            <TabHistory />
          </template>

        </div>
      </template>

      <!-- Left resize handle（固定在 sidebar 右边缘，始终渲染）-->
      <div
        v-if="!leftCollapsed"
        class="left-drag-handle"
        :class="{ active: dragTarget === 'left' }"
        @mousedown="startDrag('left', $event)"
      />
    </aside>

    <!-- ════════════════ Main Content ════════════════ -->
    <main class="main-content">
      <SearchPanel />
      <div class="flex-1 flex flex-col overflow-hidden">
        <CodeToolbar />
        <CodeViewer />
      </div>
    </main>

    <!-- Right drag handle -->
    <div
      class="right-drag-handle"
      :class="{ active: dragTarget === 'right' }"
      @mousedown="startDrag('right', $event)"
    />

    <!-- ════════════════ Right Analysis Panel ════════════════ -->
    <AnalysisPanel />

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
  /* 不加 transition，拖拽时需要即时响应 */
}

.top-bar-row {
  grid-column: 1 / -1;
  grid-row: 1;
}

/* ── Left Sidebar ── */
.sidebar {
  grid-column: 1;
  grid-row: 2;
  position: relative;
  @apply flex flex-col bg-bg-base border-r border-border-dim overflow-hidden;
  /* overflow: visible 让 drag handle 超出边界可点击 */
  overflow: visible;
}

/* icon button in collapsed strip */
.icon-btn {
  @apply w-8 h-8 flex items-center justify-center rounded
         font-mono text-[13px] transition-colors;
}
.icon-btn:hover {
  @apply bg-bg-hover text-text-primary;
}

/* Left sidebar resize handle */
.left-drag-handle {
  position: absolute;
  top: 0;
  right: -3px;
  bottom: 0;
  width: 6px;
  cursor: col-resize;
  z-index: 25;
  background: transparent;
  transition: background 0.15s;
}
.left-drag-handle:hover,
.left-drag-handle.active {
  background: rgba(0, 212, 255, 0.25);
}

/* ── Main Content ── */
.main-content {
  grid-column: 2;
  grid-row: 2;
  @apply flex flex-col overflow-hidden bg-bg-deep;
}

/* ── Right drag handle（fixed，跟随 rightW）── */
.right-drag-handle {
  position: fixed;
  top: 52px;
  bottom: 28px;
  right: v-bind('rightW + "px"');
  width: 6px;
  transform: translateX(50%);
  cursor: col-resize;
  background: transparent;
  transition: background 0.15s;
  z-index: 30;
}
.right-drag-handle:hover,
.right-drag-handle.active {
  background: rgba(0, 212, 255, 0.25);
}

/* ── Analysis Panel ── */
:deep(.analysis-panel) {
  grid-column: 3;
  grid-row: 2;
}

:deep(.status-bar) {
  grid-column: 1 / -1;
  grid-row: 3;
}
</style>
