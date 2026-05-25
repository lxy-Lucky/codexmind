<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSearchStore } from '@/stores/searchStore'
import { useRepoStore } from '@/stores/repoStore'
import { useSystemStatus } from '@/composables/useSystemStatus'
import LanguageSwitcher from '@/components/LanguageSwitcher.vue'

const { t } = useI18n()
const search = useSearchStore()
const repo   = useRepoStore()
const { online } = useSystemStatus()

const globalQ = ref('')

function onGlobalSearch(e: KeyboardEvent) {
  if (e.key !== 'Enter') return
  if (!globalQ.value.trim()) return
  search.query = globalQ.value
  search.doSearch()
}
</script>

<template>
  <header class="top-bar">
    <!-- Brand -->
    <div class="flex items-center gap-2.5">
      <div class="brand-icon">Cx</div>
      <span class="font-display text-lg text-text-primary tracking-wide">{{ t('app.title') }}</span>
      <span class="font-mono text-[10px] text-text-muted bg-bg-surface border border-border-dim px-2 py-0.5 rounded-full ml-1">
        DEV
      </span>
    </div>

    <!-- Right -->
    <div class="flex items-center gap-4">
      <!-- Language switcher -->
      <LanguageSwitcher />

      <!-- System status pill -->
      <div class="flex items-center gap-1.5 font-mono text-[11px] text-text-secondary
                  bg-bg-surface border border-border-dim px-3 py-1 rounded-full">
        <span
          class="w-1.5 h-1.5 rounded-full animate-pulse-dot"
          :class="online ? 'bg-green-accent shadow-[0_0_6px_#26de81]' : 'bg-red-accent shadow-[0_0_6px_#ff4757]'"
        />
        {{ online ? t('topbar.indexOnline') : t('topbar.backendOffline') }}
      </div>

      <!-- Repo badge -->
      <div v-if="repo.currentRepo" class="font-mono text-[11px] text-text-muted">
        {{ repo.currentRepo.name }}
      </div>

      <!-- Avatar -->
      <div class="w-7 h-7 rounded-full border-2 border-border-bright flex items-center justify-center
                  font-mono text-[11px] font-semibold text-cyan bg-bg-surface cursor-pointer
                  hover:border-cyan transition-colors">
        LY
      </div>
    </div>
  </header>
</template>

<style scoped>
.top-bar {
  @apply flex items-center justify-between px-5
         bg-bg-base border-b border-border-dim z-50;
}
.brand-icon {
  @apply w-7 h-7 rounded-sm flex items-center justify-center
         font-mono text-sm font-bold text-bg-deep;
  background: linear-gradient(135deg, #00d4ff, #a55eea);
}
</style>
