<script setup lang="ts">
import { ref, watch, nextTick, computed } from 'vue'
import { marked } from 'marked'
import { useAnalysisStore } from '@/stores/analysisStore'
import { useEditorStore }   from '@/stores/editorStore'

const analysis = useAnalysisStore()
const editor   = useEditorStore()

const inputText  = ref('')
const scrollRef  = ref<HTMLElement | null>(null)
const inputRef   = ref<HTMLTextAreaElement | null>(null)
const rows       = ref(1)

// 代码上下文摘要（显示在顶部）
const codeContext = computed(() => {
  const f = editor.currentFile
  const h = editor.highlightLines
  if (!f) return null
  const fileName = f.path.split('/').pop()
  const lineInfo = h ? `L${h[0]}–${h[1]}` : `${f.line_count} 行`
  return { fileName, lineInfo, language: f.language }
})

// 渲染 markdown
function renderMd(text: string) {
  return marked.parse(text) as string
}

// 滚动到底部
async function scrollToBottom() {
  await nextTick()
  if (scrollRef.value) {
    scrollRef.value.scrollTop = scrollRef.value.scrollHeight
  }
}

// 新消息时自动滚动
watch(() => analysis.chatHistory.length, scrollToBottom)
watch(() => {
  const last = analysis.chatHistory[analysis.chatHistory.length - 1]
  return last?.role === 'assistant' ? last.content.length : 0
}, scrollToBottom)

// 自动调整输入框高度
function onInput() {
  const lines = (inputText.value.match(/\n/g) ?? []).length + 1
  rows.value = Math.min(5, Math.max(1, lines))
}

async function submit() {
  const msg = inputText.value.trim()
  if (!msg || analysis.chatStreaming) return
  inputText.value = ''
  rows.value = 1
  await analysis.sendChat(msg)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submit()
  }
}

// 快捷提问
const QUICK_QUESTIONS = [
  '这段代码是做什么的？',
  '有没有潜在的性能问题？',
  '如何优化这段代码？',
  '有哪些边界情况需要注意？',
]
</script>

