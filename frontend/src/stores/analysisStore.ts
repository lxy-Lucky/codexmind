import { defineStore } from 'pinia'
import { ref } from 'vue'
import { streamAnalysis, streamChat } from '@/api/analysis'
import { useEditorStore } from './editorStore'
import { useRepoStore } from './repoStore'
import type { AnalysisMode, BugItem, ChatMessage, SSEChunk } from '@/types'

export const useAnalysisStore = defineStore('analysis', () => {
  // ── State ──────────────────────────────────────────────────────────────────
  const activeTab      = ref<'summary' | 'bug' | 'deps' | 'history' | 'chat'>('summary')
  const currentSymbolId  = ref('')
  const currentClassName = ref('')
  const streaming      = ref(false)
  const streamingText = ref('')
  const bugItems      = ref<BugItem[]>([])
  const confidence    = ref(0)
  const latencyMs     = ref(0)
  const error         = ref<string | null>(null)
  const hasResult     = ref(false)

  // ── Chat ───────────────────────────────────────────────────────────────────
  const chatHistory  = ref<ChatMessage[]>([])
  const chatStreaming = ref(false)

  let _abort: (() => void) | null = null

  // ── Actions ────────────────────────────────────────────────────────────────
  function setTab(tab: typeof activeTab.value) {
    activeTab.value = tab
  }

  function setGraphSymbol(symbolId: string, className?: string) {
    currentSymbolId.value  = symbolId
    currentClassName.value = className ?? ''
  }

  function abort() {
    _abort?.()
    _abort = null
    streaming.value    = false
    chatStreaming.value = false
  }

  // summary / bug / deps
  async function analyze(mode?: AnalysisMode) {
    const repoStore   = useRepoStore()
    const editorStore = useEditorStore()
    if (!repoStore.currentRepo) return

    const sel = editorStore.getSelectedCode()
    if (!sel) { error.value = '请先打开文件或选中代码'; return }

    const targetMode = mode ?? _tabToMode(activeTab.value)
    activeTab.value  = _modeToTab(targetMode)

    abort()
    streaming.value     = true
    streamingText.value = ''
    bugItems.value      = []
    confidence.value    = 0
    latencyMs.value     = 0
    error.value         = null
    hasResult.value     = false
    let rawBuf = ''

    _abort = streamAnalysis(
      {
        repo_id:    repoStore.currentRepo.id,
        file_path:  editorStore.currentFile?.path ?? '',
        line_start: sel.lineStart,
        line_end:   sel.lineEnd,
        code:       sel.code,
        mode:       targetMode,
        symbol_id:  currentSymbolId.value || undefined,
      },
      (text) => { if (targetMode === 'bug') rawBuf += text; else streamingText.value += text },
      (ev: SSEChunk) => {
        streaming.value  = false
        hasResult.value  = true
        confidence.value = ev.confidence ?? 0
        latencyMs.value  = ev.latency_ms ?? 0
        _abort = null
        if (targetMode === 'bug') {
          try { bugItems.value = JSON.parse(_extractJson(rawBuf)) }
          catch { error.value = 'Bug 检测结果解析失败\n' + rawBuf }
        }
      },
      (msg) => { streaming.value = false; error.value = msg; _abort = null },
    )
  }

  // 自由问答
  async function sendChat(userMessage: string) {
    const repoStore   = useRepoStore()
    const editorStore = useEditorStore()
    if (!repoStore.currentRepo || !userMessage.trim()) return

    const sel = editorStore.getSelectedCode()

    // 追加用户消息
    chatHistory.value.push({ role: 'user', content: userMessage.trim(), timestamp: Date.now() })

    // 追加 AI 占位
    chatHistory.value.push({ role: 'assistant', content: '', timestamp: Date.now(), streaming: true })
    const aiIdx = chatHistory.value.length - 1

    chatStreaming.value = true
    abort()

    // 传给后端的历史：排除最后两条（当前轮）
    const histForBackend = chatHistory.value.slice(0, -2)

    _abort = streamChat(
      {
        repo_id:       repoStore.currentRepo.id,
        file_path:     editorStore.currentFile?.path ?? '',
        line_start:    sel?.lineStart ?? 1,
        line_end:      sel?.lineEnd ?? 1,
        code:          sel?.code ?? '// 未选中代码',
        mode:          'custom',
        custom_prompt: userMessage.trim(),
      },
      histForBackend,
      (text) => { chatHistory.value[aiIdx].content += text },
      () => {
        chatHistory.value[aiIdx].streaming = false
        chatStreaming.value = false
        _abort = null
      },
      (msg) => {
        chatHistory.value[aiIdx].content  = `⚠ 请求失败：${msg}`
        chatHistory.value[aiIdx].streaming = false
        chatHistory.value[aiIdx].error    = true
        chatStreaming.value = false
        _abort = null
      },
    )
  }

  function clearChat() {
    abort()
    chatHistory.value  = []
    chatStreaming.value = false
  }

  function _tabToMode(tab: string): AnalysisMode {
    return ({ summary:'summary', bug:'bug', deps:'deps', history:'summary', chat:'custom' } as any)[tab] ?? 'summary'
  }
  function _modeToTab(mode: AnalysisMode): typeof activeTab.value {
    return ({ summary:'summary', bug:'bug', deps:'deps', custom:'chat' } as any)[mode] ?? 'summary'
  }


  // ── Bug JSON 提取：兼容 LLM 偶尔输出 markdown fence 的情况 ──────────────────
  function _extractJson(raw: string): string {
    let s = raw.trim()
    // 去掉 ```json ... ``` 或 ``` ... ``` 包裹
    s = s.replace(/^```(?:json)?\s*/i, '').replace(/\s*```\s*$/, '').trim()
    // 找第一个 [ 到最后一个 ]，提取数组部分
    const start = s.indexOf('[')
    const end   = s.lastIndexOf(']')
    if (start !== -1 && end !== -1 && end > start) {
      return s.slice(start, end + 1)
    }
    return s
  }

  return {
    activeTab, currentSymbolId, currentClassName,
    streaming, streamingText, bugItems,
    confidence, latencyMs, error, hasResult,
    chatHistory, chatStreaming,
    setTab, setGraphSymbol, analyze, abort, sendChat, clearChat,
  }
})
