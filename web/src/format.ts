export const fmtMs = (value: number): string => {
  if (value >= 1000) return `${(value / 1000).toFixed(2)} s`
  if (value >= 10) return `${value.toFixed(1)} ms`
  return `${value.toFixed(2)} ms`
}

export const fmtBytes = (value: number): string => {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${value} B`
}

export const shortHash = (hash: string | null | undefined, length = 10): string => (hash ? hash.slice(0, length) : '—')

export const STATUS_LABELS: Record<string, string> = {
  completed: '完成',
  failed: '失败',
  skipped: '跳过',
  running: '运行中',
}

export const GROUP_LABELS: Record<string, string> = {
  ingest: '摄取',
  index: '索引',
  query: '查询',
  retrieve: '检索',
  generate: '生成',
  policy: '策略',
  agent: 'Agent',
  operations: '运维',
  optional: '扩展',
}
