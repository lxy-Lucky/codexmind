<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useSearchStore } from '@/stores/searchStore'
import { useRepoStore }   from '@/stores/repoStore'

const emit   = defineEmits<{ (e: 'searched'): void }>()
const { t } = useI18n()
const search = useSearchStore()
const repo   = useRepoStore()

const LANG_FILTERS: { label: string; value: string }[] = [
  { label: 'JS',   value: 'javascript' },
  { label: 'Java', value: 'java'       },
  { label: 'XML',  value: 'xml'        },
]

async function submit() {
  if (!repo.isIndexDone || !search.query.trim() || search.loading) return
  await search.doSearch()
  emit('searched')
}

function toggleLang(value: string) {
  search.languageFilter = search.languageFilter === value ? null : value
}
</script>

<template>
  <div class="search-panel">
    <div class="flex items-center gap-1.5 mb-3 font-mono text-[11px] font-medium text-cyan">
      <span class="w-1.5 h-1.5 rounded-full bg-cyan" />
      {{ t('search.title') }}
    </div>

    <div class="flex gap-2.5 items-start">
      <div class="flex-1">
        <textarea
          v-model="search.query"
          :placeholder="t('search.placeholder')"
          rows="1"
          class="w-full bg-bg-surface border border-border-dim rounded-lg px-4 py-3
                 font-mono text-[13px] text-text-primary placeholder-text-muted outline-none
                 resize-none leading-relaxed transition-all
                 focus:border-cyan focus:shadow-[0_0_0_3px_rgba(0,212,255,0.12)]"
          @keydown.enter.exact.prevent="submit"
        />
        <div class="flex items-center gap-2 mt-2">
          <span class="font-mono text-[10px] text-text-muted">{{ t('search.onlyLang') }}</span>
          <button
            v-for="opt in LANG_FILTERS"
            :key="opt.value"
            class="lang-chip font-mono text-[10px] px-2.5 py-1 rounded-full border transition-all"
            :class="search.languageFilter === opt.value
              ? 'border-cyan text-cyan bg-cyan-dim'
              : 'border-border-dim bg-bg-surface text-text-muted hover:border-cyan hover:text-cyan'"
            @click="toggleLang(opt.value)"
          >
            {{ opt.label }}
          </button>
        </div>
      </div>

      <button
        class="px-5 py-3 rounded-lg font-mono text-[12px] font-semibold tracking-wide
               flex items-center gap-1.5 transition-all whitespace-nowrap flex-shrink-0"
        :class="repo.isIndexDone && search.query.trim() && !search.loading
          ? 'bg-gradient-to-br from-cyan to-[#00a5cc] text-bg-deep hover:-translate-y-px hover:shadow-[0_4px_16px_rgba(0,212,255,0.3)]'
          : 'bg-bg-surface text-text-muted border border-border-dim cursor-not-allowed opacity-60'"
        :disabled="!repo.isIndexDone || !search.query.trim() || search.loading"
        @click="submit"
      >
        <span v-if="search.loading" class="animate-spin-slow inline-block">⟳</span>
        <span v-else>⌕</span>
        {{ search.loading ? t('search.searching') : t('search.submit') }}
      </button>
    </div>

    <div v-if="repo.currentRepo && !repo.isIndexDone"
         class="mt-2 flex items-center gap-2 font-mono text-[11px] text-amber">
      <span>⚠</span> {{ t('search.warnNotIndexed') }}
    </div>
    <div v-else-if="!repo.currentRepo"
         class="mt-2 flex items-center gap-2 font-mono text-[11px] text-text-muted">
      <span>○</span> {{ t('search.warnSelectRepo') }}
    </div>
  </div>
</template>

<style scoped>
.search-panel {
  @apply px-6 py-4 border-b border-border-dim bg-bg-base flex-shrink-0;
}
.lang-chip {
  min-width: 44px;
  text-align: center;
}
</style>
