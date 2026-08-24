import { useEffect, useMemo, useState } from 'react'
import type { CatalogNode, ConfigField, ModelProfile, Recipe, Run, RunMeta, TraceEvent } from '../types'
import { fmtMs, GROUP_LABELS, shortHash, STATUS_LABELS } from '../format'
import { EXECUTION_LABELS, IMPLEMENTED_LABELS } from '../teach'
import { STAGE_LESSONS } from '../interview'
import InterviewLessonCard from './InterviewLessonCard'

type Props = {
  recipe: Recipe | null
  selectedNodeId: string | null
  catalog: Record<string, CatalogNode>
  models: ModelProfile[]
  run: Run | null
  runMeta: RunMeta | null
  teachOn: boolean
  interviewOn: boolean
  coachActive: boolean
  dirty: boolean
  onUpdateNodeConfig: (nodeId: string, config: Record<string, unknown>) => void
  onDeleteNode: (nodeId: string) => void
}

function FieldInput({ field, value, models, onChange }: { field: ConfigField; value: unknown; models: ModelProfile[]; onChange: (key: string, value: unknown) => void }) {
  if (field.type === 'number') {
    return (
      <input
        type="number"
        min={field.min}
        max={field.max}
        step={field.step}
        value={value === undefined || value === null ? '' : String(value)}
        onChange={(event) => {
          const raw = event.target.value
          if (raw === '') return
          const parsed = Number(raw)
          if (!Number.isNaN(parsed)) onChange(field.key, parsed)
        }}
      />
    )
  }
  if (field.type === 'select') {
    return (
      <select value={String(value ?? '')} onChange={(event) => onChange(field.key, event.target.value)}>
        {(field.options || []).map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
    )
  }
  if (field.type === 'boolean') {
    return (
      <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(field.key, event.target.checked)} />
    )
  }
  if (field.type === 'model') {
    const candidates = models.filter((model) => !field.model_kind || model.kind === field.model_kind)
    return (
      <select value={String(value ?? '')} onChange={(event) => onChange(field.key, event.target.value)}>
        <option value="">未绑定 / 使用默认</option>
        {candidates.map((model) => (
          <option key={model.model_id} value={model.model_id}>{model.display_name} · {model.kind}</option>
        ))}
      </select>
    )
  }
  return <input type="text" value={String(value ?? '')} onChange={(event) => onChange(field.key, event.target.value)} />
}

function NodeTrace({ events, preview }: { events: TraceEvent[]; preview: boolean }) {
  if (!events.length) return null
  return (
    <section className="inspector-section">
      <h4>本节点最近一次{preview ? ' Preview' : '运行'}</h4>
      {events.map((event) => {
        const execution = EXECUTION_LABELS[String(event.details?.execution || '')]
        return (
          <div key={event.sequence} className={`node-trace-card ${event.status}`}>
            <div className="node-trace-head">
              <span className={`status-chip ${event.status}`}>{STATUS_LABELS[event.status] || event.status}</span>
              {execution && <span className={`exec-chip ${execution.tone}`}>{execution.label}</span>}
              <em>{fmtMs(event.duration_ms)}</em>
            </div>
            <p>{event.summary}</p>
            <details>
              <summary>输入 / 输出细节</summary>
              <pre>{JSON.stringify(event.details, null, 2)}</pre>
            </details>
          </div>
        )
      })}
    </section>
  )
}

