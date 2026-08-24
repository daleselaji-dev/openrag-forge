import type { Scenario } from '../../types'

type Props = {
  scenarios: Scenario[]
  teachOn: boolean
  onUseScenario: (scenario: Scenario) => void
  onImportScenario: (file: File) => void
}

export default function ScenarioRail({ scenarios, teachOn, onUseScenario, onImportScenario }: Props) {
  return (
    <div className="rail-panel">
      <div className="rail-head">
        <h3>场景示范</h3>
        <label className="import-scenario">
          导入 JSON
          <input type="file" accept="application/json,.json" onChange={(event) => { const file = event.target.files?.[0]; if (file) onImportScenario(file); event.target.value = '' }} />
        </label>
      </div>
      {teachOn && <p className="teach-hint">教学：每个场景声明业务问题、所需资料与「应观察的 Trace」。加载示范只填入问题与 Recipe——运行前先在「数据」页导入对应资料，然后按观察清单逐条核对 Trace。</p>}
      <div className="scenario-list">
        {scenarios.map((scenario) => (
          <article className="scenario-card" key={scenario.scenario_id}>
            <span className="eyebrow">{scenario.recipe_id} · {scenario.source || 'builtin'}</span>
            <h4>{scenario.title}</h4>
            <p>{scenario.business_problem}</p>
            <small>所需资料：{scenario.data_requirements.join(' · ') || '—'}</small>
            <code>{scenario.sample_question}</code>
            {teachOn && scenario.trace_expectation.length > 0 && (
              <div className="trace-expectation">
                <b>应观察的 Trace</b>
                <ul>
                  {scenario.trace_expectation.map((item) => <li key={item}>{item}</li>)}
                </ul>
              </div>
            )}
            <button className="ghost small" onClick={() => onUseScenario(scenario)}>加载示范</button>
          </article>
        ))}
      </div>
    </div>
  )
}
