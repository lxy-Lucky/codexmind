<script setup lang="ts">
import { ref, watch, nextTick, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { marked } from 'marked'
import { useAnalysisStore } from '@/stores/analysisStore'
import { useEditorStore }   from '@/stores/editorStore'

const { t } = useI18n()
const analysis = useAnalysisStore()
const editor   = useEditorStore()

const inputText = ref('')
const scrollRef = ref<HTMLElement | null>(null)
const inputRef  = ref<HTMLTextAreaElement | null>(null)
const rows      = ref(1)

const codeContext = computed(() => {
  const f = editor.currentFile
  const h = editor.highlightLines
  if (!f) return null
  const fileName = f.path.split('/').pop()
  const lineInfo = h ? `L${h[0]}–${h[1]}` : t('toolbar.lineSuffix', { n: f.line_count })
  return { fileName, lineInfo, language: f.language }
})

const callContext = computed(() => {
  const callers = analysis.currentCallers || []
  const callees = analysis.currentCallees || []
  if (!callers.length && !callees.length) return null
  return { callers, callees }
})

function renderMd(text: string) {
  return marked.parse(text) as string
}

async function scrollToBottom() {
  await nextTick()
  if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
}

watch(() => analysis.chatHistory.length, scrollToBottom)
watch(() => {
  const last = analysis.chatHistory[analysis.chatHistory.length - 1]
  return last?.role === 'assistant' ? last.content.length : 0
}, scrollToBottom)

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
</script>

<template>
  <div class="flex flex-col h-full min-h-0">

    <div
      v-if="codeContext"
      class="flex-shrink-0 flex items-center gap-2 px-4 py-2
             bg-bg-surface border-b border-border-dim"
    >
      <span class="font-mono text-[10px] text-text-muted">{{ t('chat.contextLabel') }}</span>
      <span class="font-mono text-[11px] text-cyan font-semibold">{{ codeContext.fileName }}</span>
      <span class="font-mono text-[10px] text-text-muted">{{ codeContext.lineInfo }}</span>
      <span class="font-mono text-[10px] px-1.5 py-0.5 rounded bg-bg-elevated text-text-muted ml-auto">
        {{ codeContext.language }}
      </span>
    </div>

    <div
      v-if="callContext"
      class="flex-shrink-0 px-4 py-2 bg-bg-deep border-b border-border-dim/50
             font-mono text-[10px] flex items-center gap-3 flex-wrap"
    >
      <span class="text-text-muted">{{ t('chat.callChainLabel') }}</span>
      <span v-if="callContext.callers.length" class="text-amber">
        ▲ {{ callContext.callers.map(c => c.method_name).slice(0,2).join(', ') }}
        {{ callContext.callers.length > 2 ? `+${callContext.callers.length - 2}` : '' }}
      </span>
      <span v-if="callContext.callees.length" class="text-cyan">
        ▼ {{ callContext.callees.map(c => c.method_name).slice(0,2).join(', ') }}
        {{ callContext.callees.length > 2 ? `+${callContext.callees.length - 2}` : '' }}
      </span>
      <span class="text-text-muted ml-auto">{{ t('chat.contextInjected') }}</span>
    </div>

    <div ref="scrollRef" class="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-4 min-h-0">
      <template v-for="(msg, idx) in analysis.chatHistory" :key="idx">

        <div v-if="msg.role === 'user'" class="self-end max-w-[85%]">
          <div class="px-3.5 py-2.5 rounded-2xl rounded-tr-sm
                     bg-cyan/15 border border-cyan/25
                     font-mono text-[12px] text-text-primary leading-relaxed
                     whitespace-pre-wrap">
            {{ msg.content }}
          </div>
        </div>

        <div v-else class="max-w-[95%] flex items-start gap-2">
          <div
            class="w-6 h-6 rounded-full flex-shrink-0 flex items-center justify-center
                   font-mono text-[9px] font-bold text-bg-deep mt-0.5"
            style="background: linear-gradient(135deg,#00d4ff,#a55eea)"
          >AI</div>

          <div class="flex-1 min-w-0">
            <div
              v-if="msg.smartResolve"
              class="mb-1.5 px-2.5 py-1 rounded-lg bg-cyan/8 border border-cyan/20
                     font-mono text-[10px] text-cyan/80 flex items-center gap-1.5"
            >
              <span>{{ t('chat.smartResolve') }}</span>
              <span class="text-cyan font-semibold">{{ msg.smartResolve }}</span>
            </div>

            <div v-if="msg.streaming && !msg.content" class="flex items-center gap-1.5 py-2">
              <span
                v-for="i in 3" :key="i"
                class="w-1.5 h-1.5 rounded-full bg-cyan/60 animate-pulse-dot"
                :style="{ animationDelay: `${i * 0.15}s` }"
              />
            </div>

            <div
              v-else-if="msg.error"
              class="px-3 py-2 rounded-xl rounded-tl-sm bg-red-accent/10
                     border border-red-accent/30 font-mono text-[11px] text-red-accent"
            >{{ msg.content }}</div>

            <div
              v-else
              class="prose prose-sm prose-dark max-w-none px-3.5 py-2.5
                     rounded-2xl rounded-tl-sm bg-bg-surface border border-border-dim"
              :class="{ 'streaming-cursor': msg.streaming && msg.content }"
              v-html="renderMd(msg.content) || '&nbsp;'"
            />

            <div
              v-if="!msg.streaming && msg.sources?.length"
              class="mt-1.5 flex flex-wrap gap-1"
            >
              <span
                v-for="(src, si) in msg.sources" :key="si"
                class="font-mono text-[9px] px-1.5 py-0.5 rounded
                       bg-bg-elevated text-text-muted border border-border-dim/50"
              >
                {{ src.file_path }}:{{ src.line_start }}
              </span>
            </div>
          </div>
        </div>

      </template>

      <div v-if="!analysis.chatHistory.length" class="flex-1 flex flex-col items-center justify-center text-text-muted">
        <div class="text-3xl mb-3 opacity-20">◇</div>
        <div class="font-mono text-[12px]">{{ t('chat.emptyTitle') }}</div>
        <div class="font-mono text-[10px] mt-1 opacity-60">{{ t('chat.emptyHint') }}</div>
      </div>
    </div>

    <div
      v-if="analysis.chatHistory.length"
      class="flex-shrink-0 flex items-center gap-2 px-4 py-1.5 border-t border-border-dim"
    >
      <button
        class="font-mono text-[10px] text-text-muted hover:text-text-secondary transition-colors"
        @click="analysis.clearChat()"
      >{{ t('chat.clear') }}</button>
      <button
        v-if="analysis.chatStreaming"
        class="ml-auto font-mono text-[10px] text-red-accent hover:opacity-70
               transition-opacity flex items-center gap-1"
        @click="analysis.abort()"
      >
        <span class="animate-pulse-dot">●</span> {{ t('common.stop') }}
      </button>
      <span v-else class="ml-auto font-mono text-[10px] text-text-muted">
        {{ t('chat.questionCount', { n: analysis.chatHistory.filter(m => m.role === 'user').length }) }}
      </span>
    </div>

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
          :placeholder="t('chat.inputPlaceholder')"
          class="flex-1 bg-transparent font-mono text-[12px] text-text-primary
                 placeholder-text-muted outline-none resize-none leading-relaxed
                 min-h-[24px] max-h-[120px]"
          :disabled="analysis.chatStreaming"
          @input="onInput"
          @keydown="onKeydown"
        />
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
        <span>{{ t('chat.hotkeyHint') }}</span>
        <span v-if="inputText.length > 200" class="text-amber">{{ t('chat.charCount', { n: inputText.length }) }}</span>
      </div>
    </div>

  </div>
</template>
