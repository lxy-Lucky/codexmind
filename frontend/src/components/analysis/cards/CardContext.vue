<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAnalysisStore } from '@/stores/analysisStore'
import { useEditorStore }   from '@/stores/editorStore'

const { t } = useI18n()
const analysis = useAnalysisStore()
const editor   = useEditorStore()

const ctx = computed(() => {
  const f = editor.currentFile
  if (!f) return null
  const fileName = f.path.split('/').pop()
  const h = editor.highlightLines
  const lineInfo = h ? `L${h[0]}–${h[1]}` : ''
  return { fileName, lineInfo, language: f.language }
})

const callers = computed(() => (analysis as any).currentCallers || [])
const callees = computed(() => (analysis as any).currentCallees || [])
</script>

<template>
  <div v-if="ctx" class="mx-2.5 mt-2.5 p-2.5 rounded-lg
    bg-gradient-to-br from-cyan/5 to-purple/5
    border border-border-dim">

    <div class="flex items-center gap-1.5 font-mono text-[10px] text-cyan">
      <span>{{ ctx.fileName }}</span>
      <span v-if="ctx.lineInfo"
        class="px-1.5 py-0.5 rounded bg-cyan/10 text-[9px]">{{ ctx.lineInfo }}</span>
      <span class="px-1.5 py-0.5 rounded bg-bg-elevated text-text-muted text-[9px] ml-auto">
        {{ ctx.language }}
      </span>
    </div>

    <div v-if="analysis.currentSymbolId"
      class="mt-1 font-mono text-[13px] text-text-primary font-semibold truncate">
      {{ analysis.currentClassName ? `${analysis.currentClassName}.` : '' }}{{ t('insight.selectedMethod') }}
    </div>

    <div v-if="callers.length || callees.length"
      class="mt-2 flex gap-3 font-mono text-[10px]">
      <div v-if="callers.length" class="flex-1 min-w-0">
        <div class="text-text-muted text-[9px] mb-1">▲ CALLERS</div>
        <div v-for="c in callers.slice(0, 3)" :key="c.method_name"
          class="text-amber/80 truncate">{{ c.class_name ? `${c.class_name}.` : '' }}{{ c.method_name }}</div>
      </div>
      <div v-if="callees.length" class="flex-1 min-w-0">
        <div class="text-text-muted text-[9px] mb-1">▼ CALLEES</div>
        <div v-for="c in callees.slice(0, 3)" :key="c.method_name"
          class="text-cyan/80 truncate">{{ c.class_name ? `${c.class_name}.` : '' }}{{ c.method_name }}</div>
      </div>
    </div>
  </div>
</template>
