import { useEffect, useMemo, useState } from 'react'
import type { CatalogNode, Recipe } from '../types'
import type { DeepDiveChapter, InterviewChapterId, LessonAction } from '../interview/types'
import {
  COMPONENT_CHAPTERS, EXPERIMENTS, EXPERIMENTS_INTRO, JOURNEY, JOURNEY_INTRO,
  LANDSCAPE, LANDSCAPE_CONCLUSION, LANDSCAPE_INTRO, PARADIGMS, PARADIGMS_INTRO,
  STAGE_LESSONS, STAGE_MAP_ORDER, STAGES_INTRO, VECTORDB_CHAPTER,
} from '../interview'
import InterviewLessonCard from './InterviewLessonCard'
import { IMPLEMENTED_LABELS } from '../teach'

const CHAPTERS: { id: InterviewChapterId; label: string }[] = [
  { id: 'journey', label: '设计历程' },
  { id: 'landscape', label: '方案对比' },
  { id: 'stages', label: '环节地图' },
  { id: 'deepdive', label: '核心件深讲' },
  { id: 'experiments', label: '实验手册' },
]

type Props = {
  catalog: Record<string, CatalogNode>
  recipe: Recipe | null
  collapsed: boolean
  onToggleCollapsed: () => void
  /** 选中画布节点（联动检查器讲解 + 配置） */
  onSelectNode: (nodeId: string) => void
  /** 把某类型节点加入画布试装 */
  onAddNode: (nodeType: string) => void
  /** 执行讲解里的工作台动作（加载 Recipe / 切页签 / 预填问题） */
  onAction: (action: LessonAction) => void
}

function ActionButtons({ actions, onAction }: { actions: LessonAction[]; onAction: (a: LessonAction) => void }) {
  if (!actions.length) return null
  return (
    <div className="iv-actions">
      {actions.map((action) => (
        <button key={action.label} className="ghost small" onClick={() => onAction(action)}>{action.label} →</button>
      ))}
    </div>
  )
}

function JourneyChapter({ onAction }: { onAction: (a: LessonAction) => void }) {
  return (
    <div className="iv-chapter">
      {JOURNEY_INTRO.map((text, index) => <p key={index} className="iv-intro">{text}</p>)}
      <div className="iv-timeline">
        {JOURNEY.map((gen) => (
          <article key={gen.id} className="iv-gen">
            <div className="iv-gen-head">
              <h4>{gen.name}</h4>
              <p className="iv-tagline">{gen.tagline}</p>
            </div>
            <div className="iv-block"><h5>业务动机</h5>{gen.motivation.map((text, index) => <p key={index}>{text}</p>)}</div>
            <div className="iv-block"><h5>产品决策</h5><ul>{gen.decisions.map((text) => <li key={text}>{text}</li>)}</ul></div>
            <div className="iv-block"><h5>技术取舍 · 牺牲了什么</h5><ul>{gen.tradeoffs.map((text) => <li key={text}>{text}</li>)}</ul></div>
            <div className="iv-block iv-why-next"><h5>为什么走向下一步</h5><p>{gen.whyNext}</p></div>
            <div className="iv-block">
              <h5>面试官可能追问</h5>
              {gen.interviewQs.map((item) => (
                <details key={item.q} className="iv-qa"><summary>{item.q}</summary><p>{item.a}</p></details>
              ))}
            </div>
            <div className="iv-block"><h5>在工作台看这一代的遗产</h5><ul>{gen.legacy.map((text) => <li key={text}>{text}</li>)}</ul></div>
            <ActionButtons actions={gen.actions} onAction={onAction} />
          </article>
        ))}
      </div>
    </div>
  )
}

