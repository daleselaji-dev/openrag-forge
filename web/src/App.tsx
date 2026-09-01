import { useEffect, useMemo, useRef, useState } from 'react'
import { api, apiWithMeta, postJson } from './api'
import { shortHash } from './format'
import { TEACH_STEPS } from './teach'
import type { LessonAction } from './interview/types'
import type {
  BottomTab, CatalogNode, DocumentInfo, Health, IngestResult, Message, ModelProfile,
  RailTab, Recipe, RecipeEdgeDef, Run, RunMeta, Scenario, WorkbenchMode,
} from './types'
import TopBar from './components/TopBar'
import TeachStrip from './components/TeachStrip'
import InterviewPanel from './components/InterviewPanel'
import RecipeCanvas, { layoutRecipe } from './components/RecipeCanvas'
import Inspector from './components/Inspector'
import TracePanel from './components/TracePanel'
import RecipeRail from './components/rails/RecipeRail'
import DataRail from './components/rails/DataRail'
import ModelRail from './components/rails/ModelRail'
import ScenarioRail from './components/rails/ScenarioRail'

const RAIL_TABS: { id: RailTab; label: string; coach: string }[] = [
  { id: 'recipe', label: '装配', coach: 'rail-recipe' },
  { id: 'data', label: '数据', coach: 'rail-data' },
  { id: 'model', label: '模型', coach: 'rail-model' },
  { id: 'scenario', label: '场景', coach: 'rail-scenario' },
]

