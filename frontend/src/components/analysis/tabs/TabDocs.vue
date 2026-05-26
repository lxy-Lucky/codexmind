<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { marked } from 'marked'
import { useAnalysisStore } from '@/stores/analysisStore'
import { useEditorStore }   from '@/stores/editorStore'

const { t } = useI18n()
const analysis = useAnalysisStore()
const editor   = useEditorStore()

const rendered = computed(() =>
  analysis.docsText ? marked.parse(analysis.docsText) as string : ''
)

const progressPct = computed(() => {
  const p = analysis.docsProgress
  if (!p || p.total === 0) return 0
  return Math.round((p.current / p.total) * 100)
})

function downloadMd() {
  if (!analysis.docsText) return
  const raw   = editor.currentFile?.path ?? 'document'
  const base  = raw.replace(/\\/g, '/').split('/').pop()?.replace(/\.[^.]+$/, '') ?? 'docs'
  const blob  = new Blob([analysis.docsText], { type: 'text/markdown; charset=utf-8' })
  const url   = URL.createObjectURL(blob)
  const a     = document.createElement('a')
  a.href      = url
  a.download  = `${base}.md`
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="h-full flex flex-col overflow-hidden">

    <!-- 操作栏 -->
    <div class="flex-shrink-0 flex items-center gap-2 px-4 py-2.5 border-b border-border-dim">
      <button
        class="font-mono text-[10px] px-2.5 py-1 rounded border transition-all"
        :class="analysis.docsStreaming
          ? 'border-border-dim text-text-muted cursor-not-allowed opacity-50'
          : 'border-cyan/40 text-cyan hover:bg-cyan/10 active:scale-95'"
        :disabled="analysis.docsStreaming"
        @click="analysis.analyze('docs')"
      >
        ◆ {{ t('docs.selectionBtn') }}
      </button>

      <button
        class="font-mono text-[10px] px-2.5 py-1 rounded border transition-all"
        :class="analysis.docsStreaming
          ? 'border-border-dim text-text-muted cursor-not-allowed opacity-50'
          : 'border-purple/40 text-purple hover:bg-purple/10 active:scale-95'"
        :disabled="analysis.docsStreaming"
        @click="analysis.generateFileDocs()"
      >
        ⟳ {{ t('docs.fileBtn') }}
      </button>

      <button
        v-if="analysis.docsText && !analysis.docsStreaming"
        class="ml-auto font-mono text-[10px] px-2.5 py-1 rounded border
               border-green-accent/40 text-green-accent hover:bg-green-accent/10
               transition-all active:scale-95"
        @click="downloadMd"
      >
        ↓ {{ t('docs.download') }}
      </button>
    </div>

    <!-- 进度条（整文件批处理时显示） -->
    <Transition name="slide-down">
      <div
        v-if="analysis.docsProgress"
        class="flex-shrink-0 px-4 py-2 bg-bg-surface border-b border-border-dim/50"
      >
        <div class="flex items-center justify-between mb-1.5">
          <span class="font-mono text-[10px] text-text-muted truncate max-w-[70%]">
            {{ t('docs.processing', { name: analysis.docsProgress.className }) }}
          </span>
          <span class="font-mono text-[10px] text-purple font-semibold">
            {{ analysis.docsProgress.current }} / {{ analysis.docsProgress.total }}
          </span>
        </div>
        <div class="h-1 bg-bg-elevated rounded-full overflow-hidden">
          <div
            class="h-full rounded-full transition-all duration-500"
            style="background: linear-gradient(90deg, #a55eea, #00d4ff)"
            :style="{ width: `${progressPct}%` }"
          />
        </div>
      </div>
    </Transition>

    <!-- 内容区 -->
    <div class="flex-1 overflow-y-auto">

      <!-- 有内容 -->
      <div v-if="analysis.docsText" class="p-4">
        <div class="bg-bg-surface border border-border-dim rounded-lg p-4 animate-fade-in">
          <div class="flex items-center gap-2 mb-3">
            <span class="text-purple text-[10px] font-mono font-semibold tracking-widest">
              📄 {{ t('analysis.tabs.docs') }}
            </span>
            <span
              v-if="analysis.docsStreaming"
              class="font-mono text-[10px] text-text-muted animate-pulse"
            >{{ t('docs.generating') }}</span>
            <span v-else class="font-mono text-[10px] text-green-accent">{{ t('common.done') }}</span>
          </div>
          <div
            class="prose prose-sm prose-dark max-w-none"
            :class="{ 'streaming-cursor': analysis.docsStreaming }"
            v-html="rendered || '&nbsp;'"
          />
        </div>
      </div>

      <!-- 纯流式等待（还没内容） -->
      <div
        v-else-if="analysis.docsStreaming"
        class="p-6 flex items-center gap-2 text-text-muted font-mono text-[12px]"
      >
        <span
          v-for="i in 3" :key="i"
          class="w-1.5 h-1.5 rounded-full bg-purple/60 animate-pulse-dot"
          :style="{ animationDelay: `${i * 0.15}s` }"
        />
        <span>{{ t('docs.generating') }}</span>
      </div>

      <!-- 错误 -->
      <div
        v-else-if="analysis.error"
        class="p-4 font-mono text-[12px] text-red-accent"
      >
        ⚠ {{ analysis.error }}
      </div>

      <!-- 空状态 -->
      <div v-else class="p-8 text-center text-text-muted font-mono">
        <div class="text-4xl mb-3 opacity-20">📄</div>
        <div class="text-[12px] mb-1">{{ t('docs.emptyTitle') }}</div>
        <div class="text-[10px] opacity-60 leading-relaxed max-w-[260px] mx-auto">
          {{ t('docs.emptyHint') }}
        </div>
      </div>

    </div>
  </div>
</template>

<style scoped>
.slide-down-enter-active,
.slide-down-leave-active {
  transition: all 0.2s ease;
}
.slide-down-enter-from,
.slide-down-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
