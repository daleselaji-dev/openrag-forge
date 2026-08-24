import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  addEdge,
  Background,
  Handle,
  Controls,
  MiniMap,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

type Recipe = { recipe_id: string; name: string; version: string; status: string; hash: string; nodes: { id: string; type: string; label?: string; config?: Record<string, unknown> }[]; edges: { source: string; source_port: string; target: string; target_port: string }[] }
type TraceEvent = { node_id: string; sequence: number; status: string; summary: string; duration_ms: number; details: Record<string, unknown> }
type Run = { run_id: string; recipe_id: string; recipe_hash: string; status: string; answer?: string; artifact?: Record<string, unknown>; evidence: { citation: string; title: string; text: string; score: number; chunk_id: string }[]; trace: TraceEvent[]; safety: Record<string, unknown> }
type Document = { document_id: string; filename: string; status: string; parser_route?: string; parser_confidence?: number; reason_codes: string[]; size_bytes: number; version: number }
type Plugin = { inputs: string[]; outputs: string[]; group: string; bounded?: boolean }
type ModelProfile = { model_id: string; display_name: string; kind: 'chat' | 'embedding' | 'reranker'; provider: string; base_url: string; model_name: string; parameters: Record<string, unknown>; source: string }
type Scenario = { scenario_id: string; title: string; business_problem: string; recipe_id: string; sample_question: string; data_requirements: string[]; trace_expectation: string[]; source_urls: string[]; source?: string }
type ForgeNodeData = { label: string; nodeType: string; inputs: string[]; outputs: string[] }
type ForgeFlowNode = Node<ForgeNodeData, 'forge'>

const nodeTitles: Record<string, string> = {
  question: '问题', parse_route: '解析路由', native_parser: '文本解析', pdf_parser: 'PDF 解析', office_parser: 'Office 解析', tabular_parser: '表格解析', chunker: 'Chunker', metadata_enricher: 'Metadata', embed_index: 'Embedding / Index', intent_router: 'Intent', metadata_filter: 'Filter', dense_retrieve: 'Dense', sparse_retrieve: 'Sparse / BM25', rrf_fusion: 'RRF', reranker: 'Reranker', context_builder: 'Context', llm_generate: 'LLM', evidence_grade: 'Evidence Grade', policy_gate: '安全门', bounded_corrective: '有限纠错', graph_query: 'Graph', pdf_page_retrieve: 'PDF Page', cache: 'Cache', rate_limit: 'Rate Limit', approval: '人工审批', build_ticket_draft: '工单草稿',
}

function ForgeNode({ data, selected }: NodeProps<ForgeFlowNode>) {
  return <div className={`forge-node ${selected ? 'selected-flow-node' : ''}`}>
    {data.inputs.map((port, index) => <Handle key={`in-${port}`} type="target" position={Position.Left} id={port} style={{ top: `${((index + 1) / (data.inputs.length + 1)) * 100}%` }} />)}
    <b>{data.label}</b><small>{data.nodeType}</small>
    {data.outputs.map((port, index) => <Handle key={`out-${port}`} type="source" position={Position.Right} id={port} style={{ top: `${((index + 1) / (data.outputs.length + 1)) * 100}%` }} />)}
  </div>
}

const nodeTypes = { forge: ForgeNode }

const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, init)
  const body = await response.json()
  if (!response.ok) throw new Error(body.detail?.message || body.detail || '请求失败')
  return body as T
}

