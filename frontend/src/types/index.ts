// ── Repo ─────────────────────────────────────────────────────────────────────

export type IndexStatus = 0 | 1 | 2 | 3

export interface Repo {
  id: string
  name: string
  root_path: string
  language: string | null
  file_count: number
  chunk_count: number
  indexed: IndexStatus
  created_at: string
  updated_at: string
}

export interface RepoListResponse {
  items: Repo[]
  total: number
}

export interface IndexProgress {
  repo_id: string
  status: IndexStatus
  message: string
  chunk_count: number
  file_count: number
}

// ── File Tree ─────────────────────────────────────────────────────────────────

export interface FileNode {
  name: string
  path: string
  type: 'file' | 'dir'
  language?: string
  size?: number
  children?: FileNode[]
}

export interface FileContent {
  path: string
  language: string
  content: string
  line_count: number
  size: number
}

// ── Search ────────────────────────────────────────────────────────────────────

export type SearchMode = 'semantic' | 'bug' | 'explain' | 'deps'

export interface SearchRequest {
  query: string
  repo_id: string
  mode?: string
  language_filter?: string
  top_k?: number
}

export interface SearchResultItem {
  file_path: string
  line_start: number
  line_end: number
  snippet: string
  score: number
  language: string
  chunk_type: string
}

export interface SearchResponse {
  results: SearchResultItem[]
  total: number
  latency_ms: number
  query: string
}

export interface QueryHistory {
  id: number
  repo_id: string
  mode: string
  query: string
  result_count: number
  latency_ms: number
  created_at: string
}

// ── Analysis ──────────────────────────────────────────────────────────────────

export type AnalysisMode = 'summary' | 'bug' | 'deps' | 'custom'

export interface AnalysisRequest {
  repo_id: string
  file_path: string
  line_start: number
  line_end: number
  code: string
  mode: AnalysisMode
  custom_prompt?: string
  symbol_id?: string
}

export interface BugItem {
  severity: 'Critical' | 'Warning' | 'Suggestion'
  line: number | null
  title: string
  desc: string
  suggestion: string
  code_ref?: string
}

export interface SSEChunk {
  text?: string
  done?: boolean
  confidence?: number
  latency_ms?: number
  mode?: string
  error?: string
}

// ── Chat ──────────────────────────────────────────────────────────────────────

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  streaming?: boolean   // assistant 消息流式输出中
  error?: boolean
}

// ── System Status ─────────────────────────────────────────────────────────────

export interface SystemStatus {
  status: string
  qdrant: { online: boolean; collections: number }
  embedding_model: string
  embedding_device: string
  llm_model: string
  vector_dim: number
  context_window: string
  timestamp: number
}