function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [catalog, setCatalog] = useState<Record<string, CatalogNode>>({})
  const [recipes, setRecipes] = useState<Recipe[]>([])
  const [models, setModels] = useState<ModelProfile[]>([])
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [documents, setDocuments] = useState<DocumentInfo[]>([])
  const [probes, setProbes] = useState<Record<string, { status: string; detail?: string }>>({})

  const [selectedRecipeId, setSelectedRecipeId] = useState('v0_1_dense')
  const [workingRecipe, setWorkingRecipe] = useState<Recipe | null>(null)
  const [dirty, setDirty] = useState(false)
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>({})
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [paletteType, setPaletteType] = useState('dense_retrieve')

  const [kbId] = useState('default')
  const [question, setQuestion] = useState('客服在处理陌生扣款投诉前，应该先核对哪些信息？')
  const [topK, setTopK] = useState(5)
  const [uploadRoute, setUploadRoute] = useState('auto')
  const [embeddingModelId, setEmbeddingModelId] = useState('configured-embedding')
  const [chunkMaxChars, setChunkMaxChars] = useState(1200)
  const [chunkOverlap, setChunkOverlap] = useState(120)

  const [run, setRun] = useState<Run | null>(null)
  const [runMeta, setRunMeta] = useState<RunMeta | null>(null)
  const [ingest, setIngest] = useState<IngestResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<Message>({ text: '就绪。左侧选择 Recipe 与数据，点击节点调配，然后 Preview 或真实运行。', tone: 'info' })

  const [railTab, setRailTab] = useState<RailTab>('recipe')
  const [bottomTab, setBottomTab] = useState<BottomTab>('trace')

  // 三态模式：work（干净工作台）/ teach（7 步操作课）/ interview（RAG 设计课）。
  // 兼容旧存储：orf.teach.on=1 且未存过模式时，迁移为 teach。
  const [mode, setMode] = useState<WorkbenchMode>(() => {
    const stored = localStorage.getItem('orf.mode') as WorkbenchMode | null
    if (stored === 'work' || stored === 'teach' || stored === 'interview') return stored
    return localStorage.getItem('orf.teach.on') === '1' ? 'teach' : 'work'
  })
  const teachOn = mode === 'teach'
  const interviewOn = mode === 'interview'
  const [teachStep, setTeachStep] = useState(() => {
    const stored = Number(localStorage.getItem('orf.teach.step') || 0)
    return Number.isInteger(stored) && stored >= 0 && stored < TEACH_STEPS.length ? stored : 0
  })
  const [teachCollapsed, setTeachCollapsed] = useState(() => localStorage.getItem('orf.teach.collapsed') === '1')
  const [interviewCollapsed, setInterviewCollapsed] = useState(() => localStorage.getItem('orf.interview.collapsed') === '1')

  const requestRef = useRef<AbortController | null>(null)
  const runSeq = useRef(0)

  useEffect(() => { localStorage.setItem('orf.mode', mode) }, [mode])
  useEffect(() => { localStorage.setItem('orf.teach.step', String(teachStep)) }, [teachStep])
  useEffect(() => { localStorage.setItem('orf.teach.collapsed', teachCollapsed ? '1' : '0') }, [teachCollapsed])
  useEffect(() => { localStorage.setItem('orf.interview.collapsed', interviewCollapsed ? '1' : '0') }, [interviewCollapsed])

  const selectRecipe = (recipeId: string, list?: Recipe[]) => {
    const pool = list || recipes
    const target = pool.find((item) => item.recipe_id === recipeId) || pool[0] || null
    setSelectedRecipeId(target?.recipe_id || recipeId)
    setWorkingRecipe(target)
    setDirty(false)
    setSelectedNodeId(null)
    setRun(null)
    setRunMeta(null)
    if (target) setPositions(layoutRecipe(target))
  }

  const load = async () => {
    try {
      const [healthBody, recipeBody, pluginBody, modelBody, scenarioBody] = await Promise.all([
        api<Health>('/api/v1/health'),
        api<{ items: Recipe[] }>('/api/v1/recipes'),
        api<{ nodes: Record<string, CatalogNode> }>('/api/v1/plugins'),
        api<{ items: ModelProfile[] }>('/api/v1/models'),
        api<{ items: Scenario[] }>('/api/v1/scenarios'),
      ])
      setHealth(healthBody)
      setCatalog(pluginBody.nodes)
      setModels(modelBody.items)
      setScenarios(scenarioBody.items)
      // 刷新时保留本地未保存草稿，避免覆盖用户正在调配的 Recipe
      const server = recipeBody.items
      let merged = server
      if (dirty && workingRecipe) {
        merged = server.some((item) => item.recipe_id === workingRecipe.recipe_id)
          ? server.map((item) => (item.recipe_id === workingRecipe.recipe_id ? workingRecipe : item))
          : [...server, workingRecipe]
      }
      setRecipes(merged)
      if (!workingRecipe && merged.length) selectRecipe(selectedRecipeId, merged)
      const docs = await api<{ items: DocumentInfo[] }>(`/api/v1/knowledge-bases/${kbId}/documents`).catch(() => ({ items: [] }))
      setDocuments(docs.items)
    } catch (error) {
      setMessage({ text: `连接 API 失败：${(error as Error).message}`, tone: 'err' })
    }
  }

  useEffect(() => { void load() }, [])

  // ---- Recipe 变更（自动把内置发布版 fork 成草稿副本）----

  const applyRecipeChange = (change: (recipe: Recipe) => Recipe, note?: string) => {
    if (!workingRecipe) return
    let base = workingRecipe
    let forked = false
    if (base.status === 'published') {
      base = { ...base, recipe_id: `${base.recipe_id}_draft_${Date.now().toString(36)}`, name: `${base.name} · 草稿`, status: 'draft', hash: null }
      forked = true
    }
    const next: Recipe = { ...change(base), status: 'draft', hash: null }
    setWorkingRecipe(next)
    setDirty(true)
    if (forked) {
      setRecipes((items) => [...items, next])
      setSelectedRecipeId(next.recipe_id)
      setMessage({ text: `内置 Recipe 不可直接修改：已自动创建草稿副本「${next.name}」，改动落在副本上。`, tone: 'info' })
    } else {
      setRecipes((items) => items.map((item) => (item.recipe_id === next.recipe_id ? next : item)))
      if (note) setMessage({ text: note, tone: 'info' })
    }
  }

  const updateNodeConfig = (nodeId: string, config: Record<string, unknown>) => {
    applyRecipeChange((recipe) => ({ ...recipe, nodes: recipe.nodes.map((node) => (node.id === nodeId ? { ...node, config } : node)) }))
  }

  const addNodeOfType = (nodeType: string) => {
    if (!workingRecipe || !catalog[nodeType]) return
    const id = `${nodeType}_${Date.now().toString(36)}`
    const maxX = Math.max(0, ...Object.values(positions).map((position) => position.x))
    setPositions((current) => ({ ...current, [id]: { x: maxX + 250, y: 40 } }))
    applyRecipeChange((recipe) => ({ ...recipe, nodes: [...recipe.nodes, { id, type: nodeType, config: {} }] }))
    setSelectedNodeId(id)
    setMessage({ text: `已加入「${catalog[nodeType]?.title || nodeType}」。从端口拖线连接，编译器会拒绝类型不兼容的连线。`, tone: 'info' })
  }

  const addPaletteNode = () => addNodeOfType(paletteType)

  const connectEdge = (edge: RecipeEdgeDef) => {
    if (!workingRecipe) return
    const exists = workingRecipe.edges.some((item) => item.source === edge.source && item.target === edge.target && item.source_port === edge.source_port && item.target_port === edge.target_port)
    if (exists) return
    applyRecipeChange((recipe) => ({ ...recipe, edges: [...recipe.edges, edge] }), `已连接 ${edge.source}.${edge.source_port} → ${edge.target}.${edge.target_port}（待保存草稿）`)
  }

  const deleteNodes = (ids: string[]) => {
    if (!ids.length) return
    const removed = new Set(ids)
    applyRecipeChange((recipe) => ({
      ...recipe,
      nodes: recipe.nodes.filter((node) => !removed.has(node.id)),
      edges: recipe.edges.filter((edge) => !removed.has(edge.source) && !removed.has(edge.target)),
    }), `已删除节点 ${ids.join(', ')}（待保存草稿）`)
    setSelectedNodeId((current) => (current && removed.has(current) ? null : current))
  }

  const deleteEdges = (edges: RecipeEdgeDef[]) => {
    if (!edges.length) return
    applyRecipeChange((recipe) => ({
      ...recipe,
      edges: recipe.edges.filter((edge) => !edges.some((removed) => removed.source === edge.source && removed.target === edge.target && removed.source_port === edge.source_port && removed.target_port === edge.target_port)),
    }), '已删除连线（待保存草稿）')
  }

  const createDraft = () => {
    if (!workingRecipe) return
    const draft: Recipe = { ...workingRecipe, recipe_id: `${workingRecipe.recipe_id.replace(/_draft_\w+$/, '')}_draft_${Date.now().toString(36)}`, name: `${workingRecipe.name.replace(/ · 草稿$/, '')} · 草稿`, status: 'draft', hash: null }
    setRecipes((items) => [...items, draft])
    setSelectedRecipeId(draft.recipe_id)
    setWorkingRecipe(draft)
    setDirty(true)
    setRun(null)
    setRunMeta(null)
    setMessage({ text: `已创建草稿副本 ${draft.recipe_id}。`, tone: 'ok' })
  }

  // ---- 与服务端的 Recipe 同步 ----

  const saveDraftInternal = async (): Promise<Recipe | null> => {
    if (!workingRecipe) return null
    try {
      const saved = await api<Recipe>('/api/v1/recipes', postJson({ ...workingRecipe, status: 'draft' }))
      setWorkingRecipe(saved)
      setDirty(false)
      setRecipes((items) => (items.some((item) => item.recipe_id === saved.recipe_id) ? items.map((item) => (item.recipe_id === saved.recipe_id ? saved : item)) : [...items, saved]))
      return saved
    } catch (error) {
      setMessage({ text: `保存草稿失败（编译器拒绝）：${(error as Error).message}`, tone: 'err' })
      return null
    }
  }

  const saveDraft = async () => {
    const saved = await saveDraftInternal()
    if (saved) setMessage({ text: `草稿已保存并编译：hash ${shortHash(saved.hash, 12)}`, tone: 'ok' })
  }

  const validateRecipe = async () => {
    if (!workingRecipe) return
    let target = workingRecipe
    if (dirty || !target.hash) {
      const saved = await saveDraftInternal()
      if (!saved) return
      target = saved
    }
    try {
      const response = await fetch(`/api/v1/recipes/${target.recipe_id}/validate`, { method: 'POST' })
      const body = await response.json() as { status: string; recipe?: Recipe; errors?: string[] }
      if (body.status === 'valid' && body.recipe) {
        setWorkingRecipe(body.recipe)
        setRecipes((items) => items.map((item) => (item.recipe_id === body.recipe!.recipe_id ? body.recipe! : item)))
        setMessage({ text: `校验通过：hash ${shortHash(body.recipe.hash, 12)}`, tone: 'ok' })
      } else {
        setMessage({ text: `校验失败：${(body.errors || []).join('；')}`, tone: 'err' })
      }
    } catch (error) {
      setMessage({ text: `校验失败：${(error as Error).message}`, tone: 'err' })
    }
  }

  const publishRecipe = async () => {
    if (!workingRecipe) return
    let target = workingRecipe
    if (dirty || !target.hash) {
      const saved = await saveDraftInternal()
      if (!saved) return
      target = saved
    }
    try {
      const published = await api<Recipe>(`/api/v1/recipes/${target.recipe_id}/publish`, { method: 'POST' })
      setWorkingRecipe(published)
      setRecipes((items) => items.map((item) => (item.recipe_id === published.recipe_id ? published : item)))
      setMessage({ text: `Recipe 已发布：${published.name} v${published.version}（发布版不可变，再改会自动生成新草稿）`, tone: 'ok' })
    } catch (error) {
      setMessage({ text: `发布失败：${(error as Error).message}`, tone: 'err' })
    }
  }

  // ---- 运行 ----

  const runRecipe = async (mode: 'preview' | 'run') => {
    if (!workingRecipe) return
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller
    const seq = ++runSeq.current
    setBusy(true)
    setMessage({ text: mode === 'preview' ? `Preview 编译：${workingRecipe.name}（不调模型、不写索引）` : `真实运行：${workingRecipe.name}`, tone: 'info' })
    try {
      let recipeId = workingRecipe.recipe_id
      if (dirty || !workingRecipe.hash) {
        const saved = await saveDraftInternal()
        if (!saved) { setBusy(false); return }
        recipeId = saved.recipe_id
      }
      const { body, meta } = await apiWithMeta<Run>('/api/v1/runs', postJson({ knowledge_base_id: kbId, recipe_id: recipeId, question, top_k: topK, mode }, controller.signal))
      if (seq !== runSeq.current) return
      const otelFromTrace = body.trace.find((event) => event.otel_trace_id)?.otel_trace_id || null
      setRun(body)
      setRunMeta({ mode, requestId: meta.requestId, otelTraceId: meta.otelTraceId || otelFromTrace, finishedAt: Date.now() })
      setBottomTab('trace')
      setMessage({ text: `${mode === 'preview' ? 'Preview' : '运行'}完成：${body.trace.length} 个 Trace 事件 · ${body.evidence.length} 条证据`, tone: 'ok' })
    } catch (error) {
      if ((error as Error).name !== 'AbortError') setMessage({ text: `运行失败：${(error as Error).message}`, tone: 'err' })
    } finally {
      if (seq === runSeq.current) setBusy(false)
    }
  }

  // ---- 数据 / 模型 / 场景 ----

  const upload = async (file: File) => {
    setBusy(true)
    setMessage({ text: `上传并解析 ${file.name}…`, tone: 'info' })
    const form = new FormData()
    form.append('file', file)
    const params = new URLSearchParams()
    if (uploadRoute !== 'auto') params.set('route', uploadRoute)
    if (embeddingModelId) params.set('embedding_model_id', embeddingModelId)
    params.set('max_chars', String(chunkMaxChars))
    params.set('overlap', String(Math.min(chunkOverlap, Math.floor(chunkMaxChars / 2))))
    try {
      const body = await api<IngestResult>(`/api/v1/knowledge-bases/${kbId}/documents?${params.toString()}`, { method: 'POST', body: form })
      setDocuments((items) => [body.document, ...items.filter((item) => item.document_id !== body.document.document_id)])
      setIngest(body)
      setBottomTab('ingest')
      const deferred = body.index && body.index.status === 'deferred'
      setMessage({ text: `解析完成：${body.route.route} · ${body.blocks} blocks · ${body.chunks} chunks${deferred ? ' · 索引暂缓（Embedding/Qdrant 未就绪，真相源已保存）' : ''}`, tone: 'ok' })
    } catch (error) {
      setMessage({ text: `上传失败：${(error as Error).message}`, tone: 'err' })
    } finally {
      setBusy(false)
    }
  }

  const reprocess = async (documentId: string) => {
    setBusy(true)
    try {
      const params = new URLSearchParams({ max_chars: String(chunkMaxChars), overlap: String(Math.min(chunkOverlap, Math.floor(chunkMaxChars / 2))), embedding_model_id: embeddingModelId })
      if (uploadRoute !== 'auto') params.set('route', uploadRoute)
      const body = await api<{ document: DocumentInfo; blocks: number; chunks: number }>(`/api/v1/documents/${documentId}/reprocess?${params.toString()}`, { method: 'POST' })
      setDocuments((items) => items.map((item) => (item.document_id === documentId ? body.document : item)))
      setMessage({ text: `重解析完成：${body.blocks} blocks · ${body.chunks} chunks（使用当前 Chunker 配置）`, tone: 'ok' })
    } catch (error) {
      setMessage({ text: `重解析失败：${(error as Error).message}`, tone: 'err' })
    } finally {
      setBusy(false)
    }
  }

  const rebuildIndex = async () => {
    setBusy(true)
    try {
      const body = await api<Record<string, unknown>>(`/api/v1/knowledge-bases/${kbId}/index/rebuild`, { method: 'POST' })
      setMessage({ text: `索引重建完成：${JSON.stringify(body.indexed ?? body.status ?? 'ok')}`, tone: 'ok' })
    } catch (error) {
      setMessage({ text: `索引重建失败：${(error as Error).message}`, tone: 'err' })
    } finally {
      setBusy(false)
    }
  }

  const registerModel = async (form: { model_id: string; display_name: string; kind: string; base_url: string; model_name: string; api_key?: string }): Promise<boolean> => {
    try {
      const body = await api<{ model: ModelProfile }>('/api/v1/models', postJson({ ...form, parameters: {}, source: 'endpoint' }))
      setModels((items) => [...items.filter((item) => item.model_id !== body.model.model_id), body.model])
      setMessage({ text: `模型已注册：${body.model.model_id}。在节点检查器把 model_ref 绑到对应节点即可使用。`, tone: 'ok' })
      return true
    } catch (error) {
      setMessage({ text: `模型注册失败：${(error as Error).message}`, tone: 'err' })
      return false
    }
  }

  const probeModel = async (modelId: string) => {
    try {
      const body = await api<{ status: string; details: Record<string, unknown> }>(`/api/v1/models/${modelId}/probe`, { method: 'POST' })
      setProbes((items) => ({ ...items, [modelId]: { status: body.status, detail: JSON.stringify(body.details) } }))
      setMessage({ text: `模型探测：${modelId} → ${body.status === 'ready' ? '可达' : '不可达'}`, tone: body.status === 'ready' ? 'ok' : 'err' })
    } catch (error) {
      setMessage({ text: `模型探测失败：${(error as Error).message}`, tone: 'err' })
    }
  }

  const useScenario = (scenario: Scenario) => {
    if (recipes.some((item) => item.recipe_id === scenario.recipe_id)) selectRecipe(scenario.recipe_id)
    setQuestion(scenario.sample_question)
    setMessage({ text: `已加载示范「${scenario.title}」：Recipe 与问题已就位。先在「数据」页导入该场景要求的资料，再运行并对照 Trace 观察清单。`, tone: 'info' })
  }

  const importScenario = async (file: File) => {
    try {
      const definition = JSON.parse(await file.text()) as Record<string, unknown>
      const body = await api<{ scenario: Scenario }>('/api/v1/scenarios', postJson(definition))
      setScenarios((items) => [...items.filter((item) => item.scenario_id !== body.scenario.scenario_id), body.scenario])
      setMessage({ text: `已导入示范：${body.scenario.title}`, tone: 'ok' })
    } catch (error) {
      setMessage({ text: `示范导入失败：${(error as Error).message}`, tone: 'err' })
    }
  }

  // ---- 面试讲解：内容里的工作台动作（加载 Recipe / 切页签 / 预填问题） ----

  const runLessonAction = (action: LessonAction) => {
    if (action.recipeId) {
      if (recipes.some((item) => item.recipe_id === action.recipeId)) {
        selectRecipe(action.recipeId)
        setRailTab('recipe')
      } else {
        setMessage({ text: `未找到 Recipe「${action.recipeId}」。`, tone: 'err' })
      }
    }
    if (action.question) setQuestion(action.question)
    if (action.railTab) setRailTab(action.railTab)
    if (action.bottomTab) setBottomTab(action.bottomTab)
  }

  // ---- 教学 coach 高亮 ----

  const coachTarget = teachOn && !teachCollapsed ? TEACH_STEPS[teachStep]?.coachTarget : undefined

  const paletteGroups = useMemo(() => {
    const groups: Record<string, string[]> = {}
    for (const [type, spec] of Object.entries(catalog)) {
      if (!groups[spec.group]) groups[spec.group] = []
      groups[spec.group].push(type)
    }
    return groups
  }, [catalog])

  const railContent = railTab === 'recipe' ? (
    <RecipeRail recipes={recipes} selectedRecipeId={selectedRecipeId} catalog={catalog} dirty={dirty} teachOn={teachOn} onSelectRecipe={(id) => selectRecipe(id)} onCreateDraft={createDraft} />
  ) : railTab === 'data' ? (
    <DataRail documents={documents} models={models} kbId={kbId} uploadRoute={uploadRoute} embeddingModelId={embeddingModelId} chunkMaxChars={chunkMaxChars} chunkOverlap={chunkOverlap} busy={busy} teachOn={teachOn} setUploadRoute={setUploadRoute} setEmbeddingModelId={setEmbeddingModelId} setChunkMaxChars={setChunkMaxChars} setChunkOverlap={setChunkOverlap} onUpload={(file) => void upload(file)} onReprocess={(id) => void reprocess(id)} onRebuildIndex={() => void rebuildIndex()} />
  ) : railTab === 'model' ? (
    <ModelRail models={models} teachOn={teachOn} probes={probes} onRegister={registerModel} onProbe={(id) => void probeModel(id)} />
  ) : (
    <ScenarioRail scenarios={scenarios} teachOn={teachOn} onUseScenario={useScenario} onImportScenario={(file) => void importScenario(file)} />
  )

  return (
    <div className={`app${teachOn ? ' teach-on' : ''}${interviewOn ? ' interview-on' : ''}`}>
      <TopBar health={health} mode={mode} onSetMode={setMode} onRefresh={() => void load()} />
      {teachOn && (
        <TeachStrip
          stepIndex={teachStep}
          collapsed={teachCollapsed}
          onSelectStep={setTeachStep}
          onToggleCollapsed={() => setTeachCollapsed((current) => !current)}
          onGo={(rail, bottom) => { if (rail) setRailTab(rail); if (bottom) setBottomTab(bottom) }}
        />
      )}
      <div className={`workspace${interviewOn ? (interviewCollapsed ? ' interview-collapsed' : ' interview-open') : ''}`}>
        {interviewOn && (
          <InterviewPanel
            catalog={catalog}
            recipe={workingRecipe}
            collapsed={interviewCollapsed}
            onToggleCollapsed={() => setInterviewCollapsed((current) => !current)}
            onSelectNode={setSelectedNodeId}
            onAddNode={addNodeOfType}
            onAction={runLessonAction}
          />
        )}
        <nav className="rail-tabs">
          {RAIL_TABS.map((tab) => (
            <button key={tab.id} className={`rail-tab${railTab === tab.id ? ' active' : ''}${coachTarget === tab.coach ? ' coach-pulse' : ''}`} onClick={() => setRailTab(tab.id)}>
              {tab.label}
            </button>
          ))}
        </nav>
        <div className="rail-body">{railContent}</div>
        <div className="center-col">
          <div className="canvas-toolbar">
            <div className="recipe-identity">
              <b>{workingRecipe?.name || '未选择 Recipe'}</b>
              <span className={`recipe-status ${workingRecipe?.status || ''}`}>{workingRecipe ? `v${workingRecipe.version} · ${workingRecipe.status}${dirty ? ' · 未保存' : ''}` : ''}</span>
              <code className="mono">{shortHash(workingRecipe?.hash, 12)}</code>
            </div>
            <div className="toolbar-actions">
              <select className="palette-select" value={paletteType} onChange={(event) => setPaletteType(event.target.value)} title="选择要加入画布的组件">
                {Object.entries(paletteGroups).map(([group, types]) => (
                  <optgroup key={group} label={group}>
                    {types.map((type) => (
                      <option key={type} value={type}>
                        {catalog[type]?.title || type}{catalog[type]?.implemented !== 'live' ? `（${catalog[type]?.implemented === 'stub' ? '占位' : '退化'}）` : ''} · {type}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
              <button className="ghost small" onClick={addPaletteNode} disabled={!workingRecipe}>加入节点</button>
              <span className="toolbar-divider" />
              <button className="ghost small" onClick={() => void saveDraft()} disabled={!workingRecipe || (!dirty && workingRecipe?.status !== 'draft')}>保存草稿</button>
              <button className="ghost small" onClick={() => void validateRecipe()} disabled={!workingRecipe}>校验</button>
              <button className="ghost small" onClick={() => void publishRecipe()} disabled={!workingRecipe}>发布</button>
            </div>
          </div>
          <RecipeCanvas
            recipe={workingRecipe}
            catalog={catalog}
            positions={positions}
            selectedNodeId={selectedNodeId}
            runTrace={run?.trace || null}
            isPreview={runMeta?.mode === 'preview'}
            coachActive={coachTarget === 'canvas'}
            onSelectNode={setSelectedNodeId}
            onConnectEdge={connectEdge}
            onMoveNode={(id, position) => setPositions((current) => ({ ...current, [id]: position }))}
            onDeleteNodes={deleteNodes}
            onDeleteEdges={deleteEdges}
          />
          <TracePanel
            question={question}
            setQuestion={setQuestion}
            topK={topK}
            setTopK={setTopK}
            busy={busy}
            message={message}
            run={run}
            runMeta={runMeta}
            ingest={ingest}
            recipe={workingRecipe}
            catalog={catalog}
            bottomTab={bottomTab}
            setBottomTab={setBottomTab}
            selectedNodeId={selectedNodeId}
            onSelectNode={setSelectedNodeId}
            onRun={(mode) => void runRecipe(mode)}
            teachOn={teachOn}
            coachRun={coachTarget === 'run-buttons'}
            coachResult={coachTarget === 'bottom-result'}
          />
        </div>
        <Inspector
          recipe={workingRecipe}
          selectedNodeId={selectedNodeId}
          catalog={catalog}
          models={models}
          run={run}
          runMeta={runMeta}
          teachOn={teachOn}
          interviewOn={interviewOn}
          coachActive={coachTarget === 'inspector'}
          dirty={dirty}
          onUpdateNodeConfig={updateNodeConfig}
          onDeleteNode={(id) => deleteNodes([id])}
        />
      </div>
    </div>
  )
}

export default App
