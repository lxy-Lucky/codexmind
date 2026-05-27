<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRepoStore }     from '@/stores/repoStore'
import { useAnalysisStore } from '@/stores/analysisStore'

const { t } = useI18n()

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
const analysis = useAnalysisStore()

// ── Left sidebar ──────────────────────────────────────────────────────────────
const leftW         = ref(260)
const leftCollapsed = ref(false)
const MIN_LEFT      = 180
const MAX_LEFT      = 480
const COLLAPSED_W   = 40

// ── Right panel ───────────────────────────────────────────────────────────────
const rightW    = ref(380)
const MIN_RIGHT = 280
const MAX_RIGHT = 640

// ── Drag ──────────────────────────────────────────────────────────────────────
type DragTarget = 'left' | 'right' | null
const dragTarget = ref<DragTarget>(null)

function startDrag(target: DragTarget, e: MouseEvent) {
  dragTarget.value = target
  e.preventDefault()
}
function onMouseMove(e: MouseEvent) {
  if (!dragTarget.value) return
  if (dragTarget.value === 'left') {
    const w = e.clientX
    if (w < MIN_LEFT / 2) { leftCollapsed.value = true }
    else { leftCollapsed.value = false; leftW.value = Math.min(MAX_LEFT, Math.max(MIN_LEFT, w)) }
  } else {
    rightW.value = Math.min(MAX_RIGHT, Math.max(MIN_RIGHT, window.innerWidth - e.clientX))
  }
}
function onMouseUp() { dragTarget.value = null }

onMounted(() => {
  repo.fetchRepos()
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('mouseup', onMouseUp)
})
onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('mouseup', onMouseUp)
})

// ── Grid ──────────────────────────────────────────────────────────────────────
const gridCols = computed(() => {
  const lw = leftCollapsed.value ? COLLAPSED_W : leftW.value
  return `${lw}px 1fr ${rightW.value}px`
})
const leftHandlePos  = computed(() => (leftCollapsed.value ? COLLAPSED_W : leftW.value) + 'px')
const rightHandlePos = computed(() => rightW.value + 'px')

// ── Nav ───────────────────────────────────────────────────────────────────────
const activeNav = ref('explorer')

function onNavChange(id: string) {
  activeNav.value = id
  if (id === 'history') analysis.setTab('history')
  if (leftCollapsed.value) leftCollapsed.value = false
}
function onSearched() {
  activeNav.value = 'search'
  if (leftCollapsed.value) leftCollapsed.value = false
}
function openFileFromSearch() { activeNav.value = 'explorer' }

const NAV_LABEL_KEY: Record<string, string> = {
  explorer: 'nav.explorer', search: 'nav.search', bug: 'nav.bug', history: 'nav.history',
}
const NAV_ICONS = [
  { id: 'explorer', icon: '◫' },
  { id: 'search',   icon: '⌕' },
  { id: 'bug',      icon: '◉' },
  { id: 'history',  icon: '⟲' },
]
</script>

