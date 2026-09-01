import type { Health, WorkbenchMode } from '../types'

type Props = {
  health: Health | null
  mode: WorkbenchMode
  onSetMode: (mode: WorkbenchMode) => void
  onRefresh: () => void
}

const MODES: { id: WorkbenchMode; label: string; title: string }[] = [
  { id: 'work', label: '工作台', title: '干净的 Control Room：无教学与讲解噪声' },
  { id: 'teach', label: '辅助教学', title: '7 步操作课：怎么用这个工作台' },
  { id: 'interview', label: '面试讲解', title: 'RAG 设计课：历程 / 方案对比 / 环节地图 / 向量库专章 / 实验手册' },
]

export default function TopBar({ health, mode, onSetMode, onRefresh }: Props) {
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
        <div className="mode-switch" role="tablist" aria-label="工作台模式">
          {MODES.map((item) => (
            <button
              key={item.id}
              role="tab"
              aria-selected={mode === item.id}
              className={`mode-switch-btn${mode === item.id ? ' active' : ''}${item.id === 'interview' ? ' interview' : ''}`}
              title={item.title}
              onClick={() => onSetMode(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
    </header>
  )
}
