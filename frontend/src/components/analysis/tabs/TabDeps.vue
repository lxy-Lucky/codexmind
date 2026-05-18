<script setup lang="ts">
import { watch, ref, nextTick } from 'vue'
import { useAnalysisStore } from '@/stores/analysisStore'

const analysis = useAnalysisStore()
const mermaidEl = ref<HTMLElement | null>(null)
const renderError = ref('')

// 提取 mermaid 代码块内容
function extractMermaid(raw: string): string {
  const m = raw.match(/```mermaid\n([\s\S]*?)```/)
  return m ? m[1].trim() : raw.trim()
}

// 动态加载 mermaid（避免影响首屏）
let mermaidLoaded = false
async function renderMermaid() {
  if (!mermaidEl.value || !analysis.streamingText || analysis.streaming) return
  renderError.value = ''
  try {
    if (!mermaidLoaded) {
      const m = await import('mermaid')
      m.default.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'loose' })
      mermaidLoaded = true
    }
    const { default: mermaid } = await import('mermaid')
    const code = extractMermaid(analysis.streamingText)
    mermaidEl.value.removeAttribute('data-processed')
    mermaidEl.value.textContent = code
    await mermaid.run({ nodes: [mermaidEl.value] })
  } catch (e: any) {
    renderError.value = e.message
  }
}

watch(() => [analysis.streaming, analysis.streamingText], async ([streaming]) => {
  if (!streaming) {
    await nextTick()
    renderMermaid()
  }
})
</script>

<template>
  <div v-if="analysis.streaming" class="p-8 flex flex-col items-center gap-3 text-text-muted">
    <span class="text-2xl animate-spin-slow inline-block">⟳</span>
    <span class="font-mono text-[12px]">生成依赖图...</span>
  </div>

  <div v-else-if="analysis.hasResult" class="p-4">
    <div v-if="renderError" class="font-mono text-[11px] text-red-accent mb-2">
      Mermaid 渲染失败: {{ renderError }}
    </div>

    <!-- Mermaid 渲染容器 -->
    <div
      ref="mermaidEl"
      class="mermaid bg-bg-surface rounded-lg p-4 overflow-auto
             [&>svg]:max-w-full [&>svg]:text-text-primary"
    />

    <!-- Raw fallback -->
    <details class="mt-3">
      <summary class="font-mono text-[10px] text-text-muted cursor-pointer hover:text-text-secondary">
        查看原始 Mermaid 代码
      </summary>
      <pre class="mt-2 font-mono text-[11px] text-text-code bg-bg-deep rounded p-3 overflow-x-auto">{{ analysis.streamingText }}</pre>
    </details>
  </div>

  <div v-else-if="analysis.error" class="p-4 font-mono text-[12px] text-red-accent">
    ⚠ {{ analysis.error }}
  </div>

  <div v-else class="p-8 text-center text-text-muted font-mono text-[12px]">
    <div class="text-3xl mb-2 opacity-20">⟳</div>
    选中代码后点击工具栏「依赖」按钮
  </div>
</template>
