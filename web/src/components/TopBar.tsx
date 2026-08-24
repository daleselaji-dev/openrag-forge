import type { Health } from '../types'

type Props = {
  health: Health | null
  teachOn: boolean
  onToggleTeach: () => void
  onRefresh: () => void
}

export default function TopBar({ health, teachOn, onToggleTeach, onRefresh }: Props) {
  const warnings = health?.production_readiness?.warnings || []
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">◈</span>
        <div>
          <b>OPENRAG FORGE</b>
          <small>CONTROL ROOM · 可拆 · 可跑 · 可证明</small>
        </div>
      </div>
      <div className="status-chips">
        <span className="status-chip-item" title="能力档位">
          <small>PROFILE</small><b>{health?.profile || '…'}</b>
        </span>
        <span className="status-chip-item" title="部署环境">
          <small>ENV</small><b>{health?.environment || '…'}</b>
        </span>
        <span className="status-chip-item" title="真相源存储">
          <small>TRUTH</small><b>{health?.truth_source || '…'}</b>
        </span>
        <span className="status-chip-item" title="知识库文档数">
          <small>DOCS</small><b>{health?.documents ?? '…'}</b>
        </span>
        <span className={`status-chip-item ${health?.qdrant?.status === 'ready' ? 'good' : 'warn'}`} title="Qdrant 派生索引状态">
          <small>QDRANT</small><b>{health?.qdrant?.status || '…'}</b>
        </span>
        <span className={`status-chip-item ${health?.lm_studio?.status === 'ready' ? 'good' : 'warn'}`} title="OpenAI 兼容模型端点状态">
          <small>MODEL</small><b>{health?.lm_studio?.status || '…'}</b>
        </span>
        {warnings.length > 0 && (
          <span className="status-chip-item bad" title={warnings.join('\n')}>
            <small>READINESS</small><b>{warnings.length} 项告警</b>
          </span>
        )}
      </div>
      <div className="topbar-actions">
        <button className="ghost small" onClick={onRefresh}>刷新状态</button>
        <button className={`teach-toggle${teachOn ? ' on' : ''}`} onClick={onToggleTeach} aria-pressed={teachOn}>
          <i />
          辅助教学 {teachOn ? '开' : '关'}
        </button>
      </div>
    </header>
  )
}
