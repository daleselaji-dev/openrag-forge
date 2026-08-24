import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

type Recipe = { recipe_id: string; name: string; version: string; status: string; hash: string; nodes: { id: string; type: string; label?: string; config?: Record<string, unknown> }[]; edges: { source: string; source_port: string; target: string; target_port: string }[] }
type TraceEvent = { node_id: string; sequence: number; status: string; summary: string; duration_ms: number; details: Record<string, unknown> }
type Run = { run_id: string; recipe_id: string; recipe_hash: string; status: string; answer?: string; evidence: { citation: string; title: string; text: string; score: number; chunk_id: string }[]; trace: TraceEvent[]; safety: Record<string, unknown> }
type Document = { document_id: string; filename: string; status: string; parser_route?: string; parser_confidence?: number; reason_codes: string[]; size_bytes: number; version: number }

const nodeTitles: Record<string, string> = {
  question: '问题', parse_route: '解析路由', native_parser: '文本解析', pdf_parser: 'PDF 解析', office_parser: 'Office 解析', tabular_parser: '表格解析', chunker: 'Chunker', metadata_enricher: 'Metadata', embed_index: 'Embedding / Index', intent_router: 'Intent', metadata_filter: 'Filter', dense_retrieve: 'Dense', sparse_retrieve: 'Sparse / BM25', rrf_fusion: 'RRF', reranker: 'Reranker', context_builder: 'Context', llm_generate: 'LLM', evidence_grade: 'Evidence Grade', policy_gate: '安全门', bounded_corrective: '有限纠错', graph_query: 'Graph', pdf_page_retrieve: 'PDF Page', cache: 'Cache', rate_limit: 'Rate Limit', approval: '人工审批', build_ticket_draft: '工单草稿',
}

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
  const [question, setQuestion] = useState('我发现文档中提到的流程不清楚，客服应该先核对哪些信息？')
  const [kbId, setKbId] = useState('default')
  const [documents, setDocuments] = useState<Document[]>([])
  const [run, setRun] = useState<Run | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('等待运行。')
  const [uploadRoute, setUploadRoute] = useState('auto')
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const requestRef = useRef<AbortController | null>(null)
  const runSequence = useRef(0)

  const selectedRecipe = useMemo(() => recipes.find((recipe) => recipe.recipe_id === selectedRecipeId) || null, [recipes, selectedRecipeId])

  const load = async () => {
    try {
      const [healthBody, recipeBody] = await Promise.all([api<Record<string, any>>('/api/v1/health'), api<{ items: Recipe[] }>('/api/v1/recipes')])
      setHealth(healthBody); setRecipes(recipeBody.items)
      const docs = await api<{ items: Document[] }>(`/api/v1/knowledge-bases/${kbId}/documents`).catch(() => ({ items: [] }))
      setDocuments(docs.items)
    } catch (error) { setMessage(`连接 API 失败：${(error as Error).message}`) }
  }

  useEffect(() => { void load() }, [kbId])

  const canvasNodes = useMemo<Node[]>(() => {
    if (!selectedRecipe) return []
    return selectedRecipe.nodes.map((node, index) => ({ id: node.id, position: { x: (index % 4) * 230, y: Math.floor(index / 4) * 130 }, data: { label: <div><b>{nodeTitles[node.type] || node.type}</b><small>{node.type}</small></div> }, className: selectedNode === node.id ? 'selected-flow-node' : '' }))
  }, [selectedRecipe, selectedNode])

  const canvasEdges = useMemo<Edge[]>(() => selectedRecipe?.edges.map((edge, index) => ({ id: `e-${index}`, source: edge.source, target: edge.target, label: `${edge.source_port} → ${edge.target_port}`, animated: run?.trace.some((event) => event.node_id === edge.source) })) || [], [selectedRecipe, run])

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
      const result = await api<{ document: Document; blocks: number; chunks: number; route: { route: string; confidence: number; reason_codes: string[] } }>(`/api/v1/knowledge-bases/${kbId}/documents?route=${route.replace(/^&route=/, '')}`, { method: 'POST', body: form })
      setDocuments((items) => [result.document, ...items]); setMessage(`解析完成：${result.route.route} · ${result.blocks} blocks · ${result.chunks} chunks`)
    } catch (error) { setMessage(`上传失败：${(error as Error).message}`) }
  }

  return <div className="shell">
    <header className="topbar"><div><span className="eyebrow">OPENRAG FORGE / CONTROL ROOM</span><h1>把 RAG 变成可拆、可跑、可证明的系统。</h1><p>上传文档，选择 Recipe，观察每一个节点的真实输入、输出与 Trace。</p></div><div className="header-badges"><span>LOCAL-FIRST</span><span>TRACEABLE</span><span>PLUGINABLE</span></div></header>
    <main>
      <section className="status-strip"><div><small>PROFILE</small><b>{health?.profile || 'loading'}</b></div><div><small>TRUTH SOURCE</small><b>{health?.truth_source || '—'}</b></div><div><small>DOCUMENTS</small><b>{health?.documents ?? '—'}</b></div><div><small>MODEL</small><b>{health?.models?.chat || '—'}</b></div><button className="ghost" onClick={() => void load()}>刷新状态</button></section>

      <section className="grid-two">
        <article className="panel"><div className="panel-head"><div><span className="eyebrow">01 / KNOWLEDGE BASE</span><h2>上传并查看解析路由</h2></div><select value={uploadRoute} onChange={(event) => setUploadRoute(event.target.value)}><option value="auto">Auto route</option><option value="native_text">Native text</option><option value="html_structure">HTML structure</option><option value="pdf_page_text">PDF page text</option><option value="tabular">Tabular</option></select></div><label className="dropzone"><input type="file" onChange={(event) => event.target.files?.[0] && void upload(event.target.files[0])} /><span>拖入 PDF / DOCX / XLSX / Markdown / HTML / TXT</span><small>原始文件保留；解析失败也不会静默丢弃。</small></label><div className="document-list">{documents.length ? documents.map((doc) => <div className="document-row" key={doc.document_id}><span className={`dot ${doc.status}`} /><div><b>{doc.filename}</b><small>{doc.parser_route || 'not parsed'} · v{doc.version} · {doc.reason_codes.join(', ')}</small></div><em>{doc.status}</em></div>) : <p className="muted">还没有文档。先上传一个文件，马上能看到路由、Block 和 Chunk 计数。</p>}</div></article>
        <article className="panel"><div className="panel-head"><div><span className="eyebrow">02 / RECIPE CATALOG</span><h2>选择一套装配</h2></div><span className="pill">typed DAG</span></div><div className="recipe-list">{recipes.map((recipe) => <button className={`recipe-card ${selectedRecipeId === recipe.recipe_id ? 'active' : ''}`} key={recipe.recipe_id} onClick={() => { setSelectedRecipeId(recipe.recipe_id); setRun(null); setMessage(`已选择 ${recipe.name}`) }}><span>V{recipe.version}</span><b>{recipe.name}</b><small>{recipe.nodes.length} nodes · {recipe.status} · {recipe.hash?.slice(0, 8)}</small></button>)}</div></article>
      </section>

      <section className="panel assembly"><div className="panel-head"><div><span className="eyebrow">03 / ASSEMBLY STUDIO</span><h2>{selectedRecipe?.name || 'Recipe'} <code>{selectedRecipe?.hash?.slice(0, 12)}</code></h2></div><div className="actions"><button className="ghost" onClick={() => void runRecipe('preview')} disabled={busy}>Preview 结构</button><button onClick={() => void runRecipe('run')} disabled={busy}>运行真实链路</button></div></div><div className="assembly-layout"><div className="flow-wrap"><ReactFlow nodes={canvasNodes} edges={canvasEdges} fitView onNodeClick={(_, node) => setSelectedNode(node.id)}><Background gap={22} color="#d7d2c8" /><Controls /><MiniMap /></ReactFlow></div><aside className="inspector"><span className="eyebrow">NODE INSPECTOR</span>{selectedNode && selectedRecipe ? <><h3>{nodeTitles[selectedRecipe.nodes.find((node) => node.id === selectedNode)?.type || ''] || selectedNode}</h3><p>节点 ID：<code>{selectedNode}</code></p><p>当前 Recipe 里的输入、输出和配置都来自已发布图定义。运行后这里会与 Trace 联动。</p>{run?.trace.filter((event) => event.node_id === selectedNode).map((event) => <div className="trace-mini" key={event.sequence}><b>{event.status}</b><span>{event.summary}</span><small>{event.duration_ms} ms</small></div>)}</> : <p className="muted">点击画布节点查看输入、输出、依赖和运行状态。</p>}</aside></div></section>

      <section className="grid-two"><article className="panel query"><div className="panel-head"><div><span className="eyebrow">04 / QUERY CONSOLE</span><h2>同一个问题，切换不同 RAG</h2></div><span className={`pill ${busy ? 'warning' : ''}`}>{busy ? 'running' : 'ready'}</span></div><textarea value={question} onChange={(event) => setQuestion(event.target.value)} /><div className="actions"><button className="ghost" onClick={() => void runRecipe('preview')} disabled={busy}>只预览 Trace</button><button onClick={() => void runRecipe('run')} disabled={busy}>检索并生成</button></div><p className="run-message">{message}</p></article><article className="panel capsule"><div className="panel-head"><div><span className="eyebrow">05 / EVIDENCE CAPSULE</span><h2>可复现结果</h2></div>{run && <a className="download" href={`/api/v1/runs/${run.run_id}/capsule`}>下载 JSON</a>}</div>{run ? <><p className="answer">{run.answer || 'Preview 未生成回答；结构与能力已校验。'}</p><div className="evidence-list">{run.evidence.map((item) => <div className="evidence" key={item.chunk_id}><b>[{item.citation}] {item.title}</b><small>score {item.score} · {item.chunk_id}</small><p>{item.text}</p></div>)}</div></> : <p className="muted">运行后展示回答、引用、安全决策与完整证据胶囊。</p>}</article></section>

      <section className="panel"><div className="panel-head"><div><span className="eyebrow">06 / TRACE</span><h2>每一步到底做了什么</h2></div><span className="pill">{run ? `${run.trace.length} events` : 'no run'}</span></div><div className="trace-list">{run?.trace.map((event) => <div className={`trace-row ${event.status}`} key={event.sequence}><span>{String(event.sequence).padStart(2, '0')}</span><b>{nodeTitles[selectedRecipe?.nodes.find((node) => node.id === event.node_id)?.type || ''] || event.node_id}</b><em>{event.node_id}</em><p>{event.summary}</p><small>{event.duration_ms} ms</small></div>) || <p className="muted">先点击 Preview 或运行真实链路，Trace 会按节点顺序出现。</p>}</div></section>
    </main>
    <footer>OpenRAG Forge / framework extraction baseline · Core is provider-agnostic · CFPB is a replaceable Support Pack</footer>
  </div>
}

export default App
