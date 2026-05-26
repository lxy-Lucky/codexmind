<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useEditorStore }   from '@/stores/editorStore'
import { useAnalysisStore } from '@/stores/analysisStore'
import type { AnalysisMode } from '@/types'

const { t } = useI18n()
const editor   = useEditorStore()
const analysis = useAnalysisStore()

function triggerAnalysis(mode: AnalysisMode) {
  analysis.analyze(mode)
}

function copyPath() {
  if (editor.currentFile?.path) {
    navigator.clipboard.writeText(editor.currentFile.path)
  }
}
</script>

<template>
  <div class="flex items-center justify-between px-5 py-2
              bg-bg-base border-b border-border-dim flex-shrink-0 min-h-[38px]">
    <div v-if="editor.currentFile" class="flex items-center gap-1.5 font-mono text-[11px] text-text-muted">
      <template v-for="(seg, i) in editor.currentFile.path.split('/')" :key="i">
        <span v-if="i > 0" class="text-border-bright">›</span>
        <span
          :class="i === editor.currentFile.path.split('/').length - 1
            ? 'text-text-primary font-medium' : 'hover:text-text-secondary cursor-pointer'"
        >{{ seg }}</span>
      </template>
      <button
        class="ml-2 opacity-0 hover:opacity-100 text-text-muted hover:text-text-primary transition-opacity"
        :title="t('toolbar.copyPath')"
        @click="copyPath"
      >⎘</button>
    </div>
    <div v-else class="font-mono text-[11px] text-text-muted">{{ t('toolbar.noFile') }}</div>

    <div class="flex items-center gap-1.5">
      <span v-if="editor.currentFile" class="font-mono text-[10px] text-text-muted mr-2">
        {{ t('toolbar.lineSuffix', { n: editor.currentFile.line_count }) }} · {{ editor.currentFile.language }}
      </span>

      <button class="toolbar-btn" :title="t('toolbar.summaryTitle')" @click="triggerAnalysis('summary')">
        ◆ {{ t('toolbar.summaryShort') }}
      </button>
      <button class="toolbar-btn danger" :title="t('toolbar.bugTitle')" @click="triggerAnalysis('bug')">
        ◉ {{ t('toolbar.bugShort') }}
      </button>
      <!-- 依赖图暂时隐藏，待路径追踪功能完善后恢复
      <button class="toolbar-btn purple" :title="t('toolbar.depsTitle')" @click="analysis.openDepsGraph()">
        ⟳ {{ t('toolbar.depsShort') }}
      </button>
      -->
    </div>
  </div>
</template>

<style scoped>
.toolbar-btn {
  @apply flex items-center gap-1 px-2.5 py-1 rounded font-mono text-[11px] font-medium
         border border-border-dim text-text-muted bg-bg-surface
         hover:border-cyan hover:text-cyan hover:bg-cyan-dim transition-all;
}
.toolbar-btn.danger { @apply hover:border-red-accent hover:text-red-accent hover:bg-red-accent/10; }
.toolbar-btn.purple { @apply hover:border-purple hover:text-purple hover:bg-purple/10; }
</style>
