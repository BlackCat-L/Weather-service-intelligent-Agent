/**
 * 前后端契约类型 —— 与后端 backend/api/schemas.py 一一对应。
 *
 * 为什么单独一份：前后端分离后，这里是两边唯一的共识来源。
 * 后端改字段时，这里的类型不跟着改，编辑器就会在调用处报红，
 * 而不是等到运行时前端拿到 undefined 才发现。
 */

export type ChatMode = 'single' | 'multi'

export interface TraceEntry {
  round: number
  name: string
  args: Record<string, unknown>
  result: Record<string, unknown> | null
}

export interface DateCheck {
  dates_in_answer: string[]
  ungrounded_dates: string[]
  verdict: string
}

export interface Verification {
  grounded_rate: number | null
  total: number
  grounded: number
  ungrounded: number[]
  dates: DateCheck | null
  verdict: 'pass' | 'warn' | 'unknown'
  note?: string
}

export interface DonePayload {
  session_id: string
  answer: string
  rounds: number
  mode: ChatMode
}

export interface ToolInfo {
  name: string
  description: string
  parameters: string[]
}

export interface KbHit {
  id?: string
  text?: string
  category?: string
  source?: string
  score?: number
  _neighbor_of?: string
}

export interface KbSearchResult {
  query: string
  method: string
  total_candidates: number
  hits: KbHit[]
  paths?: {
    bm25: Array<{ id: string; score: number; text: string }>
    vector: Array<{ id: string; score: number; text: string }>
  }
}

export interface HealthInfo {
  status: string
  uptime_seconds: number
  llm_provider: string
  llm_model: string
  llm_mock: boolean
  tools_count: number
  knowledge_base: {
    chunks?: number
    vector_index?: boolean
    categories?: Record<string, number>
  }
  sessions: { sessions: number; ttl_seconds: number; max_sessions: number }
}

/** 前端内部使用：一次问答的完整记录 */
export interface Turn {
  id: string
  question: string
  mode: ChatMode
  answer: string
  trace: TraceEntry[]
  verification: Verification | null
  rounds: number
  sessionId: string | null
  /** 执行中的阶段提示（多智能体模式用） */
  stage: string
  running: boolean
  error: string | null
  startedAt: number
  elapsedMs: number | null
}