function LandscapeChapter({ onAction }: { onAction: (a: LessonAction) => void }) {
  return (
    <div className="iv-chapter">
      {LANDSCAPE_INTRO.map((text, index) => <p key={index} className="iv-intro">{text}</p>)}

      <h4 className="iv-section-title">范式：Naive / Advanced / Modular</h4>
      <p className="iv-intro">{PARADIGMS_INTRO}</p>
      {PARADIGMS.map((row) => (
        <article key={row.name} className="iv-card">
          <h5>{row.name}</h5>
          <p>{row.definition}</p>
          <p className="mono iv-pipeline">{row.pipeline}</p>
          <div className="iv-kv"><small>强项</small><p>{row.strengths}</p></div>
          <div className="iv-kv"><small>弱项</small><p>{row.weaknesses}</p></div>
          <div className="iv-kv iv-stance"><small>本仓库站位</small><p>{row.forgeStance}</p></div>
        </article>
      ))}

      {LANDSCAPE.map((category) => (
        <section key={category.id}>
          <h4 className="iv-section-title">{category.title}</h4>
          <p className="iv-intro">{category.intro}</p>
          {category.entries.map((entry) => (
            <details key={entry.id} className="iv-entry">
              <summary>
                <b>{entry.name}</b>
                <span className="iv-when">{entry.whenToPick}</span>
              </summary>
              <div className="iv-kv"><small>产品定位</small><p>{entry.positioning}</p></div>
              <div className="iv-kv"><small>装配方式</small><p>{entry.assembly}</p></div>
              <div className="iv-kv"><small>可观测性</small><p>{entry.observability}</p></div>
              <div className="iv-kv"><small>定制深度</small><p>{entry.customization}</p></div>
              <div className="iv-kv"><small>成本 / 运维</small><p>{entry.costOps}</p></div>
              <div className="iv-kv"><small>合规</small><p>{entry.compliance}</p></div>
              <div className="iv-kv"><small>对 PM 的意义</small><p>{entry.pmTakeaway}</p></div>
              <div className="iv-pros-cons">
                <div><small>优势</small><ul>{entry.strengths.map((text) => <li key={text}>{text}</li>)}</ul></div>
                <div><small>劣势</small><ul>{entry.weaknesses.map((text) => <li key={text}>{text}</li>)}</ul></div>
              </div>
              <div className="iv-kv"><small>何时该选</small><p>{entry.whenToPick}</p></div>
              <div className="iv-kv iv-stance"><small>与 OpenRAG Forge 的差异</small><p>{entry.vsForge}</p></div>
            </details>
          ))}
        </section>
      ))}

      <div className="iv-conclusion">
        <h4>{LANDSCAPE_CONCLUSION.title}</h4>
        {LANDSCAPE_CONCLUSION.points.map((text, index) => <p key={index}>{text}</p>)}
        <ActionButtons actions={[{ label: '在沙盘里装配一条候选链路', railTab: 'recipe' }]} onAction={onAction} />
      </div>
    </div>
  )
}

