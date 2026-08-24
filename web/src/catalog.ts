// 工作台 UI 文案：分组、runtime 标签、Block 类型说明、常用解析路由

export const GROUP_ORDER = ['ingest', 'index', 'query', 'retrieve', 'generate', 'policy', 'agent', 'operations', 'optional'] as const

export const GROUP_TITLES: Record<string, string> = {
  ingest: '解析 / Ingest',
  index: '索引 / Index',
  query: '查询预处理 / Query',
  retrieve: '检索 / Retrieve',
  generate: '生成 / Generate',
  policy: '安全策略 / Policy',
  agent: '受控 Agent',
  operations: '生产运维 / Operations',
  optional: '可选后端 / Optional',
}

export const RUNTIME_LABELS: Record<string, { label: string; hint: string }> = {
  implemented: { label: '运行级实现', hint: '运行层有真实行为；降级路径会写入 Trace' },
  degradable: { label: '后端可降级', hint: '有真实后端调用路径；后端不可用时如实直通并记录原因' },
  stub: { label: '编译完整 / 运行桩', hint: 'compile-complete / runtime-stub：编译期类型约束完整，运行层暂无真实后端' },
}

export const BLOCK_TYPE_DOCS: Record<string, { title: string; role: string }> = {
  heading: { title: '标题 Block', role: '文档结构骨架：为下游 Chunk 提供 title/heading_path 元数据，影响引用展示与 metadata 过滤。' },
  paragraph: { title: '段落 Block', role: '最常见的证据来源单位：Chunker 按窗口切分段落文本，chunk_id 直接来自这里。' },
  table: { title: '表格 Block', role: '整表文本化后的 Block：保留表格语义，适合被 tabular/结构化问题召回。' },
  row: { title: '行 Block', role: 'CSV/XLSX 的单行数据：行号写入 metadata，支持按行精确引用。' },
  page: { title: '页 Block', role: 'PDF 的整页文本：页码写入 metadata，是 pdf_page_retrieve 页级检索的候选池。' },
  code: { title: '代码 Block', role: '代码片段：保留原始格式供精确匹配。' },
  unknown: { title: '未知 Block', role: '解析器无法归类的内容：保留文本但建议检查解析路由是否正确。' },
}

export const PARSER_ROUTES = ['auto', 'native_text', 'html_structure', 'pdf_page_text', 'pdf_layout', 'office_structure', 'tabular', 'json_structure'] as const

export const STATUS_LABELS: Record<string, string> = {
  completed: '完成',
  failed: '失败',
  skipped: '跳过',
  running: '运行中',
}

// 从 Trace impact 字段挑选最有信息量的键做成 chips
export const IMPACT_CHIP_KEYS: { key: string; label: string }[] = [
  { key: 'backend', label: 'backend' },
  { key: 'candidate_count', label: '候选' },
  { key: 'evidence_count', label: '证据' },
  { key: 'provider', label: 'provider' },
  { key: 'skipped_reason', label: '跳过原因' },
  { key: 'fallback_reason', label: '降级原因' },
  { key: 'passthrough_reason', label: '直通原因' },
  { key: 'cache', label: 'cache' },
  { key: 'intent', label: '意图' },
  { key: 'retries_used', label: '重试' },
  { key: 'dropped_over_budget', label: '超预算丢弃' },
  { key: 'dropped_duplicates', label: '去重丢弃' },
  { key: 'citation_repaired', label: '引用修复' },
  { key: 'human_review', label: '人工复核' },
  { key: 'runtime', label: 'runtime' },
  { key: 'next_action', label: '下一步' },
]

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}
