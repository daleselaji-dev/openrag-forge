import { TEACH_STEPS } from '../teach'
import type { BottomTab, RailTab } from '../types'

type Props = {
  stepIndex: number
  collapsed: boolean
  onSelectStep: (index: number) => void
  onToggleCollapsed: () => void
  onGo: (railTab?: RailTab, bottomTab?: BottomTab) => void
}

export default function TeachStrip({ stepIndex, collapsed, onSelectStep, onToggleCollapsed, onGo }: Props) {
  const step = TEACH_STEPS[Math.min(stepIndex, TEACH_STEPS.length - 1)]
  return (
    <div className="teach-strip">
      <div className="teach-strip-head">
        <span className="teach-strip-title">辅助教学</span>
        <div className="teach-steps">
          {TEACH_STEPS.map((item, index) => (
            <button key={item.id} className={`teach-step-chip${index === stepIndex ? ' active' : ''}`} onClick={() => onSelectStep(index)}>
              {index + 1}. {item.title}
            </button>
          ))}
        </div>
        <button className="link-btn" onClick={onToggleCollapsed}>{collapsed ? '展开课程' : '收起'}</button>
      </div>
      {!collapsed && (
        <div className="teach-strip-body">
          <div className="teach-copy">
            {step.body.map((paragraph, index) => <p key={index}>{paragraph}</p>)}
          </div>
          <div className="teach-side">
            {step.lookFor && step.lookFor.length > 0 && (
              <div className="teach-lookfor">
                <b>该看什么</b>
                <ul>{step.lookFor.map((item) => <li key={item}>{item}</li>)}</ul>
              </div>
            )}
            <div className="teach-nav">
              {step.action && <button className="primary small" onClick={() => onGo(step.action?.railTab, step.action?.bottomTab)}>{step.action.label}</button>}
              <button className="ghost small" disabled={stepIndex === 0} onClick={() => onSelectStep(stepIndex - 1)}>上一步</button>
              <button className="ghost small" disabled={stepIndex >= TEACH_STEPS.length - 1} onClick={() => onSelectStep(stepIndex + 1)}>下一步</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
