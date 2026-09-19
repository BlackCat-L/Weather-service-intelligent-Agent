/**
 * HTTP 客户端与 SSE 解析。
 *
 * **为什么不用浏览器原生的 EventSource**
 * EventSource 只支持 GET 请求、不能带请求体，而我们的问题是 POST 提交的。
 * 所以用 `fetch` + `ReadableStream` 手写 SSE 解析——这也是工程上的常见做法。
 *
 * **为什么把解析单独放在这个文件**
 * 组件只消费「事件对象」，不关心事件是 SSE 来的还是 WebSocket 来的。
 * 将来若要换成 WebSocket（比如支持中途打断生成），只需改这一个文件。
 */
import type { HealthInfo, KbSearchResult, ToolInfo } from './types'

const BASE = ''   // 开发走 Vite 代理，生产同源，都写相对路径即可

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const body = await resp.json()
      detail = body.detail || body.message || detail
    } catch {
      /* 响应体不是 JSON，沿用状态码 */
    }
    throw new Error(detail)
  }
  return resp.json() as Promise<T>
}

export const api = {
  health: () => jsonFetch<HealthInfo>('/api/health'),
  tools: () => jsonFetch<{ count: number; tools: ToolInfo[] }>('/api/tools'),

  kbStats: () => jsonFetch<Record<string, unknown>>('/api/kb/stats'),

  kbSearch: (payload: {
    query: string
    categories?: string[] | null
    mode?: 'auto' | 'bm25' | 'hybrid'
    top_k?: number
  }) =>
    jsonFetch<KbSearchResult>('/api/kb/search', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}

/** SSE 事件回调表 */
export interface StreamHandlers {
  onStart?: (d: { question: string; mode: string }) => void
  onStage?: (d: { stage: string }) => void
  onStageDone?: (d: Record<string, unknown>) => void
  onToolCalls?: (d: { round: number; calls: Array<{ name: string; args: string }> }) => void
  onToolResult?: (
    d: { round: number; name: string; args: Record<string, unknown>; result: Record<string, unknown> },
  ) => void
  onVerification?: (d: Record<string, unknown>) => void
  onDone?: (d: { session_id: string; answer: string; rounds: number; mode: string }) => void
  onError?: (message: string) => void
}

/**
 * 发起流式问答。
 *
 * 返回一个 abort 函数，用于用户中途取消——长任务必须能被取消，
 * 否则用户点错一次就只能刷新页面。
 */
export function streamChat(
  payload: { question: string; session_id?: string | null; mode: string },
  handlers: StreamHandlers,
): () => void {
  const controller = new AbortController()

  ;(async () => {
    try {
      const resp = await fetch(BASE + '/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      })
      if (!resp.ok || !resp.body) {
        throw new Error(`连接失败：HTTP ${resp.status}`)
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''

      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // SSE 以空行分隔事件块；一次 read 可能拿到多个块，也可能是半个块，
        // 所以要循环切分并把不完整的部分留在 buffer 里
        let sep: number
        while ((sep = buffer.indexOf('\n\n')) >= 0) {
          const block = buffer.slice(0, sep)
          buffer = buffer.slice(sep + 2)
          dispatch(block, handlers)
        }
      }
    } catch (e) {
      const err = e as Error
      if (err.name === 'AbortError') {
        handlers.onError?.('已取消')
      } else {
        handlers.onError?.(err.message || String(e))
      }
    }
  })()

  return () => controller.abort()
}

/** 解析一个 SSE 事件块：形如 "event: xxx\ndata: {...}" */
function dispatch(block: string, handlers: StreamHandlers) {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!dataLines.length) return

  let data: any
  try {
    data = JSON.parse(dataLines.join('\n'))
  } catch {
    return
  }

  switch (event) {
    case 'start':
      handlers.onStart?.(data)
      break
    case 'stage':
      handlers.onStage?.(data)
      break
    case 'stage_done':
      handlers.onStageDone?.(data)
      break
    case 'tool_calls':
      handlers.onToolCalls?.(data)
      break
    case 'tool_result':
      handlers.onToolResult?.(data)
      break
    case 'verification':
      handlers.onVerification?.(data)
      break
    case 'done':
      handlers.onDone?.(data)
      break
    case 'error':
      handlers.onError?.(data.message || '未知错误')
      break
  }
}
