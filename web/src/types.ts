// 与后端 API 对齐的共享类型定义。

export type NodeImplemented = 'live' | 'fallback' | 'stub'

export type ConfigField = {
  key: string
  label: string
  type: 'number' | 'select' | 'text' | 'boolean' | 'model'
  min?: number
  max?: number
  step?: number
  options?: string[]
  model_kind?: 'chat' | 'embedding' | 'reranker'
  effective?: boolean
  help?: string
}

export type CatalogNode = {
  inputs: string[]
  outputs: string[]
  group: string
  bounded?: boolean
  title: string
  implemented: NodeImplemented
  execution_note: string
  description: string
  teach: { what?: string; tune?: string; pitfalls?: string }
  config_defaults: Record<string, unknown>
  config_schema: ConfigField[]
}

export type RecipeNodeDef = { id: string; type: string; label?: string | null; config?: Record<string, unknown> }
export type RecipeEdgeDef = { source: string; source_port: string; target: string; target_port: string }

export type Recipe = {
  recipe_id: string
  name: string
  version: string
  status: string
  hash: string | null
  nodes: RecipeNodeDef[]
  edges: RecipeEdgeDef[]
  created_at?: string
}

export type TraceEvent = {
  run_id?: string
  node_id: string
  sequence: number
  status: string
  summary: string
  duration_ms: number
  details: Record<string, unknown>
  otel_trace_id?: string | null
  created_at?: string
}

export type Evidence = {
  citation: string
  chunk_id: string
  document_id: string
  title: string
  text: string
  score: number
  metadata?: Record<string, unknown>
}

export type Run = {
  run_id: string
  recipe_id: string
  recipe_hash: string
  status: string
  answer?: string | null
  artifact?: Record<string, unknown> | null
  evidence: Evidence[]
  trace: TraceEvent[]
  safety: Record<string, unknown>
}

export type RunMeta = {
  mode: 'preview' | 'run'
  requestId: string | null
  otelTraceId: string | null
  finishedAt: number
}

export type DocumentInfo = {
  document_id: string
  filename: string
  status: string
  parser_route?: string | null
  parser_confidence?: number | null
  reason_codes: string[]
  size_bytes: number
  version: number
}

export type ParsedBlock = {
  block_id: string
  document_id: string
  block_type: string
  text: string
  order: number
  page?: number | null
  heading_path: string[]
  metadata: Record<string, unknown>
}

export type ChunkInfo = {
  chunk_id: string
  document_id: string
  text: string
  order: number
  block_ids: string[]
  metadata: Record<string, unknown>
}

export type ModelProfile = {
  model_id: string
  display_name: string
  kind: 'chat' | 'embedding' | 'reranker'
  provider: string
  base_url: string
  model_name: string
  parameters: Record<string, unknown>
  source: string
  has_api_key?: boolean
}

export type Scenario = {
  scenario_id: string
  title: string
  business_problem: string
  recipe_id: string
  sample_question: string
  data_requirements: string[]
  trace_expectation: string[]
  source_urls: string[]
  source?: string
}

export type Health = {
  status?: string
  profile?: string
  environment?: string
  truth_source?: string
  documents?: number
  qdrant?: { status?: string }
  lm_studio?: { status?: string }
  models?: { chat?: string; embedding?: string; reranker?: string | null }
  production_readiness?: { warnings: string[] }
}

export type IngestResult = {
  document: DocumentInfo
  blocks: number
  chunks: number
  route: { route: string; confidence: number; reason_codes: string[] }
  index?: Record<string, unknown>
  trace?: TraceEvent[]
  trace_id?: string
}

export type RailTab = 'recipe' | 'data' | 'model' | 'scenario'
export type BottomTab = 'trace' | 'ingest' | 'result'

/** 顶栏三态模式：干净工作台 / 辅助教学（7 步操作课） / 面试讲解（RAG 设计课） */
export type WorkbenchMode = 'work' | 'teach' | 'interview'

export type Message = { text: string; tone: 'info' | 'ok' | 'err' }
