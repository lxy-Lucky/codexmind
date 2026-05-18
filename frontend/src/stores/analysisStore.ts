import { defineStore } from 'pinia'
import { ref } from 'vue'
import { streamAnalysis } from '@/api/analysis'
import { useEditorStore } from './editorStore'
import { useRepoStore } from './repoStore'
import type { AnalysisMode, BugItem, SSEChunk } from '@/types'

export const useAnalysisStore = defineStore('analysis', () => {
  // ── State ──────────────────────────────────────────────────────────────────
  const activeTab      = ref<'summary' | 'bug' | 'deps' | 'history'>('summary')
  const streaming      = ref(false)
  const streamingText  = ref('')         // summary / deps 流式文本（raw markdown/mermaid）
  const bugItems       = ref<BugItem[]>([])
  const confidence     = ref(0)
  const latencyMs      = ref(0)
  const error          = ref<string | null>(null)
  const hasResult      = ref(false)

  let _abort: (() => void) | null = null

  // ── Actions ────────────────────────────────────────────────────────────────
  function setTab(tab: typeof activeTab.value) {
    activeTab.value = tab
  }

  function abort() {
    _abort?.()
    _abort = null
    streaming.value = false
  }

  async function analyze(mode?: AnalysisMode) {
    const repoStore   = useRepoStore()
    const editorStore = useEditorStore()
    if (!repoStore.currentRepo) return

    const sel = editorStore.getSelectedCode()
    if (!sel) {
      error.value = '请先在编辑器中打开文件或选中代码'
      return
    }

    const targetMode = mode ?? _tabToMode(activeTab.value)

    // 切换到对应 tab
    activeTab.value = _modeToTab(targetMode)

    // 重置状态
    abort()
    streaming.value = true
    streamingText.value = ''
    bugItems.value = []
    confidence.value = 0
    latencyMs.value  = 0
    error.value      = null
    hasResult.value  = false

    let rawBuf = ''  // bug 模式先缓冲，done 后 parse

    _abort = streamAnalysis(
      {
        repo_id:    repoStore.currentRepo.id,
        file_path:  editorStore.currentFile?.path ?? '',
        line_start: sel.lineStart,
        line_end:   sel.lineEnd,
        code:       sel.code,
        mode:       targetMode,
      },
      // onChunk
      (text) => {
        if (targetMode === 'bug') {
          rawBuf += text
        } else {
          streamingText.value += text
        }
      },
      // onDone
      (ev: SSEChunk) => {
        streaming.value  = false
        hasResult.value  = true
        confidence.value = ev.confidence ?? 0
        latencyMs.value  = ev.latency_ms ?? 0
        _abort = null

        if (targetMode === 'bug') {
          try {
            bugItems.value = JSON.parse(rawBuf.trim())
          } catch {
            error.value = 'Bug 检测结果解析失败，原始内容：\n' + rawBuf
          }
        }
      },
      // onError
      (msg) => {
        streaming.value = false
        error.value     = msg
        _abort = null
      },
    )
  }

  // ── Helpers ────────────────────────────────────────────────────────────────
  function _tabToMode(tab: string): AnalysisMode {
    const map: Record<string, AnalysisMode> = {
      summary: 'summary',
      bug:     'bug',
      deps:    'deps',
      history: 'summary',
    }
    return map[tab] ?? 'summary'
  }

  function _modeToTab(mode: AnalysisMode): typeof activeTab.value {
    const map: Record<AnalysisMode, typeof activeTab.value> = {
      summary: 'summary',
      bug:     'bug',
      deps:    'deps',
    }
    return map[mode]
  }

  return {
    activeTab, streaming, streamingText, bugItems,
    confidence, latencyMs, error, hasResult,
    setTab, analyze, abort,
  }
})
