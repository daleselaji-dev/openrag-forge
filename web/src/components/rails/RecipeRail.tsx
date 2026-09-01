import type { CatalogNode, Recipe } from '../../types'
import { shortHash } from '../../format'

type Props = {
  recipes: Recipe[]
  selectedRecipeId: string
  catalog: Record<string, CatalogNode>
  dirty: boolean
  teachOn: boolean
  onSelectRecipe: (id: string) => void
  onCreateDraft: () => void
}

const STATUS_ORDER: Record<string, number> = { published: 0, validated: 1, draft: 2, deprecated: 3 }

export default function RecipeRail({ recipes, selectedRecipeId, catalog, dirty, teachOn, onSelectRecipe, onCreateDraft }: Props) {
  const sorted = [...recipes].sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || a.version.localeCompare(b.version, undefined, { numeric: true }))
  return (
    <div className="rail-panel">
      <div className="rail-head">
        <h3>Recipe 目录</h3>
        <button className="ghost small" onClick={onCreateDraft}>创建草稿副本</button>
      </div>
      {teachOn && <p className="teach-hint">教学：Recipe 是编译过的 typed DAG。V0.2 的 BM25 + RRF、上下文预算、纠错、缓存和限流已有真实执行；重排依赖可达的 /rerank 端点，图谱仍是 stub。始终结合节点徽标与 Trace 判断能力，不要只按名字推断。</p>}
      <div className="recipe-list">
        {sorted.map((recipe) => {
          const stubCount = recipe.nodes.filter((node) => catalog[node.type]?.implemented && catalog[node.type].implemented !== 'live').length
          const active = selectedRecipeId === recipe.recipe_id
          return (
            <button key={recipe.recipe_id} className={`recipe-card${active ? ' active' : ''}`} onClick={() => onSelectRecipe(recipe.recipe_id)}>
              <span className="recipe-version">V{recipe.version} · {recipe.status}{active && dirty ? ' · 未保存' : ''}</span>
              <b>{recipe.name}</b>
              <small>{recipe.nodes.length} 节点 · {shortHash(recipe.hash, 8)}{stubCount > 0 ? ` · ${stubCount} 个占位/退化` : ''}</small>
            </button>
          )
        })}
      </div>
    </div>
  )
}
