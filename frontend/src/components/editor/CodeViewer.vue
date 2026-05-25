<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMonaco }      from '@/composables/useMonaco'
import { useEditorStore } from '@/stores/editorStore'

const { t } = useI18n()
const containerRef = ref<HTMLElement | null>(null)
const editor = useEditorStore()

useMonaco(containerRef)
</script>

<template>
  <div class="flex-1 relative overflow-hidden">
    <div ref="containerRef" class="absolute inset-0" />

    <div
      v-if="!editor.currentFile && !editor.loading"
      class="absolute inset-0 flex flex-col items-center justify-center text-text-muted pointer-events-none"
    >
      <div class="text-5xl mb-4 opacity-20">◫</div>
      <p class="font-mono text-[13px] opacity-40">{{ t('editor.emptyPrimary') }}</p>
      <p class="font-mono text-[11px] opacity-25 mt-1">{{ t('editor.emptySecondary') }}</p>
    </div>

    <div
      v-if="editor.loading"
      class="absolute inset-0 bg-bg-deep/60 flex items-center justify-center"
    >
      <span class="font-mono text-[12px] text-cyan animate-pulse">{{ t('editor.loading') }}</span>
    </div>
  </div>
</template>
