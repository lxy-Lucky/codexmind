import type { AnalysisRequest, ChatMessage, SSEChunk, DocsProgress } from '@/types'
import { i18n, t } from '@/i18n'

const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export function streamAnalysis(
  req: AnalysisRequest & { _history?: { role: string; content: string }[] },
  onChunk: (text: string) => void,
  onDone: (ev: SSEChunk) => void,
  onError: (msg: string) => void,
): () => void {
  const controller = new AbortController()

  ;(async () => {
    try {
      const res = await fetch(`${BASE}/api/analyze/stream`, {
        method:  'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Lang':       i18n.global.locale.value,
        },
        body:    JSON.stringify(req),
        signal:  controller.signal,
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        onError(err.detail ?? t('errors.analyzeFailed'))
        return
      }

      const reader  = res.body!.getReader()
      const decoder = new TextDecoder()
      let buf = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n\n')
        buf = lines.pop() ?? ''

        for (const block of lines) {
          const line = block.trim()
          if (!line.startsWith('data:')) continue
          const json = line.slice(5).trim()
          if (!json) continue
          try {
            const chunk: SSEChunk = JSON.parse(json)
            if (chunk.error) { onError(chunk.error); return }
            if (chunk.done)  { onDone(chunk); return }
            if (chunk.text)  { onChunk(chunk.text) }
          } catch { /* 不完整 JSON */ }
        }
      }
    } catch (e: any) {
      if (e?.name !== 'AbortError') onError(e?.message ?? t('errors.connectionLost'))
    }
  })()

  return () => controller.abort()
}

/** 整文件文档生成（批处理，GET /api/analyze/docs-file） */
export function streamFileDocs(
  repoId: string,
  filePath: string,
  onChunk:    (text: string) => void,
  onProgress: (p: DocsProgress) => void,
  onDone:     (total: number) => void,
  onError:    (msg: string) => void,
): () => void {
  const controller = new AbortController()

  ;(async () => {
    try {
      const params = new URLSearchParams({ repo_id: repoId, file_path: filePath })
      const res = await fetch(`${BASE}/api/analyze/docs-file?${params}`, {
        headers: { 'X-Lang': i18n.global.locale.value },
        signal:  controller.signal,
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        onError(err.detail ?? t('errors.analyzeFailed'))
        return
      }

      const reader  = res.body!.getReader()
      const decoder = new TextDecoder()
      let buf = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buf += decoder.decode(value, { stream: true })
        const blocks = buf.split('\n\n')
        buf = blocks.pop() ?? ''

        for (const block of blocks) {
          const line = block.trim()
          if (!line.startsWith('data:')) continue
          const raw = line.slice(5).trim()
          if (!raw) continue
          try {
            const ev: SSEChunk = JSON.parse(raw)
            if (ev.error)    { onError(ev.error); return }
            if (ev.text)       onChunk(ev.text)
            if (ev.progress)   onProgress({ current: ev.progress.current, total: ev.progress.total, className: ev.progress.class_name })
            if (ev.done)     { onDone(ev.total ?? 0); return }
          } catch { /* 不完整 JSON */ }
        }
      }
    } catch (e: any) {
      if (e?.name !== 'AbortError') onError(e?.message ?? t('errors.connectionLost'))
    }
  })()

  return () => controller.abort()
}

/** 带对话历史的问答请求 */
export function streamChat(
  req: AnalysisRequest,
  history: ChatMessage[],
  onChunk: (text: string) => void,
  onDone:  (ev: SSEChunk) => void,
  onError: (msg: string) => void,
): () => void {
  return streamAnalysis(
    { ...req, _history: history.map(m => ({ role: m.role, content: m.content })) },
    onChunk, onDone, onError,
  )
}
