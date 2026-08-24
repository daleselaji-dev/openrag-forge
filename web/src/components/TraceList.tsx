// Trace 时间线渲染：状态、耗时、摘要、impact chips 与可展开的完整 details

import { useState } from 'react'
import { IMPACT_CHIP_KEYS, STATUS_LABELS } from '../catalog'
import type { Plugin, Recipe, TraceEvent } from '../types'

function ImpactChips({ event }: { event: TraceEvent }) {
  const impact = (event.details?.impact || {}) as Record<string, unknown>
  const chips = IMPACT_CHIP_KEYS
    .filter(({ key }) => impact[key] !== undefined && impact[key] !== null && impact[key] !== false && !(Array.isArray(impact[key]) && (impact[key] as unknown[]).length === 0))
    .map(({ key, label }) => {
      const value = impact[key]
      const text = Array.isArray(value) ? (value as unknown[]).join(', ') : typeof value === 'boolean' ? '是' : String(value)
      return <span className={`impact-chip ${key}`} key={key}>{label}: {text.slice(0, 60)}</span>
    })
  const evidenceIds = impact.evidence_ids as string[] | undefined
  if (evidenceIds?.length) chips.push(<span className="impact-chip" key="evidence_ids" title={evidenceIds.join('\n')}>证据 ID ×{evidenceIds.length}</span>)
  return chips.length ? <div className="impact-chips">{chips}</div> : null
}

type Props = {
  trace: TraceEvent[]
  recipe?: Recipe | null
  plugins?: Record<string, Plugin>
  emptyText?: string
  onSelectNode?: (nodeId: string) => void
}

export function TraceList({ trace, recipe, plugins, emptyText, onSelectNode }: Props) {
  const [expanded, setExpanded] = useState<number | null>(null)
  if (!trace.length) return <p className="muted">{emptyText || '还没有 Trace。运行 Preview 或真实链路后，每个节点的执行状态、耗时与影响会按顺序出现在这里。'}</p>
  return (
    <div className="trace-list" role="list">
      {trace.map((event) => {
        const nodeType = recipe?.nodes.find((node) => node.id === event.node_id)?.type
        const title = (nodeType && plugins?.[nodeType]?.title) || nodeType || event.node_id
        return (
          <div className={`trace-row ${event.status}`} key={`${event.node_id}-${event.sequence}`} role="listitem">
            <button
              className="trace-row-main"
              onClick={() => { setExpanded(expanded === event.sequence ? null : event.sequence); onSelectNode?.(event.node_id) }}
              aria-expanded={expanded === event.sequence}
            >
              <span className="trace-seq">{String(event.sequence).padStart(2, '0')}</span>
              <b>{title}</b>
              <em>{event.node_id}</em>
              <span className={`trace-status ${event.status}`}>{STATUS_LABELS[event.status] || event.status}</span>
              <p>{event.summary}</p>
              <small>{event.duration_ms ? `${event.duration_ms} ms` : '—'}</small>
            </button>
            <ImpactChips event={event} />
            {expanded === event.sequence && (
              <pre className="trace-details">{JSON.stringify(event.details, null, 2)}</pre>
            )}
          </div>
        )
      })}
    </div>
  )
}
