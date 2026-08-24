// 与后端 Pydantic 模型对应的共享类型

export type Tunable = {
  name: string
  type: 'int' | 'float' | 'enum' | 'bool' | 'string' | 'json' | 'model'
  min?: number
  max?: number
  options?: string[]
  kind?: 'chat' | 'embedding' | 'reranker'
  description: string
}

export type Plugin = {
  inputs: string[]
  outputs: string[]
  group: string
  bounded?: boolean
  title: string
  runtime: 'implemented' | 'degradable' | 'stub'
  description: string
  why: string
  downstream: string
  tunables: Tunable[]
  config_defaults: Record<string, unknown>
}

export type RecipeNode = { id: string; type: string; label?: string; config?: Record<string, unknown> }
export type RecipeEdge = { source: string; source_port: string; target: string; target_port: string }

export type Recipe = {
  recipe_id: string
  name: string
  version: string
  status: string
  hash: string | null
  nodes: RecipeNode[]
  edges: RecipeEdge[]
}

export type TraceEvent = {
  run_id?: string
  node_id: string
  sequence: number
  status: 'running' | 'completed' | 'failed' | 'skipped'
  summary: string
  duration_ms: number
  details: Record<string, unknown> & { impact?: Record<string, unknown> }
}

export type EvidenceItem = {
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
  evidence: EvidenceItem[]
  trace: TraceEvent[]
  safety: Record<string, unknown>
}

export type RunSummary = {
  run_id: string
  recipe_id: string
  recipe_hash: string
  status: string
  answer_preview: string
  evidence_count: number
  trace_count: number
  safety: Record<string, unknown>
  created_at: string
}

export type DocumentInfo = {
  document_id: string
  knowledge_base_id: string
  filename: string
  media_type: string
  size_bytes: number
  sha256: string
  status: string
  parser_route?: string | null
  parser_confidence?: number | null
  reason_codes: string[]
  version: number
  created_at: string
}

export type ParsedBlock = {
  block_id: string
  document_id: string
  block_type: 'heading' | 'paragraph' | 'table' | 'row' | 'page' | 'code' | 'unknown'
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
  status: string
  profile: string
  truth_source: string
  qdrant: { url: string; status: string; points?: number; error?: string }
  lm_studio: { chat_base_url: string; status: string; models?: string[]; error?: string }
  models: { chat: string; embedding: string; reranker?: string | null }
  documents: number
}

export type KnowledgeBase = { knowledge_base_id: string; name: string; created_at: string }

export type UploadResult = {
  job_id?: string
  document: DocumentInfo
  route: { route: string; confidence: number; reason_codes: string[] }
  blocks: number
  chunks: number
  index?: { status: string; indexed?: number; reason?: string; next_action?: string; embedding_model_id?: string }
  trace_id?: string
  trace?: TraceEvent[]
}
