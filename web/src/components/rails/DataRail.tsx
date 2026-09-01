import { useState } from 'react'
import { api } from '../../api'
import type { ChunkInfo, DocumentInfo, ModelProfile, ParsedBlock } from '../../types'
import { fmtBytes } from '../../format'

type Props = {
  documents: DocumentInfo[]
  models: ModelProfile[]
  kbId: string
  uploadRoute: string
  embeddingModelId: string
  chunkMaxChars: number
  chunkOverlap: number
  busy: boolean
  teachOn: boolean
  setUploadRoute: (route: string) => void
  setEmbeddingModelId: (id: string) => void
  setChunkMaxChars: (value: number) => void
  setChunkOverlap: (value: number) => void
  onUpload: (file: File) => void
  onReprocess: (documentId: string) => void
  onRebuildIndex: () => void
}

const ROUTES = ['auto', 'native_text', 'html_structure', 'pdf_page_text', 'pdf_layout', 'office_structure', 'tabular', 'json_structure']

export default function DataRail({ documents, models, kbId, uploadRoute, embeddingModelId, chunkMaxChars, chunkOverlap, busy, teachOn, setUploadRoute, setEmbeddingModelId, setChunkMaxChars, setChunkOverlap, onUpload, onReprocess, onRebuildIndex }: Props) {
  const [selectedDocument, setSelectedDocument] = useState<string | null>(null)
  const [blocks, setBlocks] = useState<ParsedBlock[]>([])
  const [chunks, setChunks] = useState<ChunkInfo[]>([])
  const [explorerTab, setExplorerTab] = useState<'blocks' | 'chunks'>('blocks')
  const [explorerBusy, setExplorerBusy] = useState(false)
  const [explorerError, setExplorerError] = useState<string | null>(null)

  const inspectDocument = async (documentId: string) => {
    if (selectedDocument === documentId) {
      setSelectedDocument(null)
      return
    }
    setSelectedDocument(documentId)
    setExplorerBusy(true)
    setExplorerError(null)
    try {
      const [blockBody, chunkBody] = await Promise.all([
        api<{ items: ParsedBlock[] }>(`/api/v1/documents/${documentId}/blocks`),
        api<{ items: ChunkInfo[] }>(`/api/v1/documents/${documentId}/chunks`),
      ])
      setBlocks(blockBody.items)
      setChunks(chunkBody.items)
    } catch (error) {
      setExplorerError((error as Error).message)
      setBlocks([])
      setChunks([])
    } finally {
      setExplorerBusy(false)
    }
  }

  const selected = documents.find((doc) => doc.document_id === selectedDocument)
  return (
    <div className="rail-panel">
      <div className="rail-head">
        <h3>数据 / 知识库 <code>{kbId}</code></h3>
        <button className="ghost small" onClick={onRebuildIndex} disabled={busy} title="用真相源里的 Chunk 重建 Qdrant 派生索引">重建索引</button>
      </div>
      {teachOn && <p className="teach-hint">教学：上传后走 路由 → Chunk → Metadata → 索引 四步，全部记录在 Ingest Trace（底部第二个标签）。SQLite 是真相源；Qdrant 只是可重建的派生索引。</p>}
      <div className="upload-controls">
        <label>
          <span>解析路由</span>
          <select value={uploadRoute} onChange={(event) => setUploadRoute(event.target.value)}>
            {ROUTES.map((route) => <option key={route} value={route}>{route === 'auto' ? 'auto（按签名判定）' : route}</option>)}
          </select>
        </label>
        <label>
          <span>Embedding 模型</span>
          <select value={embeddingModelId} onChange={(event) => setEmbeddingModelId(event.target.value)}>
            {models.filter((model) => model.kind === 'embedding').map((model) => (
              <option key={model.model_id} value={model.model_id}>{model.display_name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Chunk 大小</span>
          <input type="number" min={200} max={4000} step={50} value={chunkMaxChars} onChange={(event) => setChunkMaxChars(Math.min(4000, Math.max(200, Number(event.target.value) || 1200)))} />
        </label>
        <label>
          <span>Overlap</span>
          <input type="number" min={0} max={Math.floor(chunkMaxChars / 2)} step={10} value={chunkOverlap} onChange={(event) => setChunkOverlap(Math.min(Math.floor(chunkMaxChars / 2), Math.max(0, Number(event.target.value) || 0)))} />
        </label>
      </div>
      <label className="dropzone">
        <input type="file" onChange={(event) => { const file = event.target.files?.[0]; if (file) onUpload(file); event.target.value = '' }} />
        <span>选择或拖入 PDF / DOCX / XLSX / Markdown / HTML / TXT</span>
        <small>原始文件永久保留；解析失败不会静默丢弃。</small>
      </label>
      <div className="document-list">
        {documents.length === 0 && <p className="muted">还没有文档。上传后马上能看到路由决策、Block 与 Chunk 计数。</p>}
        {documents.map((doc) => (
          <div className="document-row" key={doc.document_id}>
            <span className={`dot ${doc.status}`} />
            <div className="document-info">
              <b>{doc.filename}</b>
              <small>{doc.parser_route || '未解析'} · v{doc.version} · {fmtBytes(doc.size_bytes)}{doc.reason_codes.length ? ` · ${doc.reason_codes.join(', ')}` : ''}</small>
            </div>
            <div className="document-actions">
              <button className="link-btn" onClick={() => void inspectDocument(doc.document_id)} title="查看解析后的 Block 与 Chunk">查看</button>
              <button className="link-btn" onClick={() => onReprocess(doc.document_id)} title="用当前路由与 Chunker 配置重新解析">重解析</button>
            </div>
          </div>
        ))}
      </div>
      {selected && (
        <section className="document-explorer">
          <div className="explorer-head">
            <div>
              <b>解析产物</b>
              <small>{selected.filename} · v{selected.version}</small>
            </div>
            <button className="ghost small" onClick={() => setSelectedDocument(null)}>关闭</button>
          </div>
          <div className="explorer-tabs">
            <button className={explorerTab === 'blocks' ? 'active' : ''} onClick={() => setExplorerTab('blocks')}>Blocks · {blocks.length}</button>
            <button className={explorerTab === 'chunks' ? 'active' : ''} onClick={() => setExplorerTab('chunks')}>Chunks · {chunks.length}</button>
          </div>
          {explorerBusy && <p className="muted">正在读取解析产物…</p>}
          {explorerError && <p className="form-error">读取失败：{explorerError}</p>}
          {!explorerBusy && !explorerError && explorerTab === 'blocks' && (
            <div className="explorer-list">
              {blocks.length === 0 && <p className="muted">没有 Block；请先完成解析。</p>}
              {blocks.map((block) => (
                <details className="explorer-card" key={block.block_id}>
                  <summary><b>#{block.order + 1} {block.block_type}</b><span>{block.page ? `p.${block.page}` : ''}</span></summary>
                  {block.heading_path.length > 0 && <small className="mono">{block.heading_path.join(' / ')}</small>}
                  <p>{block.text || '（空 Block）'}</p>
                </details>
              ))}
            </div>
          )}
          {!explorerBusy && !explorerError && explorerTab === 'chunks' && (
            <div className="explorer-list">
              {chunks.length === 0 && <p className="muted">没有 Chunk；请先完成解析。</p>}
              {chunks.map((chunk) => (
                <details className="explorer-card" key={chunk.chunk_id}>
                  <summary><b>#{chunk.order + 1}</b><span className="mono">{chunk.chunk_id.slice(0, 14)}</span></summary>
                  <small className="mono">blocks: {chunk.block_ids.join(', ') || '—'}</small>
                  <p>{chunk.text || '（空 Chunk）'}</p>
                </details>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  )
}
