// 统一 API 客户端：所有请求经 /api/v1，错误统一转成 Error(message)

import type {
  ChunkInfo, DocumentInfo, Health, KnowledgeBase, ModelProfile, ParsedBlock,
  Plugin, Recipe, Run, RunSummary, Scenario, UploadResult,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = (body as { detail?: unknown }).detail
    const message = typeof detail === 'string' ? detail : (detail as { message?: string })?.message || JSON.stringify(detail) || '请求失败'
    throw new Error(message)
  }
  return body as T
}

const json = (payload: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
})

export const api = {
  health: () => request<Health>('/api/v1/health'),
  knowledgeBases: () => request<{ items: KnowledgeBase[] }>('/api/v1/knowledge-bases'),
  createKnowledgeBase: (name: string) => request<{ knowledge_base_id: string }>('/api/v1/knowledge-bases', json({ name })),
  recipes: () => request<{ items: Recipe[] }>('/api/v1/recipes'),
  plugins: () => request<{ nodes: Record<string, Plugin>; parsers: string[] }>('/api/v1/plugins'),
  models: () => request<{ items: ModelProfile[] }>('/api/v1/models'),
  scenarios: () => request<{ items: Scenario[] }>('/api/v1/scenarios'),
  documents: (kbId: string) => request<{ items: DocumentInfo[] }>(`/api/v1/knowledge-bases/${kbId}/documents`),
  blocks: (documentId: string) => request<{ items: ParsedBlock[] }>(`/api/v1/documents/${documentId}/blocks`),
  chunks: (documentId: string) => request<{ items: ChunkInfo[] }>(`/api/v1/documents/${documentId}/chunks`),
  runs: () => request<{ items: RunSummary[] }>('/api/v1/runs?limit=30'),
  getRun: (runId: string) => request<Run>(`/api/v1/runs/${runId}`),

  upload: (kbId: string, file: File, options: { route?: string; embeddingModelId?: string; maxChars?: number; overlap?: number }) => {
    const form = new FormData()
    form.append('file', file)
    const params = new URLSearchParams()
    if (options.route && options.route !== 'auto') params.set('route', options.route)
    if (options.embeddingModelId) params.set('embedding_model_id', options.embeddingModelId)
    if (options.maxChars) params.set('max_chars', String(options.maxChars))
    if (options.overlap !== undefined) params.set('overlap', String(options.overlap))
    return request<UploadResult>(`/api/v1/knowledge-bases/${kbId}/documents?${params}`, { method: 'POST', body: form })
  },
  reprocess: (documentId: string, options: { route?: string; maxChars?: number; overlap?: number; embeddingModelId?: string }) => {
    const params = new URLSearchParams()
    if (options.route && options.route !== 'auto') params.set('route', options.route)
    if (options.maxChars) params.set('max_chars', String(options.maxChars))
    if (options.overlap !== undefined) params.set('overlap', String(options.overlap))
    if (options.embeddingModelId) params.set('embedding_model_id', options.embeddingModelId)
    return request<UploadResult>(`/api/v1/documents/${documentId}/reprocess?${params}`, { method: 'POST' })
  },
  rebuildIndex: (kbId: string) => request<{ status: string; indexed?: number }>(`/api/v1/knowledge-bases/${kbId}/index/rebuild`, { method: 'POST' }),

  saveRecipe: (recipe: Recipe) => request<Recipe>('/api/v1/recipes', json({ ...recipe, status: 'draft' })),
  validateRecipe: (recipeId: string) => request<{ status: string; recipe?: Recipe; errors?: string[] }>(`/api/v1/recipes/${recipeId}/validate`, { method: 'POST' }),
  publishRecipe: (recipeId: string) => request<Recipe>(`/api/v1/recipes/${recipeId}/publish`, { method: 'POST' }),
  importRecipe: (payload: unknown) => request<{ status: string; count: number; items: Recipe[] }>('/api/v1/recipes/import', json(payload)),
  exportRecipeUrl: (recipeId: string) => `/api/v1/recipes/${recipeId}/export`,

  registerModel: (model: Record<string, unknown>) => request<{ status: string; model: ModelProfile }>('/api/v1/models', json(model)),
  probeModel: (modelId: string) => request<{ status: string; model_id: string; kind: string; details: Record<string, unknown> }>(`/api/v1/models/${modelId}/probe`, { method: 'POST' }),

  createScenario: (scenario: unknown) => request<{ scenario: Scenario }>('/api/v1/scenarios', json(scenario)),

  createRun: (payload: { knowledge_base_id: string; recipe_id: string; question: string; mode: 'run' | 'preview'; top_k?: number }, signal?: AbortSignal) =>
    request<Run>('/api/v1/runs', { ...json(payload), signal }),
  capsuleUrl: (runId: string) => `/api/v1/runs/${runId}/capsule`,
}