function App() {
  const [health, setHealth] = useState<Record<string, any> | null>(null)
  const [recipes, setRecipes] = useState<Recipe[]>([])
  const [selectedRecipeId, setSelectedRecipeId] = useState('v0_1_dense')
  const [workingRecipe, setWorkingRecipe] = useState<Recipe | null>(null)
  const [plugins, setPlugins] = useState<Record<string, Plugin>>({})
  const [models, setModels] = useState<ModelProfile[]>([])
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [paletteType, setPaletteType] = useState('dense_retrieve')
  const [configText, setConfigText] = useState('{}')
  const [modelForm, setModelForm] = useState({ model_id: '', display_name: '', kind: 'chat', base_url: 'http://localhost:23145/v1', model_name: '' })
  const [question, setQuestion] = useState('我发现文档中提到的流程不清楚，客服应该先核对哪些信息？')
  const [kbId, setKbId] = useState('default')
  const [documents, setDocuments] = useState<Document[]>([])
  const [run, setRun] = useState<Run | null>(null)
  const [ingestTrace, setIngestTrace] = useState<TraceEvent[]>([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('等待运行。')
  const [uploadRoute, setUploadRoute] = useState('auto')
  const [embeddingModelId, setEmbeddingModelId] = useState('configured-embedding')
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const requestRef = useRef<AbortController | null>(null)
  const runSequence = useRef(0)

  const selectedRecipe = useMemo(() => workingRecipe || recipes.find((recipe) => recipe.recipe_id === selectedRecipeId) || null, [recipes, selectedRecipeId, workingRecipe])

  const load = async () => {
    try {
      const [healthBody, recipeBody, pluginBody, modelBody, scenarioBody] = await Promise.all([api<Record<string, any>>('/api/v1/health'), api<{ items: Recipe[] }>('/api/v1/recipes'), api<{ nodes: Record<string, Plugin> }>('/api/v1/plugins'), api<{ items: ModelProfile[] }>('/api/v1/models'), api<{ items: Scenario[] }>('/api/v1/scenarios')])
      setHealth(healthBody); setRecipes(recipeBody.items); setPlugins(pluginBody.nodes); setModels(modelBody.items); setScenarios(scenarioBody.items)
      if (!workingRecipe && recipeBody.items.length) setWorkingRecipe(recipeBody.items.find((recipe) => recipe.recipe_id === selectedRecipeId) || recipeBody.items[0])
      const docs = await api<{ items: Document[] }>(`/api/v1/knowledge-bases/${kbId}/documents`).catch(() => ({ items: [] }))
      setDocuments(docs.items)
    } catch (error) { setMessage(`连接 API 失败：${(error as Error).message}`) }
  }

  useEffect(() => { void load() }, [kbId])

  useEffect(() => {
    const node = selectedRecipe?.nodes.find((item) => item.id === selectedNode)
    setConfigText(JSON.stringify(node?.config || {}, null, 2))
  }, [selectedNode, selectedRecipe])

  const canvasNodes = useMemo<Node[]>(() => {
    if (!selectedRecipe) return []
    return selectedRecipe.nodes.map((node, index) => ({ id: node.id, type: 'forge', position: { x: (index % 4) * 230, y: Math.floor(index / 4) * 130 }, data: { label: nodeTitles[node.type] || node.type, nodeType: node.type, inputs: plugins[node.type]?.inputs || [], outputs: plugins[node.type]?.outputs || [] }, selected: selectedNode === node.id }))
  }, [selectedRecipe, selectedNode, plugins])

  const canvasEdges = useMemo<Edge[]>(() => selectedRecipe?.edges.map((edge, index) => ({ id: `e-${index}`, source: edge.source, target: edge.target, sourceHandle: edge.source_port, targetHandle: edge.target_port, label: `${edge.source_port} → ${edge.target_port}`, animated: run?.trace.some((event) => event.node_id === edge.source) })) || [], [selectedRecipe, run])

  const updateWorkingRecipe = (next: Recipe) => {
    setWorkingRecipe(next)
    setRecipes((items) => items.map((item) => item.recipe_id === next.recipe_id ? next : item))
  }

  const addPaletteNode = () => {
    if (!selectedRecipe) return
    const id = `${paletteType}_${Date.now().toString(36)}`
    updateWorkingRecipe({ ...selectedRecipe, status: 'draft', hash: '', nodes: [...selectedRecipe.nodes, { id, type: paletteType, config: {} }] })
    setSelectedNode(id); setMessage(`已加入 ${nodeTitles[paletteType] || paletteType}，请连接端口并保存草稿。`)
  }

  const onConnect = (connection: Connection) => {
    if (!selectedRecipe || !connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) return
    const edge = { source: connection.source, source_port: connection.sourceHandle, target: connection.target, target_port: connection.targetHandle }
    updateWorkingRecipe({ ...selectedRecipe, status: 'draft', hash: '', edges: [...selectedRecipe.edges, edge] })
  }

  const createDraft = () => {
    if (!selectedRecipe) return
    const draft = { ...selectedRecipe, recipe_id: `draft_${Date.now().toString(36)}`, name: `${selectedRecipe.name} / Draft`, status: 'draft', hash: '' }
    setRecipes((items) => [...items, draft]); setSelectedRecipeId(draft.recipe_id); setWorkingRecipe(draft); setRun(null); setMessage('已创建可编辑草稿。')
  }

  const saveRecipe = async () => {
    if (!selectedRecipe) return
    try { const saved = await api<Recipe>('/api/v1/recipes', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...selectedRecipe, status: 'draft' }) }); updateWorkingRecipe(saved); setMessage(`草稿已保存：${saved.hash.slice(0, 12)}`) } catch (error) { setMessage(`保存草稿失败：${(error as Error).message}`) }
  }

  const validateRecipe = async () => {
    if (!selectedRecipe) return
    try { const body = await api<{ status: string; recipe?: Recipe; errors?: string[] }>(`/api/v1/recipes/${selectedRecipe.recipe_id}/validate`, { method: 'POST' }); if (body.recipe) updateWorkingRecipe(body.recipe); setMessage(body.status === 'valid' ? `校验通过：${body.recipe?.hash.slice(0, 12)}` : `校验失败：${body.errors?.join(', ')}`) } catch (error) { setMessage(`校验失败：${(error as Error).message}`) }
  }

  const publishRecipe = async () => {
    if (!selectedRecipe) return
    try { const saved = await api<Recipe>(`/api/v1/recipes/${selectedRecipe.recipe_id}/publish`, { method: 'POST' }); updateWorkingRecipe(saved); setMessage(`Recipe 已发布：${saved.version}`) } catch (error) { setMessage(`发布失败：${(error as Error).message}`) }
  }

  const saveNodeConfig = () => {
    if (!selectedRecipe || !selectedNode) return
    try { const config = JSON.parse(configText); updateWorkingRecipe({ ...selectedRecipe, status: 'draft', hash: '', nodes: selectedRecipe.nodes.map((node) => node.id === selectedNode ? { ...node, config } : node) }); setMessage('节点配置已更新，等待保存草稿。') } catch { setMessage('配置必须是合法 JSON。') }
  }

  const setNodeModel = (modelId: string) => {
    try { setConfigText(JSON.stringify({ ...JSON.parse(configText), model_ref: modelId }, null, 2)) } catch { setConfigText(JSON.stringify({ model_ref: modelId }, null, 2)) }
  }

  const registerModel = async (event: FormEvent) => {
    event.preventDefault()
    try { const model = await api<ModelProfile>('/api/v1/models', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...modelForm, parameters: {}, source: 'endpoint' }) }); setModels((items) => [...items.filter((item) => item.model_id !== model.model_id), model]); setMessage(`模型已注册：${model.model_id}`) } catch (error) { setMessage(`模型注册失败：${(error as Error).message}`) }
  }

  const runRecipe = async (mode: 'preview' | 'run' | 'dry_run') => {
    requestRef.current?.abort()
    const controller = new AbortController(); requestRef.current = controller
    const sequence = ++runSequence.current
    setBusy(true); setMessage(`${mode === 'preview' ? '正在编译 Preview' : '正在运行 Recipe'}：${selectedRecipe?.name || selectedRecipeId}`)
    try {
      const result = await api<Run>('/api/v1/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ knowledge_base_id: kbId, recipe_id: selectedRecipeId, question, mode }), signal: controller.signal })
      if (sequence !== runSequence.current) return
      setRun(result); setMessage(`完成：${result.trace.length} 个 Trace 节点，${result.evidence.length} 条证据`)
    } catch (error) {
      if ((error as Error).name !== 'AbortError') setMessage(`运行失败：${(error as Error).message}`)
    } finally { if (sequence === runSequence.current) setBusy(false) }
  }

  const upload = async (file: File) => {
    setMessage(`上传并解析 ${file.name}…`)
    const form = new FormData(); form.append('file', file)
    const route = uploadRoute === 'auto' ? '' : `&route=${encodeURIComponent(uploadRoute)}`
    try {
      const query = new URLSearchParams({ route: route.replace(/^&route=/, ''), embedding_model_id: embeddingModelId })
      const result = await api<{ document: Document; blocks: number; chunks: number; route: { route: string; confidence: number; reason_codes: string[] }; index?: { status: string; embedding_model_id?: string }; trace?: TraceEvent[] }>(`/api/v1/knowledge-bases/${kbId}/documents?${query.toString()}`, { method: 'POST', body: form })
      setDocuments((items) => [result.document, ...items]); setIngestTrace(result.trace || []); setMessage(`解析完成：${result.route.route} · ${result.blocks} blocks · ${result.chunks} chunks`)
    } catch (error) { setMessage(`上传失败：${(error as Error).message}`) }
  }

  const useScenario = (scenario: Scenario) => {
    const recipe = recipes.find((item) => item.recipe_id === scenario.recipe_id)
    if (recipe) { setSelectedRecipeId(recipe.recipe_id); setWorkingRecipe(recipe) }
    setQuestion(scenario.sample_question); setRun(null); setMessage(`已加载示范：${scenario.title}。先导入该场景要求的文档，再运行问题。`)
  }

  const importScenario = async (file: File) => {
    try { const definition = JSON.parse(await file.text()); const body = await api<{ scenario: Scenario }>('/api/v1/scenarios', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(definition) }); setScenarios((items) => [...items.filter((item) => item.scenario_id !== body.scenario.scenario_id), body.scenario]); setMessage(`已导入示范：${body.scenario.title}`) } catch (error) { setMessage(`示范导入失败：${(error as Error).message}`) }
  }

  return <div className="shell">
    <header className="topbar"><div><span className="eyebrow">OPENRAG FORGE / CONTROL ROOM</span><h1>把 RAG 变成可拆、可跑、可证明的系统。</h1><p>上传文档，选择 Recipe，观察每一个节点的真实输入、输出与 Trace。</p></div><div className="header-badges"><span>LOCAL-FIRST</span><span>TRACEABLE</span><span>PLUGINABLE</span></div></header>
    <main>
      <section className="status-strip"><div><small>PROFILE</small><b>{health?.profile || 'loading'}</b></div><div><small>TRUTH SOURCE</small><b>{health?.truth_source || '—'}</b></div><div><small>DOCUMENTS</small><b>{health?.documents ?? '—'}</b></div><div><small>MODEL</small><b>{health?.models?.chat || '—'}</b></div><button className="ghost" onClick={() => void load()}>刷新状态</button></section>

      <section className="grid-two">
        <article className="panel"><div className="panel-head"><div><span className="eyebrow">01 / CUSTOM DOCUMENT PARSING</span><h2>上传并运行解析 Recipe</h2></div><div className="actions"><select value={uploadRoute} onChange={(event) => setUploadRoute(event.target.value)}><option value="auto">Auto route</option><option value="native_text">Native text</option><option value="html_structure">HTML structure</option><option value="pdf_page_text">PDF page text</option><option value="tabular">Tabular</option></select><select value={embeddingModelId} onChange={(event) => setEmbeddingModelId(event.target.value)}>{models.filter((model) => model.kind === 'embedding').map((model) => <option key={model.model_id} value={model.model_id}>{model.display_name}</option>)}</select></div></div><p className="muted">选择 Embedding 模型后上传自己的文档。系统会执行路由、解析、Chunk、Embedding 和 Qdrant 写入，并把每一步归档到文档版本。</p><label className="dropzone"><input type="file" onChange={(event) => event.target.files?.[0] && void upload(event.target.files[0])} /><span>拖入 PDF / DOCX / XLSX / Markdown / HTML / TXT</span><small>原始文件保留；解析失败也不会静默丢弃。</small></label><div className="document-list">{documents.length ? documents.map((doc) => <div className="document-row" key={doc.document_id}><span className={`dot ${doc.status}`} /><div><b>{doc.filename}</b><small>{doc.parser_route || 'not parsed'} · v{doc.version} · {doc.reason_codes.join(', ')}</small></div><em>{doc.status}</em></div>) : <p className="muted">还没有文档。先上传一个文件，马上能看到路由、Block 和 Chunk 计数。</p>}</div>{ingestTrace.length > 0 && <div className="ingest-trace"><b>Custom Ingest Trace</b>{ingestTrace.map((event) => <div className={`ingest-row ${event.status}`} key={event.sequence}><span>{event.sequence}</span><strong>{event.node_id}</strong><p>{event.summary}</p><small>{event.status}</small></div>)}</div>}</article>
        <article className="panel"><div className="panel-head"><div><span className="eyebrow">02 / RECIPE CATALOG</span><h2>选择一套装配</h2></div><span className="pill">typed DAG</span></div><div className="recipe-list">{recipes.map((recipe) => <button className={`recipe-card ${selectedRecipeId === recipe.recipe_id ? 'active' : ''}`} key={recipe.recipe_id} onClick={() => { setSelectedRecipeId(recipe.recipe_id); setWorkingRecipe(recipe); setRun(null); setMessage(`已选择 ${recipe.name}`) }}><span>V{recipe.version}</span><b>{recipe.name}</b><small>{recipe.nodes.length} nodes · {recipe.status} · {recipe.hash?.slice(0, 8)}</small></button>)}</div></article>
      </section>

      <section className="panel scenario-panel"><div className="panel-head"><div><span className="eyebrow">REAL SCENARIO GALLERY</span><h2>真实企业场景示范</h2></div><div className="actions"><label className="import-scenario">导入 Scenario JSON<input type="file" accept="application/json,.json" onChange={(event) => event.target.files?.[0] && void importScenario(event.target.files[0])} /></label><span className="pill">问题 → Recipe → Trace</span></div></div><p className="muted">每个示范都声明业务问题、所需资料、默认装配和应观察的 Trace。点击后会加载示例问题；运行前仍需导入对应知识库文档。</p><div className="scenario-grid">{scenarios.map((scenario) => <article className="scenario-card" key={scenario.scenario_id}><span className="eyebrow">{scenario.recipe_id} · {scenario.source || 'builtin'}</span><h3>{scenario.title}</h3><p>{scenario.business_problem}</p><small>资料：{scenario.data_requirements.join(' · ')}</small><code>{scenario.sample_question}</code><button className="ghost" onClick={() => useScenario(scenario)}>加载示范问题</button></article>)}</div></section>

      <section className="panel model-panel"><div className="panel-head"><div><span className="eyebrow">MODEL REGISTRY / PROVIDER-AGNOSTIC</span><h2>导入与注册模型</h2></div><span className="pill">权重留在模型服务</span></div><p className="muted">这里注册 LM Studio、Ollama、vLLM 或云端 OpenAI-compatible Endpoint。网页只保存连接配置和模型 ID，不执行用户上传的权重文件。</p><form className="model-form" onSubmit={registerModel}><input placeholder="model_id" value={modelForm.model_id} onChange={(event) => setModelForm({ ...modelForm, model_id: event.target.value })} required /><input placeholder="显示名称" value={modelForm.display_name} onChange={(event) => setModelForm({ ...modelForm, display_name: event.target.value })} required /><select value={modelForm.kind} onChange={(event) => setModelForm({ ...modelForm, kind: event.target.value })}><option value="chat">Chat</option><option value="embedding">Embedding</option><option value="reranker">Reranker</option></select><input placeholder="base_url，例如 http://localhost:23145/v1" value={modelForm.base_url} onChange={(event) => setModelForm({ ...modelForm, base_url: event.target.value })} required /><input placeholder="服务中的 model name" value={modelForm.model_name} onChange={(event) => setModelForm({ ...modelForm, model_name: event.target.value })} required /><button type="submit">注册模型</button></form><div className="model-list">{models.map((model) => <div className="model-row" key={model.model_id}><b>{model.display_name}</b><span>{model.kind}</span><small>{model.model_id} · {model.base_url}</small></div>)}</div></section>

      <section className="panel assembly"><div className="panel-head"><div><span className="eyebrow">03 / ASSEMBLY STUDIO</span><h2>{selectedRecipe?.name || 'Recipe'} <code>{selectedRecipe?.hash?.slice(0, 12)}</code></h2></div><div className="actions"><button className="ghost" onClick={createDraft} disabled={!selectedRecipe}>编辑副本</button><button className="ghost" onClick={() => void saveRecipe()} disabled={!selectedRecipe}>保存草稿</button><button className="ghost" onClick={() => void validateRecipe()} disabled={!selectedRecipe}>校验</button><button className="ghost" onClick={() => void publishRecipe()} disabled={!selectedRecipe}>发布</button><button className="ghost" onClick={() => void runRecipe('preview')} disabled={busy}>Preview 结构</button><button onClick={() => void runRecipe('run')} disabled={busy}>运行真实链路</button></div></div><div className="palette-row"><span>添加组件</span><select value={paletteType} onChange={(event) => setPaletteType(event.target.value)}>{Object.keys(plugins).map((type) => <option key={type} value={type}>{nodeTitles[type] || type} / {type}</option>)}</select><button className="ghost" onClick={addPaletteNode}>加入画布</button><small>拖动节点、从右侧端口连线；Recipe Compiler 会在保存/校验时拒绝非法连接。</small></div><div className="assembly-layout"><div className="flow-wrap"><ReactFlow nodes={canvasNodes} edges={canvasEdges} nodeTypes={nodeTypes} fitView onConnect={onConnect} onNodeClick={(_, node) => setSelectedNode(node.id)}><Background gap={22} color="#d7d2c8" /><Controls /><MiniMap /></ReactFlow></div><aside className="inspector"><span className="eyebrow">NODE INSPECTOR</span>{selectedNode && selectedRecipe ? <><h3>{nodeTitles[selectedRecipe.nodes.find((node) => node.id === selectedNode)?.type || ''] || selectedNode}</h3><p>节点 ID：<code>{selectedNode}</code></p><p>输入/输出端口来自插件目录。配置会写入 Recipe 草稿，不会直接修改已发布版本。</p><label className="model-select">模型绑定<select value={(() => { try { return String(JSON.parse(configText).model_ref || '') } catch { return '' } })()} onChange={(event) => setNodeModel(event.target.value)}><option value="">不绑定 / 使用默认</option>{models.map((model) => <option key={model.model_id} value={model.model_id}>{model.display_name} · {model.kind}</option>)}</select></label><textarea className="node-config" value={configText} onChange={(event) => setConfigText(event.target.value)} /><button className="ghost" onClick={saveNodeConfig}>保存节点配置</button>{run?.trace.filter((event) => event.node_id === selectedNode).map((event) => <div className="trace-mini" key={event.sequence}><b>{event.status}</b><span>{event.summary}</span><small>{event.duration_ms} ms</small></div>)}</> : <p className="muted">点击画布节点查看输入、输出、依赖和运行状态。</p>}</aside></div></section>

      <section className="grid-two"><article className="panel query"><div className="panel-head"><div><span className="eyebrow">04 / QUERY CONSOLE</span><h2>同一个问题，切换不同 RAG</h2></div><span className={`pill ${busy ? 'warning' : ''}`}>{busy ? 'running' : 'ready'}</span></div><textarea value={question} onChange={(event) => setQuestion(event.target.value)} /><div className="actions"><button className="ghost" onClick={() => void runRecipe('preview')} disabled={busy}>只预览 Trace</button><button onClick={() => void runRecipe('run')} disabled={busy}>检索并生成</button></div><p className="run-message">{message}</p></article><article className="panel capsule"><div className="panel-head"><div><span className="eyebrow">05 / EVIDENCE CAPSULE</span><h2>可复现结果</h2></div>{run && <a className="download" href={`/api/v1/runs/${run.run_id}/capsule`}>下载 JSON</a>}</div>{run ? <><p className="answer">{run.answer || 'Preview 未生成回答；结构与能力已校验。'}</p>{run.artifact && <pre className="artifact-preview">{JSON.stringify(run.artifact, null, 2)}</pre>}<div className="evidence-list">{run.evidence.map((item) => <div className="evidence" key={item.chunk_id}><b>[{item.citation}] {item.title}</b><small>score {item.score} · {item.chunk_id}</small><p>{item.text}</p></div>)}</div></> : <p className="muted">运行后展示回答、引用、安全决策、Agent 草稿与完整证据胶囊。</p>}</article></section>

      <section className="panel"><div className="panel-head"><div><span className="eyebrow">06 / TRACE</span><h2>每一步到底做了什么</h2></div><span className="pill">{run ? `${run.trace.length} events` : 'no run'}</span></div><div className="trace-list">{run?.trace.map((event) => <div className={`trace-row ${event.status}`} key={event.sequence}><span>{String(event.sequence).padStart(2, '0')}</span><b>{nodeTitles[selectedRecipe?.nodes.find((node) => node.id === event.node_id)?.type || ''] || event.node_id}</b><em>{event.node_id}</em><p>{event.summary}</p><small>{event.duration_ms} ms</small></div>) || <p className="muted">先点击 Preview 或运行真实链路，Trace 会按节点顺序出现。</p>}</div></section>
    </main>
    <footer>OpenRAG Forge / framework extraction baseline · Core is provider-agnostic · CFPB is a replaceable Support Pack</footer>
  </div>
}

export default App
