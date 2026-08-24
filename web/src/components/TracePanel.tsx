import { useMemo } from 'react'
import type { BottomTab, CatalogNode, IngestResult, Message, Recipe, Run, RunMeta, TraceEvent } from '../types'
import { fmtMs, shortHash, STATUS_LABELS } from '../format'
import { EXECUTION_LABELS } from '../teach'

type Props = {
  question: string
  setQuestion: (value: string) => void
  topK: number
  setTopK: (value: number) => void
  busy: boolean
  message: Message
  run: Run | null
  runMeta: RunMeta | null
  ingest: IngestResult | null
  recipe: Recipe | null
  catalog: Record<string, CatalogNode>
  bottomTab: BottomTab
  setBottomTab: (tab: BottomTab) => void
  selectedNodeId: string | null
  onSelectNode: (id: string | null) => void
  onRun: (mode: 'preview' | 'run') => void
  teachOn: boolean
  coachRun: boolean
  coachResult: boolean
}

function TraceRows({ events, catalog, recipe, selectedNodeId, onSelectNode, clickable }: { events: TraceEvent[]; catalog: Record<string, CatalogNode>; recipe: Recipe | null; selectedNodeId: string | null; onSelectNode: (id: string | null) => void; clickable: boolean }) {
  const maxDuration = Math.max(0.001, ...events.map((event) => event.duration_ms))
  return (
    <div className="trace-list" role="table">
      {events.map((event) => {
        const nodeType = String(event.details?.node_type || recipe?.nodes.find((node) => node.id === event.node_id)?.type || '')
        const title = catalog[nodeType]?.title || nodeType || event.node_id
        const execution = EXECUTION_LABELS[String(event.details?.execution || '')]
        const selected = clickable && selectedNodeId === event.node_id
        return (
          <div
            key={`${event.node_id}-${event.sequence}`}
            className={`trace-row ${event.status}${selected ? ' selected' : ''}${clickable ? ' clickable' : ''}`}
            onClick={() => clickable && onSelectNode(selectedNodeId === event.node_id ? null : event.node_id)}
          >
            <span className="trace-seq">{String(event.sequence).padStart(2, '0')}</span>
            <div className="trace-node">
              <b>{title}</b>
              <code>{event.node_id}</code>
            </div>
            <span className={`status-chip ${event.status}`}>{STATUS_LABELS[event.status] || event.status}</span>
            <span className={`exec-chip ${execution ? execution.tone : 'live'}`}>{execution ? execution.label : '—'}</span>
            <p className="trace-summary">{event.summary}</p>
            <div className="trace-duration">
              <em>{fmtMs(event.duration_ms)}</em>
              <span className="duration-bar"><i style={{ width: `${Math.max(2, (event.duration_ms / maxDuration) * 100)}%` }} /></span>
            </div>
            <details onClick={(clickEvent) => clickEvent.stopPropagation()}>
              <summary>细节</summary>
              <pre>{JSON.stringify(event.details, null, 2)}</pre>
            </details>
          </div>
        )
      })}
    </div>
  )
}

function SafetyChips({ safety }: { safety: Record<string, unknown> }) {
  const gate = Array.isArray(safety.request_safety_gate) ? (safety.request_safety_gate as string[]) : []
  return (
    <div className="safety-chips">
      <span className={`safety-chip ${safety.side_effects ? 'bad' : 'good'}`}>副作用：{safety.side_effects ? '有' : '无'}</span>
      {safety.mode === 'preview' && <span className="safety-chip preview">Preview 模式</span>}
      {'human_review' in safety && <span className={`safety-chip ${safety.human_review ? 'warn' : 'good'}`}>人工复核：{safety.human_review ? '需要' : '不需要'}</span>}
      {gate.length > 0 && <span className="safety-chip bad">安全门拒绝：{gate.join(', ')}</span>}
    </div>
  )
}

