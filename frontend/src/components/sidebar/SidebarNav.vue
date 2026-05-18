<script setup lang="ts">
import { ref } from 'vue'
import { useSearchStore } from '@/stores/searchStore'
import { useRepoStore } from '@/stores/repoStore'

type NavItem = { id: string; icon: string; label: string; badge?: string | number }
const emit = defineEmits<{ (e: 'change', id: string): void }>()

const active = ref('explorer')
const search = useSearchStore()
const repo   = useRepoStore()

const navItems: NavItem[] = [
  { id: 'explorer', icon: '◫', label: '文件资源' },
  { id: 'search',   icon: '⌕', label: '语义搜索' },
  { id: 'bug',      icon: '◉', label: 'Bug 扫描' },
  { id: 'history',  icon: '⟲', label: '历史记录' },
]

function select(id: string) {
  active.value = id
  emit('change', id)
}
</script>

<template>
  <nav class="px-2 flex flex-col gap-0.5">
    <button
      v-for="item in navItems"
      :key="item.id"
      class="nav-item"
      :class="active === item.id ? 'active' : ''"
      @click="select(item.id)"
    >
      <span class="w-5 text-center text-[13px] opacity-70">{{ item.icon }}</span>
      <span class="font-mono text-[12px]">{{ item.label }}</span>
      <span
        v-if="item.id === 'search' && search.results.length"
        class="ml-auto font-mono text-[10px] px-1.5 h-[18px] leading-[18px] rounded-full
               bg-green-accent/10 text-green-accent"
      >
        {{ search.results.length }}
      </span>
    </button>
  </nav>
</template>

<style scoped>
.nav-item {
  @apply flex items-center gap-2.5 px-2.5 py-1.5 rounded transition-all text-text-secondary;
}
.nav-item:hover { @apply bg-bg-hover text-text-primary; }
.nav-item.active { @apply bg-cyan-dim text-cyan; }
.nav-item.active span { opacity: 1; }
</style>