function StagesChapter({ catalog, recipe, activeStage, onPickStage, onAddNode, onAction }: {
  catalog: Record<string, CatalogNode>
  recipe: Recipe | null
  activeStage: string | null
  onPickStage: (type: string) => void
  onAddNode: (type: string) => void
  onAction: (a: LessonAction) => void
}) {
  const nodeIdByType = useMemo(() => {
    const map: Record<string, string> = {}
    for (const node of recipe?.nodes || []) if (!map[node.type]) map[node.type] = node.id
    return map
  }, [recipe])

  const lesson = activeStage ? STAGE_LESSONS[activeStage] : null
  const spec = activeStage ? catalog[activeStage] : null
  const isVirtual = Boolean(activeStage?.startsWith('_'))
  const inRecipe = Boolean(activeStage && nodeIdByType[activeStage])

  return (
    <div className="iv-chapter">
      {STAGES_INTRO.map((text, index) => <p key={index} className="iv-intro">{text}</p>)}
      {STAGE_MAP_ORDER.map((lane) => (
        <section key={lane.lane} className="iv-lane">
          <h4 className="iv-section-title">{lane.title}</h4>
          <div className="iv-stage-chips">
            {lane.types.map((type) => {
              const stageLesson = STAGE_LESSONS[type]
              if (!stageLesson) return null
              const stageSpec = catalog[type]
              const impl = stageSpec ? IMPLEMENTED_LABELS[stageSpec.implemented] : null
              return (
                <button
                  key={type}
                  className={`iv-stage-chip${activeStage === type ? ' active' : ''}${nodeIdByType[type] ? ' in-recipe' : ''}`}
                  title={nodeIdByType[type] ? '点击：选中画布节点并打开检查器讲解' : type.startsWith('_') ? '系统级环节（无画布节点）' : '当前 Recipe 没有该节点，点击在下方阅读讲解'}
                  onClick={() => onPickStage(type)}
                >
                  {stageLesson.title}
                  {impl && stageSpec!.implemented !== 'live' && <em className={`iv-chip-flag ${stageSpec!.implemented}`}>{impl.label}</em>}
                  {type.startsWith('_') && <em className="iv-chip-flag system">系统</em>}
                </button>
              )
            })}
          </div>
        </section>
      ))}

      {lesson ? (
        <div className="iv-stage-detail">
          {!isVirtual && (inRecipe ? (
            <p className="iv-stage-hint ok">已选中画布上的对应节点——右侧检查器同时显示讲解与可改配置。</p>
          ) : (
            <p className="iv-stage-hint">
              当前 Recipe 没有「{lesson.title}」节点。
              <button className="link-btn" onClick={() => onAddNode(lesson.nodeType)}>把它加入画布试装 →</button>
            </p>
          ))}
          <InterviewLessonCard lesson={lesson} spec={spec} />
          <ActionButtons actions={[{ label: '改完配置去跑一次', bottomTab: 'trace' }]} onAction={onAction} />
        </div>
      ) : (
        <p className="muted">点击上方任意环节查看该环节的完整讲解。</p>
      )}
    </div>
  )
}

