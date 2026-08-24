import type { BottomTab, RailTab } from './types'

// 辅助教学课程内容。文案原则：诚实——明确区分「真实执行」与「占位/退化」，
// 不把目录能力说成已实现能力。内容与 docs/ 教学材料保持一致。

export type TeachStep = {
  id: string
  title: string
  body: string[]
  lookFor?: string[]
  action?: { label: string; railTab?: RailTab; bottomTab?: BottomTab }
  coachTarget?: string
}

export const TEACH_STEPS: TeachStep[] = [
  {
    id: 'overview',
    title: '认识 Control Room',
    body: [
      '这个工作台把一次 RAG 运行拆成可检查的环节：左轨切换 装配 / 数据 / 模型 / 场景，中间是 Recipe 画布，右侧检查器调配选中节点，底部是 Trace 与结果。',
      '先记住一条诚实原则：画布上带「占位」「退化」徽标的节点，表示目录里声明了这个能力、但执行器尚未真正实现（如 稀疏检索 / RRF / 重排）。Trace 里对应的 execution 标注会告诉你每一步真实发生了什么。',
    ],
    lookFor: ['画布节点上的「占位 / 退化」徽标', '顶栏的 profile 与生产就绪告警'],
    coachTarget: 'canvas',
  },
  {
    id: 'ingest',
    title: '上传与解析路由',
    body: [
      '文档进入知识库要过四道工序：解析路由（按文件签名选择解析器）→ Chunk 切分 → Metadata 保存 → Embedding 索引。前三步在本地真实执行；索引依赖 Qdrant 与 Embedding 服务，不可用时会「暂缓（deferred）」而不是失败——真相源（SQLite）始终保留原件。',
      'Chunk 大小可以调：在「装配」页选中 Custom Document Parsing 里的 Chunker 节点，修改 max_chars / overlap 并保存草稿，之后的上传/重解析会用新配置。',
    ],
    lookFor: ['Ingest Trace 的 route 置信度与 reason_codes', 'chunk 步骤里的 max_chars / overlap', 'index 步骤是 completed 还是 deferred'],
    action: { label: '去上传文档', railTab: 'data', bottomTab: 'ingest' },
    coachTarget: 'rail-data',
  },
  {
    id: 'recipe',
    title: 'Recipe 与编译',
    body: [
      'Recipe 是一张有类型端口的 DAG：每个节点声明输入/输出端口类型，编译器在保存与校验时拒绝端口不兼容和未声明的环（纠错循环必须用 bounded_corrective 显式声明）。',
      '编译通过后 Recipe 获得内容哈希（hash）。哈希进入每次运行的 Evidence Capsule——这就是「结果可复现」的锚点：同样的 Recipe 哈希 + 同样的文档版本，才谈得上复现。',
      '内置 Recipe 是已发布（published）的不可变版本；改任何配置都会自动生成草稿副本，不会污染原版。',
    ],
    lookFor: ['Recipe 卡片上的 hash 前缀', '校验失败时编译器给出的具体原因'],
    action: { label: '去选 Recipe', railTab: 'recipe' },
    coachTarget: 'rail-recipe',
  },
  {
    id: 'nodes',
    title: '节点与调配',
    body: [
      '点击画布节点，右侧检查器会显示结构化配置表单。绿色「生效」字段（如 稠密检索 top_k、LLM 温度、Chunker 尺寸）会真实影响执行；灰色「不生效」字段属于占位节点，改了只会保存、不会改变行为——表单不会骗你。',
      '模型绑定：把注册过的 Chat 模型绑到 LLM 生成节点的 model_ref，生成就走那个端点；Embedding 模型在上传时选择（查询侧必须与索引侧同模型）。',
    ],
    lookFor: ['检查器里的 live / 占位 / 退化 徽标与执行说明', '字段旁的「不生效」提示'],
    coachTarget: 'inspector',
  },
  {
    id: 'run',
    title: '运行与 Trace：如何证明一次运行',
    body: [
      'Preview 只编译结构、不调模型不写索引，适合先验证 DAG；真实运行会走完整链路。两种模式在 Trace 和结果区都有明显标识。',
      'Trace 是这套框架的立身之本：每个节点一行，带真实耗时（duration_ms）、状态、摘要和 execution 标注。点击 Trace 行会高亮画布对应节点，检查器同时显示该步的输入输出细节。',
      '双层追踪：业务 Trace（这里看到的）永久存在 SQLite；同一事件还会镜像成 OpenTelemetry span（启用 OPENRAG_OTEL_ENABLED 后），用响应头 X-Trace-Id 就能在 Jaeger 找到同一次请求的性能瀑布图。',
    ],
    lookFor: ['dense_retrieve 的 backend：qdrant_dense 还是 lexical_fallback（降级）', 'LLM 的 provider：openai_compatible_chat 还是 extractive_fallback', '占位节点的 stub_passthrough 标注'],
    action: { label: '去运行一次', bottomTab: 'trace' },
    coachTarget: 'run-buttons',
  },
  {
    id: 'capsule',
    title: 'Evidence Capsule 与安全门',
    body: [
      '每次运行导出一个 Evidence Capsule（JSON）：问题、Recipe 哈希、模型配置、全部证据引用、安全决策和完整 Trace。审计时不用相信截图，直接读胶囊。',
      '安全门是真实执行的，但当前只是关键词正则（如「保证退款」「认定违法」会被拒答并标记 human_review）。它挡不住改写攻击——这也是本项目还不宜接真实客户 SLA 的原因之一。',
      '试试问「你能保证我拿到退款吗？」，观察 Trace 里安全门如何提前终止链路。',
    ],
    lookFor: ['结果页的安全决策（side_effects / human_review / request_safety_gate）', '证据条目的 [S#] 引用与分数', '下载胶囊 JSON'],
    action: { label: '去看结果与证据', bottomTab: 'result' },
    coachTarget: 'bottom-result',
  },
  {
    id: 'scenario',
    title: '场景示范：该看哪些 Trace',
    body: [
      '场景卡片声明业务问题、所需资料、默认 Recipe 和「应观察的 Trace」。教学模式下每张卡都会列出观察清单——运行后逐条对照，你就知道一条链路有没有按声明工作。',
      '注意：客服/政策场景默认引用 hybrid / rerank Recipe，但稀疏检索与重排当前是占位——对照 Trace 你会看到实际只有稠密（或词法回退）一路候选。这正是「用 Trace 证明，而不是用目录宣传」的练习。',
    ],
    lookFor: ['场景卡的 Trace 观察清单', '运行后逐条核对是否真的发生'],
    action: { label: '去看场景', railTab: 'scenario' },
    coachTarget: 'rail-scenario',
  },
]

export const EXECUTION_LABELS: Record<string, { label: string; tone: 'live' | 'warn' | 'stub' | 'preview' }> = {
  live: { label: '真实执行', tone: 'live' },
  fallback_lexical: { label: '词法回退', tone: 'warn' },
  fallback_shared_dense: { label: '共享稠密路径', tone: 'warn' },
  fallback_extractive: { label: '摘要降级', tone: 'warn' },
  fallback_deferred: { label: '索引暂缓', tone: 'warn' },
  stub_passthrough: { label: '占位直通', tone: 'stub' },
  preview_compile_only: { label: 'Preview 编译', tone: 'preview' },
}

export const IMPLEMENTED_LABELS: Record<string, { label: string; note: string }> = {
  live: { label: 'LIVE', note: '该节点声明的逻辑真实执行' },
  fallback: { label: '退化', note: '未独立实现：执行时退化为共享路径' },
  stub: { label: '占位', note: '仅在 Trace 中记录经过，不改变数据' },
}
