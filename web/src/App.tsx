// OpenRAG Forge 装配工作台
// 布局：顶栏 / 左侧组件库 / 中央 React Flow 画布 / 右侧检查器（调配 · Block 作用 · Trace）/ 底部运行台

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { DocumentsPanel, type UploadOptions } from './components/DocumentsPanel'
import { DownloadDrawer } from './components/DownloadDrawer'
import { FlowCanvas } from './components/FlowCanvas'
import { ImportsDrawer } from './components/ImportsDrawer'
import { Inspector } from './components/Inspector'
import { Palette } from './components/Palette'
import { RunDock, type DockTab } from './components/RunDock'
import { TopBar } from './components/TopBar'
import type {
  ChunkInfo, DocumentInfo, Health, KnowledgeBase, ModelProfile, ParsedBlock,
  Plugin, Recipe, Run, RunSummary, Scenario, TraceEvent, UploadResult,
} from './types'

function App() {
  // ---- 全局数据 ----
  const [health, setHealth] = useState<Health | null>(null)
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [kbId, setKbId] = useState('default')
  const [recipes, setRecipes] = useState<Recipe[]>([])
  const [plugins, setPlugins] = useState<Record<string, Plugin>>({})
  const [models, setModels] = useState<ModelProfile[]>([])
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [documents, setDocuments] = useState<DocumentInfo[]>([])
  const [history, setHistory] = useState<RunSummary[]>([])

  // ---- 装配状态 ----
  const [selectedRecipeId, setSelectedRecipeId] = useState('v0_1_dense')
  const [working, setWorking] = useState<Record<string, Recipe>>({})
  const undoStack = useRef<{ recipeId: string; recipe: Recipe }[]>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)

  // ---- 运行状态 ----
  const [run, setRun] = useState<Run | null>(null)
  const [question, setQuestion] = useState('客服在处理陌生扣款投诉时，应该先核对哪些信息？')
  const [topK, setTopK] = useState(5)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('等待运行。上传文档、选择 Recipe、点击运行——每一步都会出现在 Trace 里。')
  const requestRef = useRef<AbortController | null>(null)
  const runSequence = useRef(0)

  // ---- 文档 / ingest 状态 ----
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null)
  const [docBlocks, setDocBlocks] = useState<ParsedBlock[]>([])
  const [docChunks, setDocChunks] = useState<ChunkInfo[]>([])
  const [ingestResult, setIngestResult] = useState<UploadResult | null>(null)
  const [ingestTrace, setIngestTrace] = useState<TraceEvent[]>([])
  const [uploadOptions, setUploadOptions] = useState<UploadOptions>({ route: 'auto', embeddingModelId: 'configured-embedding', maxChars: 1200, overlap: 120 })

  // ---- UI 状态 ----
  const [dockTab, setDockTab] = useState<DockTab>('result')
  const [drawer, setDrawer] = useState<'imports' | 'download' | null>(null)

  const selectedRecipe = useMemo(
    () => working[selectedRecipeId] || recipes.find((recipe) => recipe.recipe_id === selectedRecipeId) || null,
    [working, recipes, selectedRecipeId],
  )
  const dirty = Boolean(working[selectedRecipeId])

  // ---- 数据加载 ----
  const load = useCallback(async () => {
    try {
      const [healthBody, kbBody, recipeBody, pluginBody, modelBody, scenarioBody, runsBody] = await Promise.all([
        api.health(), api.knowledgeBases(), api.recipes(), api.plugins(), api.models(), api.scenarios(), api.runs().catch(() => ({ items: [] })),
      ])
      setHealth(healthBody)
      setKnowledgeBases(kbBody.items)
      setRecipes(recipeBody.items)
      setPlugins(pluginBody.nodes)
      setModels(modelBody.items)
      setScenarios(scenarioBody.items)
      setHistory(runsBody.items)
    } catch (error) {
      setMessage(`连接 API 失败：${(error as Error).message}。请确认 uvicorn 已在 18000 端口启动。`)
    }
  }, [])

  const loadDocuments = useCallback(async () => {
    const body = await api.documents(kbId).catch(() => ({ items: [] as DocumentInfo[] }))
    setDocuments(body.items)
  }, [kbId])

  useEffect(() => { void load() }, [load])
  useEffect(() => { void loadDocuments(); setSelectedDocumentId(null) }, [loadDocuments])

  // ---- Recipe 编辑（undo 友好）----
  const mutateRecipe = useCallback((mutator: (recipe: Recipe) => Recipe) => {
    if (!selectedRecipe) return
    undoStack.current.push({ recipeId: selectedRecipeId, recipe: selectedRecipe })
    if (undoStack.current.length > 40) undoStack.current.shift()
    const next = { ...mutator(selectedRecipe), status: 'draft', hash: '' }
    setWorking((state) => ({ ...state, [selectedRecipeId]: next }))
  }, [selectedRecipe, selectedRecipeId])

  const undo = useCallback(() => {
    const last = undoStack.current.pop()
    if (!last) return setMessage('没有可撤销的编辑。')
    setSelectedRecipeId(last.recipeId)
    setWorking((state) => ({ ...state, [last.recipeId]: last.recipe }))
    setMessage('已撤销上一步编辑。')
  }, [])

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT') return
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); undo() }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [undo])

  const addNode = (nodeType: string) => {
    const plugin = plugins[nodeType]
    const id = `${nodeType}_${Date.now().toString(36)}`
    mutateRecipe((recipe) => ({ ...recipe, nodes: [...recipe.nodes, { id, type: nodeType, config: {} }] }))
    setSelectedNodeId(id)
    setMessage(`已加入「${plugin?.title || nodeType}」。连接端口后保存草稿；编译器会拒绝非法连接。`)
  }

  const onConnect = (edge: { source: string; source_port: string; target: string; target_port: string }) => {
    mutateRecipe((recipe) => ({ ...recipe, edges: [...recipe.edges, edge] }))
  }

  const deleteNodes = (nodeIds: string[]) => {
    mutateRecipe((recipe) => ({
      ...recipe,
      nodes: recipe.nodes.filter((node) => !nodeIds.includes(node.id)),
      edges: recipe.edges.filter((edge) => !nodeIds.includes(edge.source) && !nodeIds.includes(edge.target)),
    }))
    if (selectedNodeId && nodeIds.includes(selectedNodeId)) setSelectedNodeId(null)
    setMessage(`已删除 ${nodeIds.length} 个节点（Ctrl+Z 可撤销）。`)
  }

  const deleteEdges = (edges: { source: string; target: string; sourceHandle?: string | null; targetHandle?: string | null }[]) => {
    mutateRecipe((recipe) => ({
      ...recipe,
      edges: recipe.edges.filter((edge) => !edges.some((deleted) =>
        deleted.source === edge.source && deleted.target === edge.target
        && (deleted.sourceHandle ?? edge.source_port) === edge.source_port
        && (deleted.targetHandle ?? edge.target_port) === edge.target_port)),
    }))
  }

  const applyConfig = (nodeId: string, config: Record<string, unknown>) => {
    mutateRecipe((recipe) => ({ ...recipe, nodes: recipe.nodes.map((node) => node.id === nodeId ? { ...node, config } : node) }))
    setMessage('节点配置已写入草稿。保存草稿 → 校验 → 发布后生成新的 recipe hash。')
  }

  const createDraftCopy = () => {
    if (!selectedRecipe) return
    const draft: Recipe = { ...selectedRecipe, recipe_id: `draft_${Date.now().toString(36)}`, name: `${selectedRecipe.name} / Draft`, status: 'draft', hash: '' }
    setRecipes((items) => [...items, draft])
    setWorking((state) => ({ ...state, [draft.recipe_id]: draft }))
    setSelectedRecipeId(draft.recipe_id)
    setRun(null)
    setMessage(`已创建可编辑副本 ${draft.recipe_id}；原 Recipe 不受影响。`)
  }

  const syncSaved = (saved: Recipe) => {
    setRecipes((items) => items.some((item) => item.recipe_id === saved.recipe_id) ? items.map((item) => item.recipe_id === saved.recipe_id ? saved : item) : [...items, saved])
    setWorking((state) => { const next = { ...state }; delete next[saved.recipe_id]; return next })
  }

  const saveDraft = async () => {
    if (!selectedRecipe) return
    try {
      const saved = await api.saveRecipe(selectedRecipe)
      syncSaved(saved)
      setMessage(`草稿已编译保存：hash ${saved.hash?.slice(0, 12)}`)
    } catch (error) { setMessage(`保存失败（编译器拒绝）：${(error as Error).message}`) }
  }

  const validateRecipe = async () => {
    if (!selectedRecipe) return
    try {
      if (dirty) await api.saveRecipe(selectedRecipe)
      const body = await api.validateRecipe(selectedRecipe.recipe_id)
      if (body.recipe) syncSaved(body.recipe)
      setMessage(body.status === 'valid' ? `校验通过：${body.recipe?.hash?.slice(0, 12)}` : `校验失败：${body.errors?.join('，')}`)
    } catch (error) { setMessage(`校验失败：${(error as Error).message}`) }
  }

  const publishRecipe = async () => {
    if (!selectedRecipe) return
    try {
      if (dirty) await api.saveRecipe(selectedRecipe)
      const saved = await api.publishRecipe(selectedRecipe.recipe_id)
      syncSaved(saved)
      setMessage(`已发布：${saved.recipe_id} v${saved.version}（hash ${saved.hash?.slice(0, 12)}）。已发布 Recipe 不可变。`)
    } catch (error) { setMessage(`发布失败：${(error as Error).message}`) }
  }

  // ---- 运行（含请求中止保护）----
  const runRecipe = async (mode: 'run' | 'preview') => {
    if (!selectedRecipe) return
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller
    const sequence = ++runSequence.current
    setBusy(true)
    setMessage(mode === 'preview' ? `正在 dry compile：${selectedRecipe.name}` : `正在运行真实链路：${selectedRecipe.name}`)
    try {
      if (dirty) {
        const saved = await api.saveRecipe(selectedRecipe)
        syncSaved(saved)
      }
      const result = await api.createRun({ knowledge_base_id: kbId, recipe_id: selectedRecipe.recipe_id, question, mode, top_k: topK }, controller.signal)
      if (sequence !== runSequence.current) return
      setRun(result)
      setDockTab(mode === 'preview' ? 'trace' : 'result')
      const fallbacks = result.trace.filter((event) => {
        const impact = (event.details?.impact || {}) as Record<string, unknown>
        return impact.backend === 'lexical_fallback' || impact.fallback_reason || impact.skipped_reason
      }).length
      setMessage(`完成：${result.trace.length} 个 Trace 事件，${result.evidence.length} 条证据${fallbacks ? `，${fallbacks} 个节点走了降级/跳过路径（详见 Trace）` : ''}。`)
      void api.runs().then((body) => setHistory(body.items)).catch(() => undefined)
    } catch (error) {
      if ((error as Error).name !== 'AbortError') setMessage(`运行失败：${(error as Error).message}`)
    } finally {
      if (sequence === runSequence.current) setBusy(false)
    }
  }

  const loadRun = async (runId: string) => {
    try {
      const body = await api.getRun(runId)
      setRun(body)
      if (body.recipe_id && recipes.some((recipe) => recipe.recipe_id === body.recipe_id)) setSelectedRecipeId(body.recipe_id)
      setDockTab('result')
      setMessage(`已载入历史运行 ${runId.slice(0, 16)}…，画布高亮该次 Trace。`)
    } catch (error) { setMessage(`载入运行失败：${(error as Error).message}`) }
  }

  // ---- 文档 ----
  const upload = async (file: File) => {
    setBusy(true)
    setMessage(`上传并解析 ${file.name}…`)
    try {
      const result = await api.upload(kbId, file, {
        route: uploadOptions.route,
        embeddingModelId: uploadOptions.embeddingModelId || undefined,
        maxChars: uploadOptions.maxChars,
        overlap: uploadOptions.overlap,
      })
      setIngestResult(result)
      setIngestTrace(result.trace || [])
      await loadDocuments()
      await selectDocument(result.document.document_id)
      setDockTab('documents')
      const indexNote = result.index?.status === 'deferred' ? `；索引 deferred：${result.index.next_action}` : ''
      setMessage(`解析完成：${result.route.route}（置信度 ${result.route.confidence}）→ ${result.blocks} blocks → ${result.chunks} chunks${indexNote}`)
      void api.health().then(setHealth).catch(() => undefined)
    } catch (error) { setMessage(`上传失败：${(error as Error).message}`) } finally { setBusy(false) }
  }

  const reprocess = async (documentId: string) => {
    setBusy(true)
    try {
      const result = await api.reprocess(documentId, {
        route: uploadOptions.route,
        maxChars: uploadOptions.maxChars,
        overlap: uploadOptions.overlap,
        embeddingModelId: uploadOptions.embeddingModelId || undefined,
      })
      setIngestResult(result)
      setIngestTrace(result.trace || [])
      await loadDocuments()
      await selectDocument(documentId)
      setMessage(`重解析完成：v${result.document.version} · ${result.route.route} → ${result.chunks} chunks。源文件未覆盖。`)
    } catch (error) { setMessage(`重解析失败：${(error as Error).message}`) } finally { setBusy(false) }
  }

  const selectDocument = async (documentId: string | null) => {
    setSelectedDocumentId(documentId)
    if (!documentId) { setDocBlocks([]); setDocChunks([]); return }
    const [blocksBody, chunksBody] = await Promise.all([
      api.blocks(documentId).catch(() => ({ items: [] as ParsedBlock[] })),
      api.chunks(documentId).catch(() => ({ items: [] as ChunkInfo[] })),
    ])
    setDocBlocks(blocksBody.items)
    setDocChunks(chunksBody.items)
  }

  const useScenario = (scenario: Scenario) => {
    if (recipes.some((recipe) => recipe.recipe_id === scenario.recipe_id)) setSelectedRecipeId(scenario.recipe_id)
    setQuestion(scenario.sample_question)
    setRun(null)
    setDockTab('result')
    setMessage(`已加载场景「${scenario.title}」：Recipe 与示例问题就绪。运行前请先导入该场景要求的资料（${scenario.data_requirements.join('、') || '任意文档'}）。`)
  }

  const createKb = async () => {
    const name = window.prompt('新知识库名称：')
    if (!name?.trim()) return
    try {
      const body = await api.createKnowledgeBase(name.trim())
      const kbs = await api.knowledgeBases()
      setKnowledgeBases(kbs.items)
      setKbId(body.knowledge_base_id)
      setMessage(`已创建知识库「${name.trim()}」。`)
    } catch (error) { setMessage(`创建失败：${(error as Error).message}`) }
  }

  return (
    <div className="workbench">
      <TopBar
        health={health}
        knowledgeBases={knowledgeBases}
        kbId={kbId}
        onKbChange={setKbId}
        onCreateKb={() => void createKb()}
        onOpenImports={() => setDrawer('imports')}
        onOpenDownload={() => setDrawer('download')}
        onRefresh={() => void load()}
      />

      <div className="workbench-main">
        <Palette plugins={plugins} onAddNode={addNode} disabled={!selectedRecipe} />

        <section className="canvas-area" aria-label="装配区">
          <div className="canvas-toolbar">
            <label className="recipe-select">Recipe
              <select value={selectedRecipeId} onChange={(event) => { setSelectedRecipeId(event.target.value); setSelectedNodeId(null); setRun(null); setMessage(`已切换到 ${event.target.value}`) }}>
                {recipes.map((recipe) => <option key={recipe.recipe_id} value={recipe.recipe_id}>{recipe.name}（{recipe.recipe_id}）</option>)}
              </select>
            </label>
            {selectedRecipe && (
              <span className={`pill ${dirty ? 'warn' : ''}`} title={selectedRecipe.hash || '未编译'}>
                {dirty ? 'dirty 草稿' : selectedRecipe.status}{selectedRecipe.hash ? ` · ${selectedRecipe.hash.slice(0, 10)}` : ''}
              </span>
            )}
            <div className="toolbar-actions">
              <button className="ghost small" onClick={undo} title="撤销上一步编辑（Ctrl+Z）">撤销</button>
              <button className="ghost small" onClick={createDraftCopy} disabled={!selectedRecipe}>编辑副本</button>
              <button className="ghost small" onClick={() => void saveDraft()} disabled={!selectedRecipe}>保存草稿</button>
              <button className="ghost small" onClick={() => void validateRecipe()} disabled={!selectedRecipe}>校验</button>
              <button className="ghost small" onClick={() => void publishRecipe()} disabled={!selectedRecipe}>发布</button>
              {selectedRecipe && <a className="ghost small export-link" href={api.exportRecipeUrl(selectedRecipe.recipe_id)} download>导出 JSON</a>}
            </div>
          </div>
          <FlowCanvas
            recipe={selectedRecipe}
            plugins={plugins}
            selectedNodeId={selectedNodeId}
            trace={run?.trace || []}
            onSelectNode={setSelectedNodeId}
            onConnect={onConnect}
            onDeleteNodes={deleteNodes}
            onDeleteEdges={deleteEdges}
          />
        </section>

        <Inspector
          recipe={selectedRecipe}
          plugins={plugins}
          models={models}
          selectedNodeId={selectedNodeId}
          trace={run?.trace || []}
          onApplyConfig={applyConfig}
          onSelectNode={setSelectedNodeId}
        />
      </div>

      <RunDock
        tab={dockTab}
        onTab={setDockTab}
        question={question}
        onQuestion={setQuestion}
        topK={topK}
        onTopK={setTopK}
        busy={busy}
        onRun={(mode) => void runRecipe(mode)}
        message={message}
        run={run}
        recipe={selectedRecipe}
        plugins={plugins}
        scenarios={scenarios}
        onUseScenario={useScenario}
        history={history}
        onLoadRun={(runId) => void loadRun(runId)}
        onSelectNode={setSelectedNodeId}
        documentsCount={documents.length}
        documentsSlot={
          <DocumentsPanel
            documents={documents}
            models={models}
            uploadOptions={uploadOptions}
            onUploadOptions={setUploadOptions}
            onUpload={(file) => void upload(file)}
            onReprocess={(documentId) => void reprocess(documentId)}
            selectedDocumentId={selectedDocumentId}
            onSelectDocument={(documentId) => void selectDocument(documentId)}
            blocks={docBlocks}
            chunks={docChunks}
            ingestResult={ingestResult}
            ingestTrace={ingestTrace}
            busy={busy}
          />
        }
      />

      <ImportsDrawer
        open={drawer === 'imports'}
        onClose={() => setDrawer(null)}
        models={models}
        onModelsChanged={() => void api.models().then((body) => setModels(body.items))}
        onRecipesChanged={() => void api.recipes().then((body) => setRecipes(body.items))}
        onScenariosChanged={() => void api.scenarios().then((body) => setScenarios(body.items))}
        onUseEmbedding={(modelId) => setUploadOptions((options) => ({ ...options, embeddingModelId: modelId }))}
        log={setMessage}
      />
      <DownloadDrawer open={drawer === 'download'} onClose={() => setDrawer(null)} recipe={selectedRecipe} run={run} />

      <footer className="workbench-footer">
        OpenRAG Forge · 真相源 SQLite + 本地文件 · Qdrant 为可重建派生索引 · 模型经 OpenAI-compatible 端点接入，权重不进 Web · 每次运行产出 Evidence Capsule
      </footer>
    </div>
  )
}

export default App