<template>
  <div class="flex flex-col h-full">

    <!-- ── 代码上下文提示条 ── -->
    <div
      v-if="codeContext"
      class="flex-shrink-0 flex items-center gap-2 px-4 py-2
             bg-bg-surface border-b border-border-dim"
    >
      <span class="font-mono text-[9px] text-text-muted">上下文</span>
      <span class="font-mono text-[10px] text-cyan font-medium truncate">
        {{ codeContext.fileName }}
      </span>
      <span class="font-mono text-[10px] text-text-muted">{{ codeContext.lineInfo }}</span>
      <span
        class="ml-auto font-mono text-[9px] px-1.5 py-0.5 rounded
               bg-bg-elevated border border-border-dim text-text-muted"
      >
        {{ codeContext.language }}
      </span>
    </div>
    <div
      v-else
      class="flex-shrink-0 flex items-center gap-2 px-4 py-2
             bg-bg-surface border-b border-border-dim"
    >
      <span class="font-mono text-[10px] text-amber">⚠ 请先在编辑器中打开文件</span>
    </div>

    <!-- ── 对话区 ── -->
    <div ref="scrollRef" class="flex-1 overflow-y-auto min-h-0 px-3 py-3 flex flex-col gap-3">

      <!-- 空状态 -->
      <div v-if="!analysis.chatHistory.length"
        class="flex flex-col items-center justify-center h-full gap-3 py-8">
        <div class="text-3xl opacity-20">💬</div>
        <p class="font-mono text-[11px] text-text-muted text-center leading-relaxed">
          针对当前代码提问<br>支持多轮对话
        </p>
        <!-- 快捷提问 -->
        <div class="flex flex-col gap-1.5 w-full mt-2">
          <button
            v-for="q in QUICK_QUESTIONS" :key="q"
            class="text-left px-3 py-2 rounded-lg font-mono text-[11px]
                   border border-border-dim text-text-muted bg-bg-surface
                   hover:border-cyan/40 hover:text-cyan hover:bg-cyan-dim
                   transition-all"
            @click="inputText = q; inputRef?.focus()"
          >
            {{ q }}
          </button>
        </div>
      </div>

      <!-- 消息气泡 -->
      <template v-else>
        <div
          v-for="(msg, idx) in analysis.chatHistory"
          :key="idx"
          class="flex animate-fade-in"
          :class="msg.role === 'user' ? 'justify-end' : 'justify-start'"
        >
          <!-- User 气泡 -->
          <div
            v-if="msg.role === 'user'"
            class="max-w-[85%] px-3.5 py-2.5 rounded-2xl rounded-tr-sm
                   bg-cyan/15 border border-cyan/25
                   font-mono text-[12px] text-text-primary leading-relaxed
                   whitespace-pre-wrap"
          >
            {{ msg.content }}
          </div>

          <!-- AI 气泡 -->
          <div v-else class="max-w-[95%] flex items-start gap-2">
            <!-- AI 头像 -->
            <div
              class="w-6 h-6 rounded-full flex-shrink-0 flex items-center justify-center
                     font-mono text-[9px] font-bold text-bg-deep mt-0.5"
              style="background: linear-gradient(135deg,#00d4ff,#a55eea)"
            >AI</div>

            <div class="flex-1 min-w-0">
              <!-- 流式输出中 -->
              <div
                v-if="msg.streaming && !msg.content"
                class="flex items-center gap-1.5 py-2"
              >
                <span
                  v-for="i in 3" :key="i"
                  class="w-1.5 h-1.5 rounded-full bg-cyan/60 animate-pulse-dot"
                  :style="{ animationDelay: `${i * 0.15}s` }"
                />
              </div>

              <!-- 错误消息 -->
              <div
                v-else-if="msg.error"
                class="px-3 py-2 rounded-xl rounded-tl-sm bg-red-accent/10
                       border border-red-accent/30 font-mono text-[11px] text-red-accent"
              >
                {{ msg.content }}
              </div>

              <!-- 正常消息（Markdown 渲染） -->
              <div
                v-else
                class="prose prose-sm prose-dark max-w-none px-3.5 py-2.5
                       rounded-2xl rounded-tl-sm bg-bg-surface border border-border-dim"
                :class="{ 'streaming-cursor': msg.streaming && msg.content }"
                v-html="renderMd(msg.content) || '&nbsp;'"
              />
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- ── 操作栏（清空 + 停止） ── -->
    <div
      v-if="analysis.chatHistory.length"
      class="flex-shrink-0 flex items-center gap-2 px-4 py-1.5 border-t border-border-dim"
    >
      <button
        class="font-mono text-[10px] text-text-muted hover:text-text-secondary transition-colors"
        @click="analysis.clearChat()"
      >
        清空对话
      </button>
      <button
        v-if="analysis.chatStreaming"
        class="ml-auto font-mono text-[10px] text-red-accent hover:opacity-70
               transition-opacity flex items-center gap-1"
        @click="analysis.abort()"
      >
        <span class="animate-pulse-dot">●</span> 停止
      </button>
      <span v-else class="ml-auto font-mono text-[10px] text-text-muted">
        {{ analysis.chatHistory.filter(m => m.role === 'user').length }} 条问题
      </span>
    </div>

    <!-- ── 输入框 ── -->
    <div class="flex-shrink-0 px-3 pb-3 pt-1.5">
      <div
        class="flex items-end gap-2 bg-bg-surface border rounded-xl px-3 py-2 transition-all"
        :class="analysis.chatStreaming
          ? 'border-border-dim opacity-70'
          : 'border-border-dim focus-within:border-cyan focus-within:shadow-[0_0_0_3px_rgba(0,212,255,0.1)]'"
      >
        <textarea
          ref="inputRef"
          v-model="inputText"
          :rows="rows"
          placeholder="输入问题... (Enter 发送 / Shift+Enter 换行)"
          class="flex-1 bg-transparent font-mono text-[12px] text-text-primary
                 placeholder-text-muted outline-none resize-none leading-relaxed
                 min-h-[24px] max-h-[120px]"
          :disabled="analysis.chatStreaming"
          @input="onInput"
          @keydown="onKeydown"
        />

        <!-- 发送按钮 -->
        <button
          class="flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center
                 font-mono text-[12px] transition-all"
          :class="inputText.trim() && !analysis.chatStreaming
            ? 'bg-cyan text-bg-deep hover:opacity-90 hover:-translate-y-px'
            : 'bg-bg-elevated text-text-muted cursor-not-allowed'"
          :disabled="!inputText.trim() || analysis.chatStreaming"
          @click="submit"
        >
          <span v-if="analysis.chatStreaming" class="animate-spin-slow text-[10px]">⟳</span>
          <span v-else>↑</span>
        </button>
      </div>

      <div class="font-mono text-[9px] text-text-muted mt-1 px-1 flex justify-between">
        <span>Enter 发送 · Shift+Enter 换行</span>
        <span v-if="inputText.length > 200" class="text-amber">{{ inputText.length }} 字</span>
      </div>
    </div>

  </div>
</template>
