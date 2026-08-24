// 左侧组件库：按 group 分组的 27 节点目录，含"这个 Block 做什么"说明与 runtime 标签

import { useState } from 'react'
import { GROUP_ORDER, GROUP_TITLES, RUNTIME_LABELS } from '../catalog'
import type { Plugin } from '../types'

type Props = {
  plugins: Record<string, Plugin>
  onAddNode: (nodeType: string) => void
  disabled: boolean
}

export function Palette({ plugins, onAddNode, disabled }: Props) {
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({ ingest: true, retrieve: true, generate: true })
  const [filter, setFilter] = useState('')

  const grouped: Record<string, [string, Plugin][]> = {}
  for (const [type, plugin] of Object.entries(plugins)) {
    const keyword = filter.trim().toLowerCase()
    if (keyword && !type.includes(keyword) && !plugin.title.includes(filter.trim()) && !plugin.description.includes(filter.trim())) continue
    grouped[plugin.group] = grouped[plugin.group] || []
    grouped[plugin.group].push([type, plugin])
  }

  return (
    <aside className="palette" aria-label="组件库">
      <div className="palette-head">
        <h2>组件库</h2>
        <input type="search" placeholder="搜索节点…" value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="搜索节点" />
      </div>
      <p className="palette-hint">点击「+」把节点加入当前 Recipe 草稿，然后在画布上连接端口。端口类型不兼容的连线会在校验时被编译器拒绝。</p>
      <div className="palette-groups">
        {GROUP_ORDER.filter((group) => grouped[group]?.length).map((group) => (
          <section key={group} className="palette-group">
            <button
              className="palette-group-toggle"
              onClick={() => setOpenGroups((state) => ({ ...state, [group]: !(state[group] ?? Boolean(filter)) }))}
              aria-expanded={openGroups[group] ?? Boolean(filter)}
            >
              <span>{GROUP_TITLES[group] || group}</span>
              <em>{grouped[group].length}</em>
            </button>
            {(openGroups[group] ?? Boolean(filter)) && grouped[group].map(([type, plugin]) => (
              <div key={type} className={`palette-item runtime-${plugin.runtime}`}>
                <div className="palette-item-head">
                  <b>{plugin.title}</b>
                  <span className={`runtime-dot ${plugin.runtime}`} title={`${RUNTIME_LABELS[plugin.runtime]?.label}：${RUNTIME_LABELS[plugin.runtime]?.hint}`} />
                  <button className="add-node" onClick={() => onAddNode(type)} disabled={disabled} title={`把 ${plugin.title} 加入画布`}>+</button>
                </div>
                <code>{type}</code>
                <p>{plugin.description}</p>
                <small>
                  {plugin.inputs.length > 0 && <>入 {plugin.inputs.join('/')} · </>}出 {plugin.outputs.join('/')}
                  {plugin.runtime === 'stub' && <strong className="stub-text"> · runtime-stub</strong>}
                </small>
              </div>
            ))}
          </section>
        ))}
      </div>
    </aside>
  )
}
