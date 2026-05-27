<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { marked } from 'marked'
import { useAnalysisStore } from '@/stores/analysisStore'
import CardContext from './cards/CardContext.vue'
import CardImpact from './cards/CardImpact.vue'
import CardSimilar from './cards/CardSimilar.vue'
import CardBug from './cards/CardBug.vue'

const { t } = useI18n()
const analysis = useAnalysisStore()

const inputText = ref('')
const scrollRef = ref<HTMLElement | null>(null)
const rows = ref(1)

function renderMd(text: string) {
  return marked.parse(text) as string
}

async function scrollToBottom() {
  await nextTick()
  if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
}

watch(() => analysis.insightCards.length, scrollToBottom)
watch(() => {
  const last = analysis.insightCards[analysis.insightCards.length - 1]
  return last?.type === 'ai-msg' ? last.data.content?.length ?? 0 : 0
}, scrollToBottom)

function onInput() {
  const lines = (inputText.value.match(/\n/g) ?? []).length + 1
  rows.value = Math.min(5, Math.max(1, lines))
}

async function submit() {
  const msg = inputText.value.trim()
  if (!msg || analysis.insightStreaming) return
  inputText.value = ''
  rows.value = 1
  await analysis.sendInsight(msg)
}

function sendQuick(prompt: string) {
  if (analysis.insightStreaming) return
  inputText.value = ''
  analysis.sendInsight(prompt)
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

    <CardContext />

    <!-- 卡片流区域 -->
    <div ref="scrollRef" class="flex-1 overflow-y-auto px-2.5 py-3 flex flex-col gap-2.5 min-h-0">

      <template v-for="card in analysis.insightCards" :key="card.id">

        <!-- 用户消息 -->
        <div v-if="card.type === 'user-msg'" class="self-end max-w-[85%]">
          <div class="px-3.5 py-2.5 rounded-2xl rounded-tr-sm
            bg-cyan/15 border border-cyan/25
            font-mono text-[12px] text-text-primary leading-relaxed
            whitespace-pre-wrap">
            {{ card.data.content }}
          </div>
        </div>

        <!-- AI 消息 -->
        <div v-else-if="card.type === 'ai-msg'" class="max-w-[95%] flex items-start gap-2">
          <div class="w-6 h-6 rounded-full flex-shrink-0 flex items-center justify-center
            font-mono text-[9px] font-bold text-bg-deep mt-0.5"
            style="background: linear-gradient(135deg,#00d4ff,#a55eea)">AI</div>
          <div class="flex-1 min-w-0">
            <div v-if="card.data.smartResolve"
              class="mb-1.5 px-2.5 py-1 rounded-lg bg-cyan/8 border border-cyan/20
                font-mono text-[10px] text-cyan/80 flex items-center gap-1.5">
              <span>{{ t('chat.smartResolve') }}</span>
              <span class="text-cyan font-semibold">{{ card.data.smartResolve }}</span>
            </div>

            <div v-if="card.streaming && !card.data.content" class="flex items-center gap-1.5 py-2">
              <span v-for="i in 3" :key="i"
                class="w-1.5 h-1.5 rounded-full bg-cyan/60 animate-pulse-dot"
                :style="{ animationDelay: `${i * 0.15}s` }" />
            </div>

            <div v-else-if="card.data.error"
              class="px-3 py-2 rounded-xl rounded-tl-sm bg-red-accent/10
                border border-red-accent/30 font-mono text-[11px] text-red-accent">
              {{ card.data.content }}
            </div>

            <div v-else
              class="prose prose-sm prose-dark max-w-none px-3.5 py-2.5
                rounded-2xl rounded-tl-sm bg-bg-surface border border-border-dim"
              :class="{ 'streaming-cursor': card.streaming && card.data.content }"
              v-html="renderMd(card.data.content) || '&nbsp;'" />
          </div>
        </div>

        <!-- Impact 卡片 -->
        <CardImpact v-else-if="card.type === 'impact'" :data="card.data" />

        <!-- Similar 卡片 -->
        <CardSimilar v-else-if="card.type === 'similar'" :data="card.data" />

        <!-- Bug 卡片 -->
        <CardBug v-else-if="card.type === 'bug'" :data="card.data" />

      </template>

      <!-- 空状态 -->
      <div v-if="!analysis.insightCards.length"
        class="flex-1 flex flex-col items-center justify-center text-text-muted">
        <div class="text-3xl mb-3 opacity-20">◇</div>
        <div class="font-mono text-[12px]">{{ t('insight.emptyTitle') }}</div>
        <div class="font-mono text-[10px] mt-1 opacity-60">{{ t('insight.emptyHint') }}</div>
      </div>
    </div>

    <!-- 控制栏 -->
    <div v-if="analysis.insightCards.length"
      class="flex-shrink-0 flex items-center gap-2 px-4 py-1.5 border-t border-border-dim">
      <button class="font-mono text-[10px] text-text-muted hover:text-text-secondary transition-colors"
        @click="analysis.clearInsight()">{{ t('chat.clear') }}</button>
      <button v-if="analysis.insightStreaming"
        class="ml-auto font-mono text-[10px] text-red-accent hover:opacity-70
          transition-opacity flex items-center gap-1"
        @click="analysis.abort()">
        <span class="animate-pulse-dot">●</span> {{ t('common.stop') }}
      </button>
    </div>

    <!-- 输入区 -->
    <div class="flex-shrink-0 px-3 pb-3 pt-1.5">
      <div class="flex items-end gap-2 bg-bg-surface border rounded-xl px-3 py-2 transition-all"
        :class="analysis.insightStreaming
          ? 'border-border-dim opacity-70'
          : 'border-border-dim focus-within:border-cyan focus-within:shadow-[0_0_0_3px_rgba(0,212,255,0.1)]'">
        <textarea
          v-model="inputText"
          :rows="rows"
          :placeholder="t('insight.inputPlaceholder')"
          class="flex-1 bg-transparent font-mono text-[12px] text-text-primary
            placeholder-text-muted outline-none resize-none leading-relaxed
            min-h-[24px] max-h-[120px]"
          :disabled="analysis.insightStreaming"
          @input="onInput"
          @keydown="onKeydown" />
        <button
          class="flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center
            font-mono text-[12px] transition-all"
          :class="inputText.trim() && !analysis.insightStreaming
            ? 'bg-cyan text-bg-deep hover:opacity-90 hover:-translate-y-px'
            : 'bg-bg-elevated text-text-muted cursor-not-allowed'"
          :disabled="!inputText.trim() || analysis.insightStreaming"
          @click="submit">
          <span v-if="analysis.insightStreaming" class="animate-spin-slow text-[10px]">⟳</span>
          <span v-else>↑</span>
        </button>
      </div>

      <!-- 快捷按钮 -->
      <div class="flex gap-1 mt-1.5 flex-wrap">
        <button v-for="action in [
          { label: t('insight.quickExplain'), prompt: t('insight.promptExplain') },
          { label: t('insight.quickBug'),     prompt: t('insight.promptBug') },
          { label: t('insight.quickImpact'),  prompt: t('insight.promptImpact') },
          { label: t('insight.quickSimilar'), prompt: t('insight.promptSimilar') },
          { label: t('insight.quickDocs'),    prompt: t('insight.promptDocs') },
        ]" :key="action.label"
          class="font-mono text-[9px] px-2 py-1 rounded-md
            bg-bg-elevated/50 border border-border-dim text-text-muted
            hover:border-cyan hover:text-cyan transition-all cursor-pointer"
          :disabled="analysis.insightStreaming"
          @click="sendQuick(action.prompt)">
          {{ action.label }}
        </button>
      </div>
    </div>

  </div>
</template>
