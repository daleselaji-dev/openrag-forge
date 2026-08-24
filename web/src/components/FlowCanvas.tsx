// 中央装配画布：类型化端口连线、节点选择/删除、Trace 状态高亮、真实触发边动画

import { useCallback, useMemo, useRef } from 'react'
import {
  Background, Controls, Handle, MiniMap, Position, ReactFlow,
  type Connection, type Edge, type Node, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { Plugin, Recipe, TraceEvent } from '../types'

type ForgeNodeData = {
  label: string
  nodeType: string
  inputs: string[]
  outputs: string[]
  runtime: string
  traceStatus?: string
}
type ForgeFlowNode = Node<ForgeNodeData, 'forge'>

function ForgeNode({ data, selected }: NodeProps<ForgeFlowNode>) {
  return (
    <div className={`forge-node runtime-${data.runtime} ${selected ? 'selected-flow-node' : ''} ${data.traceStatus ? `trace-${data.traceStatus}` : ''}`}>
      {data.inputs.map((port, index) => (
        <Handle key={`in-${port}-${index}`} type="target" position={Position.Left} id={port} style={{ top: `${((index + 1) / (data.inputs.length + 1)) * 100}%` }} title={`输入端口 ${port}`} />
      ))}
      <b>{data.label}</b>
      <small>{data.nodeType}</small>
      {data.traceStatus && <em className={`node-status ${data.traceStatus}`}>{data.traceStatus}</em>}
      {data.runtime === 'stub' && <span className="stub-badge" title="compile-complete / runtime-stub">桩</span>}
      {data.outputs.map((port, index) => (
        <Handle key={`out-${port}-${index}`} type="source" position={Position.Right} id={port} style={{ top: `${((index + 1) / (data.outputs.length + 1)) * 100}%` }} title={`输出端口 ${port}`} />
      ))}
    </div>
  )
}

const nodeTypes = { forge: ForgeNode }

// 按最长路径深度分层布局
function layout(recipe: Recipe): Record<string, { x: number; y: number }> {
  const depth: Record<string, number> = {}
  recipe.nodes.forEach((node) => { depth[node.id] = 0 })
  for (let iteration = 0; iteration < recipe.nodes.length; iteration += 1) {
    let changed = false
    for (const edge of recipe.edges) {
      const next = (depth[edge.source] ?? 0) + 1
      if (next > (depth[edge.target] ?? 0)) { depth[edge.target] = next; changed = true }
    }
    if (!changed) break
  }
  const lanes: Record<number, number> = {}
  const positions: Record<string, { x: number; y: number }> = {}
  for (const node of recipe.nodes) {
    const column = depth[node.id] ?? 0
    const lane = lanes[column] ?? 0
    lanes[column] = lane + 1
    positions[node.id] = { x: column * 250 + 20, y: lane * 130 + 20 }
  }
  return positions
}

type Props = {
  recipe: Recipe | null
  plugins: Record<string, Plugin>
  selectedNodeId: string | null
  trace: TraceEvent[]
  onSelectNode: (nodeId: string | null) => void
  onConnect: (edge: { source: string; source_port: string; target: string; target_port: string }) => void
  onDeleteNodes: (nodeIds: string[]) => void
  onDeleteEdges: (edges: { source: string; target: string; sourceHandle?: string | null; targetHandle?: string | null }[]) => void
}

export function FlowCanvas({ recipe, plugins, selectedNodeId, trace, onSelectNode, onConnect, onDeleteNodes, onDeleteEdges }: Props) {
  // 记住用户拖拽过的位置（按 recipe + node 记）
  const dragged = useRef<Record<string, { x: number; y: number }>>({})

  const traceStatus = useMemo(() => {
    const map: Record<string, string> = {}
    for (const event of trace) map[event.node_id] = event.status
    return map
  }, [trace])

  const nodes = useMemo<Node[]>(() => {
    if (!recipe) return []
    const auto = layout(recipe)
    return recipe.nodes.map((node) => {
      const plugin = plugins[node.type]
      return {
        id: node.id,
        type: 'forge' as const,
        position: dragged.current[`${recipe.recipe_id}:${node.id}`] || auto[node.id] || { x: 0, y: 0 },
        data: {
          label: node.label || plugin?.title || node.type,
          nodeType: node.type,
          inputs: plugin?.inputs || [],
          outputs: plugin?.outputs || [],
          runtime: plugin?.runtime || 'implemented',
          traceStatus: traceStatus[node.id],
        },
        selected: selectedNodeId === node.id,
      }
    })
  }, [recipe, plugins, selectedNodeId, traceStatus])

  const edges = useMemo<Edge[]>(() => {
    if (!recipe) return []
    return recipe.edges.map((edge, index) => {
      const fired = traceStatus[edge.source] === 'completed' && Boolean(traceStatus[edge.target]) && traceStatus[edge.target] !== 'skipped'
      return {
        id: `e-${index}`,
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.source_port,
        targetHandle: edge.target_port,
        label: `${edge.source_port} → ${edge.target_port}`,
        animated: fired,
        className: fired ? 'edge-fired' : traceStatus[edge.source] === 'skipped' ? 'edge-skipped' : '',
      }
    })
  }, [recipe, traceStatus])

  const handleConnect = useCallback((connection: Connection) => {
    if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) return
    onConnect({ source: connection.source, source_port: connection.sourceHandle, target: connection.target, target_port: connection.targetHandle })
  }, [onConnect])

  return (
    <div className="flow-wrap" aria-label="Recipe 装配画布">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        deleteKeyCode={['Backspace', 'Delete']}
        onConnect={handleConnect}
        onNodeClick={(_, node) => onSelectNode(node.id)}
        onPaneClick={() => onSelectNode(null)}
        onNodeDragStop={(_, node) => { if (recipe) dragged.current[`${recipe.recipe_id}:${node.id}`] = node.position }}
        onNodesDelete={(deleted) => onDeleteNodes(deleted.map((node) => node.id))}
        onEdgesDelete={(deleted) => onDeleteEdges(deleted.map((edge) => ({ source: edge.source, target: edge.target, sourceHandle: edge.sourceHandle, targetHandle: edge.targetHandle })))}
      >
        <Background gap={22} color="#d8dee9" />
        <Controls />
        <MiniMap pannable zoomable />
      </ReactFlow>
    </div>
  )
}
