// 文档面板：上传（路由/Chunker/Embedding 可调）、路由决策展示、ParsedBlock 与 Chunk 检查、不覆盖源的重解析

import { useState } from 'react'
import { BLOCK_TYPE_DOCS, PARSER_ROUTES, formatBytes } from '../catalog'
import type { ChunkInfo, DocumentInfo, ModelProfile, ParsedBlock, TraceEvent, UploadResult } from '../types'
import { TraceList } from './TraceList'

export type UploadOptions = { route: string; embeddingModelId: string; maxChars: number; overlap: number }

type Props = {
  documents: DocumentInfo[]
  models: ModelProfile[]
  uploadOptions: UploadOptions
  onUploadOptions: (options: UploadOptions) => void
  onUpload: (file: File) => void
  onReprocess: (documentId: string) => void
  selectedDocumentId: string | null
  onSelectDocument: (documentId: string | null) => void
  blocks: ParsedBlock[]
  chunks: ChunkInfo[]
  ingestResult: UploadResult | null
  ingestTrace: TraceEvent[]
  busy: boolean
}

export function DocumentsPanel({ documents, models, uploadOptions, onUploadOptions, onUpload, onReprocess, selectedDocumentId, onSelectDocument, blocks, chunks, ingestResult, ingestTrace, busy }: Props) {
  const [view, setView] = useState<'blocks' | 'chunks' | 'trace'>('blocks')
  const embeddingModels = models.filter((model) => model.kind === 'embedding')
  const selected = documents.find((doc) => doc.document_id === selectedDocumentId) || null

  return (
    <div className="documents-panel">
      <div className="documents-left">
        <div className="upload-controls">
          <label>解析路由
            <select value={uploadOptions.route} onChange={(event) => onUploadOptions({ ...uploadOptions, route: event.target.value })}>
              {PARSER_ROUTES.map((route) => <option key={route} value={route}>{route === 'auto' ? 'auto（内容感知自动路由）' : route}</option>)}
            </select>
          </label>
          <label>Embedding 模型
            <select value={uploadOptions.embeddingModelId} onChange={(event) => onUploadOptions({ ...uploadOptions, embeddingModelId: event.target.value })}>
              {embeddingModels.map((model) => <option key={model.model_id} value={model.model_id}>{model.display_name}</option>)}
            </select>
          </label>
          <label>max_chars
            <input type="number" min={200} max={4000} value={uploadOptions.maxChars} onChange={(event) => onUploadOptions({ ...uploadOptions, maxChars: Number(event.target.value) || 1200 })} />
          </label>
          <label>overlap
            <input type="number" min={0} max={400} value={uploadOptions.overlap} onChange={(event) => onUploadOptions({ ...uploadOptions, overlap: Number(event.target.value) || 0 })} />
          </label>
        </div>
        <label className="dropzone">
          <input type="file" onChange={(event) => { const file = event.target.files?.[0]; if (file) onUpload(file); event.target.value = '' }} disabled={busy} />
          <span>拖入 / 选择 PDF · DOCX · XLSX · Markdown · HTML · TXT · JSON</span>
          <small>原始文件永远保留；解析失败不会静默丢弃；索引失败记为 deferred 而不是 500。</small>
        </label>
        <div className="document-list">
          {documents.length === 0 && <p className="muted">还没有文档。上传一个文件，马上能看到路由决策（confidence + reason_codes）、Block 与 Chunk。</p>}
          {documents.map((doc) => (
            <button key={doc.document_id} className={`document-row ${selectedDocumentId === doc.document_id ? 'active' : ''}`} onClick={() => onSelectDocument(doc.document_id)}>
              <span className={`dot ${doc.status}`} />
              <div>
                <b>{doc.filename}</b>
                <small>
                  {doc.parser_route || '未解析'} · v{doc.version} · {formatBytes(doc.size_bytes)}
                  {typeof doc.parser_confidence === 'number' && ` · 置信度 ${doc.parser_confidence}`}
                </small>
                {doc.reason_codes.length > 0 && <small className="reason-codes">reason: {doc.reason_codes.join(', ')}</small>}
              </div>
              <em>{doc.status}</em>
            </button>
          ))}
        </div>
      </div>

      <div className="documents-right">
        {!selected && (
          <div className="muted document-placeholder">
            <p>选择左侧文档查看它的 ParsedBlock（角色、来源、顺序）和 Chunk（切分结果、增强 Metadata）。</p>
            {ingestResult && (
              <div className="ingest-summary">
                <b>最近一次 ingest：{ingestResult.document.filename}</b>
                <p>路由 {ingestResult.route.route}（置信度 {ingestResult.route.confidence}，{ingestResult.route.reason_codes.join(', ')}）→ {ingestResult.blocks} blocks → {ingestResult.chunks} chunks → 索引 {ingestResult.index?.status}{ingestResult.index?.next_action ? `（${ingestResult.index.next_action}）` : ''}</p>
                <TraceList trace={ingestTrace} emptyText="" />
              </div>
            )}
          </div>
        )}
        {selected && (
          <>
            <header className="document-detail-head">
              <div>
                <h3>{selected.filename}</h3>
                <small>v{selected.version} · {selected.parser_route} · sha256 {selected.sha256.slice(0, 12)}…</small>
              </div>
              <div className="actions">
                <button className="ghost small" onClick={() => onReprocess(selected.document_id)} disabled={busy} title="用上方的路由 / Chunker 配置重新解析；源文件不覆盖，版本 +1">按当前配置重解析（v{selected.version + 1}）</button>
                <button className="ghost small" onClick={() => onSelectDocument(null)}>关闭</button>
              </div>
            </header>
            <div className="doc-view-tabs" role="tablist">
              <button role="tab" aria-selected={view === 'blocks'} className={view === 'blocks' ? 'active' : ''} onClick={() => setView('blocks')}>Blocks（{blocks.length}）</button>
              <button role="tab" aria-selected={view === 'chunks'} className={view === 'chunks' ? 'active' : ''} onClick={() => setView('chunks')}>Chunks（{chunks.length}）</button>
              <button role="tab" aria-selected={view === 'trace'} className={view === 'trace' ? 'active' : ''} onClick={() => setView('trace')}>Ingest Trace</button>
            </div>
            {view === 'blocks' && (
              <div className="block-list">
                {blocks.map((block) => (
                  <div className="block-row" key={block.block_id}>
                    <span className={`block-type ${block.block_type}`} title={BLOCK_TYPE_DOCS[block.block_type]?.role}>{BLOCK_TYPE_DOCS[block.block_type]?.title || block.block_type}</span>
                    <div>
                      <p>{block.text.slice(0, 280)}{block.text.length > 280 ? '…' : ''}</p>
                      <small>#{block.order}{block.page ? ` · 第 ${block.page} 页` : ''} · {block.block_id}</small>
                      <small className="block-role">{BLOCK_TYPE_DOCS[block.block_type]?.role}</small>
                    </div>
                  </div>
                ))}
                {blocks.length === 0 && <p className="muted">该文档没有 Block（可能解析失败或为空）。</p>}
              </div>
            )}
            {view === 'chunks' && (
              <div className="block-list">
                {chunks.map((chunk) => (
                  <div className="block-row" key={chunk.chunk_id}>
                    <span className="block-type chunk">Chunk</span>
                    <div>
                      <p>{chunk.text.slice(0, 280)}{chunk.text.length > 280 ? '…' : ''}</p>
                      <small>#{chunk.order} · {chunk.chunk_id}</small>
                      <small className="chunk-meta">
                        title: {String(chunk.metadata.title || '—')} · lang: {String(chunk.metadata.language || '—')}
                        {Array.isArray(chunk.metadata.keywords) && (chunk.metadata.keywords as string[]).length > 0 && ` · 关键词: ${(chunk.metadata.keywords as string[]).join(', ')}`}
                      </small>
                    </div>
                  </div>
                ))}
                {chunks.length === 0 && <p className="muted">该文档没有 Chunk。</p>}
              </div>
            )}
            {view === 'trace' && <TraceList trace={ingestTrace} emptyText="本次会话中该文档没有新的 ingest Trace。重新上传或重解析后会出现。" />}
          </>
        )}
      </div>
    </div>
  )
}
