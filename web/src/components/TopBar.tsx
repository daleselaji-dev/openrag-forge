// 顶栏：品牌、知识库选择/创建、健康状态、导入与下载入口

import type { Health, KnowledgeBase } from '../types'

type Props = {
  health: Health | null
  knowledgeBases: KnowledgeBase[]
  kbId: string
  onKbChange: (kbId: string) => void
  onCreateKb: () => void
  onOpenImports: () => void
  onOpenDownload: () => void
  onRefresh: () => void
}

function HealthChip({ label, status, title }: { label: string; status: string; title?: string }) {
  const tone = status === 'ready' ? 'ok' : status === 'not_initialized' ? 'warn' : 'down'
  return <span className={`health-chip ${tone}`} title={title || status}>{label}: {status}</span>
}

export function TopBar({ health, knowledgeBases, kbId, onKbChange, onCreateKb, onOpenImports, onOpenDownload, onRefresh }: Props) {
  return (
    <header className="topbar" role="banner">
      <div className="brand">
        <b>OpenRAG Forge</b>
        <span>装配工作台 · 可拆 / 可跑 / 可证明</span>
      </div>
      <div className="topbar-controls">
        <label className="kb-select">知识库
          <select value={kbId} onChange={(event) => onKbChange(event.target.value)} aria-label="选择知识库">
            {knowledgeBases.map((kb) => <option key={kb.knowledge_base_id} value={kb.knowledge_base_id}>{kb.name}</option>)}
          </select>
        </label>
        <button className="ghost small" onClick={onCreateKb}>+ 新建</button>
      </div>
      <div className="topbar-health">
        {health && (
          <>
            <span className="health-chip ok" title={`truth source: ${health.truth_source}`}>truth: sqlite</span>
            <HealthChip label="qdrant" status={health.qdrant.status} title={health.qdrant.error || health.qdrant.url} />
            <HealthChip label="模型服务" status={health.lm_studio.status} title={health.lm_studio.error || health.lm_studio.chat_base_url} />
            <span className="health-chip neutral">{health.documents} 文档</span>
          </>
        )}
        <button className="ghost small" onClick={onRefresh} title="刷新健康状态">刷新</button>
      </div>
      <div className="topbar-actions">
        <button className="ghost" onClick={onOpenImports}>导入 API / 模型 / Recipe</button>
        <button className="ghost" onClick={onOpenDownload}>下载 / 自托管</button>
      </div>
    </header>
  )
}
