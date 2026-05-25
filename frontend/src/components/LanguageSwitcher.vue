<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SUPPORTED_LOCALES, setLocale, type Locale } from '@/i18n'

const { locale } = useI18n()

const LABEL: Record<Locale, string> = { zh: '中', ja: '日', en: 'EN' }

function pick(lng: Locale) {
  if (lng !== locale.value) setLocale(lng)
}
</script>

<template>
  <div class="lang-switcher" role="group" aria-label="Language">
    <button
      v-for="lng in SUPPORTED_LOCALES"
      :key="lng"
      class="lang-btn"
      :class="{ active: locale === lng }"
      :title="lng"
      @click="pick(lng)"
    >{{ LABEL[lng] }}</button>
  </div>
</template>

<style scoped>
.lang-switcher {
  @apply inline-flex items-center bg-bg-surface border border-border-dim rounded-full p-0.5;
}
.lang-btn {
  @apply font-mono text-[10px] font-semibold px-2 py-0.5 rounded-full
         text-text-muted transition-colors;
}
.lang-btn:hover { @apply text-text-primary; }
.lang-btn.active {
  @apply bg-cyan-dim text-cyan;
}
</style>
