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
      { token: 'comment',   foreground: '556a8e', fontStyle: 'italic' },
      { token: 'keyword',   foreground: '00d4ff', fontStyle: 'bold' },
      { token: 'string',    foreground: '26de81' },
      { token: 'number',    foreground: 'ffb347' },
      { token: 'type',      foreground: 'a55eea' },
      { token: 'decorator', foreground: 'ffb347' },
      { token: 'annotation',foreground: 'ffb347' },
    ],
    colors: {
      'editor.background':           '#06090f',
      'editor.foreground':           '#c8d6f0',
      'editor.lineHighlightBackground': '#0f1628',
      'editor.selectionBackground':  '#1a2540',
      'editorLineNumber.foreground': '#2a3a5a',
      'editorLineNumber.activeForeground': '#556a8e',
      'editorCursor.foreground':     '#00d4ff',
      'editor.findMatchBackground':  'rgba(0,212,255,0.25)',
      'editorGutter.background':     '#06090f',
      'scrollbar.shadow':            '#00000000',
      'scrollbarSlider.background':  '#1c2a4a80',
      'scrollbarSlider.hoverBackground': '#263758aa',
    },
  })
}

export function useMonaco(containerRef: Ref<HTMLElement | null>) {
  const editorStore = useEditorStore()
  let editor: Monaco.editor.IStandaloneCodeEditor | null = null
  let decorationIds: string[] = []

  onMounted(async () => {
    if (!containerRef.value) return

    // 配置 Monaco CDN 路径（使用本地 node_modules）
    loader.config({
      paths: { vs: '/node_modules/monaco-editor/min/vs' },
    })

    const monaco = await loader.init()
    defineTheme(monaco)

    editor = monaco.editor.create(containerRef.value, {
      value:           '',
      language:        'java',
      theme:           THEME_NAME,
      fontSize:        13,
      fontFamily:      '"JetBrains Mono", monospace',
      fontLigatures:   true,
      lineNumbers:     'on',
      minimap:         { enabled: true, scale: 1 },
      scrollBeyondLastLine: false,
      wordWrap:        'off',
      readOnly:        true,
      renderLineHighlight: 'all',
      smoothScrolling: true,
      cursorBlinking:  'smooth',
      glyphMargin:     true,
      folding:         true,
      padding:         { top: 16, bottom: 16 },
    })

    // 注入 monaco 到 window，供 editorStore.revealAndHighlight 使用
    ;(window as any).monaco = monaco

    if (editor) editorStore.setEditorInstance(editor)

    // 监听文件变化 → 更新 Monaco model
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

    // 监听高亮行变化 → 更新 decorations
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
            overviewRuler: { color: '#00d4ff', position: monaco.editor.OverviewRulerLane.Left },
          },
        }])
        editor.revealLineInCenter(s)
      },
    )
  })

  onBeforeUnmount(() => {
    editor?.dispose()
    editor = null
  })
}
