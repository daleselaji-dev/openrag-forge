// 导入抽屉：API / 模型注册（key 只存服务端）、Recipe JSON、Scenario JSON

import { useState, type FormEvent } from 'react'
import { api } from '../api'
import type { ModelProfile } from '../types'

type Props = {
  open: boolean
  onClose: () => void
  models: ModelProfile[]
  onModelsChanged: () => void
  onRecipesChanged: () => void
  onScenariosChanged: () => void
  onUseEmbedding: (modelId: string) => void
  log: (message: string) => void
}

const EMPTY_FORM = { model_id: '', display_name: '', kind: 'chat', base_url: 'http://localhost:1234/v1', model_name: '', api_key: '' }

export function ImportsDrawer({ open, onClose, models, onModelsChanged, onRecipesChanged, onScenariosChanged, onUseEmbedding, log }: Props) {
  const [tab, setTab] = useState<'models' | 'recipe' | 'scenario'>('models')
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [probeResults, setProbeResults] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)

  if (!open) return null

  const register = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    try {
      const payload: Record<string, unknown> = { ...form, parameters: {}, source: 'endpoint' }
      if (!form.api_key) delete payload.api_key
      const body = await api.registerModel(payload)
      log(`模型已注册：${body.model.model_id}（API key ${body.model.has_api_key ? '已保存在服务端并脱敏' : '未提供'}）`)
      setForm({ ...EMPTY_FORM })
      onModelsChanged()
      if (body.model.kind === 'embedding') onUseEmbedding(body.model.model_id)
    } catch (error) { log(`模型注册失败：${(error as Error).message}`) } finally { setBusy(false) }
  }

  const probe = async (modelId: string) => {
    setProbeResults((state) => ({ ...state, [modelId]: 'probing…' }))
    try {
      const body = await api.probeModel(modelId)
      const summary = body.status === 'ready' ? `ready（HTTP ${String((body.details as { http_status?: number }).http_status)}）` : `unreachable：${String((body.details as { error?: string }).error || '').slice(0, 120)}`
      setProbeResults((state) => ({ ...state, [modelId]: summary }))
      log(`探测 ${modelId}：${body.status}`)
    } catch (error) { setProbeResults((state) => ({ ...state, [modelId]: (error as Error).message })) }
  }

  const importJsonFile = async (file: File, kind: 'recipe' | 'scenario') => {
    setBusy(true)
    try {
      const payload = JSON.parse(await file.text())
      if (kind === 'recipe') {
        const body = await api.importRecipe(payload)
        log(`已导入 ${body.count} 个 Recipe（draft 状态，已编译校验）：${body.items.map((item) => item.recipe_id).join(', ')}`)
        onRecipesChanged()
      } else {
        const body = await api.createScenario(payload)
        log(`已导入 Scenario：${body.scenario.title}`)
        onScenariosChanged()
      }
    } catch (error) { log(`导入失败：${(error as Error).message}`) } finally { setBusy(false) }
  }

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <section className="drawer" onClick={(event) => event.stopPropagation()} aria-label="导入面板">
        <header className="drawer-head">
          <h2>导入</h2>
          <button className="ghost small" onClick={onClose}>关闭</button>
        </header>
        <div className="drawer-tabs" role="tablist">
          <button role="tab" aria-selected={tab === 'models'} className={tab === 'models' ? 'active' : ''} onClick={() => setTab('models')}>API / 模型</button>
          <button role="tab" aria-selected={tab === 'recipe'} className={tab === 'recipe' ? 'active' : ''} onClick={() => setTab('recipe')}>Recipe JSON</button>
          <button role="tab" aria-selected={tab === 'scenario'} className={tab === 'scenario' ? 'active' : ''} onClick={() => setTab('scenario')}>Scenario JSON</button>
        </div>

        {tab === 'models' && (
          <div className="drawer-body">
            <p className="muted">注册任意 OpenAI-compatible 端点（LM Studio / Ollama / vLLM / llama.cpp / 云端）。网页只保存连接配置：<b>API key 存在服务端 SQLite，所有接口一律脱敏返回，永不进入 Trace / Capsule；模型权重永远不会进入 Web 应用。</b></p>
            <form className="model-form" onSubmit={register}>
              <input placeholder="model_id（唯一标识）" value={form.model_id} onChange={(event) => setForm({ ...form, model_id: event.target.value })} required aria-label="model_id" />
              <input placeholder="显示名称" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} required aria-label="显示名称" />
              <select value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })} aria-label="模型类型">
                <option value="chat">Chat</option>
                <option value="embedding">Embedding</option>
                <option value="reranker">Reranker</option>
              </select>
              <input placeholder="base_url，如 https://api.openai.com/v1" value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} required aria-label="base_url" />
              <input placeholder="服务中的 model name" value={form.model_name} onChange={(event) => setForm({ ...form, model_name: event.target.value })} required aria-label="model name" />
              <input placeholder="API key（可选，只存服务端）" type="password" value={form.api_key} onChange={(event) => setForm({ ...form, api_key: event.target.value })} autoComplete="off" aria-label="API key" />
              <button type="submit" className="primary" disabled={busy}>注册并探测</button>
            </form>
            <div className="model-list">
              {models.map((model) => (
                <div className="model-row" key={model.model_id}>
                  <div>
                    <b>{model.display_name}</b>
                    <span className={`kind-tag ${model.kind}`}>{model.kind}</span>
                    {model.has_api_key && <span className="kind-tag key">key 已存</span>}
                    <small>{model.model_id} · {model.base_url} · {model.model_name}</small>
                    {probeResults[model.model_id] && <small className="probe-result">{probeResults[model.model_id]}</small>}
                  </div>
                  <div className="actions">
                    <button className="ghost small" onClick={() => void probe(model.model_id)}>探测</button>
                    {model.kind === 'embedding' && <button className="ghost small" onClick={() => { onUseEmbedding(model.model_id); log(`下次 ingest 将使用 Embedding：${model.model_id}`) }}>用于下次 ingest</button>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {tab === 'recipe' && (
          <div className="drawer-body">
            <p className="muted">导入 Recipe JSON（单个 Recipe、<code>{'{"recipe": …}'}</code> 或 <code>{'{"recipes": […]}'}</code>）。导入即编译：未知节点、端口类型不兼容、未声明的环都会被拒绝。与已发布 Recipe 同名时自动加 <code>_imported</code> 后缀，不覆盖。</p>
            <label className="dropzone">
              <input type="file" accept="application/json,.json" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importJsonFile(file, 'recipe'); event.target.value = '' }} disabled={busy} />
              <span>选择 Recipe JSON 文件</span>
              <small>提示：画布工具栏的「导出 JSON」可以导出当前 Recipe 再导入到其它实例。</small>
            </label>
          </div>
        )}

        {tab === 'scenario' && (
          <div className="drawer-body">
            <p className="muted">导入 Scenario JSON：声明业务问题、所需资料、默认 Recipe 与应观察的 Trace。加载后仍需导入对应的知识库文档。</p>
            <label className="dropzone">
              <input type="file" accept="application/json,.json" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importJsonFile(file, 'scenario'); event.target.value = '' }} disabled={busy} />
              <span>选择 Scenario JSON 文件</span>
              <small>字段：scenario_id / title / business_problem / recipe_id / sample_question …</small>
            </label>
          </div>
        )}
      </section>
    </div>
  )
}
