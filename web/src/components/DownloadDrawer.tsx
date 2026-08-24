// 下载 / 自托管抽屉：本地部署命令、Recipe 导出、Evidence Capsule 下载

import { api } from '../api'
import type { Recipe, Run } from '../types'

type Props = {
  open: boolean
  onClose: () => void
  recipe: Recipe | null
  run: Run | null
}

const SETUP_COMMANDS = `# 1. 克隆并安装（Python 3.11+）
git clone https://github.com/daleselaji-dev/openrag-forge.git
cd openrag-forge
python -m venv .venv && source .venv/bin/activate   # Windows: .\\.venv\\Scripts\\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env

# 2. 启动 API + 工作台（Lite：无需 Qdrant / 模型服务也能跑）
uvicorn openrag_forge.app:app --reload --port 18000
# 打开 http://localhost:18000

# 3. 可选：带 Qdrant 的完整 Lite 栈
docker compose up -d qdrant api

# 4. 可选：前端开发模式（Vite 代理 /api 到 18000）
cd web && npm install && npm run dev

# 5. 可选：构建静态工作台，由 FastAPI 直接伺服
cd web && npm run build`

export function DownloadDrawer({ open, onClose, recipe, run }: Props) {
  if (!open) return null
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <section className="drawer" onClick={(event) => event.stopPropagation()} aria-label="下载与自托管">
        <header className="drawer-head">
          <h2>下载 / 自托管</h2>
          <button className="ghost small" onClick={onClose}>关闭</button>
        </header>
        <div className="drawer-body">
          <h3>本地运行整个工作台</h3>
          <p className="muted">真相源是 SQLite + 本地文件（<code>./data</code>），可以完整备份与离线检查。模型通过 OpenAI-compatible 端点接入（在 <code>.env</code> 或「导入 → API / 模型」里配置），权重永远不进入 Web 应用。</p>
          <pre className="command-block"><code>{SETUP_COMMANDS}</code></pre>

          <h3>导出当前装配</h3>
          <div className="download-actions">
            {recipe ? (
              <a className="download-link" href={api.exportRecipeUrl(recipe.recipe_id)} download>下载 Recipe JSON（{recipe.recipe_id}）</a>
            ) : <span className="muted">先选择一个 Recipe</span>}
            {run ? (
              <a className="download-link" href={api.capsuleUrl(run.run_id)} download>下载 Evidence Capsule（{run.run_id.slice(0, 16)}…）</a>
            ) : <span className="muted">运行一次后可下载 Evidence Capsule</span>}
          </div>
          <p className="muted">Evidence Capsule 是单文件 JSON：配置 + 模型 ID + recipe hash + 证据 + 引用 + 安全决策 + 完整 Trace，可归档、可复现、可仲裁。Recipe JSON 可以在任何 OpenRAG Forge 实例上通过「导入 → Recipe JSON」还原。</p>
        </div>
      </section>
    </div>
  )
}
