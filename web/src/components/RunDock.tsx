// 底部运行台：问题输入 + Preview/真实运行 + 结果（答案/证据/Capsule）+ 全量 Trace + 场景库 + 运行历史

import type { ReactNode } from 'react'
import { api } from '../api'
import type { Plugin, Recipe, Run, RunSummary, Scenario, TraceEvent } from '../types'
import { TraceList } from './TraceList'

export type DockTab = 'result' | 'trace' | 'documents' | 'scenarios' | 'history'

type Props = {
  tab: DockTab
  onTab: (tab: DockTab) => void
  question: string
  onQuestion: (question: string) => void
  topK: number
  onTopK: (topK: number) => void
  busy: boolean
  onRun: (mode: 'run' | 'preview') => void
  message: string
  run: Run | null
  recipe: Recipe | null
  plugins: Record<string, Plugin>
  scenarios: Scenario[]
  onUseScenario: (scenario: Scenario) => void
  history: RunSummary[]
  onLoadRun: (runId: string) => void
  onSelectNode: (nodeId: string) => void
  documentsSlot: ReactNode
  documentsCount: number
}

export function RunDock({ tab, onTab, question, onQuestion, topK, onTopK, busy, onRun, message, run, recipe, plugins, scenarios, onUseScenario, history, onLoadRun, onSelectNode, documentsSlot, documentsCount }: Props) {
  return (
    <section className="run-dock" aria-label="运行台">
      <div className="run-bar">
        <textarea
          value={question}
          onChange={(event) => onQuestion(event.target.value)}
          placeholder="输入要问知识库的问题…"
          aria-label="问题输入"
          rows={2}
        />
        <label className="topk-control">top_k
          <input type="number" min={1} max={20} value={topK} onChange={(event) => onTopK(Math.min(20, Math.max(1, Number(event.target.value) || 5)))} />
        </label>
        <div className="run-buttons">
          <button className="ghost" onClick={() => onRun('preview')} disabled={busy} title="dry compile：校验图结构与配置，不调用模型、不写索引">Preview 结构</button>
          <button className="primary" onClick={() => onRun('run')} disabled={busy}>{busy ? '运行中…' : '运行真实链路'}</button>
        </div>
      </div>
      <p className="run-message" role="status">{message}</p>

      <div className="dock-tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'result'} className={tab === 'result' ? 'active' : ''} onClick={() => onTab('result')}>运行结果</button>
        <button role="tab" aria-selected={tab === 'trace'} className={tab === 'trace' ? 'active' : ''} onClick={() => onTab('trace')}>Trace 时间线{run ? `（${run.trace.length}）` : ''}</button>
        <button role="tab" aria-selected={tab === 'documents'} className={tab === 'documents' ? 'active' : ''} onClick={() => onTab('documents')}>文档 / Blocks（{documentsCount}）</button>
        <button role="tab" aria-selected={tab === 'scenarios'} className={tab === 'scenarios' ? 'active' : ''} onClick={() => onTab('scenarios')}>场景库</button>
        <button role="tab" aria-selected={tab === 'history'} className={tab === 'history' ? 'active' : ''} onClick={() => onTab('history')}>运行历史</button>
      </div>

      <div className="dock-body">
        {tab === 'result' && (
          <div className="result-panel">
            {!run && <p className="muted">运行后这里展示：受证据约束的回答（含 [S#] 引用）、Agent 草稿、安全决策与可下载的 Evidence Capsule。模型离线时会看到抽取式降级回答——降级同样写入 Trace。</p>}
            {run && (
              <>
                <div className="result-head">
                  <span className="pill">{run.recipe_id}</span>
                  <span className="pill hash" title={run.recipe_hash}>hash {run.recipe_hash.slice(0, 12)}</span>
                  {Boolean(run.safety.cache_hit) && <span className="pill warn">cache hit</span>}
                  {Boolean(run.safety.rate_limited) && <span className="pill warn">rate limited</span>}
                  {Array.isArray(run.safety.request_safety_gate) && <span className="pill danger">安全门拦截：{(run.safety.request_safety_gate as string[]).join(', ')}</span>}
                  {Boolean(run.safety.human_review) && <span className="pill warn">需人工复核</span>}
                  <a className="download-link" href={api.capsuleUrl(run.run_id)} download>下载 Evidence Capsule</a>
                </div>
                <p className="answer">{run.answer || 'Preview 未生成回答；图结构与端口类型已校验通过。'}</p>
                {run.artifact != null && (
                  <details className="artifact-details" open>
                    <summary>Agent 产物（停在人工审批门）</summary>
                    <pre className="artifact-preview">{JSON.stringify(run.artifact, null, 2)}</pre>
                  </details>
                )}
                <div className="evidence-list">
                  {run.evidence.map((item) => (
                    <div className="evidence" key={item.chunk_id}>
                      <b>[{item.citation}] {item.title}</b>
                      <small>score {item.score} · {item.chunk_id}{item.metadata?.parent_expanded === true ? ' · 已父子扩展' : ''}</small>
                      <p>{item.text.slice(0, 480)}{item.text.length > 480 ? '…' : ''}</p>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}
        {tab === 'trace' && (
          <TraceList
            trace={run?.trace || []}
            recipe={recipe}
            plugins={plugins}
            onSelectNode={onSelectNode}
            emptyText="先 Preview 或运行真实链路。每一行都会显示：状态、耗时、摘要，以及 impact——候选数量、使用的后端、降级/跳过原因、证据 ID、配置快照。点击行可展开完整 details 并联动选中画布节点。"
          />
        )}
        {tab === 'documents' && documentsSlot}
        {tab === 'scenarios' && (
          <div className="scenario-grid">
            {scenarios.map((scenario) => (
              <article className="scenario-card" key={scenario.scenario_id}>
                <span className="eyebrow">{scenario.recipe_id} · {scenario.source || 'builtin'}</span>
                <h3>{scenario.title}</h3>
                <p>{scenario.business_problem}</p>
                <small>需要资料：{scenario.data_requirements.join(' · ') || '—'}</small>
                <small>应观察的 Trace：{scenario.trace_expectation.join(' → ') || '—'}</small>
                <code>{scenario.sample_question}</code>
                <button className="ghost small" onClick={() => onUseScenario(scenario)}>加载该场景（Recipe + 示例问题）</button>
              </article>
            ))}
          </div>
        )}
        {tab === 'history' && (
          <div className="history-list">
            {history.length === 0 && <p className="muted">还没有运行记录。</p>}
            {history.map((item) => (
              <button className="history-row" key={item.run_id} onClick={() => onLoadRun(item.run_id)}>
                <span className="pill">{item.recipe_id}</span>
                <b>{item.answer_preview || '（无回答 / Preview）'}</b>
                <small>{item.evidence_count} 证据 · {item.trace_count} trace · {item.created_at.slice(0, 19).replace('T', ' ')}</small>
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
