import { useMemo } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { CatalogNode, Recipe, RecipeEdgeDef, TraceEvent } from '../types'
import { fmtMs, STATUS_LABELS } from '../format'
import { IMPLEMENTED_LABELS } from '../teach'

export type NodeRunState = { status: string; durationMs: number; execution: string }

export type ForgeNodeData = {
  title: string
  nodeType: string
  group: string
  implemented: string
  inputs: string[]
  outputs: string[]
  run?: NodeRunState
  preview?: boolean
}

type ForgeFlowNode = Node<ForgeNodeData, 'forge'>

function ForgeNode({ data, selected }: NodeProps<ForgeFlowNode>) {
  const flag = data.implemented !== 'live' ? IMPLEMENTED_LABELS[data.implemented] : null
  const runClass = data.run ? ` run-${data.run.status}` : ''
  return (
    <div className={`forge-node group-${data.group}${selected ? ' is-selected' : ''}${runClass}`}>
      {data.inputs.map((port, index) => (
        <Handle
          key={`in-${port}-${index}`}
          type="target"
          position={Position.Left}
          id={port}
          title={`输入端口：${port}`}
          style={{ top: `${((index + 1) / (data.inputs.length + 1)) * 100}%` }}
        />
      ))}
      <div className="forge-node-head">
        <b>{data.title}</b>
        {flag && <span className={`node-flag flag-${data.implemented}`}>{flag.label}</span>}
      </div>
      <small className="forge-node-type">{data.nodeType}</small>
      {data.run && (
        <div className={`forge-node-run ${data.run.status}${data.preview ? ' preview' : ''}`}>
          <span>{data.preview ? 'PREVIEW' : STATUS_LABELS[data.run.status] || data.run.status}</span>
          <em>{fmtMs(data.run.durationMs)}</em>
        </div>
      )}
      {data.outputs.map((port, index) => (
        <Handle
          key={`out-${port}-${index}`}
          type="source"
          position={Position.Right}
          id={port}
          title={`输出端口：${port}`}
          style={{ top: `${((index + 1) / (data.outputs.length + 1)) * 100}%` }}
        />
      ))}
    </div>
  )
}

const nodeTypes = { forge: ForgeNode }

// 分层布局：按最长路径深度分列，同层纵向排布。
export function layoutRecipe(recipe: Recipe): Record<string, { x: number; y: number }> {
  const depth: Record<string, number> = {}
  for (const node of recipe.nodes) depth[node.id] = 0
  for (let pass = 0; pass < recipe.nodes.length; pass += 1) {
    let changed = false
    for (const edge of recipe.edges) {
      if (depth[edge.source] === undefined || depth[edge.target] === undefined) continue
      const next = depth[edge.source] + 1
      if (next > depth[edge.target]) {
        depth[edge.target] = next
        changed = true
      }
    }
    if (!changed) break
  }
  const layers: Record<number, string[]> = {}
  for (const node of recipe.nodes) {
    const layer = depth[node.id] ?? 0
    if (!layers[layer]) layers[layer] = []
    layers[layer].push(node.id)
  }
  const positions: Record<string, { x: number; y: number }> = {}
  for (const [layer, ids] of Object.entries(layers)) {
    ids.forEach((id, index) => {
      positions[id] = { x: Number(layer) * 250, y: index * 120 + (Number(layer) % 2) * 24 }
    })
  }
  return positions
}

type Props = {
  recipe: Recipe | null
  catalog: Record<string, CatalogNode>
  positions: Record<string, { x: number; y: number }>
  selectedNodeId: string | null
  runTrace: TraceEvent[] | null
  isPreview: boolean
  coachActive: boolean
  onSelectNode: (id: string | null) => void
  onConnectEdge: (edge: RecipeEdgeDef) => void
  onMoveNode: (id: string, position: { x: number; y: number }) => void
  onDeleteNodes: (ids: string[]) => void
  onDeleteEdges: (edges: RecipeEdgeDef[]) => void
}

export default function RecipeCanvas({ recipe, catalog, positions, selectedNodeId, runTrace, isPreview, coachActive, onSelectNode, onConnectEdge, onMoveNode, onDeleteNodes, onDeleteEdges }: Props) {
  const nodeRunState = useMemo(() => {
    const map: Record<string, NodeRunState> = {}
    for (const event of runTrace || []) {
      map[event.node_id] = { status: event.status, durationMs: event.duration_ms, execution: String(event.details?.execution || '') }
    }
    return map
  }, [runTrace])

  const nodes = useMemo<Node[]>(() => {
    if (!recipe) return []
    return recipe.nodes.map((node) => {
      const spec = catalog[node.type]
      return {
        id: node.id,
        type: 'forge' as const,
        position: positions[node.id] || { x: 0, y: 0 },
        selected: selectedNodeId === node.id,
        data: {
          title: spec?.title || node.type,
          nodeType: node.type,
          group: spec?.group || 'query',
          implemented: spec?.implemented || 'stub',
          inputs: spec?.inputs || [],
          outputs: spec?.outputs || [],
          run: nodeRunState[node.id],
          preview: isPreview,
        },
      }
    })
  }, [recipe, catalog, positions, selectedNodeId, nodeRunState, isPreview])

  const edges = useMemo<Edge[]>(() => {
    if (!recipe) return []
    return recipe.edges.map((edge, index) => {
      const executed = Boolean(nodeRunState[edge.source] && nodeRunState[edge.target])
      const failed = nodeRunState[edge.source]?.status === 'failed' || nodeRunState[edge.target]?.status === 'failed'
      return {
        id: `e-${index}`,
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.source_port,
        targetHandle: edge.target_port,
        label: `${edge.source_port} → ${edge.target_port}`,
        animated: executed && !isPreview,
        className: `forge-edge${executed ? (isPreview ? ' edge-preview' : ' edge-live') : ''}${failed ? ' edge-failed' : ''}`,
      }
    })
  }, [recipe, nodeRunState, isPreview])

  const handleConnect = (connection: Connection) => {
    if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) return
    onConnectEdge({ source: connection.source, source_port: connection.sourceHandle, target: connection.target, target_port: connection.targetHandle })
  }

  const handleEdgesDelete = (deleted: Edge[]) => {
    onDeleteEdges(deleted.map((edge) => ({ source: edge.source, source_port: edge.sourceHandle || '', target: edge.target, target_port: edge.targetHandle || '' })))
  }

  if (!recipe) return <div className="canvas-empty">先在左侧「装配」页选择一个 Recipe。</div>

  return (
    <div className={`flow-wrap${coachActive ? ' coach-pulse' : ''}`}>
      <ReactFlow
        key={recipe.recipe_id}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
        minZoom={0.3}
        deleteKeyCode={['Backspace', 'Delete']}
        onConnect={handleConnect}
        onNodeClick={(_, node) => onSelectNode(node.id)}
        onPaneClick={() => onSelectNode(null)}
        onNodeDragStop={(_, node) => onMoveNode(node.id, node.position)}
        onNodesDelete={(deleted) => onDeleteNodes(deleted.map((node) => node.id))}
        onEdgesDelete={handleEdgesDelete}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} size={1.4} color="var(--canvas-dot)" variant={BackgroundVariant.Dots} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable className="forge-minimap" />
      </ReactFlow>
    </div>
  )
}
