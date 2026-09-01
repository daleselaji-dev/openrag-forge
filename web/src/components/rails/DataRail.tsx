import type { DocumentInfo, ModelProfile } from '../../types'
import { fmtBytes } from '../../format'

type Props = {
  documents: DocumentInfo[]
  models: ModelProfile[]
  kbId: string
  uploadRoute: string
  embeddingModelId: string
  busy: boolean
  teachOn: boolean
  setUploadRoute: (route: string) => void
  setEmbeddingModelId: (id: string) => void
  onUpload: (file: File) => void
  onReprocess: (documentId: string) => void
  onRebuildIndex: () => void
}

const ROUTES = ['auto', 'native_text', 'html_structure', 'pdf_page_text', 'pdf_layout', 'office_structure', 'tabular', 'json_structure']

export default function DataRail({ documents, models, kbId, uploadRoute, embeddingModelId, busy, teachOn, setUploadRoute, setEmbeddingModelId, onUpload, onReprocess, onRebuildIndex }: Props) {
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
            <button className="link-btn" onClick={() => onReprocess(doc.document_id)} title="用当前路由与 Chunker 配置重新解析">重解析</button>
          </div>
        ))}
      </div>
    </div>
  )
}
