<script setup lang="ts">
import { ref } from 'vue'
import { useMonaco }      from '@/composables/useMonaco'
import { useEditorStore } from '@/stores/editorStore'

const containerRef = ref<HTMLElement | null>(null)
const editor = useEditorStore()

useMonaco(containerRef)
</script>

<template>
  <div class="flex-1 relative overflow-hidden">
    <!-- Monaco mount point -->
    <div ref="containerRef" class="absolute inset-0" />

    <!-- Empty state -->
    <div
      v-if="!editor.currentFile && !editor.loading"
      class="absolute inset-0 flex flex-col items-center justify-center text-text-muted pointer-events-none"
    >
      <div class="text-5xl mb-4 opacity-20">◫</div>
      <p class="font-mono text-[13px] opacity-40">从左侧文件树选择文件</p>
      <p class="font-mono text-[11px] opacity-25 mt-1">或通过语义搜索定位代码</p>
    </div>

    <!-- Loading overlay -->
    <div
      v-if="editor.loading"
      class="absolute inset-0 bg-bg-deep/60 flex items-center justify-center"
    >
      <span class="font-mono text-[12px] text-cyan animate-pulse">加载中...</span>
    </div>
  </div>
</template>