function DeepDiveChapterView({ chapter }: { chapter: DeepDiveChapter }) {
  return (
    <div>
      <p className="iv-intro">{chapter.intro}</p>
      {chapter.sections.map((section) => (
        <section key={section.heading} className="iv-dd-section">
          <h4 className="iv-section-title">{section.heading}</h4>
          {section.paragraphs.map((text, index) => <p key={index}>{text}</p>)}
          {section.bullets && <ul>{section.bullets.map((text) => <li key={text}>{text}</li>)}</ul>}
          {section.table && (
            <div className="iv-table-wrap">
              <table className="iv-table">
                <thead><tr>{section.table.columns.map((col) => <th key={col}>{col}</th>)}</tr></thead>
                <tbody>
                  {section.table.rows.map((row, index) => (
                    <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ))}
      {chapter.talkTrack && (
        <section className="iv-dd-section">
          <h4 className="iv-section-title">口述提纲（面试 8-12 分钟版）</h4>
          <div className="iv-talktrack">
            {chapter.talkTrack.map((segment) => (
              <div key={segment.marker} className="iv-talk-seg">
                <b>{segment.marker}</b>
                <ul>{segment.points.map((point) => <li key={point}>{point}</li>)}</ul>
              </div>
            ))}
          </div>
        </section>
      )}
      <section className="iv-dd-section">
        <h4 className="iv-section-title">面试官可能追问</h4>
        {chapter.interviewQs.map((item) => (
          <details key={item.q} className="iv-qa"><summary>{item.q}</summary><p>{item.a}</p></details>
        ))}
      </section>
    </div>
  )
}

function DeepDiveChapterTabs() {
  const chapters = [VECTORDB_CHAPTER, ...COMPONENT_CHAPTERS]
  const [active, setActive] = useState(chapters[0].id)
  const chapter = chapters.find((item) => item.id === active) || chapters[0]
  return (
    <div className="iv-chapter">
      <div className="iv-subtabs">
        {chapters.map((item) => (
          <button key={item.id} className={`iv-subtab${active === item.id ? ' active' : ''}`} onClick={() => setActive(item.id)}>
            {item.id === 'vectordb' ? '向量库（必修）' : item.title.split('：')[0].split('（')[0]}
          </button>
        ))}
      </div>
      <h3 className="iv-dd-title">{chapter.title}</h3>
      <DeepDiveChapterView chapter={chapter} />
    </div>
  )
}

function ExperimentsChapter({ onAction }: { onAction: (a: LessonAction) => void }) {
  return (
    <div className="iv-chapter">
      {EXPERIMENTS_INTRO.map((text, index) => <p key={index} className="iv-intro">{text}</p>)}
      {EXPERIMENTS.map((exp) => (
        <article key={exp.id} className="iv-card iv-exp">
          <h5>{exp.title}</h5>
          <p className="iv-exp-change"><b>改什么：</b>{exp.change}</p>
          <div className="iv-kv"><small>步骤</small><ol>{exp.steps.map((step) => <li key={step}>{step}</li>)}</ol></div>
          <div className="iv-kv"><small>看 Trace / 结果哪一行变了</small><ul>{exp.watch.map((item) => <li key={item}>{item}</li>)}</ul></div>
          <div className="iv-kv"><small>预期与解释</small><p>{exp.expected}</p></div>
          {exp.honestNote && <p className="iv-honest">诚实备注：{exp.honestNote}</p>}
          <ActionButtons actions={exp.actions} onAction={onAction} />
        </article>
      ))}
    </div>
  )
}

export default function InterviewPanel({ catalog, recipe, collapsed, onToggleCollapsed, onSelectNode, onAddNode, onAction }: Props) {
  const [chapter, setChapter] = useState<InterviewChapterId>(() => {
    const stored = localStorage.getItem('orf.interview.chapter') as InterviewChapterId | null
    return stored && CHAPTERS.some((item) => item.id === stored) ? stored : 'journey'
  })
  const [activeStage, setActiveStage] = useState<string | null>(null)

  useEffect(() => { localStorage.setItem('orf.interview.chapter', chapter) }, [chapter])

  const pickStage = (type: string) => {
    setActiveStage(type)
    const node = recipe?.nodes.find((item) => item.type === type)
    if (node) onSelectNode(node.id)
  }

  if (collapsed) {
    return (
      <aside className="interview-panel collapsed">
        <button className="iv-reopen" onClick={onToggleCollapsed} title="展开面试讲解">面试讲解 ▸</button>
      </aside>
    )
  }

  return (
    <aside className="interview-panel">
      <div className="iv-head">
        <div>
          <span className="eyebrow">面试讲解 · RAG 设计课</span>
          <p className="iv-head-note">以本仓库真实演进为底稿的可口述设计讲解。诚实原则：目录能力 ≠ 已实现能力。</p>
        </div>
        <button className="link-btn" onClick={onToggleCollapsed}>收起</button>
      </div>
      <div className="iv-tabs">
        {CHAPTERS.map((item) => (
          <button key={item.id} className={`iv-tab${chapter === item.id ? ' active' : ''}`} onClick={() => setChapter(item.id)}>{item.label}</button>
        ))}
      </div>
      <div className="iv-body">
        {chapter === 'journey' && <JourneyChapter onAction={onAction} />}
        {chapter === 'landscape' && <LandscapeChapter onAction={onAction} />}
        {chapter === 'stages' && (
          <StagesChapter catalog={catalog} recipe={recipe} activeStage={activeStage} onPickStage={pickStage} onAddNode={onAddNode} onAction={onAction} />
        )}
        {chapter === 'deepdive' && <DeepDiveChapterTabs />}
        {chapter === 'experiments' && <ExperimentsChapter onAction={onAction} />}
      </div>
    </aside>
  )
}