export default function TracePanel(props: Props) {
  const { question, setQuestion, topK, setTopK, busy, message, run, runMeta, ingest, recipe, catalog, bottomTab, setBottomTab, selectedNodeId, onSelectNode, onRun, teachOn, coachRun, coachResult } = props

  const isPreview = runMeta?.mode === 'preview'
  const totalMs = useMemo(() => (run ? run.trace.reduce((sum, event) => sum + event.duration_ms, 0) : 0), [run])

  return (
    <section className="bottom-dock">
      <div className="query-console">
        <textarea
          rows={2}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="输入要验证的问题…"
        />
        <label className="topk-field">
          <span>Top K</span>
          <input type="number" min={1} max={20} value={topK} onChange={(event) => { const parsed = Number(event.target.value); if (!Number.isNaN(parsed)) setTopK(Math.min(20, Math.max(1, parsed))) }} />
        </label>
        <div className={`run-buttons${coachRun ? ' coach-pulse' : ''}`}>
          <button className="ghost" onClick={() => onRun('preview')} disabled={busy} title="只编译结构，不调用模型、不写索引">Preview 编译</button>
          <button className="primary" onClick={() => onRun('run')} disabled={busy}>{busy ? '运行中…' : '真实运行'}</button>
        </div>
      </div>
      <div className="console-status">
        <span className={`message-line ${message.tone}`}>{message.text}</span>
        {run && runMeta && (
          <div className="run-meta">
            <span className={`mode-badge ${isPreview ? 'preview' : 'live'}`}>{isPreview ? 'PREVIEW · 未调用模型' : 'LIVE · 真实链路'}</span>
            <span className="meta-chip mono" title="业务运行 ID">{run.run_id}</span>
            <span className="meta-chip mono" title="Recipe 编译哈希">recipe {shortHash(run.recipe_hash)}</span>
            {runMeta.requestId && <span className="meta-chip mono" title="X-Request-Id：结构化日志关联键">req {runMeta.requestId}</span>}
            {runMeta.otelTraceId
              ? <span className="meta-chip mono" title="X-Trace-Id：粘贴到 Jaeger 查看瀑布图">otel {runMeta.otelTraceId}</span>
              : <span className="meta-chip dim" title="设置 OPENRAG_OTEL_ENABLED=true 后，这里会显示可在 Jaeger 检索的 trace id">OTel 未启用</span>}
            <span className="meta-chip">Σ {fmtMs(totalMs)}</span>
          </div>
        )}
      </div>

      <div className="dock-tabs">
        <button className={bottomTab === 'trace' ? 'active' : ''} onClick={() => setBottomTab('trace')}>运行 Trace {run ? `· ${run.trace.length}` : ''}</button>
        <button className={bottomTab === 'ingest' ? 'active' : ''} onClick={() => setBottomTab('ingest')}>Ingest Trace {ingest?.trace ? `· ${ingest.trace.length}` : ''}</button>
        <button className={`${bottomTab === 'result' ? 'active' : ''}${coachResult ? ' coach-pulse' : ''}`} onClick={() => setBottomTab('result')}>回答与证据 {run ? `· ${run.evidence.length}` : ''}</button>
      </div>

      <div className="dock-body">
        {bottomTab === 'trace' && (
          run ? (
            <>
              {teachOn && (
                <p className="teach-hint">教学：点击任意 Trace 行会高亮画布对应节点；「execution」列告诉你这一步是真实执行、降级回退还是占位直通。{isPreview ? ' 当前是 Preview——所有节点只做编译检查。' : ''}</p>
              )}
              <TraceRows events={run.trace} catalog={catalog} recipe={recipe} selectedNodeId={selectedNodeId} onSelectNode={onSelectNode} clickable />
            </>
          ) : (
            <p className="muted pad">先 Preview 或真实运行，Trace 会按节点顺序出现在这里。</p>
          )
        )}
        {bottomTab === 'ingest' && (
          ingest?.trace?.length ? (
            <>
              <div className="ingest-summary">
                <b>{ingest.document.filename}</b>
                <span>路由 {ingest.route.route}（置信度 {ingest.route.confidence}）</span>
                <span>{ingest.blocks} blocks · {ingest.chunks} chunks</span>
                <span className="mono">{ingest.trace_id}</span>
              </div>
              {teachOn && <p className="teach-hint">教学：Ingest Trace 证明文档如何进入知识库。route 的 reason_codes 解释路由依据；index 一步若为「暂缓」，说明 Embedding/Qdrant 未就绪——真相源仍完整，之后可重建索引。</p>}
              <TraceRows events={ingest.trace} catalog={catalog} recipe={null} selectedNodeId={null} onSelectNode={() => undefined} clickable={false} />
            </>
          ) : (
            <p className="muted pad">上传文档后，这里展示解析路由 → Chunk → Metadata → 索引的完整 Ingest Trace。</p>
          )
        )}
        {bottomTab === 'result' && (
          run ? (
            <div className="result-grid">
              <div className="result-answer">
                <div className="result-head">
                  <span className={`mode-badge ${isPreview ? 'preview' : 'live'}`}>{isPreview ? 'PREVIEW' : 'LIVE'}</span>
                  <SafetyChips safety={run.safety} />
                  <a className="download" href={`/api/v1/runs/${run.run_id}/capsule`} target="_blank" rel="noreferrer">下载 Evidence Capsule</a>
                </div>
                <p className="answer">{run.answer || (isPreview ? 'Preview 不生成回答：结构与端口类型已校验，未调用模型。' : '本次运行没有生成回答。')}</p>
                {run.artifact && (
                  <details className="artifact" open>
                    <summary>Agent 工单草稿（待人工审批）</summary>
                    <pre>{JSON.stringify(run.artifact, null, 2)}</pre>
                  </details>
                )}
                {teachOn && !isPreview && (
                  <p className="teach-hint">教学：回答里的 [S#] 必须能对应右侧证据条目；对应不上的回答会被 citation_repair 拦下换成证据摘要。Evidence Capsule 把问题、Recipe 哈希、证据与安全决策打包成一个可审计 JSON。</p>
                )}
              </div>
              <div className="result-evidence">
                <h4>证据（{run.evidence.length}）</h4>
                {run.evidence.length === 0 && <p className="muted">没有证据。{isPreview ? 'Preview 不检索。' : '知识库可能为空，或分数阈值过滤了全部候选。'}</p>}
                {run.evidence.map((item) => (
                  <details key={item.chunk_id} className="evidence">
                    <summary>
                      <b>[{item.citation}]</b> {item.title || item.document_id}
                      <em>score {item.score}</em>
                    </summary>
                    <small className="mono">{item.chunk_id}</small>
                    <p>{item.text}</p>
                  </details>
                ))}
              </div>
            </div>
          ) : (
            <p className="muted pad">运行后这里展示回答、安全决策、Agent 草稿与全部证据引用。</p>
          )
        )}
      </div>
    </section>
  )
}
