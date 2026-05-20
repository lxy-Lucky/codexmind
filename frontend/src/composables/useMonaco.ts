import { onMounted, onBeforeUnmount, watch, type Ref } from 'vue'
import loader from '@monaco-editor/loader'
import type * as Monaco from 'monaco-editor'
import { useEditorStore } from '@/stores/editorStore'

const THEME_NAME = 'codexmind-dark'

function defineTheme(monaco: typeof Monaco) {
  monaco.editor.defineTheme(THEME_NAME, {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: 'comment',    foreground: '556a8e', fontStyle: 'italic' },
      { token: 'keyword',    foreground: '00d4ff', fontStyle: 'bold' },
      { token: 'string',     foreground: '26de81' },
      { token: 'number',     foreground: 'ffb347' },
      { token: 'type',       foreground: 'a55eea' },
      { token: 'decorator',  foreground: 'ffb347' },
      { token: 'annotation', foreground: 'ffb347' },
    ],
    colors: {
      'editor.background':              '#06090f',
      'editor.foreground':              '#c8d6f0',
      'editor.lineHighlightBackground': '#0f1628',
      'editor.selectionBackground':     '#1a2540',
      'editorLineNumber.foreground':    '#2a3a5a',
      'editorLineNumber.activeForeground': '#556a8e',
      'editorCursor.foreground':        '#00d4ff',
      'editor.findMatchBackground':     'rgba(0,212,255,0.25)',
      'editorGutter.background':        '#06090f',
      'scrollbar.shadow':               '#00000000',
      'scrollbarSlider.background':     '#1c2a4a80',
      'scrollbarSlider.hoverBackground':'#263758aa',
    },
  })
}

export function useMonaco(containerRef: Ref<HTMLElement | null>) {
  const editorStore = useEditorStore()
  let editor: Monaco.editor.IStandaloneCodeEditor | null = null
  let decorationIds: string[] = []
  let resizeObserver: ResizeObserver | null = null

  onMounted(async () => {
    if (!containerRef.value) return

    loader.config({ paths: { vs: '/node_modules/monaco-editor/min/vs' } })

    const monaco = await loader.init()
    defineTheme(monaco)

    editor = monaco.editor.create(containerRef.value, {
      value:               '',
      language:            'java',
      theme:               THEME_NAME,
      fontSize:            13,
      fontFamily:          '"JetBrains Mono", monospace',
      fontLigatures:       true,
      lineNumbers:         'on',
      minimap:             { enabled: true, scale: 1 },
      scrollBeyondLastLine: false,
      wordWrap:            'off',
      readOnly:            true,
      renderLineHighlight: 'all',
      smoothScrolling:     true,
      cursorBlinking:      'smooth',
      glyphMargin:         true,
      folding:             true,
      padding:             { top: 16, bottom: 16 },
    })

    ;(window as any).monaco = monaco
    if (editor) editorStore.setEditorInstance(editor)

    // ── ResizeObserver ────────────────────────────────────────────────────────
    // 监听容器尺寸变化（sidebar 收缩/拖拽/右侧拖拽/窗口缩放），
    // 通知 Monaco 重新计算布局，确保编辑器始终填满容器。
    resizeObserver = new ResizeObserver(() => {
      editor?.layout()
    })
    resizeObserver.observe(containerRef.value)

    // ── 文件变化 → 更新 model ─────────────────────────────────────────────────
    watch(
      () => editorStore.currentFile,
      (file) => {
        if (!editor || !file) return
        const model = monaco.editor.createModel(
          file.content,
          file.language === 'typescript' ? 'typescript' : file.language,
        )
        const old = editor.getModel()
        editor.setModel(model)
        old?.dispose()
      },
      { immediate: true },
    )

    // ── 高亮行变化 → 更新 decorations ────────────────────────────────────────
    watch(
      () => editorStore.highlightLines,
      (range) => {
        if (!editor || !range) {
          decorationIds = editor?.deltaDecorations(decorationIds, []) ?? []
          return
        }
        const [s, e] = range
        decorationIds = editor.deltaDecorations(decorationIds, [{
          range: new monaco.Range(s, 1, e, 9999),
          options: {
            isWholeLine: true,
            className: 'bg-cyan-dim border-l-2 border-cyan',
            overviewRuler: {
              color: '#00d4ff',
              position: monaco.editor.OverviewRulerLane.Left,
            },
          },
        }])
        editor.revealLineInCenter(s)
        // 将光标也移到高亮起始行，确保"依赖"按钮能通过光标位置查到正确方法
        editor.setPosition({ lineNumber: s, column: 1 })
      },
    )
  })

  onBeforeUnmount(() => {
    resizeObserver?.disconnect()
    resizeObserver = null
    editor?.dispose()
    editor = null
  })
}
