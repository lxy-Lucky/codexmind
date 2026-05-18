import type { AnalysisRequest, SSEChunk } from '@/types'

const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

/**
 * 调用 /api/analyze/stream，通过 fetch ReadableStream 逐 chunk 回调。
 * onChunk: 每收到一个 SSE data 事件调用
 * onDone:  收到 done 事件后调用
 * onError: 出错时调用
 * 返回 abort 函数
 */
export function streamAnalysis(
  req: AnalysisRequest,
  onChunk: (text: string) => void,
  onDone: (ev: SSEChunk) => void,
  onError: (msg: string) => void,
): () => void {
  const controller = new AbortController()

  ;(async () => {
    try {
      const res = await fetch(`${BASE}/api/analyze/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req),
        signal: controller.signal,
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        onError(err.detail ?? '分析请求失败')
        return
      }

      const reader = res.body!.getReader()
      const decoder = new TextDecoder()
      let buf = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buf += decoder.decode(value, { stream: true })

        // SSE 格式：每条消息 "data: {...}\n\n"
        const lines = buf.split('\n\n')
        buf = lines.pop() ?? ''   // 最后一段可能不完整，留给下次

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
          } catch {
            // 忽略不完整 JSON
          }
        }
      }
    } catch (e: any) {
      if (e?.name !== 'AbortError') onError(e?.message ?? '连接中断')
    }
  })()

  return () => controller.abort()
}
