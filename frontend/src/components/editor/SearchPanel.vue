<script setup lang="ts">
import { ref } from 'vue'
import { useSearchStore } from '@/stores/searchStore'
import { useRepoStore }   from '@/stores/repoStore'
import type { SearchMode } from '@/types'

const search = useSearchStore()
const repo   = useRepoStore()

type Tab = { id: SearchMode; label: string; color: string; dot: string }
const tabs: Tab[] = [
  { id: 'semantic', label: '语义检索', color: 'text-cyan', dot: 'bg-cyan' },
  { id: 'bug',      label: 'Bug 定位', color: 'text-red-accent', dot: 'bg-red-accent' },
  { id: 'explain',  label: '逻辑解释', color: 'text-amber', dot: 'bg-amber' },
  { id: 'deps',     label: '依赖分析', color: 'text-purple', dot: 'bg-purple' },
]

const HINTS: Record<SearchMode, string[]> = {
  semantic: ['用户登录在哪实现', '字符串判空方法', 'JWT Token 生成'],
  bug:      ['空指针隐患', '未关闭资源', 'SQL 注入风险'],
  explain:  ['订单状态流转', '分页查询逻辑', '权限校验流程'],
  deps:     ['AuthController 依赖', 'UserService 调用链'],
}

function submit() {
  if (!repo.isIndexDone) return
  search.doSearch()
}
</script>

<template>
  <div class="search-panel">
    <!-- Mode tabs -->
    <div class="flex gap-0.5 mb-3.5 bg-bg-surface rounded-lg p-0.5 w-fit">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        class="mode-tab"
        :class="search.mode === tab.id ? ['bg-bg-elevated shadow-sm', tab.color] : 'text-text-muted hover:text-text-secondary'"
        @click="search.mode = tab.id"
      >
        <span class="w-1.5 h-1.5 rounded-full flex-shrink-0" :class="tab.dot" />
        {{ tab.label }}
      </button>
    </div>

    <!-- Input row -->
    <div class="flex gap-2.5 items-start">
      <div class="flex-1">
        <input
          v-model="search.query"
          :placeholder="`输入${tabs.find(t=>t.id===search.mode)?.label}问题...`"
          rows="2"
          class="w-full bg-bg-surface border border-border-dim rounded-lg px-4 py-3
                 font-mono text-[13px] text-text-primary placeholder-text-muted outline-none
                 resize-none leading-relaxed transition-all
                 focus:border-cyan focus:shadow-[0_0_0_3px_rgba(0,212,255,0.12)]"
          @keydown.enter.exact.prevent="submit"
        />
        <!-- Hint chips -->
        <div class="flex gap-1.5 mt-2 flex-wrap">
          <button
            v-for="hint in HINTS[search.mode]"
            :key="hint"
            class="font-mono text-[10px] px-2.5 py-1 rounded-full border border-border-dim
                   bg-bg-surface text-text-muted hover:border-cyan hover:text-cyan
                   hover:bg-cyan-dim transition-all"
            @click="search.query = hint"
          >
            {{ hint }}
          </button>
        </div>
      </div>

      <!-- Submit button -->
      <button
        class="px-5 py-3 rounded-lg font-mono text-[12px] font-semibold tracking-wide
               flex items-center gap-1.5 transition-all whitespace-nowrap"
        :class="repo.isIndexDone && search.query.trim()
          ? 'bg-gradient-to-br from-cyan to-[#00a5cc] text-bg-deep hover:-translate-y-px hover:shadow-[0_4px_16px_rgba(0,212,255,0.3)]'
          : 'bg-bg-surface text-text-muted border border-border-dim cursor-not-allowed'"
        :disabled="!repo.isIndexDone || !search.query.trim() || search.loading"
        @click="submit"
      >
        <span v-if="search.loading" class="animate-spin-slow inline-block">⟳</span>
        <span v-else>⌕</span>
        {{ search.loading ? '检索中' : '检索' }}
      </button>
    </div>

    <!-- Not indexed warning -->
    <div
      v-if="repo.currentRepo && !repo.isIndexDone"
      class="mt-2 flex items-center gap-2 font-mono text-[11px] text-amber"
    >
      <span>⚠</span>
      仓库尚未索引，请在左侧仓库面板点击「建索引」
    </div>
  </div>
</template>

<style scoped>
.search-panel {
  @apply px-6 py-4 border-b border-border-dim bg-bg-base flex-shrink-0;
}
.mode-tab {
  @apply flex items-center gap-1.5 font-mono text-[11px] font-medium
         px-3.5 py-1.5 rounded transition-all;
}
</style>
