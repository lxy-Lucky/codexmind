<script setup lang="ts">
import { ref } from 'vue'
import { useEditorStore } from '@/stores/editorStore'
import { useAnalysisStore } from '@/stores/analysisStore'
import type { FileNode } from '@/types'

const props = defineProps<{ nodes: FileNode[]; depth?: number }>()
const editor   = useEditorStore()
const analysis = useAnalysisStore()

const expanded = ref<Record<string, boolean>>({})

const LANG_COLOR: Record<string, string> = {
  java: 'text-[#b07219]', python: 'text-[#3572a5]',
  typescript: 'text-[#3178c6]', javascript: 'text-[#f1e05a]',
  go: 'text-[#00add8]', rust: 'text-[#dea584]',
  kotlin: 'text-[#a97bff]', vue: 'text-[#41b883]',
}
const LANG_ICON: Record<string, string> = {
  java: 'J', python: 'Py', typescript: 'TS', javascript: 'JS',
  go: 'Go', rust: 'Rs', kotlin: 'Kt', vue: 'V',
  markdown: '≡', json: '{}', yaml: '—', sql: '⊡',
}

function toggle(path: string) {
  expanded.value[path] = !expanded.value[path]
}

async function openFile(node: FileNode) {
  if (node.type !== 'file') return
  await editor.openFile(node.path)
  // 打开新文件时清空分析结果
  analysis.$patch({ streamingText: '', bugItems: [], hasResult: false, error: null })
}

function isActive(node: FileNode) {
  return editor.currentFile?.path === node.path
}
</script>

<template>
  <div :style="{ paddingLeft: `${(depth ?? 0) * 10}px` }">
    <template v-for="node in nodes" :key="node.path">
      <!-- Directory -->
      <div
        v-if="node.type === 'dir'"
        class="tree-row text-text-muted hover:text-text-primary hover:bg-bg-hover cursor-pointer"
        @click="toggle(node.path)"
      >
        <span class="text-amber text-[11px]">{{ expanded[node.path] ? '▾' : '▸' }}</span>
        <span class="text-amber text-[11px]">📁</span>
        <span class="font-mono text-[12px]">{{ node.name }}</span>
      </div>

      <!-- Directory children -->
      <FileTree
        v-if="node.type === 'dir' && expanded[node.path] && node.children"
        :nodes="node.children"
        :depth="(depth ?? 0) + 1"
      />

      <!-- File -->
      <div
        v-if="node.type === 'file'"
        class="tree-row cursor-pointer"
        :class="isActive(node)
          ? 'bg-cyan-dim text-cyan'
          : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'"
        @click="openFile(node)"
      >
        <span
          class="font-mono text-[10px] font-bold w-5 text-center"
          :class="LANG_COLOR[node.language ?? ''] ?? 'text-text-muted'"
        >
          {{ LANG_ICON[node.language ?? ''] ?? '·' }}
        </span>
        <span class="font-mono text-[12px] truncate">{{ node.name }}</span>
        <span v-if="node.size" class="ml-auto font-mono text-[10px] text-text-muted flex-shrink-0">
          {{ node.size > 1024 ? `${(node.size/1024).toFixed(0)}k` : node.size }}
        </span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.tree-row {
  @apply flex items-center gap-2 py-1 px-2.5 rounded transition-all select-none;
}
</style>
