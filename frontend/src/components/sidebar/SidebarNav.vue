<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

defineProps<{ active: string }>()
const emit = defineEmits<{ (e: 'change', id: string): void }>()

const { t } = useI18n()

type NavItem = { id: string; icon: string; labelKey: string }
const navItems = computed<NavItem[]>(() => [
  { id: 'explorer', icon: '◫', labelKey: 'nav.explorer' },
  { id: 'search',   icon: '⌕', labelKey: 'nav.semantic' },
])
</script>

<template>
  <nav class="px-2 flex flex-col gap-0.5">
    <button
      v-for="item in navItems"
      :key="item.id"
      class="nav-item"
      :class="active === item.id ? 'active' : ''"
      @click="emit('change', item.id)"
    >
      <span class="w-5 text-center text-[13px]">{{ item.icon }}</span>
      <span class="font-mono text-[12px]">{{ t(item.labelKey) }}</span>
    </button>
  </nav>
</template>

<style scoped>
.nav-item {
  @apply flex items-center gap-2.5 px-2.5 py-1.5 rounded transition-all text-text-secondary;
}
.nav-item:hover { @apply bg-bg-hover text-text-primary; }
.nav-item.active { @apply bg-cyan-dim text-cyan; }
</style>
