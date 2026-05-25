<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { SUPPORTED_LOCALES, setLocale, type Locale } from '@/i18n'

const { t, locale } = useI18n()

const LABEL: Record<Locale, string> = { zh: '中', ja: '日', en: 'EN' }

const open = ref(false)
const rootRef = ref<HTMLElement | null>(null)

function toggle() { open.value = !open.value }

function pick(lng: Locale) {
  if (lng !== locale.value) setLocale(lng)
  open.value = false
}

function onDocClick(e: MouseEvent) {
  if (!open.value) return
  if (rootRef.value && !rootRef.value.contains(e.target as Node)) {
    open.value = false
  }
}

function onEsc(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
}

onMounted(() => {
  window.addEventListener('mousedown', onDocClick)
  window.addEventListener('keydown', onEsc)
})
onBeforeUnmount(() => {
  window.removeEventListener('mousedown', onDocClick)
  window.removeEventListener('keydown', onEsc)
})
</script>

<template>
  <div ref="rootRef" class="relative">
    <!-- 头像按钮：显示当前语言 -->
    <button
      class="lang-avatar"
      :class="{ active: open }"
      :title="t('language.label')"
      @click="toggle"
    >
      {{ LABEL[locale as Locale] }}
    </button>

    <!-- 下拉菜单 -->
    <Transition name="dropdown">
      <div v-if="open" class="dropdown">
        <div class="dropdown-header">{{ t('language.label') }}</div>
        <button
          v-for="lng in SUPPORTED_LOCALES"
          :key="lng"
          class="dropdown-item"
          :class="{ active: locale === lng }"
          @click="pick(lng)"
        >
          <span class="item-tag">{{ LABEL[lng] }}</span>
          <span class="item-label">{{ t(`language.${lng}`) }}</span>
          <span v-if="locale === lng" class="item-check">✓</span>
        </button>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.lang-avatar {
  @apply w-7 h-7 rounded-full border-2 border-border-bright flex items-center justify-center
         font-mono text-[11px] font-semibold text-cyan bg-bg-surface cursor-pointer
         transition-all select-none;
}
.lang-avatar:hover { @apply border-cyan; }
.lang-avatar.active {
  @apply border-cyan bg-cyan-dim;
  box-shadow: 0 0 0 3px rgba(0, 212, 255, 0.12);
}

.dropdown {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  min-width: 140px;
  z-index: 60;
  @apply bg-bg-elevated border border-border-dim rounded-lg overflow-hidden;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.45);
}
.dropdown-header {
  @apply px-3 py-1.5 font-mono text-[9px] uppercase tracking-widest text-text-muted
         border-b border-border-dim bg-bg-surface;
}
.dropdown-item {
  @apply w-full flex items-center gap-2 px-3 py-2 font-mono text-[12px]
         text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors;
}
.dropdown-item.active {
  @apply text-cyan bg-cyan-dim;
}
.item-tag {
  @apply inline-flex items-center justify-center w-6 h-5 rounded
         bg-bg-base border border-border-dim text-[10px] font-bold flex-shrink-0;
}
.dropdown-item.active .item-tag {
  @apply border-cyan/40 text-cyan;
}
.item-label { @apply flex-1 text-left truncate; }
.item-check { @apply text-cyan text-[11px]; }

/* Dropdown transition */
.dropdown-enter-active { transition: all 0.14s ease-out; }
.dropdown-leave-active { transition: all 0.1s ease-in; }
.dropdown-enter-from, .dropdown-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.97);
}
</style>
