import type { StageLesson } from '../interview/types'
import type { CatalogNode } from '../types'
import { IMPLEMENTED_LABELS } from '../teach'

const LANE_LABELS: Record<StageLesson['lane'], string> = {
  ingest: '摄取线',
  query: '查询线',
  crosscut: '横切件',
  system: '系统级',
}

type Props = {
  lesson: StageLesson
  /** 对应的目录节点（系统级环节没有） */
  spec?: CatalogNode | null
  /** 紧凑模式：用于检查器窄栏 */
  compact?: boolean
}

/** 面试讲解 · 单个环节的产品规格级讲解卡。检查器与环节地图共用。 */
export default function InterviewLessonCard({ lesson, spec, compact }: Props) {
  const impl = spec ? IMPLEMENTED_LABELS[spec.implemented] : null
  return (
    <div className={`iv-lesson${compact ? ' compact' : ''}`}>
      <div className="iv-lesson-head">
        <span className="iv-lane-chip">{LANE_LABELS[lesson.lane]}</span>
        <b>{lesson.title}</b>
        {impl && <span className={`impl-badge impl-${spec!.implemented}`} title={impl.note}>{impl.label}</span>}
      </div>

      <p className="iv-purpose">{lesson.purpose}</p>

      <div className="iv-block">
        <h5>影响面</h5>
        <div className="iv-impact-grid">
          <div><small>答案质量</small><p>{lesson.impact.quality}</p></div>
          <div><small>时延</small><p>{lesson.impact.latency}</p></div>
          <div><small>成本</small><p>{lesson.impact.cost}</p></div>
          <div><small>风险</small><p>{lesson.impact.risk}</p></div>
        </div>
      </div>

      <div className="iv-block">
        <h5>可调旋钮</h5>
        <ul className="iv-knobs">
          {lesson.knobs.map((knob) => (
            <li key={knob.name}>
              <span className={`iv-knob-chip${knob.effective ? '' : ' off'}`}>{knob.effective ? '生效' : '不生效'}</span>
              <b>{knob.name}</b> — {knob.effect}
            </li>
          ))}
        </ul>
      </div>

      <div className="iv-block">
        <h5>在本工作台怎么装配</h5>
        <p>{lesson.assembly}</p>
      </div>

      <div className="iv-block iv-dynamics">
        <h5>动力：为什么存在</h5>
        <p>{lesson.dynamics.why}</p>
        <div className="iv-dyn-pair">
          <div><small>缺了会怎样</small><p>{lesson.dynamics.ifMissing}</p></div>
          <div><small>过度设计会怎样</small><p>{lesson.dynamics.ifOverdone}</p></div>
        </div>
      </div>

      <div className="iv-block iv-live-status">
        <h5>live vs stub · 诚实状态</h5>
        <p>{lesson.liveStatus}</p>
      </div>

      <div className="iv-block">
        <h5>面试官可能追问</h5>
        {lesson.interviewQs.map((item) => (
          <details key={item.q} className="iv-qa">
            <summary>{item.q}</summary>
            <p>{item.a}</p>
          </details>
        ))}
      </div>
    </div>
  )
}