<template>
  <div
    class="app-shell"
    :style="{ gridTemplateColumns: gridCols }"
    :class="{ 'cursor-col-resize select-none': dragTarget }"
  >
    <TopBar class="top-bar-row" />

    <!-- ════ Left Sidebar ════ -->
    <aside class="sidebar">

      <!-- Collapsed icon strip -->
      <template v-if="leftCollapsed">
        <div class="flex flex-col items-center pt-2 gap-1 w-full overflow-hidden">
          <button class="icon-btn text-cyan" :title="t('nav.expand')" @click="leftCollapsed = false">›</button>
          <div class="w-5 border-t border-border-dim my-1" />
          <button
            v-for="nav in NAV_ICONS" :key="nav.id"
            class="icon-btn"
            :class="activeNav === nav.id ? 'text-cyan' : 'text-text-muted hover:text-text-primary'"
            @click="onNavChange(nav.id)"
          >{{ nav.icon }}</button>
        </div>
      </template>

      <!-- Expanded：整体一个滚动容器 -->
      <template v-else>
        <!-- Fixed header: 标题 + 收起按钮 -->
        <div class="px-3 pt-3 pb-1 flex items-center justify-between flex-shrink-0">
          <span class="font-mono text-[10px] font-semibold uppercase tracking-widest text-text-muted">{{ t('nav.section') }}</span>
          <button
            class="w-5 h-5 flex items-center justify-center rounded font-mono text-[11px]
                   text-text-muted hover:text-cyan hover:bg-cyan-dim transition-colors"
            @click="leftCollapsed = true"
          >‹</button>
        </div>

        <!-- 整个内容区：一个统一的滚动容器 -->
        <!-- 这样仓库列表多了、文件树深了，都能正常滚动 -->
        <div class="flex-1 overflow-y-auto overflow-x-hidden min-h-0 flex flex-col">

          <!-- 仓库区域 -->
          <RepoSelector />

          <div class="border-t border-border-dim my-1 flex-shrink-0" />

          <!-- Nav label -->
          <div class="px-3 py-1 flex-shrink-0">
            <span class="font-mono text-[10px] font-semibold uppercase tracking-widest text-text-muted">
              {{ NAV_LABEL_KEY[activeNav] ? t(NAV_LABEL_KEY[activeNav]) : activeNav }}
            </span>
          </div>
          <div class="flex-shrink-0">
            <SidebarNav :active="activeNav" @change="onNavChange" />
          </div>
          <div class="border-t border-border-dim my-1 flex-shrink-0" />

          <!-- Nav content：不再嵌套滚动容器，撑高由外层容器滚 -->
          <div class="flex-1 min-h-0 overflow-x-auto">

            <template v-if="activeNav === 'explorer'">
              <div v-if="!repo.currentRepo"
                class="px-4 py-3 font-mono text-[11px] text-text-muted">
                {{ t('repo.noRepo') }}
              </div>
              <div v-else-if="!repo.fileTree.length"
                class="px-4 py-3 font-mono text-[11px] text-text-muted animate-pulse">
                {{ t('repo.treeLoading') }}
              </div>
              <!-- min-w-max 让文件树横向撑开，外层 overflow-x-auto 提供横滚 -->
              <div v-else class="min-w-max pb-2">
                <FileTree :nodes="repo.fileTree" />
              </div>
              <div
                v-if="repo.indexing && repo.indexProgress"
                class="mx-3 my-2 px-3 py-2 rounded-md bg-amber/10 border border-amber/20
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
                  {{ t('repo.bugTip') }}
                </p>
                <button
                  class="py-2.5 rounded-lg font-mono text-[12px] font-semibold
                         border border-red-accent/40 text-red-accent hover:bg-red-accent/10
                         transition-colors flex items-center justify-center gap-2"
                  :disabled="!repo.isIndexDone"
                  :class="{ 'opacity-40 cursor-not-allowed': !repo.isIndexDone }"
                  @click="analysis.setTab('insight')"
                >
                  <span>◉</span> {{ t('repo.openBugPanel') }}
                </button>
              </div>
            </template>

            <template v-else-if="activeNav === 'history'">
              <TabHistory />
            </template>

          </div>
        </div>
      </template>

      <!-- Left drag handle：fixed，不受 overflow:hidden 裁剪 -->
      <div
        class="left-drag-handle"
        :class="{ active: dragTarget === 'left' }"
        @mousedown="startDrag('left', $event)"
      />
    </aside>

    <!-- ════ Main Content ════ -->
    <main class="main-content">
      <SearchPanel @searched="onSearched" />
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

    <AnalysisPanel />
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
}
.top-bar-row { grid-column: 1 / -1; grid-row: 1; }

.sidebar {
  grid-column: 1;
  grid-row: 2;
  position: relative;
  /* overflow:hidden — 滚动由内层 flex-1 子容器负责 */
  @apply flex flex-col bg-bg-base border-r border-border-dim overflow-hidden;
}
.icon-btn {
  @apply w-8 h-8 flex items-center justify-center rounded font-mono text-[13px] transition-colors;
}
.icon-btn:hover { @apply bg-bg-hover text-text-primary; }

/* fixed 定位，脱离 sidebar overflow:hidden 裁剪 */
.left-drag-handle {
  position: fixed;
  top: 52px;
  bottom: 28px;
  left: v-bind(leftHandlePos);
  width: 6px;
  transform: translateX(-50%);
  cursor: col-resize;
  z-index: 25;
  background: transparent;
  transition: background 0.15s;
}
.left-drag-handle:hover,
.left-drag-handle.active { background: rgba(0,212,255,0.25); }

.main-content {
  grid-column: 2;
  grid-row: 2;
  @apply flex flex-col overflow-hidden bg-bg-deep;
}

.right-drag-handle {
  position: fixed;
  top: 52px;
  bottom: 28px;
  right: v-bind(rightHandlePos);
  width: 6px;
  transform: translateX(50%);
  cursor: col-resize;
  background: transparent;
  transition: background 0.15s;
  z-index: 30;
}
.right-drag-handle:hover,
.right-drag-handle.active { background: rgba(0,212,255,0.25); }

:deep(.analysis-panel) { grid-column: 3; grid-row: 2; }
:deep(.status-bar)      { grid-column: 1 / -1; grid-row: 3; }
</style>
