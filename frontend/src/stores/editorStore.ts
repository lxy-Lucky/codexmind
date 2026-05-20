import { defineStore } from 'pinia'
import { ref, shallowRef } from 'vue'
import { repoApi } from '@/api/repo'
import { useRepoStore } from './repoStore'
import type { FileContent } from '@/types'
import type * as Monaco from 'monaco-editor'

export const useEditorStore = defineStore('editor', () => {
  // ── State ──────────────────────────────────────────────────────────────────
  const currentFile      = ref<FileContent | null>(null)
  const highlightLines   = ref<[number, number] | null>(null)  // [start, end] 1-indexed
  const loading          = ref(false)
  const error            = ref<string | null>(null)

  // Monaco 实例（shallow 避免响应式代理 Monaco 内部对象）
  const editorInstance   = shallowRef<Monaco.editor.IStandaloneCodeEditor | null>(null)
  // 保存高亮装饰 ID，下次调用时先清除旧装饰，避免持续累积
  let _decorationIds: string[] = []

  // ── Actions ────────────────────────────────────────────────────────────────
  async function openFile(path: string, lineStart?: number, lineEnd?: number) {
    const repoStore = useRepoStore()
    if (!repoStore.currentRepo) return

    loading.value = true
    error.value   = null
    try {
      const content = await repoApi.fileContent(repoStore.currentRepo.id, path)
      currentFile.value = content

      if (lineStart != null) {
        highlightLines.value = [lineStart, lineEnd ?? lineStart]
      } else {
        highlightLines.value = null
      }
    } catch (e: any) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }

  function setEditorInstance(editor: Monaco.editor.IStandaloneCodeEditor) {
    editorInstance.value = editor
  }

  /** 跳转到指定行并高亮 */
  function revealAndHighlight(lineStart: number, lineEnd: number) {
    const editor = editorInstance.value
    if (!editor) return

    highlightLines.value = [lineStart, lineEnd]

    editor.revealLineInCenter(lineStart)
    // 传入旧装饰 ID 以清除，再接收新 ID 保存，避免装饰持续堆叠
    _decorationIds = editor.deltaDecorations(_decorationIds, [{
      range: new (window as any).monaco.Range(lineStart, 1, lineEnd, 9999),
      options: {
        isWholeLine: true,
        className: 'monaco-highlight-line',
        glyphMarginClassName: 'monaco-highlight-glyph',
      },
    }])
  }

  /** 获取当前选中或高亮的代码文本 */
  function getSelectedCode(): { code: string; lineStart: number; lineEnd: number } | null {
    const editor = editorInstance.value
    if (!editor) return null

    const selection = editor.getSelection()
    if (selection && !selection.isEmpty()) {
      const model = editor.getModel()
      if (!model) return null
      return {
        code:      model.getValueInRange(selection),
        lineStart: selection.startLineNumber,
        lineEnd:   selection.endLineNumber,
      }
    }

    // 没有选中时使用高亮行范围
    if (highlightLines.value && currentFile.value) {
      const [s, e] = highlightLines.value
      const lines = currentFile.value.content.split('\n').slice(s - 1, e)
      return { code: lines.join('\n'), lineStart: s, lineEnd: e }
    }

    // 回退：整个文件（截断前 300 行）
    if (currentFile.value) {
      const lines = currentFile.value.content.split('\n')
      const sliced = lines.slice(0, 300)
      return { code: sliced.join('\n'), lineStart: 1, lineEnd: sliced.length }
    }

    return null
  }

  return {
    currentFile, highlightLines, loading, error, editorInstance,
    openFile, setEditorInstance, revealAndHighlight, getSelectedCode,
  }
})