export default function Inspector({ recipe, selectedNodeId, catalog, models, run, runMeta, teachOn, interviewOn, coachActive, dirty, onUpdateNodeConfig, onDeleteNode }: Props) {
  const node = recipe?.nodes.find((item) => item.id === selectedNodeId) || null
  const spec = node ? catalog[node.type] : null
  const [advanced, setAdvanced] = useState(false)
  const [jsonText, setJsonText] = useState('{}')
  const [jsonError, setJsonError] = useState<string | null>(null)

  useEffect(() => {
    setAdvanced(false)
    setJsonError(null)
    setJsonText(JSON.stringify(node?.config || {}, null, 2))
  }, [selectedNodeId, recipe?.recipe_id])

  useEffect(() => {
    if (!advanced) setJsonText(JSON.stringify(node?.config || {}, null, 2))
  }, [node?.config, advanced])

  const nodeEvents = useMemo(() => (run && selectedNodeId ? run.trace.filter((event) => event.node_id === selectedNodeId) : []), [run, selectedNodeId])

  const stubCount = useMemo(() => (recipe ? recipe.nodes.filter((item) => catalog[item.type]?.implemented !== 'live').length : 0), [recipe, catalog])

  const applyField = (key: string, value: unknown) => {
    if (!node) return
    onUpdateNodeConfig(node.id, { ...(node.config || {}), [key]: value })
  }

  const applyJson = () => {
    if (!node) return
    try {
      const parsed = JSON.parse(jsonText) as Record<string, unknown>
      setJsonError(null)
      onUpdateNodeConfig(node.id, parsed)
    } catch {
      setJsonError('不是合法 JSON，未应用。')
    }
  }

  if (!recipe) {
    return <aside className={`inspector${coachActive ? ' coach-pulse' : ''}`}><p className="muted">先选择一个 Recipe。</p></aside>
  }

  if (!node || !spec) {
    return (
      <aside className={`inspector${coachActive ? ' coach-pulse' : ''}`}>
        <span className="eyebrow">RECIPE 概览</span>
        <h3>{recipe.name}</h3>
        <div className="kv-list">
          <div><small>版本 / 状态</small><b>v{recipe.version} · {recipe.status}{dirty ? '（未保存）' : ''}</b></div>
          <div><small>编译哈希</small><b className="mono">{shortHash(recipe.hash, 14)}</b></div>
          <div><small>节点 / 连线</small><b>{recipe.nodes.length} / {recipe.edges.length}</b></div>
        </div>
        {stubCount > 0 && (
          <p className="honesty-note">本 Recipe 含 {stubCount} 个占位/退化节点：它们出现在画布与 Trace 中，但尚未真正实现对应算法。点击节点查看诚实标注。</p>
        )}
        {run && runMeta && (
          <div className="kv-list">
            <div><small>最近一次{runMeta.mode === 'preview' ? ' Preview' : '运行'}</small><b className="mono">{run.run_id}</b></div>
            <div><small>证据 / Trace</small><b>{run.evidence.length} 条证据 · {run.trace.length} 个事件</b></div>
          </div>
        )}
        <p className="muted">点击画布节点查看端口、诚实标注与结构化配置表单。</p>
        {teachOn && (
          <div className="teach-card">
            <b>教学 · 为什么是 DAG？</b>
            <p>RAG 不是一根管道而是一张图：检索、融合、生成、安全门各自有类型化端口。编译器在保存时校验端口兼容与无环，保证画布上的结构就是运行时的结构。</p>
          </div>
        )}
        {interviewOn && (
          <div className="teach-card">
            <b>面试讲解 · 从这里开始</b>
            <p>点击画布任意节点，这里会显示该环节的产品规格级讲解（目的 / 影响 / 旋钮 / 动力 / live vs stub / 面试追问）+ 可改配置。也可以在左侧讲解面板的「环节地图」里点环节，会自动选中对应节点。</p>
          </div>
        )}
      </aside>
    )
  }

  const implemented = IMPLEMENTED_LABELS[spec.implemented]
  const config = node.config || {}
  const schemaKeys = new Set(spec.config_schema.map((field) => field.key))
  const extraKeys = Object.keys(config).filter((key) => !schemaKeys.has(key))

  return (
    <aside className={`inspector${coachActive ? ' coach-pulse' : ''}`}>
      <span className="eyebrow">节点检查器</span>
      <div className="inspector-title">
        <h3>{spec.title}</h3>
        <span className={`impl-badge impl-${spec.implemented}`} title={implemented.note}>{implemented.label}</span>
      </div>
      <p className="node-meta mono">{node.type} · {node.id} · {GROUP_LABELS[spec.group] || spec.group}</p>
      <p className="execution-note">{spec.execution_note}</p>
      <p className="port-line">
        <span>入 {spec.inputs.length ? spec.inputs.join(' / ') : '—'}</span>
        <span>出 {spec.outputs.length ? spec.outputs.join(' / ') : '—'}</span>
      </p>

      {teachOn && (spec.teach.what || spec.teach.tune || spec.teach.pitfalls) && (
        <div className="teach-card">
          <b>教学 · 这一步在 RAG 里做什么</b>
          {spec.teach.what && <p>{spec.teach.what}</p>}
          {spec.teach.tune && <p><i>怎么调：</i>{spec.teach.tune}</p>}
          {spec.teach.pitfalls && <p><i>常见误区：</i>{spec.teach.pitfalls}</p>}
        </div>
      )}

      {interviewOn && STAGE_LESSONS[node.type] && (
        <section className="inspector-section">
          <h4>面试讲解 · 环节规格</h4>
          <InterviewLessonCard lesson={STAGE_LESSONS[node.type]} spec={spec} compact />
        </section>
      )}

      <section className="inspector-section">
        <div className="section-head">
          <h4>配置{dirty ? '（草稿未保存）' : ''}</h4>
          <button className="link-btn" onClick={() => setAdvanced((current) => !current)}>{advanced ? '返回表单' : '高级 JSON'}</button>
        </div>
        {!advanced && spec.config_schema.length === 0 && <p className="muted">该节点没有可配置项。</p>}
        {!advanced && spec.config_schema.map((field) => (
          <label key={field.key} className={`config-field${field.effective === false ? ' ineffective' : ''}`}>
            <span className="config-label">
              {field.label}
              {field.effective === false ? <em className="not-effective">不生效</em> : <em className="effective">生效</em>}
            </span>
            <FieldInput field={field} value={config[field.key] ?? spec.config_defaults[field.key]} models={models} onChange={applyField} />
            {field.help && <small>{field.help}</small>}
          </label>
        ))}
        {!advanced && extraKeys.length > 0 && <p className="muted">还有 {extraKeys.length} 个字段（{extraKeys.join(', ')}）仅在高级 JSON 中可编辑。</p>}
        {advanced && (
          <>
            <textarea className="node-config" value={jsonText} onChange={(event) => setJsonText(event.target.value)} spellCheck={false} />
            {jsonError && <p className="form-error">{jsonError}</p>}
            <button className="ghost small" onClick={applyJson}>应用 JSON</button>
          </>
        )}
      </section>

      <NodeTrace events={nodeEvents} preview={runMeta?.mode === 'preview'} />

      <section className="inspector-section">
        <button className="danger-btn" onClick={() => onDeleteNode(node.id)}>删除节点（连带连线）</button>
      </section>
    </aside>
  )
}
