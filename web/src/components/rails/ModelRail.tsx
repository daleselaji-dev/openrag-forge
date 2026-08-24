import { useState, type FormEvent } from 'react'
import type { ModelProfile } from '../../types'

type ProbeState = Record<string, { status: string; detail?: string }>

type Props = {
  models: ModelProfile[]
  teachOn: boolean
  probes: ProbeState
  onRegister: (form: { model_id: string; display_name: string; kind: string; base_url: string; model_name: string }) => Promise<boolean>
  onProbe: (modelId: string) => void
}

export default function ModelRail({ models, teachOn, probes, onRegister, onProbe }: Props) {
  const [form, setForm] = useState({ model_id: '', display_name: '', kind: 'chat', base_url: 'http://localhost:1234/v1', model_name: '' })

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const ok = await onRegister(form)
    if (ok) setForm({ model_id: '', display_name: '', kind: 'chat', base_url: form.base_url, model_name: '' })
  }

  return (
    <div className="rail-panel">
      <div className="rail-head"><h3>模型注册</h3></div>
      {teachOn && <p className="teach-hint">教学：这里只保存 OpenAI 兼容端点的连接配置（LM Studio / Ollama / vLLM / 云端），权重永远留在模型服务里。注册后在节点检查器把 model_ref 绑到 LLM 生成节点即可生效；Embedding 模型在上传时选择。</p>}
      <form className="model-form" onSubmit={submit}>
        <input placeholder="model_id（唯一标识）" value={form.model_id} onChange={(event) => setForm({ ...form, model_id: event.target.value })} required />
        <input placeholder="显示名称" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} required />
        <select value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>
          <option value="chat">Chat</option>
          <option value="embedding">Embedding</option>
          <option value="reranker">Reranker</option>
        </select>
        <input placeholder="base_url，如 http://localhost:1234/v1" value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} required />
        <input placeholder="服务中的 model name" value={form.model_name} onChange={(event) => setForm({ ...form, model_name: event.target.value })} required />
        <button type="submit" className="primary">注册模型</button>
      </form>
      <div className="model-list">
        {models.map((model) => {
          const probe = probes[model.model_id]
          return (
            <div className="model-row" key={model.model_id}>
              <div className="model-info">
                <b>{model.display_name}</b>
                <span className={`kind-chip ${model.kind}`}>{model.kind}</span>
                <small className="mono">{model.model_id} · {model.base_url}</small>
              </div>
              <div className="model-actions">
                {probe && <span className={`probe-chip ${probe.status === 'ready' ? 'good' : 'bad'}`} title={probe.detail}>{probe.status === 'ready' ? '可达' : '不可达'}</span>}
                <button className="link-btn" onClick={() => onProbe(model.model_id)}>探测</button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
