// 右侧检查器：始终回答三个问题 —— 这个节点的 Trace 与影响是什么、这个 Block 是干什么的、怎么调配

import { useEffect, useMemo, useState } from 'react'
import { RUNTIME_LABELS } from '../catalog'
import type { ModelProfile, Plugin, Recipe, TraceEvent, Tunable } from '../types'
import { TraceList } from './TraceList'

type Props = {
  recipe: Recipe | null
  plugins: Record<string, Plugin>
  models: ModelProfile[]
  selectedNodeId: string | null
  trace: TraceEvent[]
  onApplyConfig: (nodeId: string, config: Record<string, unknown>) => void
  onSelectNode: (nodeId: string) => void
}

function TunableField({ tunable, value, models, onChange }: { tunable: Tunable; value: unknown; models: ModelProfile[]; onChange: (value: unknown) => void }) {
  const id = `knob-${tunable.name}`
  if (tunable.type === 'model') {
    const options = models.filter((model) => !tunable.kind || model.kind === tunable.kind)
    return (
      <label className="knob" htmlFor={id}>
        <span>{tunable.name}<em title={tunable.description}>ⓘ</em></span>
        <select id={id} value={String(value ?? '')} onChange={(event) => onChange(event.target.value || undefined)}>
          <option value="">默认 / 不绑定</option>
          {options.map((model) => <option key={model.model_id} value={model.model_id}>{model.display_name} · {model.kind}</option>)}
        </select>
      </label>
    )
  }
  if (tunable.type === 'enum') {
    return (
      <label className="knob" htmlFor={id}>
        <span>{tunable.name}<em title={tunable.description}>ⓘ</em></span>
        <select id={id} value={String(value ?? tunable.options?.[0] ?? '')} onChange={(event) => onChange(event.target.value)}>
          {tunable.options?.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      </label>
    )
  }
  if (tunable.type === 'bool') {
    return (
      <label className="knob knob-bool" htmlFor={id}>
        <input id={id} type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />
        <span>{tunable.name}<em title={tunable.description}>ⓘ</em></span>
      </label>
    )
  }
  if (tunable.type === 'int' || tunable.type === 'float') {
    return (
      <label className="knob" htmlFor={id}>
        <span>{tunable.name}<em title={tunable.description}>ⓘ</em>{tunable.min !== undefined && <small>{tunable.min}–{tunable.max}</small>}</span>
        <input
          id={id} type="number" step={tunable.type === 'float' ? 0.05 : 1} min={tunable.min} max={tunable.max}
          value={value === undefined || value === null ? '' : Number(value)}
          onChange={(event) => onChange(event.target.value === '' ? undefined : Number(event.target.value))}
        />
      </label>
    )
  }
  if (tunable.type === 'json') {
    return (
      <label className="knob knob-json" htmlFor={id}>
        <span>{tunable.name}<em title={tunable.description}>ⓘ</em></span>
        <textarea
          id={id}
          value={typeof value === 'string' ? value : JSON.stringify(value ?? (tunable.name === 'weights' ? [1, 1] : {}), null, 0)}
          onChange={(event) => { try { onChange(JSON.parse(event.target.value)) } catch { onChange(event.target.value) } }}
        />
      </label>
    )
  }
  return (
    <label className="knob" htmlFor={id}>
      <span>{tunable.name}<em title={tunable.description}>ⓘ</em></span>
      <input id={id} type="text" value={String(value ?? '')} onChange={(event) => onChange(event.target.value || undefined)} />
    </label>
  )
}

export function Inspector({ recipe, plugins, models, selectedNodeId, trace, onApplyConfig, onSelectNode }: Props) {
  const [tab, setTab] = useState<'config' | 'block' | 'trace'>('config')
  const [draftConfig, setDraftConfig] = useState<Record<string, unknown>>({})
  const [rawMode, setRawMode] = useState(false)
  const [rawText, setRawText] = useState('{}')
  const [dirty, setDirty] = useState(false)

  const node = recipe?.nodes.find((item) => item.id === selectedNodeId) || null
  const plugin = node ? plugins[node.type] : null
  const nodeTrace = useMemo(() => trace.filter((event) => event.node_id === selectedNodeId), [trace, selectedNodeId])

  useEffect(() => {
    const config = node?.config || {}
    setDraftConfig(config)
    setRawText(JSON.stringify(config, null, 2))
    setDirty(false)
  }, [selectedNodeId, recipe?.recipe_id])

  const effectiveDefaults = plugin?.config_defaults || {}

  const applyKnob = (name: string, value: unknown) => {
    setDraftConfig((config) => {
      const next = { ...config }
      if (value === undefined) delete next[name]
      else next[name] = value
      setRawText(JSON.stringify(next, null, 2))
      return next
    })
    setDirty(true)
  }

  const apply = () => {
    if (!node) return
    let config = draftConfig
    if (rawMode) {
      try { config = JSON.parse(rawText) } catch { return alert('原始 JSON 配置不合法') }
    }
    onApplyConfig(node.id, config)
    setDirty(false)
  }

  return (
    <aside className="inspector" aria-label="节点检查器">
      <div className="inspector-tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'config'} className={tab === 'config' ? 'active' : ''} onClick={() => setTab('config')}>调配</button>
        <button role="tab" aria-selected={tab === 'block'} className={tab === 'block' ? 'active' : ''} onClick={() => setTab('block')}>Block 作用</button>
        <button role="tab" aria-selected={tab === 'trace'} className={tab === 'trace' ? 'active' : ''} onClick={() => setTab('trace')}>Trace</button>
      </div>

      {!node && (
        <div className="inspector-empty">
          <p className="muted">点击画布上的节点，这里会显示：</p>
          <ul className="muted">
            <li><b>调配</b>：该节点全部可调参数（默认值、范围、模型绑定），应用后进入草稿。</li>
            <li><b>Block 作用</b>：这个组件是什么、为什么存在、如何影响下游。</li>
            <li><b>Trace</b>：最近一次运行中该节点的状态、耗时与实际影响。</li>
          </ul>
          <p className="muted">文档的 ParsedBlock 请在底部「文档 / Blocks」页查看。</p>
        </div>
      )}

      {node && plugin && tab === 'config' && (
        <div className="inspector-body">
          <header className="inspector-node-head">
            <h3>{plugin.title}</h3>
            <code>{node.id}</code>
            <span className={`runtime-tag ${plugin.runtime}`} title={RUNTIME_LABELS[plugin.runtime]?.hint}>{RUNTIME_LABELS[plugin.runtime]?.label}</span>
          </header>
          {plugin.tunables.length === 0 && <p className="muted">该节点没有运行期可调参数。它的行为由输入数据与图结构决定。</p>}
          {!rawMode && plugin.tunables.map((tunable) => (
            <TunableField
              key={tunable.name}
              tunable={tunable}
              models={models}
              value={draftConfig[tunable.name] ?? effectiveDefaults[tunable.name]}
              onChange={(value) => applyKnob(tunable.name, value)}
            />
          ))}
          {rawMode && (
            <textarea className="node-config" value={rawText} onChange={(event) => { setRawText(event.target.value); setDirty(true) }} aria-label="原始 JSON 配置" />
          )}
          <div className="inspector-actions">
            <button className="ghost small" onClick={() => setRawMode(!rawMode)}>{rawMode ? '结构化编辑' : '原始 JSON'}</button>
            <button className="primary small" onClick={apply} disabled={!dirty}>{dirty ? '应用到草稿' : '已同步'}</button>
          </div>
          <p className="muted small-note">应用后 Recipe 变为 dirty 草稿；用画布上方的「保存草稿 → 校验 → 发布」流程固化为新的 recipe hash。默认值：<code>{JSON.stringify(effectiveDefaults)}</code></p>
        </div>
      )}

      {node && plugin && tab === 'block' && (
        <div className="inspector-body">
          <header className="inspector-node-head">
            <h3>{plugin.title}</h3>
            <span className={`runtime-tag ${plugin.runtime}`}>{RUNTIME_LABELS[plugin.runtime]?.label}</span>
          </header>
          <dl className="block-doc">
            <dt>它做什么</dt><dd>{plugin.description}</dd>
            <dt>为什么存在</dt><dd>{plugin.why || '—'}</dd>
            <dt>对下游的影响</dt><dd>{plugin.downstream || '—'}</dd>
            <dt>输入端口</dt><dd>{plugin.inputs.length ? plugin.inputs.map((port) => <code key={port}>{port}</code>) : '（源节点）'}</dd>
            <dt>输出端口</dt><dd>{plugin.outputs.map((port) => <code key={port}>{port}</code>)}</dd>
            {plugin.runtime === 'stub' && <><dt>诚实声明</dt><dd className="stub-text">该节点是 compile-complete / runtime-stub：编译期端口类型完整，运行层暂无真实后端，执行时会在 Trace 中记录 skipped 而不是伪装产出。</dd></>}
          </dl>
        </div>
      )}

      {node && tab === 'trace' && (
        <div className="inspector-body">
          <TraceList trace={nodeTrace} recipe={recipe} plugins={plugins} emptyText="该节点在最近一次运行中没有 Trace 事件。先运行 Preview 或真实链路。" onSelectNode={onSelectNode} />
        </div>
      )}
    </aside>
  )
}
